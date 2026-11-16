"""End-to-end test: PC app <-> fake deck, plus the settings API."""
import io
import json
import os
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["KNOBDECK_HOME"] = tempfile.mkdtemp()

from PIL import Image  # noqa: E402

from fake_deck import FakeDeck  # noqa: E402
from knobdeck import webui  # noqa: E402
from knobdeck.app import App  # noqa: E402
from knobdeck.plugin import REGISTRY, DialState, Ring, State, dial, stats, action  # noqa: E402

# test-only plugins that work on Linux
calls = []


@action("test.ping", "Ping", group="Test")
def ping(ctx, p):
    calls.append("ping")
    ctx.toast("pong")


@ping.state
def ping_state(ctx, p):
    return State(value=f"{len(calls)}x", color="#00FF00" if calls else None)


@dial("test.dial", "Dial", group="Test")
class D:
    v = 50
    def open(self, ctx, p): return DialState(self.v, text=f"{self.v}%")
    def turn(self, ctx, p, d):
        self.v = max(0, min(100, self.v + d * 2)); return DialState(self.v, text=f"{self.v}%")
    def press(self, ctx, p): return DialState(self.v, text="Muted")


@stats("test.stats", "Stats", group="Test")
def st(ctx, p):
    return [Ring("CPU", 12, "12%"), Ring("GPU", 34, "34% 56C")]


deck = FakeDeck()
app = App()
cfg = app.store.data
cfg["device"]["port"] = deck.path
home = cfg["pages"][0]["tiles"]
home[0] = {"id": "ping", "type": "action", "label": "Pïng Grüße", "icon": "sym:ok", "color": "#123456",
           "action": "test.ping", "params": {}, "long": {"action": "deck.toast", "params": {"text": "held"}}}
home[2] = {"id": "dl", "type": "dial", "label": "Dial", "icon": "sym:volume", "color": "#43A047", "action": "test.dial", "params": {}}
home[4] = {"id": "st", "type": "stats", "label": "Stats", "icon": "sym:charge", "color": "#FB8C00", "action": "test.stats", "params": {}}
app.store.save(cfg)
app.start_background()
srv = webui.start_server(app, 0)
base = f"http://127.0.0.1:{srv.server_port}"

ok = True
def check(name, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name)

check("layout sent on connect", deck.got("layout", 8))
lay = deck.got("layout")
check("labels transliterated to ASCII", lay["pages"][0]["tiles"][0]["label"] == "Ping Gruesse")
check("cfg sent", deck.got("cfg", bright=80))
check("time sent", deck.got("time"))
check("app sees deck connected", app.device.connected)

deck.send({"t": "press", "id": "ping", "page": "home"})
check("action ran", deck.got("toast", text="pong"))
check("state pushed after action", deck.got("state", id="ping", value="1x", color="#00FF00"))
deck.send({"t": "long", "id": "ping", "page": "home"})
check("long press action", deck.got("toast", text="held"))

deck.send({"t": "screen", "name": "dial", "id": "dl"})
check("dial opened", deck.got("dial", id="dl", value=50))
for _ in range(3):
    deck.send({"t": "dial", "id": "dl", "d": 1})
time.sleep(0.6)
dials = [m for m in deck.rx if m["t"] == "dial"]
check("dial turned to 56", dials[-1]["value"] == 56)
deck.send({"t": "dial_press", "id": "dl"})
check("dial press", deck.got("dial", text="Muted"))

deck.send({"t": "screen", "name": "stats", "id": "st"})
m = deck.got("stats", 3)
check("stats pushed", m and m["rings"][1]["text"] == "34% 56C")
deck.send({"t": "screen", "name": "deck", "page": "home"})

# ---- settings API
def req(path, body=None, token=webui.TOKEN, ctype="application/json", host=None):
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    r = urllib.request.Request(base + path, data=data, method="POST" if data is not None else "GET")
    if token:
        r.add_header("X-KD-Token", token)
    if host:
        r.add_header("Host", host)
    r.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()

code, body = req("/api/state", token="")
check("API refuses requests without token", code == 403)
code, body = req("/api/state", host="evil.example:80")
check("API refuses foreign Host header", code == 403)
code, body = req("/api/state")
st = json.loads(body)
check("API state ok", code == 200 and st["device"]["connected"] and len(st["catalog"]["entries"]) > 20)
code, html = req("/")
check("index has token injected", webui.TOKEN.encode() in html)

im = Image.new("RGBA", (300, 200), (255, 0, 0, 255))
buf = io.BytesIO(); im.save(buf, "PNG")
code, body = req("/api/icon", buf.getvalue(), ctype="image/png")
h = json.loads(body)["hash"]
check("icon converted", code == 200 and len(h) == 10)
code, png = req(f"/api/icon/{h}.png?k={webui.TOKEN}", token="")
check("icon preview served", code == 200 and png[:4] == b"\x89PNG")

cfg = st["config"]
cfg["pages"][0]["tiles"][1] = {"id": "pic", "type": "action", "label": "Picture", "icon": "img:" + h,
                               "color": "#000000", "action": "test.ping", "params": {}}
before = len([m for m in deck.rx if m["t"] == "layout"])
code, body = req("/api/config", {"config": cfg})
check("config saved", code == 200 and json.loads(body)["ok"])
time.sleep(2.5)
check("icon uploaded to deck", h in deck.icons and len(deck.icons[h]) == 8 + 48 * 48 * 3)
check("new layout sent", len([m for m in deck.rx if m["t"] == "layout"]) == before + 1)

code, body = req("/api/test", {"tile": {"action": "test.stats"}})
check("test endpoint", json.loads(body)["message"].startswith("OK"))

# deck restarts -> hello again with old layout -> app re-syncs
deck.layout_v = "default"; deck.icons.clear()
n = len([m for m in deck.rx if m["t"] == "layout"])
deck.hello()
time.sleep(2.5)
check("resync after deck restart", len([m for m in deck.rx if m["t"] == "layout"]) == n + 1 and h in deck.icons)

print("\nALL PASSED" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
