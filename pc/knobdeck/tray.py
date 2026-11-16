"""System tray icon."""
import logging
import sys

from PIL import Image, ImageDraw
import pystray

from . import autostart, webui

log = logging.getLogger("knobdeck.tray")


def _icon(connected: bool) -> Image.Image:
    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((2, 2, 62, 62), fill=(30, 30, 30, 255))
    ring = (67, 160, 71, 255) if connected else (120, 120, 120, 255)
    d.arc((6, 6, 58, 58), 200, 340, fill=ring, width=6)
    d.ellipse((22, 22, 42, 42), fill=(30, 136, 229, 255))
    return im


def run_tray(app, port: int):
    state = {"connected": None}

    def open_settings(icon=None, item=None):
        webui.open_settings(port)

    def toggle_autostart(icon, item):
        autostart.set_mode("off" if autostart.get_mode() == "plugin" else "plugin")

    def quit_app(icon, item):
        log.info("quit from tray menu")
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Settings", open_settings, default=True),
        pystray.MenuItem("Reconnect deck", lambda i, it: app.device.reconnect()),
        pystray.MenuItem("Start when the deck is plugged in", toggle_autostart, checked=lambda it: autostart.get_mode() == "plugin",
                         visible=autostart.supported()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", quit_app),
    )
    icon = pystray.Icon("knobdeck", _icon(False), "KnobDeck - not connected", menu)

    def watch(icon):
        hidden = getattr(app, "background", False)
        icon.visible = not hidden              # background start: appear only while the deck is plugged in
        log.info("tray icon %s", "waiting for the deck" if hidden else "shown")
        if sys.platform == "win32" and not hidden:
            try:
                # Windows 11 tucks new tray icons away under the ^ arrow - say where we are
                icon.notify("KnobDeck is running. Find it in the tray (under ^ next to the clock).", "KnobDeck")
            except Exception:
                pass
        while not app._stop.wait(1.0):
            try:
                c = app.device.connected
                if c != state["connected"]:
                    state["connected"] = c
                    icon.icon = _icon(c)
                    icon.title = "KnobDeck - connected" if c else "KnobDeck - not connected"
                    if hidden:
                        icon.visible = c
            except Exception:
                log.exception("tray update failed")

    icon.run(setup=watch)
    log.info("tray icon stopped")
