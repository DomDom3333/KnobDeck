"""Settings stored on the deck (firmware 2.1): fresh PC pulls, edits push, edits elsewhere pull, conflicts."""
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
HOME_A = tempfile.mkdtemp()
HOME_B = tempfile.mkdtemp()
os.environ["KNOBDECK_HOME"] = HOME_A

from PIL import Image  # noqa: E402

from fake_deck import FakeDeck  # noqa: E402
from knobdeck import sync as cfgsync  # noqa: E402
from knobdeck.app import App  # noqa: E402
from knobdeck.config import ConfigStore  # noqa: E402
from knobdeck.iconconv import icon_hash, to_kdi  # noqa: E402
from knobdeck.plugin import REGISTRY  # noqa: E402

ok = True
def check(name, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name)

CAPS = ["screens", "popup", "led", "beep", "ir", "ir_learn", "utf8", "files"]
deck = FakeDeck(fw="2.1.0", caps=CAPS)

def wait_for(cond, t=8):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False

# ---------------- PC "A": user's settings with a picture and a secret -> stored on the (empty) deck
kdi = to_kdi(Image.new("RGBA", (64, 64), (200, 30, 30, 255)))
h = icon_hash(kdi)
(os.path.join(HOME_A, "icons") and None)
store = ConfigStore()
(store.path.parent / "icons" / f"{h}.kdi").write_bytes(kdi)
cfg = store.data
cfg["device"]["port"] = deck.path
cfg["pages"][0]["title"] = "Mein Deck"
cfg["pages"][0]["tiles"][0]["icon"] = "img:" + h
cfg["plugins"]["obs"] = {"host": "studio-pc", "port": 4455, "password": "hunter2"}
store.save(cfg, touch=True)
tag_a = store.data["rev"]["id"]

app = App()
app.load_plugins = lambda: None            # plugins once only (registry is global)
REGISTRY.load_dir(__import__("pathlib").Path(__file__).parent.parent / "knobdeck" / "plugins", "knobdeck.plugins")
app.start_background()
check("A: settings pushed to empty deck", wait_for(lambda: "config" in deck.files))
check("A: deck tag = PC revision", deck.files.get("config", (b"", ""))[1] == tag_a)
stored = cfgsync.unpack(deck.files["config"][0])
check("A: password not stored on the deck", "password" not in stored["plugins"]["obs"] and stored["plugins"]["obs"]["host"] == "studio-pc")
check("A: port not stored on the deck", "port" not in stored["device"])
check("A: picture on deck", wait_for(lambda: h in deck.icons))

# user edits on A -> pushed
cfg = json.loads(json.dumps(app.store.data))
cfg["pages"][0]["title"] = "Edited on A"
app.save_config(cfg)
check("A: edit pushed", wait_for(lambda: cfgsync.unpack(deck.files["config"][0])["pages"][0]["title"] == "Edited on A"))
app.stop()
time.sleep(0.5)

# ---------------- PC "B": fresh install -> loads everything from the deck
os.environ["KNOBDECK_HOME"] = HOME_B
store_b = ConfigStore()
store_b.data["device"]["port"] = deck.path
store_b.data["plugins"]["obs"] = {"password": "local-secret"}
store_b.save()
check("B: fresh PC has no revision", "rev" not in store_b.data)
deck.rx.clear()
app_b = App()
app_b.load_plugins = lambda: None
app_b.start_background()
check("B: pulled from deck", wait_for(lambda: app_b.store.data["pages"][0]["title"] == "Edited on A"))
check("B: picture fetched from deck", wait_for(lambda: os.path.exists(os.path.join(HOME_B, "icons", f"{h}.kdi"))))
check("B: own port kept", app_b.store.data["device"]["port"] == deck.path)
check("B: own password kept, A's not copied", app_b.store.data["plugins"]["obs"].get("password") == "local-secret")
check("B: host setting came along", app_b.store.data["plugins"]["obs"].get("host") == "studio-pc")
check("B: backup of the old settings", any(f.endswith("before-load.json") for f in os.listdir(os.path.join(HOME_B, "backups"))))
check("B: popup shown on deck", wait_for(lambda: deck.got("popup", 0.1) is not None))
check("B: in sync", cfgsync.status(app_b)["in_sync"])

# ---------------- the deck gets edited on another PC while B is unchanged -> B pulls on reconnect
other = json.loads(json.dumps(app_b.store.data))
other["pages"][0]["title"] = "From PC C"
other["rev"] = {"id": "c0ffee000001", "time": time.time()}
deck.files["config"] = (cfgsync.pack(other), "c0ffee000001")
deck.hello()                                   # replug
check("B: newer settings from another PC pulled", wait_for(lambda: app_b.store.data["pages"][0]["title"] == "From PC C"))

# ---------------- both changed: newer wins, other kept
mine = json.loads(json.dumps(app_b.store.data))
mine["pages"][0]["title"] = "Edited on B (newer)"
app_b.store.save(mine, touch=True)            # changed while the deck was away (no push)
older = json.loads(json.dumps(other))
older["pages"][0]["title"] = "Old edit on C"
older["rev"] = {"id": "c0ffee000002", "time": time.time() - 3600}
deck.files["config"] = (cfgsync.pack(older), "c0ffee000002")
deck.hello()
check("conflict: newer (PC) wins and is pushed",
      wait_for(lambda: cfgsync.unpack(deck.files["config"][0])["pages"][0]["title"] == "Edited on B (newer)"))
check("conflict: deck's version backed up",
      any("deck-before-overwrite" in f for f in os.listdir(os.path.join(HOME_B, "backups"))))

# ---------------- undo restores the previous settings
from knobdeck import webui  # noqa: E402
r = webui.sync_action(app_b, "pull")
check("manual load from deck", r["ok"])

print("\nALL PASSED" if ok else "\nSOME FAILED")
os._exit(0 if ok else 1)
