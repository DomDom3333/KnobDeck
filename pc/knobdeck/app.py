"""KnobDeck PC app: connects to the deck, runs actions, pushes live state, serves the settings window."""
from __future__ import annotations

import argparse
import concurrent.futures
import gc
import logging
import logging.handlers
import queue
import sys
import threading
import time
from pathlib import Path

from . import __version__
from .config import ConfigStore, data_dir, deck_text, device_layout, used_icons
from .device import Device
from .iconconv import icon_hash, kdi_from_bytes, to_kdi
from .plugin import REGISTRY, DialState, Entry, Event, People, State
from .screens import Screen, TileScreen

log = logging.getLogger("knobdeck")


class Context:
    """What plugins get to talk to the app."""

    def __init__(self, app: "App"):
        self._app = app
        self.log = logging.getLogger("knobdeck.plugins")

    # ------------------------------------------------------------ basics
    def toast(self, text: str, ms: int = 1800):
        self._app.device.send({"t": "toast", "text": self._app.text(text, 60), "ms": ms})

    def goto(self, page_id: str):
        self._app.device.send({"t": "goto", "page": page_id})

    def settings(self, plugin_id: str) -> dict:
        spec = REGISTRY.settings.get(plugin_id, {"params": []})
        values = {p.key: p.default for p in spec["params"]}
        values.update(self._app.store.data.get("plugins", {}).get(plugin_id, {}))
        return values

    def refresh(self):
        """Ask for an immediate state refresh (e.g. after an action changed something)."""
        self._app.post(self._app.poll_states)

    def notify(self):
        """Something changed (may be called from any thread): refresh tiles and the people screen soon."""
        self._app.notify()

    # ------------------------------------------------------------ what the deck can do
    @property
    def firmware(self) -> str:
        return str(self._app.device.info.get("fw") or "")

    @property
    def caps(self) -> set:
        """e.g. {"screens", "popup", "led", "beep", "ir", "ir_learn", "utf8"} (empty for firmware 1.x)"""
        return set(self._app.device.info.get("caps") or [])

    def has(self, cap: str) -> bool:
        return self._app.has_cap(cap)

    # ------------------------------------------------------------ screens (firmware 2.x)
    def screen(self, id: str, on_event=None) -> Screen:
        """Handle for a screen that isn't tied to a tile (e.g. opened by an action or a background event).
        on_event(ev: Event) gets the knob input while it's open. Draw with .list() / .card() / .canvas() ..."""
        return self._app.get_screen(id, on_event)

    def show(self, id: str, tpl: str, on_event=None, **fields) -> Screen:
        scr = self.screen(id, on_event)
        scr.show(tpl, **fields)
        return scr

    def close(self, id: str | None = None):
        """Close a screen (any open screen when id is None) and go back to the tiles."""
        scr = self._app.active_screen
        if scr and (id is None or scr.id == id):
            scr.close()

    def popup(self, title: str, text: str = "", icon="sym:bell", color: str = "#3D8BFD", ms: int = 5000,
              id: str = "popup", on_dismiss=None, sound: str | None = None):
        """Notification over whatever is on screen; a knob press closes it (then on_dismiss() runs).
        ms=0 keeps it until pressed. sound: "notify", "ok", "error", "alarm", "click"."""
        if not self.has("popup"):
            self.toast(f"{title}: {text}" if text else title, max(ms, 1500) if ms else 4000)
            return
        if on_dismiss:
            self._app.popups[id] = on_dismiss
        self._app.device.send({"t": "popup", "id": id[:23], "title": self._app.text(title, 120),
                               "text": self._app.text(text, 300), "icon": self._app.image(icon) if icon and not
                               isinstance(icon, str) else icon, "color": color, "ms": int(ms)})
        if sound:
            self.beep(sound)

    def popup_close(self):
        self._app.device.send({"t": "popup_close"})

    # ------------------------------------------------------------ hardware (firmware 2.x)
    def led(self, color: str = "#FFFFFF", effect: str = "solid", period: int = 1000, bright: int = 30, ms: int = 0):
        """RGB LED: effect solid | blink | pulse | rainbow | off; period in ms; ms>0 = switch off after that."""
        if self.has("led"):
            self._app.device.send({"t": "led", "color": color, "effect": effect, "period": int(period),
                                   "bright": int(bright), "ms": int(ms)})

    def led_off(self):
        self.led("#000000", "off")

    def beep(self, sound: str | None = None, freq: int | None = None, ms: int = 100, tones=None, vol: int = 60):
        """Speaker: a preset `sound` (click, ok, error, notify, alarm), one tone (freq Hz, ms) or a melody
        tones=[(freq, ms), ...] (freq 0 = pause)."""
        if not self.has("beep"):
            return
        msg = {"t": "beep", "vol": int(vol)}
        if sound:
            msg["sound"] = sound
        if tones:
            msg["tones"] = [[int(f), int(d)] for f, d in tones][:32]
        elif freq:
            msg.update(freq=int(freq), ms=int(ms))
        self._app.device.send(msg)

    def ir_send(self, raw, khz: int = 38, repeat: int = 0):
        """Send an IR code: raw = [mark_us, space_us, mark_us, ...] (as learned) or a Pronto hex string."""
        from .ircodes import parse_code
        raw, k = parse_code(raw)
        if not self.has("ir"):
            raise RuntimeError("the deck firmware has no IR support (update to 2.0)")
        self._app.device.send({"t": "ir", "raw": raw, "khz": int(k or khz), "repeat": int(repeat)})

    def ir_learn(self, timeout: float = 10) -> list | None:
        """Wait for a remote control to be pointed at the deck; returns the raw timings or None.
        Blocks up to `timeout` s - call it from your own thread for long waits."""
        return self._app.ir_learn(timeout)

    # ------------------------------------------------------------ images
    def image(self, src, size: int = 48, circle: bool = False) -> str:
        """Put a picture on the deck and get its icon name ("img:...") for tiles, lists, cards, popups, canvas.
        src: file path, URL, bytes or PIL image. size up to 128 px (big images take RAM on the deck)."""
        return self._app.image(src, size, circle)


class App:
    def __init__(self):
        self.store = ConfigStore()
        self.ctx = Context(self)
        self.device = Device(self.on_message, self.on_connect, self.on_disconnect,
                             lambda: self.store.data["device"].get("port", "auto"))
        self.jobs: queue.Queue = queue.Queue()
        self.sync_lock = threading.Lock()
        self.screen = "deck"
        self.page = "home"
        self.dial_tile: dict | None = None
        self.stats_tile: dict | None = None
        self.people_tile: dict | None = None
        self.sent_people: dict | None = None
        self.avatars: dict[str, str | None] = {}       # avatar url -> icon hash (None while downloading)
        self._notify_pending = False
        self.sent_states: dict[str, dict] = {}
        self.screens: dict[str, Screen] = {}            # screen id -> handle
        self.active_screen: Screen | None = None        # plugin screen the deck is showing
        self.popups: dict[str, object] = {}             # popup id -> on_dismiss callback
        self.images: dict[str, str] = {}                # source key -> icon hash
        self.dynamic_images: list[str] = []             # images sent by plugins (recently used last)
        self._ir_waiter: dict | None = None
        self.fw_status = {"state": "idle", "progress": 0, "message": ""}
        self.config_msg = {"text": "", "time": 0}
        self.background = False             # started hidden: tray icon only while the deck is connected
        self.last_error = ""
        self._stop = threading.Event()

    # ---------------------------------------------------------------- plugins
    def load_plugins(self):
        builtin = Path(__file__).parent / "plugins"
        REGISTRY.load_dir(builtin, "knobdeck.plugins")
        REGISTRY.load_dir(data_dir() / "plugins", "knobdeck_user_plugins")
        log.info("%d actions from plugins", len(REGISTRY.entries))

    def entry_for(self, tile: dict | None) -> tuple[Entry | None, dict]:
        if not tile:
            return None, {}
        e = REGISTRY.get(tile.get("action") or "")
        if not e:
            return None, {}
        params = e.defaults()
        params.update(tile.get("params") or {})
        return e, params

    def config_event(self, text: str):
        """The settings changed without the settings window (loaded from the deck): tell the user."""
        self.config_msg = {"text": text, "time": time.time()}
        if self.has_cap("popup"):
            self.ctx.popup("KnobDeck", text, icon="sym:sync", color="#43A047", ms=4000)
        else:
            self.ctx.toast(text, 3000)

    # ---------------------------------------------------------------- deck capabilities / text
    def has_cap(self, cap: str) -> bool:
        return cap in (self.device.info.get("caps") or [])

    def text(self, s, limit: int = 31) -> str:
        return deck_text(s, limit, self.has_cap("utf8"))

    # ---------------------------------------------------------------- plugin screens
    def get_screen(self, id: str, on_event=None) -> Screen:
        scr = self.screens.get(str(id)[:23])
        if not scr or isinstance(scr, TileScreen):
            scr = Screen(self, id, on_event)
            self.screens[scr.id] = scr
        elif on_event:
            scr.on_event = on_event
        return scr

    def screen_shown(self, scr: Screen):
        if self.active_screen is not scr:
            self.screen_left(None)
            self.active_screen = scr
            self.screen = "plugin"

    def screen_left(self, scr: Screen | None):
        """The deck left the plugin screen (scr=None: whatever was open)."""
        old = self.active_screen
        if old and (scr is None or old is scr):
            self.active_screen = None
            old._last = None
            self.post(self._screen_event, old, Event("close"))

    def _screen_event(self, scr: Screen, ev: Event):
        if ev.type == "turn":
            # merge queued turns so a fast spin doesn't lag behind
            with self.jobs.mutex:
                q = self.jobs.queue
                while q and q[0][0] == self._screen_event and q[0][1][0] is scr and q[0][1][1].type == "turn":
                    ev.delta += q.popleft()[1][1].delta
        try:
            scr._event(ev)
        except Exception as ex:
            log.exception("screen %s: %s failed", scr.id, ev.type)
            self.ctx.toast(str(ex)[:60], 2500)

    def open_tile_screen(self, tile: dict):
        scr = TileScreen(self, tile)
        self.screens[scr.id] = scr
        self.screen_left(None)
        self.active_screen = scr          # the deck already shows a "Loading..." card for it
        self.screen = "plugin"
        e, params = self.entry_for(tile)
        if not e or e.kind != "screen":
            scr.card(title=tile.get("label") or "Screen", text="No screen chosen for this tile in the settings",
                     icon="sym:warning", color="#FB8C00")
            return
        try:
            e.fn.open(self.ctx, params, scr)
        except Exception as ex:
            log.exception("screen %s failed to open", e.id)
            scr.card(title=e.name, text=f"{type(ex).__name__}: {ex}", icon="sym:warning", color="#E53935")

    def poll_screen(self):
        scr = self.active_screen
        if isinstance(scr, TileScreen):
            e, params = self.entry_for(scr.tile)
            if e and e.kind == "screen" and hasattr(e.fn, "poll"):
                e.fn.poll(self.ctx, params, scr)

    # ---------------------------------------------------------------- images for plugins
    def image(self, src, size: int = 48, circle: bool = False) -> str:
        if isinstance(src, str) and src.startswith(("sym:", "txt:", "img:")):
            return src
        size = max(8, min(int(size), int(self.device.info.get("img_max") or 48)))
        key = None
        if isinstance(src, (str, Path)) and not str(src).startswith(("http://", "https://")):
            p = Path(src)
            key = f"{p.resolve()}|{p.stat().st_mtime}|{size}|{circle}"
        elif isinstance(src, str):
            key = f"{src}|{size}|{circle}"
        elif isinstance(src, (bytes, bytearray)):
            import hashlib
            key = hashlib.sha1(bytes(src)).hexdigest() + f"|{size}|{circle}"
        h = self.images.get(key) if key else None
        if not h:
            from PIL import Image
            import io
            if isinstance(src, Image.Image):
                img = src
            elif isinstance(src, (bytes, bytearray)):
                img = Image.open(io.BytesIO(src))
            elif str(src).startswith(("http://", "https://")):
                import urllib.request
                req = urllib.request.Request(str(src), headers={"User-Agent": f"KnobDeck/{__version__}"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    img = Image.open(io.BytesIO(r.read()))
            else:
                img = Image.open(src)
            kdi = to_kdi(img, size, circle)
            h = icon_hash(kdi)
            f = data_dir() / "icons" / f"{h}.kdi"
            if not f.exists():
                f.write_bytes(kdi)
            if key:
                self.images[key] = h
        self._ensure_on_deck(h)
        return f"img:{h}"

    def _ensure_on_deck(self, h: str):
        have = self.device.info.setdefault("icons", [])
        if h in self.dynamic_images:
            self.dynamic_images.remove(h)
        self.dynamic_images.append(h)
        if h in have:
            return True
        f = data_dir() / "icons" / f"{h}.kdi"
        if not self.device.upload_icon(h, f.read_bytes()):
            return False
        have.append(h)
        if len(self.dynamic_images) > 24:     # keep the deck's storage from filling up with old pictures
            keep = used_icons(self.store.data) | set(self.dynamic_images[-12:]) | \
                {v for v in self.avatars.values() if v}
            self.dynamic_images = self.dynamic_images[-12:]
            self.device.send({"t": "icon_gc", "keep": sorted(keep)})
            self.device.info["icons"] = [i for i in have if i in keep]
        return True

    # ---------------------------------------------------------------- IR learning
    def ir_learn(self, timeout: float = 10) -> list | None:
        if not self.has_cap("ir_learn"):
            raise RuntimeError("the deck firmware can't learn IR codes (update to 2.0)")
        w = {"ev": threading.Event(), "raw": None}
        self._ir_waiter = w
        self.device.send({"t": "ir_learn", "ms": int(timeout * 1000)})
        w["ev"].wait(timeout + 2)
        self._ir_waiter = None
        return w["raw"]

    # ---------------------------------------------------------------- worker
    def post(self, fn, *args):
        self.jobs.put((fn, args))

    def call_in_worker(self, fn, *args, timeout: float = 15):
        """Run fn on the worker thread and wait for the result (plugins may rely on per-thread COM state)."""
        fut: concurrent.futures.Future = concurrent.futures.Future()

        def job():
            try:
                fut.set_result(fn(*args))
            except Exception as e:
                fut.set_exception(e)
        self.post(job)
        return fut.result(timeout)

    def _worker(self):
        if sys.platform == "win32":
            try:
                import comtypes
                comtypes.CoInitialize()      # audio plugins use COM from this thread
            except Exception:
                pass
        last_gc = time.monotonic()
        while not self._stop.is_set():
            # Python's automatic garbage collection is off (see start_background): collect here, on the thread
            # that owns the COM objects, so Windows audio objects are never released from another thread
            if time.monotonic() - last_gc > 2.0:
                gc.collect()
                last_gc = time.monotonic()
            try:
                fn, args = self.jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                fn(*args)
            except Exception as e:
                log.exception("job %s failed", getattr(fn, "__name__", fn))
                self.last_error = f"{type(e).__name__}: {e}"
            finally:
                fn = args = None             # drop references here, not on the next loop in another state

    def _scheduler(self):
        n = 0
        while not self._stop.wait(0.5):
            n += 1
            if not self.device.connected:
                continue
            if self.screen == "dial" and self.dial_tile:
                self.post(self.dial_poll)
            if n % 2 == 0 and isinstance(self.active_screen, TileScreen):
                self.post(self.poll_screen)
            if n % 2 == 0:
                self.post(self.poll_states)
                if self.screen == "stats" and self.stats_tile:
                    self.post(self.push_stats)
                if self.screen == "people" and self.people_tile:
                    self.post(self.push_people)
            if n % 1200 == 0:
                self.send_time()

    # ---------------------------------------------------------------- device events
    def on_connect(self):
        if not self.sync_lock.acquire(blocking=False):
            return
        try:
            self.sync()
        except Exception:
            log.exception("sync failed")
        finally:
            self.sync_lock.release()

    def on_disconnect(self):
        log.info("deck disconnected")
        self.screen = "deck"
        self.dial_tile = self.stats_tile = self.people_tile = None
        self.screen_left(None)

    def notify(self):
        if self._notify_pending:
            return
        self._notify_pending = True

        def job():
            self._notify_pending = False
            if self.screen == "people" and self.people_tile:
                self.push_people()
            self.poll_states()
        threading.Timer(0.1, self.post, args=(job,)).start()

    def sync(self):
        info = self.device.info
        log.info("deck connected: fw %s, layout %s", info.get("fw"), info.get("v"))
        try:
            from . import sync as cfgsync
            cfgsync.sync_on_connect(self)            # full settings live on the deck too (firmware 2.1+)
        except Exception:
            log.exception("settings sync with the deck failed")
        cfg = self.store.data
        d = cfg["device"]
        self.device.send({"t": "cfg", "bright": int(d["brightness"]), "dim": int(d["dim"]),
                          "idle": int(d["idle"]), "invert": bool(d["invert"]), "click": bool(d.get("click")),
                          "ir_pin": int(d.get("ir_pin", 4))})
        self.send_time()
        needed = used_icons(cfg)
        have = set(info.get("icons") or [])
        for h in sorted(needed - have):
            f = data_dir() / "icons" / f"{h}.kdi"
            if f.exists() and self.device.upload_icon(h, f.read_bytes()):
                have.add(h)
        layout = device_layout(cfg, self.has_cap("utf8"))
        if info.get("v") != layout["v"]:
            self.device.send(layout)
            if self.device.wait_ack("layout", 5.0):
                info["v"] = layout["v"]
            else:
                log.warning("deck did not confirm the layout")
        if have - needed:
            self.device.send({"t": "icon_gc", "keep": sorted(needed)})
            info["icons"] = sorted(needed & have)
        else:
            info["icons"] = sorted(have)
        self.sent_states.clear()
        self.screen_left(None)
        self.screen, self.page = "deck", "home"
        self.post(self.poll_states)

    def send_time(self):
        lt = time.localtime()
        self.device.send({"t": "time", "epoch": int(time.time()), "tz": int(lt.tm_gmtoff // 60)})

    def on_message(self, msg: dict):
        t = msg.get("t")
        tile = None
        if msg.get("id"):
            _, tile = self.store.find_tile(msg["id"])
        if t == "press" and tile:
            self.post(self.run_tile, tile, False)
        elif t == "long" and tile:
            self.post(self.run_tile, tile, True)
        elif t == "page":
            self.page = msg.get("id") or self.page
            self.post(self.poll_states)
        elif t == "input":
            sid, ev = str(msg.get("id") or ""), str(msg.get("ev") or "")
            if ev == "dismiss":
                cb = self.popups.pop(sid, None)
                if cb:
                    self.post(cb)
            elif self.active_screen and self.active_screen.id == sid:
                self.post(self._screen_event, self.active_screen,
                          Event(ev, int(msg.get("d") or 0), str(msg.get("item") or "")))
        elif t == "ir":
            w = self._ir_waiter
            if w:
                w["raw"] = msg.get("raw") if msg.get("raw") else None
                w["ev"].set()
        elif t == "screen":
            name = msg.get("name")
            self.screen_left(None)
            if name == "screen" and tile:
                self.post(self.open_tile_screen, tile)
                return
            self.screen = name
            self.dial_tile = tile if name == "dial" else None
            self.stats_tile = tile if name == "stats" else None
            self.people_tile = tile if name == "people" else None
            self.sent_people = None
            if name == "dial" and tile:
                self.post(self.dial_call, tile, "open", None)
            elif name == "stats" and tile:
                self.post(self.push_stats)
            elif name == "people" and tile:
                self.post(self.push_people)
            elif name == "deck":
                self.page = msg.get("page") or self.page
        elif t == "dial" and tile:
            self.post(self.dial_call, tile, "turn", int(msg.get("d") or 0))
        elif t == "dial_press" and tile:
            self.post(self.dial_call, tile, "press", None)
        elif t == "people_press" and tile:
            self.post(self.people_press, tile, str(msg.get("item") or ""))

    # ---------------------------------------------------------------- actions
    def run_tile(self, tile: dict, long: bool = False):
        if long:
            spec = tile.get("long") or {}
            if not spec.get("action"):
                return
            tile = {"action": spec["action"], "params": spec.get("params") or {}}
        e, params = self.entry_for(tile)
        if not e:
            self.ctx.toast("No action set")
            return
        if e.kind == "screen":
            self.ctx.toast("Screens need deck firmware 2.0", 2500)
            return
        if e.kind != "action":
            return
        try:
            e.fn(self.ctx, params)
        except Exception as ex:
            log.exception("action %s failed", e.id)
            self.ctx.toast(f"{e.name}: {ex}"[:40], 2500)
            return
        if e.state_fn:
            time.sleep(0.15)
            self.poll_states(only=tile.get("id"))

    def test_tile(self, tile: dict) -> str:
        """Run an action from the settings window; returns an error text or ''."""
        e, params = self.entry_for(tile)
        if not e:
            return "No action selected"
        try:
            if e.kind == "action":
                e.fn(self.ctx, params)
            elif e.kind == "dial":
                st = e.fn.open(self.ctx, params)
                return f"OK - current value: {st.text or st.value}"
            elif e.kind == "stats":
                rings = e.fn(self.ctx, params)
                return "OK - " + ", ".join(f"{r.label} {r.text}" for r in rings)
            elif e.kind == "screen":
                if not self.device.connected:
                    return "Connect the deck to try a screen"
                if not self.has_cap("screens"):
                    return "Screens need deck firmware 2.0 - update it in the Device tab"
                tile = dict(tile, id=tile.get("id") or "test")
                self.device.send({"t": "show", "id": tile["id"][:23], "tpl": "card", "title": "...", "text": ""})
                self.open_tile_screen(tile)
                return "OK - shown on the deck (hold the knob to leave)"
            elif e.kind == "people":
                ppl = e.fn.get(self.ctx, params)
                names = ", ".join(p.name + (" (speaking)" if p.speaking else "") for p in ppl.items)
                return f"OK - {ppl.title}: {names}" if ppl.items else f"OK - {ppl.text or 'nobody there'}"
        except Exception as ex:
            return f"{type(ex).__name__}: {ex}"
        return ""

    # ---------------------------------------------------------------- live state
    def poll_states(self, only: str | None = None):
        if not self.device.connected:
            return
        for p in self.store.data["pages"]:
            for tile in p["tiles"]:
                if not tile or (only and tile["id"] != only):
                    continue
                e, params = self.entry_for(tile)
                fn = e.state_fn if e else None
                if e and not fn and e.kind in ("dial", "people", "screen"):
                    fn = getattr(e.fn, "state", None)
                if not fn:
                    continue
                try:
                    st = fn(self.ctx, params)
                except Exception as ex:
                    log.debug("state %s: %s", e.id, ex)
                    continue
                self._send_state(tile["id"], st)

    def _send_state(self, tile_id: str, st: State | None):
        msg = {"t": "state", "id": tile_id, "clear": True}
        if st:
            for k in ("label", "icon", "value"):
                v = getattr(st, k)
                if v is not None:
                    msg[k] = self.text(v, 31 if k != "value" else 19)
            if st.color is not None:
                msg["color"] = st.color
        if self.sent_states.get(tile_id) != msg:
            self.sent_states[tile_id] = msg
            self.device.send(msg)

    # ---------------------------------------------------------------- dial / stats
    def dial_call(self, tile: dict, what: str, arg):
        e, params = self.entry_for(tile)
        if not e or e.kind != "dial":
            self.ctx.toast("No dial action set")
            return
        obj = e.fn
        if what == "open":
            st = obj.open(self.ctx, params)
        elif what == "turn":
            # merge queued turns so a fast spin doesn't lag behind
            with self.jobs.mutex:
                q = self.jobs.queue
                while q and q[0][0] == self.dial_call and q[0][1][0] is tile and q[0][1][1] == "turn":
                    arg += q.popleft()[1][2]
            st = obj.turn(self.ctx, params, arg)
        else:
            st = obj.press(self.ctx, params) if hasattr(obj, "press") else None
        if st:
            self._send_dial(tile, st)

    def dial_poll(self):
        tile = self.dial_tile
        e, params = self.entry_for(tile)
        if tile and e and e.kind == "dial" and hasattr(e.fn, "poll"):
            st = e.fn.poll(self.ctx, params)
            if st:
                self._send_dial(tile, st)

    def _send_dial(self, tile, st: DialState):
        self.device.send({"t": "dial", "id": tile["id"], "value": int(st.value), "min": int(st.min),
                          "max": int(st.max), "step": int(st.step), "text": self.text(st.text, 19)})

    def push_stats(self):
        tile = self.stats_tile
        e, params = self.entry_for(tile)
        if not tile or not e or e.kind != "stats":
            return
        rings = e.fn(self.ctx, params) or []
        out = []
        for r in rings[:3]:
            d = {"label": self.text(r.label, 12), "value": float(r.value), "max": float(r.max), "text": self.text(r.text, 20)}
            if r.color:
                d["color"] = r.color
            out.append(d)
        self.device.send({"t": "stats", "rings": out})

    # ---------------------------------------------------------------- people screen
    def people_press(self, tile: dict, item: str):
        e, params = self.entry_for(tile)
        if e and e.kind == "people" and hasattr(e.fn, "press"):
            try:
                e.fn.press(self.ctx, params, item)
            except Exception as ex:
                self.ctx.toast(str(ex)[:40], 2500)
            self.notify()

    def _avatar_icon(self, url: str) -> str | None:
        """Icon name for an avatar URL once it's downloaded and on the deck; starts the download otherwise."""
        if url not in self.avatars:
            self.avatars[url] = None
            threading.Thread(target=self._fetch_avatar, args=(url,), daemon=True).start()
            return None
        h = self.avatars[url]
        if not h:
            return None
        have = self.device.info.setdefault("icons", [])
        if h not in have:
            f = data_dir() / "icons" / f"{h}.kdi"
            if not self.device.upload_icon(h, f.read_bytes()):
                return None
            have.append(h)
        return h

    def _fetch_avatar(self, url: str):
        import urllib.request
        try:
            req = urllib.request.Request(url, headers={"User-Agent": f"KnobDeck/{__version__}"})
            with urllib.request.urlopen(req, timeout=8) as r:
                kdi = kdi_from_bytes(r.read(), circle=True)
            h = icon_hash(kdi)
            (data_dir() / "icons" / f"{h}.kdi").write_bytes(kdi)
            self.avatars[url] = h
            self.notify()
        except Exception as ex:
            log.debug("avatar %s: %s", url, ex)
            self.avatars.pop(url, None)

    def push_people(self):
        tile = self.people_tile
        e, params = self.entry_for(tile)
        if not tile or not e or e.kind != "people":
            return
        ppl: People = e.fn.get(self.ctx, params) or People()
        items = []
        for p in ppl.items[:8]:
            icon = p.icon
            if p.avatar_url:
                h = self._avatar_icon(p.avatar_url)
                if h:
                    icon = f"img:{h}"
            if not icon:
                letters = "".join(w[0] for w in self.text(p.name).split()[:2]) or self.text(p.name)[:2] or "?"
                icon = "txt:" + letters[:2].upper()
            items.append({"id": str(p.id)[:23], "name": self.text(p.name, 31), "icon": icon[:23], "color": p.color,
                          "speaking": bool(p.speaking), "muted": bool(p.muted), "deaf": bool(p.deaf)})
        title = self.text(ppl.title, 31)
        if len(ppl.items) > 8:
            title = self.text(f"{ppl.title} +{len(ppl.items) - 8}", 31)
        msg = {"t": "people", "id": tile["id"], "title": title, "text": self.text(ppl.text, 31), "items": items}
        if msg != self.sent_people:
            self.sent_people = msg
            self.device.send(msg)

    # ---------------------------------------------------------------- settings window
    def save_config(self, cfg: dict):
        self.store.save(cfg, touch=True)
        if self.device.connected:
            threading.Thread(target=self.on_connect, name="sync", daemon=True).start()

    # ---------------------------------------------------------------- run
    def start_background(self):
        # The automatic garbage collector runs on whichever thread happens to allocate memory (USB, web
        # server, sync ...). If it frees a leftover Windows audio (COM) object there, a thread without COM
        # releases it and the whole process crashes without a Python error. So collect only on the worker.
        gc.collect()
        gc.disable()
        self.load_plugins()
        threading.Thread(target=self._worker, name="worker", daemon=True).start()
        threading.Thread(target=self._scheduler, name="scheduler", daemon=True).start()
        self.device.start()

    def stop(self):
        self._stop.set()
        self.device.stop()


def setup_logging(verbose: bool):
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    logging.getLogger("comtypes").setLevel(logging.INFO)     # its DEBUG lines (every COM Release) drown the log
    fh = logging.handlers.RotatingFileHandler(data_dir() / "knobdeck.log", maxBytes=1_000_000, backupCount=2,
                                              encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr and sys.stdout:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)


def install_crash_logging():
    """pythonw has no console: make sure every crash ends up in the log folder."""
    import faulthandler
    import traceback
    crash = open(data_dir() / "crash.log", "a", encoding="utf-8", buffering=1)
    crash.write(f"\n=== start {time.strftime('%Y-%m-%d %H:%M:%S')} (KnobDeck {__version__}, Python {sys.version.split()[0]}) ===\n")
    faulthandler.enable(file=crash, all_threads=True)          # native crashes (e.g. in audio drivers)

    def hook(exc_type, exc, tb):
        log.critical("unhandled error:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
        crash.write("".join(traceback.format_exception(exc_type, exc, tb)))
    sys.excepthook = hook
    threading.excepthook = lambda a: hook(a.exc_type, a.exc_value, a.exc_traceback)
    return crash


def import_session_env(wait: float = 60):
    """Linux: when systemd starts us (deck plugged in), take the desktop session's variables (display, D-Bus ...)
    from the user manager, waiting a little if the desktop isn't fully up yet (deck plugged in at boot)."""
    import os
    import subprocess
    if not sys.platform.startswith("linux") or os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return
    keys = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP",
            "DESKTOP_SESSION", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR", "XDG_SESSION_ID")
    end = time.monotonic() + wait
    while True:
        try:
            out = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True, text=True,
                                 timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            return
        env = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        if env.get("DISPLAY") or env.get("WAYLAND_DISPLAY") or time.monotonic() > end:
            for k in keys:
                if env.get(k) and not os.environ.get(k):
                    os.environ[k] = env[k]
            log.info("desktop session: %s", env.get("WAYLAND_DISPLAY") or env.get("DISPLAY") or "none found")
            return
        time.sleep(2)


def main():
    ap = argparse.ArgumentParser(prog="knobdeck", description="KnobDeck PC app")
    ap.add_argument("--no-tray", action="store_true", help="run without the tray icon (console mode)")
    ap.add_argument("--settings", action="store_true", help="open the settings window on start")
    ap.add_argument("--background", action="store_true",
                    help="start hidden: no window, tray icon only while the deck is plugged in (used by autostart)")
    ap.add_argument("--port", type=int, default=7413, help="port of the local settings server")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    setup_logging(args.verbose)
    install_crash_logging()

    from . import autostart, webui
    if webui.already_running(args.port):
        if not args.background:
            webui.open_settings(args.port)    # second start: just show the window of the running app
        return
    if args.background:
        import_session_env()

    log.info("KnobDeck %s starting%s, data in %s", __version__, " in the background" if args.background else "",
             data_dir())
    autostart.first_run_default()
    app = App()
    app.background = args.background
    app.start_background()
    server = webui.start_server(app, args.port)
    if args.settings:
        webui.open_settings(server.server_port)
    if args.no_tray:
        log.info("running without tray. Settings: http://127.0.0.1:%d/  (Ctrl+C to quit)", server.server_port)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    else:
        try:
            from .tray import run_tray         # importing pystray already needs a working desktop session
            run_tray(app, server.server_port)
        except Exception:
            log.exception("tray icon failed - KnobDeck keeps running without it (settings: http://127.0.0.1:%d/)",
                          server.server_port)
            while not app._stop.wait(1):
                pass
    log.info("KnobDeck exiting")
    app.stop()
    server.shutdown()
