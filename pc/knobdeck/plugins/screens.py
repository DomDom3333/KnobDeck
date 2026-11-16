"""Built-in screens (deck firmware 2.x). They double as examples for the screen API - see PLUGINS.md."""
import time

import psutil

from knobdeck import osutil
from knobdeck.plugin import Param, State, arc, chart, image, label, list_item, screen

# ------------------------------------------------------------------ power menu: list + confirm card

POWER = [
    ("lock", "Lock", "sym:lock", "#546E7A"),
    ("sleep", "Sleep", "sym:moon", "#3949AB"),
    ("restart", "Restart", "sym:refresh", "#FB8C00"),
    ("shutdown", "Shut down", "sym:power", "#E53935"),
    ("signout", "Sign out", "sym:user", "#8E24AA"),
]


def _power(what):
    {"lock": osutil.lock_screen, "sleep": osutil.suspend, "restart": osutil.reboot, "shutdown": osutil.power_off,
     "signout": osutil.log_out}[what]()


@screen("screens.power", "Power menu", group="Screens", icon="sym:power",
        help="Lock, sleep, restart, shut down. Restart and shut down ask for a second press.")
class PowerMenu:
    def __init__(self):
        self.confirm = None

    def open(self, ctx, p, scr):
        self.confirm = None
        scr.list([list_item(k, n, icon=i, color=c) for k, n, i, c in POWER], title="Power")

    def event(self, ctx, p, scr, ev):
        if self.confirm:                       # second step: press = do it, turn = cancel
            what, self.confirm = self.confirm, None
            if ev.type == "press":
                scr.card(title="Bye!", icon="sym:power", color="#E53935")
                _power(what)
            else:
                self.open(ctx, p, scr)
            return
        if ev.type != "press":
            return
        if ev.item in ("restart", "shutdown", "signout"):
            self.confirm = ev.item
            name, icon, color = {k: (n, i, c) for k, n, i, c in POWER}[ev.item]
            scr.card(title=f"{name}?", text="Press again to confirm, turn to cancel", icon=icon, color=color)
        else:
            _power(ev.item)
            scr.close()


# ------------------------------------------------------------------ launcher: list from the settings

DEFAULT_APPS = ("Browser | https://www.google.com\nExplorer | explorer.exe\nCalculator | calc.exe\n"
                "Notepad | notepad.exe\nSettings | ms-settings:") if osutil.IS_WIN else (
                "Browser | https://www.google.com\nFiles | ~\nTerminal | x-terminal-emulator\n"
                "Calculator | gnome-calculator\nText editor | gnome-text-editor")


@screen("screens.launcher", "App launcher (list)", group="Screens", icon="sym:list",
        params=[Param("items", "Entries, one per line:  Name | program, file or URL", "textarea",
                      DEFAULT_APPS),
                Param("title", "Title", "text", "Launch")],
        help="A scrolling list of programs, files and websites - more than fit on a page of tiles.")
class Launcher:
    def _items(self, p):
        out = []
        for i, line in enumerate((p.get("items") or "").splitlines()):
            if "|" in line:
                name, target = (s.strip() for s in line.split("|", 1))
                if name and target:
                    out.append((str(i), name, target))
        return out

    def open(self, ctx, p, scr):
        items = self._items(p)
        icon_for = lambda t: "sym:globe" if t.startswith("http") else "sym:folder" if t.endswith(("\\", "/")) else "sym:rocket"
        scr.list([list_item(i, n, sub=t[:40], icon=icon_for(t), color="#4FC3F7") for i, n, t in items],
                 title=p.get("title") or "Launch")

    def event(self, ctx, p, scr, ev):
        if ev.type == "press":
            for i, name, target in self._items(p):
                if i == ev.item:
                    osutil.open_target(target)
                    ctx.toast(f"Opening {name}")
                    scr.close()


# ------------------------------------------------------------------ timer: dial -> countdown card -> popup, LED, sound

@screen("screens.timer", "Timer", group="Screens", icon="sym:hourglass",
        params=[Param("minutes", "Start value (minutes)", "number", 5),
                Param("led", "Flash the LED when done", "bool", True)],
        help="Turn to set the minutes, press to start. While running: turn adds/removes minutes, press pauses. "
             "When it's done the deck shows a popup and beeps (and flashes its LED).")
class Timer:
    def __init__(self):
        self.minutes = None
        self.end = None           # time.time() when it rings
        self.left = None          # seconds left while paused

    def open(self, ctx, p, scr):
        if self.minutes is None:
            self.minutes = int(p.get("minutes") or 5)
        self.draw(ctx, scr)

    def draw(self, ctx, scr):
        if self.end is None and self.left is None:
            scr.dial(self.minutes, 1, 120, 1, text=f"{self.minutes} min", label="press to start",
                     icon="sym:hourglass", color="#00897B")
            return
        left = self.left if self.left is not None else max(0, self.end - time.time())
        total = self.minutes * 60
        m, s = divmod(int(left + 0.99), 60)
        scr.card(value=f"{m}:{s:02d}", title="Paused" if self.left is not None else "Timer",
                 text="press to continue" if self.left is not None else "turn ±1 min · press to pause",
                 progress=total - left, max=total, color="#FFB300" if self.left is not None else "#00897B",
                 footer="hold to leave (keeps running)")

    def event(self, ctx, p, scr, ev):
        if self.end is None and self.left is None:           # setting the time
            if ev.type == "turn":
                self.minutes = max(1, min(120, self.minutes + ev.delta))
            elif ev.type == "press":
                self.end = time.time() + self.minutes * 60
                ctx.beep("click")
            if ev.type != "close":
                self.draw(ctx, scr)
            return
        if ev.type == "press":
            if self.left is None:
                self.left = max(0, self.end - time.time())
            else:
                self.end, self.left = time.time() + self.left, None
        elif ev.type == "turn":
            if self.left is not None:
                self.left = max(0, self.left + ev.delta * 60)
            else:
                self.end += ev.delta * 60
            self.minutes = max(1, self.minutes + ev.delta)
        if ev.type != "close":
            self.draw(ctx, scr)

    def poll(self, ctx, p, scr):
        self.draw(ctx, scr)

    def state(self, ctx, p):
        """Runs once a second for the tile - also when the screen isn't open, so it rings from the tiles too."""
        if self.end is not None and self.left is None:
            left = self.end - time.time()
            if left <= 0:
                self.end = None
                ctx.popup("Time's up!", f"{self.minutes} min timer", icon="sym:bell", color="#00897B", ms=0,
                          id="timer", sound="alarm", on_dismiss=ctx.led_off)
                if p.get("led", True):
                    ctx.led("#00E676", "blink", period=500, bright=60, ms=30000)
                return State(value="done", color="")
            m, s = divmod(int(left + 0.99), 60)
            return State(value=f"{m}:{s:02d}", color="#00897B")
        if self.left is not None:
            m, s = divmod(int(self.left), 60)
            return State(value=f"paused {m}:{s:02d}")
        return State(value="", color="")


# ------------------------------------------------------------------ network graph: canvas + patch

def _fmt(bps):
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if bps < 1000 or unit == "GB/s":
            return f"{bps:.0f} {unit}" if unit == "B/s" else f"{bps:.1f} {unit}"
        bps /= 1000


@screen("screens.network", "Network graph", group="Screens", icon="sym:network",
        params=[Param("direction", "Show", "select", "down", options=[["down", "Download"], ["up", "Upload"]])],
        help="Live network speed with a 60 second graph. Turn the knob to switch download/upload.")
class Network:
    def __init__(self):
        self.last = None
        self.hist = {"down": [], "up": []}
        self.dir = None

    def _sample(self):
        c = psutil.net_io_counters()
        now = time.time()
        if self.last:
            t, rx, tx = self.last
            dt = max(0.2, now - t)
            for k, v in (("down", (c.bytes_recv - rx) / dt), ("up", (c.bytes_sent - tx) / dt)):
                self.hist[k] = (self.hist[k] + [v])[-60:]
        self.last = (now, c.bytes_recv, c.bytes_sent)

    def _scale(self):
        peak = max(self.hist[self.dir] or [1]) or 1
        return peak

    def open(self, ctx, p, scr):
        self.dir = self.dir or p.get("direction") or "down"
        self._sample()
        self.draw(scr)

    def draw(self, scr):
        h = self.hist[self.dir]
        cur = h[-1] if h else 0
        peak = self._scale()
        color = "#7E57C2" if self.dir == "down" else "#26A69A"
        scr.canvas([
            arc("ring", value=int(100 * cur / peak), w=228, width=8, color=color),
            image("ic", "sym:download" if self.dir == "down" else "sym:upload", y=-72, size=28, color=color),
            label("speed", _fmt(cur), y=-34, size=28),
            label("what", "download" if self.dir == "down" else "upload", y=-6, size=12, color="#9E9E9E"),
            chart("graph", [int(v * 100 / peak) for v in h], y=40, w=170, h=60, max=100, color=color),
            label("peak", "peak " + _fmt(peak), y=86, size=12, color="#757575"),
        ])

    def poll(self, ctx, p, scr):
        self._sample()
        h = self.hist[self.dir]
        cur, peak = h[-1], self._scale()
        if cur >= peak or len(h) < 3:
            self.draw(scr)                          # new peak: rescale the whole graph
        else:
            scr.patch({"id": "speed", "text": _fmt(cur)}, {"id": "ring", "value": int(100 * cur / peak)},
                      {"id": "graph", "push": int(100 * cur / peak)}, {"id": "peak", "text": "peak " + _fmt(peak)})

    def event(self, ctx, p, scr, ev):
        if ev.type in ("turn", "press"):
            self.dir = "up" if self.dir == "down" else "down"
            self.draw(scr)
