"""Screenshots of the settings window (headless Chrome) against a running app + fake deck."""
import os, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["KNOBDECK_HOME"] = tempfile.mkdtemp()
from fake_deck import FakeDeck
from knobdeck import webui
from knobdeck.app import App
from playwright.sync_api import sync_playwright

deck = FakeDeck()
app = App()
app.store.data["device"]["port"] = deck.path
app.store.save()
app.start_background()
srv = webui.start_server(app, 0)
time.sleep(3)
url = f"http://127.0.0.1:{srv.server_port}/"
out = sys.argv[1]
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 1280, "height": 820})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.goto(url); pg.wait_for_timeout(1200)
    pg.screenshot(path=f"{out}/gui_1_overview.png")
    pg.click(".slot >> nth=3"); pg.wait_for_timeout(300)          # mic tile
    pg.screenshot(path=f"{out}/gui_2_tile.png")
    for i in range(8):
        pg.click(f".slot >> nth={i}"); pg.wait_for_timeout(80)
    pg.click(".tabs button[data-tab=plugins]"); pg.wait_for_timeout(200)
    pg.click(".tabs button[data-tab=tile]"); pg.wait_for_timeout(200)
    print("dirty after browsing:", repr(pg.inner_text("#dirty")))
    pg.click(".slot >> nth=2"); pg.wait_for_timeout(300)          # dial
    pg.screenshot(path=f"{out}/gui_3_dial.png")
    pg.click("#pagelist li >> nth=1"); pg.wait_for_timeout(300)
    pg.click(".slot >> nth=4"); pg.wait_for_timeout(300)          # empty slot
    pg.screenshot(path=f"{out}/gui_4_empty.png")
    pg.click(".quick button >> nth=0"); pg.wait_for_timeout(300)  # new action
    pg.select_option(".tab:not(.hidden) select", "keys.hotkey"); pg.wait_for_timeout(300)
    pg.screenshot(path=f"{out}/gui_5_new_action.png")
    pg.click(".tabs button[data-tab=device]"); pg.wait_for_timeout(300)
    pg.screenshot(path=f"{out}/gui_6_device.png")
    pg.click(".tabs button[data-tab=plugins]"); pg.wait_for_timeout(300)
    pg.screenshot(path=f"{out}/gui_7_plugins.png")
    pg.click("#save"); pg.wait_for_timeout(1500)
    pg.screenshot(path=f"{out}/gui_8_saved.png")
    print("errors:", errs)
    b.close()
print("layouts sent:", len([m for m in deck.rx if m["t"] == "layout"]))
