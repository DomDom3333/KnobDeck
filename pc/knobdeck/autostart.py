"""Start KnobDeck automatically.

Modes:
  off     - never
  login   - when the user logs in (tray icon always there)
  plugin  - when the deck is plugged in:
              Linux:   a udev rule (installed by install.sh) asks systemd to start a user service as soon as the
                       deck appears - nothing runs while the deck isn't plugged in.
              Windows: Windows can't start programs on a USB event by itself, so KnobDeck starts hidden at login
                       (no tray icon, no window) and shows up when the deck is plugged in.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

IS_WIN = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")
UDEV_RULE = Path("/etc/udev/rules.d/60-knobdeck.rules")
MODES = ("off", "login", "plugin")


def supported() -> bool:
    return IS_WIN or IS_LINUX


def label() -> str:
    return "Start automatically"


def _workdir() -> Path:
    return Path(__file__).resolve().parent.parent


def _python(windowless: bool = True) -> str:
    exe = sys.executable
    if IS_WIN and windowless and exe.lower().endswith("python.exe"):
        exe = exe[:-10] + "pythonw.exe"
    return exe


def _config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def _win_script() -> Path:
    return (Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" /
            "Startup" / "KnobDeck.vbs")


def _desktop_file() -> Path:
    return _config_home() / "autostart" / "knobdeck.desktop"


def _service_file() -> Path:
    return _config_home() / "systemd" / "user" / "knobdeck.service"


def plug_rule_installed() -> bool:
    """Linux: does the udev rule start our service when the deck appears?"""
    try:
        return "SYSTEMD_USER_WANTS" in UDEV_RULE.read_text()
    except OSError:
        return False


def _systemd_user() -> bool:
    try:
        r = subprocess.run(["systemctl", "--user", "is-system-running"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() not in ("", "offline")
    except (OSError, subprocess.TimeoutExpired):
        return False


# ------------------------------------------------------------------ state

def get_mode() -> str:
    if IS_WIN:
        f = _win_script()
        if not f.exists():
            return "off"
        return "plugin" if "--background" in f.read_text(encoding="utf-8", errors="ignore") else "login"
    if IS_LINUX:
        if _service_file().exists():
            return "plugin"
        f = _desktop_file()
        if f.exists():
            return "plugin" if "--background" in f.read_text(encoding="utf-8", errors="ignore") else "login"
    return "off"


def is_enabled() -> bool:
    return supported() and get_mode() != "off"


def set_enabled(on: bool):
    set_mode("plugin" if on else "off")


def modes() -> list[list[str]]:
    plug = ("When the deck is plugged in (starts hidden with Windows, appears with the deck)" if IS_WIN
            else "When the deck is plugged in")
    return [["off", "Off"], ["login", "When I log in"], ["plugin", plug]]


def note() -> str:
    """Extra explanation for the settings window."""
    if IS_LINUX and get_mode() == "plugin" and not _service_file().exists():
        return ("The udev rule for starting on plug-in isn't installed (run install.sh), so KnobDeck starts hidden "
                "at login instead and appears when the deck is plugged in.")
    return ""


# ------------------------------------------------------------------ changing it

def _remove_all():
    for f in (_win_script() if IS_WIN else None, _desktop_file() if IS_LINUX else None,
              _service_file() if IS_LINUX else None):
        if f is not None:
            f.unlink(missing_ok=True)
    if IS_LINUX:
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=10, check=False)


def set_mode(mode: str):
    if mode not in MODES:
        raise ValueError(f"unknown autostart mode {mode!r}")
    if not supported():
        raise RuntimeError(f"autostart is not available on {sys.platform}")
    try:
        _remove_all()
    except (OSError, subprocess.TimeoutExpired):
        pass
    if mode == "off":
        return
    args = "-m knobdeck" + (" --background" if mode == "plugin" else "")
    if IS_WIN:
        f = _win_script()
        f.write_text(
            'Set sh = CreateObject("WScript.Shell")\r\n'
            f'sh.CurrentDirectory = "{_workdir()}"\r\n'
            f'sh.Run """{_python()}"" {args}", 0, False\r\n', encoding="utf-8")
        return
    if mode == "plugin" and plug_rule_installed() and _systemd_user():
        f = _service_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(
            "[Unit]\n"
            "Description=KnobDeck (started when the deck is plugged in)\n"
            "After=graphical-session.target\n\n"
            "[Service]\n"
            "Type=simple\n"
            f"WorkingDirectory={_workdir()}\n"
            f'ExecStart="{_python()}" {args}\n'
            "Restart=no\n", encoding="utf-8")
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=10, check=False)
        return
    f = _desktop_file()            # login, or plug-in without the udev rule: start (hidden) at login
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=KnobDeck\n"
        "Comment=Stream-deck style controller (ESP32-C3-LCDkit)\n"
        f'Exec="{_python()}" {args}\n'
        f"Path={_workdir()}\n"
        f"Icon={_workdir() / 'knobdeck' / 'static' / 'knobdeck.png'}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
        "X-GNOME-Autostart-Delay=5\n", encoding="utf-8")


def first_run_default():
    """Turn on 'start when plugged in' the first time KnobDeck runs (can be turned off in the settings)."""
    from .config import data_dir
    marker = data_dir() / ".autostart-default"
    if marker.exists() or not supported():
        return
    try:
        if get_mode() == "off":
            set_mode("plugin")
    except Exception:
        pass
    marker.write_text("done")
