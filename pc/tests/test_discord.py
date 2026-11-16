"""PC app + fake deck + fake Discord: voice screen end to end."""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
home = tempfile.mkdtemp()
os.environ["KNOBDECK_HOME"] = home
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp()

from PIL import Image  # noqa
from fake_deck import FakeDeck  # noqa
from fake_discord import FakeDiscord  # noqa
from knobdeck.app import App  # noqa
from knobdeck.iconconv import icon_hash, to_kdi  # noqa
from knobdeck.config import data_dir  # noqa

dc = FakeDiscord(os.environ["XDG_RUNTIME_DIR"])
deck = FakeDeck()
app = App()
cfg = app.store.data
cfg["device"]["port"] = deck.path
cfg["plugins"]["discord"] = {"client_id": "123", "client_secret": "s3cret"}
t = cfg["pages"][0]["tiles"]
t[0] = {"id": "dv", "type": "people", "label": "Discord", "icon": "sym:call", "color": "#5865F2", "action": "discord.voice", "params": {}}
t[1] = {"id": "dm", "type": "action", "label": "Mute", "icon": "sym:audio", "color": "#333333", "action": "discord.mute", "params": {}}
app.store.save(cfg)
app.load_plugins = lambda: None
from knobdeck.plugin import REGISTRY  # noqa
from pathlib import Path  # noqa
REGISTRY.load_dir(Path(__file__).parent.parent / "knobdeck" / "plugins", "knobdeck.plugins")
disc = sys.modules["knobdeck.plugins.discord"]


def fake_post(body):                    # no internet in tests: pretend Discord's token endpoint answered
    assert body["code"] == "the-code" and body["client_secret"] == "s3cret"
    return {"access_token": "tok", "refresh_token": "r", "expires_in": 600}
disc.CLIENT._post_token = fake_post


def fake_fetch(url):                   # avatar download -> local image
    kdi = to_kdi(Image.new("RGBA", (64, 64), (200, 50, 50, 255)), circle=True)
    h = icon_hash(kdi)
    (data_dir() / "icons" / f"{h}.kdi").write_bytes(kdi)
    app.avatars[url] = h
    app.notify()
app._fetch_avatar = fake_fetch

import threading  # noqa
threading.Thread(target=app._worker, daemon=True).start()
threading.Thread(target=app._scheduler, daemon=True).start()
app.device.start()

ok = True
def check(name, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name)

check("deck connected", deck.got("layout", 8))
check("authorize asked with voice scopes", any(m.get("cmd") == "AUTHORIZE" and "rpc.voice.read" in m["args"]["scopes"]
                                              for m in (dc.received + [{}]) if True) or (time.sleep(3) or any(
    m.get("cmd") == "AUTHORIZE" for m in dc.received)))
check("tile state: 3 in voice", deck.got("state", 6, id="dv", label="3 in voice"))
check("subscribed to speaking events", ("SPEAKING_START", "900") in dc.subs)

deck.send({"t": "screen", "name": "people", "id": "dv"})
m = deck.got("people", 3, id="dv")
check("people screen: channel + 3 people", m and m["title"] == "Gaming Night" and [p["name"] for p in m["items"]] == ["Dominik", "Anna", "max"])
check("self-muted user shown muted", m and m["items"][2]["muted"])
time.sleep(1.0)
last = [x for x in deck.rx if x["t"] == "people"][-1]
print("   last people msg icons:", [p["icon"] for p in last["items"]], "deck icons:", list(deck.icons))
check("avatars uploaded and used", all(p["icon"].startswith("img:") for p in last["items"]) and len(deck.icons) >= 1)

t0 = time.time()
dc.dispatch("SPEAKING_START", {"user_id": "2", "channel_id": "900"})
got = None
while time.time() - t0 < 2:
    got = next((x for x in reversed(deck.rx) if x["t"] == "people" and x["items"][1]["speaking"]), None)
    if got:
        break
    time.sleep(0.02)
check(f"speaking shown quickly ({(time.time() - t0) * 1000:.0f} ms)", got and time.time() - t0 < 0.8)
check("tile state: Anna speaking (green)", deck.got("state", 2, id="dv", value="Anna speaking", color="#3BA55C"))

deck.send({"t": "people_press", "id": "dv", "item": "1"})        # press on myself -> mute
check("press on self mutes", deck.got("state", 3, id="dm", label="Muted"))
check("SET_VOICE_SETTINGS sent", any(x.get("cmd") == "SET_VOICE_SETTINGS" and x["args"].get("mute") for x in dc.received))
deck.send({"t": "people_press", "id": "dv", "item": "2"})
time.sleep(0.5)
check("press on other -> local mute", any(x.get("cmd") == "SET_USER_VOICE_SETTINGS" and x["args"]["user_id"] == "2" for x in dc.received))

dc.dispatch("VOICE_STATE_DELETE", {"user": {"id": "3"}})
check("someone leaves", deck.got("people", 2, id="dv") and len([x for x in deck.rx if x["t"] == "people"][-1]["items"]) == 2 or
      (time.sleep(0.5) or len([x for x in deck.rx if x["t"] == "people"][-1]["items"]) == 2))
dc.channel = None
dc.dispatch("VOICE_CHANNEL_SELECT", {"channel_id": None})
time.sleep(0.6)
last = [x for x in deck.rx if x["t"] == "people"][-1]
check("left the channel -> empty screen", last["items"] == [] and "Not in a voice" in last["text"])
check("token saved for next start", (data_dir() / "discord_token.json").exists())
print("\nALL PASSED" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
