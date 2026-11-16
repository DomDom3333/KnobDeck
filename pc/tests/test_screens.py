"""Firmware 2.x features: plugin screens, input events, popups, LED/beep/IR, images, UTF-8 text."""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["KNOBDECK_HOME"] = tempfile.mkdtemp()

from PIL import Image  # noqa: E402

from fake_deck import FakeDeck  # noqa: E402
from knobdeck.app import App  # noqa: E402
from knobdeck.plugin import Event, action, list_item, label, screen  # noqa: E402

events = []


@screen("test.menu", "Menu", group="Test")
class Menu:
    def open(self, ctx, p, scr):
        scr.list([list_item("a", "Äpfel", sub="Grüße"), list_item("b", "Birnen")], title="Obst")

    def event(self, ctx, p, scr, ev: Event):
        events.append(ev)
        if ev.type == "press" and ev.item == "b":
            scr.card(title="Birnen", text="gewählt", icon=Image.new("RGB", (200, 100), (255, 0, 0)))


@action("test.adhoc", "Ad-hoc", group="Test")
def adhoc(ctx, p):
    scr = ctx.show("adhoc", "canvas", on_event=lambda ev: events.append(("adhoc", ev)),
                   elements=[label("t", "Hallo €", y=-10)])
    scr.patch({"id": "t", "text": "Welt"})
    ctx.popup("Neue Nachricht", "Jürgen schreibt…", on_dismiss=lambda: events.append("dismissed"), sound="notify")
    ctx.led("#FF0000", "pulse")


deck = FakeDeck(fw="2.0.0", caps=["screens", "popup", "led", "beep", "ir", "ir_learn", "utf8"])
app = App()
cfg = app.store.data
cfg["device"]["port"] = deck.path
home = cfg["pages"][0]["tiles"]
home[0] = {"id": "menu", "type": "screen", "label": "Menü", "icon": "sym:list", "color": "#123456",
           "action": "test.menu", "params": {}}
home[1] = {"id": "ah", "type": "action", "label": "Ad-hoc", "icon": "sym:ok", "color": "#123456",
           "action": "test.adhoc", "params": {}}
app.store.save(cfg)
app.start_background()

ok = True
def check(name, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name)

check("layout sent", deck.got("layout", 8))
lay = deck.got("layout")
check("utf-8 labels kept for 2.x firmware", lay["pages"][0]["tiles"][0]["label"] == "Menü")
check("cfg has ir pin + click", deck.got("cfg", ir_pin=4, click=False))

deck.send({"t": "screen", "name": "screen", "id": "menu"})
m = deck.got("show", id="menu", tpl="list")
check("tile screen opened as list", m and m["items"][0]["text"] == "Äpfel" and m["title"] == "Obst")
deck.send({"t": "input", "id": "menu", "ev": "turn", "d": 1})
deck.send({"t": "input", "id": "menu", "ev": "press", "item": "b"})
m = deck.got("show", id="menu", tpl="card")
check("press event -> card", m and m["text"] == "gewählt")
check("card image uploaded", m and m["icon"].startswith("img:") and m["icon"][4:] in deck.icons)
check("image is 128px max", m and len(deck.icons[m["icon"][4:]]) == 8 + 48 * 48 * 3)
check("events delivered", [e.type for e in events if isinstance(e, Event)] == ["turn", "press"])
deck.send({"t": "input", "id": "other", "ev": "press"})
time.sleep(0.3)
check("events for other ids ignored", len(events) == 2)
deck.send({"t": "screen", "name": "deck", "page": "home"})
time.sleep(0.3)
check("close event when leaving", events[-1].type == "close" and app.active_screen is None)

deck.send({"t": "press", "id": "ah", "page": "home"})
check("ad-hoc canvas", deck.got("show", id="adhoc", tpl="canvas"))
check("canvas text keeps €", deck.got("show", id="adhoc")["elements"][0]["text"] == "Hallo €")
check("patch sent", deck.got("patch", id="adhoc"))
check("popup sent", deck.got("popup", title="Neue Nachricht", text="Jürgen schreibt…"))
check("popup sound", deck.got("beep", sound="notify"))
check("led sent", deck.got("led", color="#FF0000", effect="pulse"))
deck.send({"t": "input", "id": "popup", "ev": "dismiss"})
deck.send({"t": "input", "id": "adhoc", "ev": "turn", "d": -2})
time.sleep(0.4)
check("popup dismiss callback", "dismissed" in events)
check("ad-hoc screen event", any(isinstance(e, tuple) and e[1].delta == -2 for e in events))

raw = app.ir_learn(3)
check("ir learn", raw == [9000, 4500, 560, 560, 560, 1690])
app.ctx.ir_send("0000 006D 0002 0000 0157 00AC 0015 0016")
m = deck.got("ir")
check("ir send (pronto)", m and m["khz"] == 38 and m["raw"][0] == 9019)

# the built-in screens load and draw
from knobdeck.plugin import REGISTRY  # noqa: E402
for sid in ("screens.power", "screens.launcher", "screens.timer", "screens.network"):
    tile = {"id": sid.replace(".", "_")[:20], "type": "screen", "action": sid, "params": {}}
    app.call_in_worker(app.open_tile_screen, tile)
    check(f"{sid} opens", deck.got("show", id=tile["id"]))
    app.call_in_worker(app.poll_screen)
for _ in range(4):
    time.sleep(0.3)
    app.call_in_worker(app.poll_screen)
check("network graph patches", deck.got("patch", id="screens_network"))

# timer rings from the tile state, even with the screen closed
cfg = app.store.data
cfg["pages"][0]["tiles"][2] = {"id": "tm", "type": "screen", "label": "Timer", "icon": "sym:hourglass",
                               "color": "#00897B", "action": "screens.timer", "params": {}}
app.store.save(cfg)
timer = REGISTRY.get("screens.timer").fn
timer.minutes, timer.end = 1, time.time() - 1
app.call_in_worker(app.poll_states)
check("timer rings: popup", deck.got("popup", id="timer", title="Time's up!"))
check("timer rings: alarm + led", deck.got("beep", sound="alarm") and deck.got("led", effect="blink"))

print("\nALL PASSED" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
