"""Firmware update from the settings window: flashes the firmware that ships with the app (or a chosen .bin)
over the deck's USB port with esptool. Layout and pictures on the deck are kept (only the app part is written)."""
from __future__ import annotations

import logging
import re
import subprocess
import sys
import time
from pathlib import Path

from .device import auto_port

log = logging.getLogger("knobdeck.firmware")
FW_DIR = Path(__file__).parent / "firmware"
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _ver(s: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", s or "")[:3])


def bundled() -> tuple[str, Path] | None:
    """(version, path) of the newest firmware shipped with the app."""
    best = None
    for f in FW_DIR.glob("knobdeck-*-v*.bin"):
        m = re.search(r"-v(\d+\.\d+\.\d+)\.bin$", f.name)
        if m and (not best or _ver(m.group(1)) > _ver(best[0])):
            best = (m.group(1), f)
    return best


def newer_available(deck_fw: str) -> bool:
    b = bundled()
    return bool(b and deck_fw and _ver(b[0]) > _ver(deck_fw))


def esptool_available() -> bool:
    try:
        import esptool  # noqa: F401
        return True
    except ImportError:
        return False


def flash(app, path: Path | None = None):
    """Runs in its own thread; progress goes to app.fw_status."""
    st = app.fw_status

    def set_(state, msg="", progress=None):
        st["state"], st["message"] = state, msg
        if progress is not None:
            st["progress"] = progress
        log.info("firmware update: %s %s", state, msg)

    if path is None:
        b = bundled()
        if not b:
            return set_("error", "No firmware file found in the app folder")
        path = b[1]
    if not esptool_available():
        return set_("error", "esptool is missing - run install.bat again (or: pip install esptool)")
    port = app.device.port or auto_port()
    if not port:
        return set_("error", "Deck not found - is it plugged in?")
    set_("running", "Releasing the USB port...", 0)
    app.device.pause()
    time.sleep(0.5)
    cmd = [sys.executable, "-m", "esptool", "--chip", "esp32c3", "--port", port, "--baud", "460800",
           "--before", "default_reset", "--after", "hard_reset", "write_flash", "-z", "0x0", str(path)]
    if sys.executable.lower().endswith("pythonw.exe"):
        cmd[0] = sys.executable[:-11] + "python.exe"
    out_tail: list[str] = []
    try:
        set_("running", "Starting the bootloader...", 0)
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                             creationflags=NO_WINDOW)
        buf = ""
        while True:
            ch = p.stdout.read(1)
            if not ch:
                break
            if ch in "\r\n":
                line, buf = buf.strip(), ""
                if not line:
                    continue
                out_tail = (out_tail + [line])[-15:]
                m = re.search(r"(\d+(?:\.\d+)?) ?%", line)     # esptool 4: "(12 %)", 5: "12.3%"
                if m and "Writing" in line:
                    set_("running", "Writing firmware...", int(float(m.group(1))))
                elif line.startswith(("Connecting", "Chip is", "Erasing", "Compressed")):
                    set_("running", line[:80])
            else:
                buf += ch
        rc = p.wait()
        if rc != 0:
            log.error("esptool failed:\n%s", "\n".join(out_tail))
            hint = ""
            if any("Failed to connect" in l or "No serial data" in l for l in out_tail):
                hint = (" - unplug the deck, keep the knob pressed (it doubles as the BOOT button) while plugging it "
                        "back in, then try again")
            errs = [l for l in out_tail if "error" in l.lower() and "deprecated" not in l.lower()]
            text = (errs[-1] if errs else out_tail[-1] if out_tail else f"esptool exit code {rc}")
            return set_("error", text.replace("A fatal error occurred: ", "")[:200] + hint)
        set_("done", "Updated - the deck restarts now", 100)
    except Exception as e:
        log.exception("firmware update failed")
        set_("error", f"{type(e).__name__}: {e}")
    finally:
        time.sleep(1.5)                   # let the deck boot before we open the port again
        app.device.resume()
