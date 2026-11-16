"""Operating-system helpers shared by the built-in plugins (Windows and Linux)."""
from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import sys
import webbrowser

IS_WIN = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")
NO_WINDOW = 0x08000000 if IS_WIN else 0
log = logging.getLogger("knobdeck.os")
URL_SCHEMES = ("http://", "https://", "mailto:", "steam://", "spotify:", "ms-settings:", "file://", "discord://")


def os_name() -> str:
    return "Windows" if IS_WIN else "Linux" if IS_LINUX else sys.platform


def wayland() -> bool:
    return IS_LINUX and (os.environ.get("XDG_SESSION_TYPE") == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY")))


def x11() -> bool:
    return IS_LINUX and not wayland() and bool(os.environ.get("DISPLAY"))


def desktop() -> str:
    """'gnome', 'kde', 'xfce', ... (lower case, may be empty)"""
    return (os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION") or "").lower()


def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def run(cmd: list[str] | str, check: bool = True, timeout: float = 10) -> subprocess.CompletedProcess:
    if isinstance(cmd, str):
        cmd = shlex.split(cmd)
    return subprocess.run(cmd, check=check, timeout=timeout, capture_output=True, creationflags=NO_WINDOW)


def spawn(cmd: list[str] | str, **kw):
    if isinstance(cmd, str):
        cmd = shlex.split(cmd)
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=not IS_WIN,
                            creationflags=NO_WINDOW, **kw)


def first_working(cands: list[list[str]], what: str):
    """Run the first command whose program exists and that succeeds."""
    errors = []
    for c in cands:
        if not have(c[0]):
            continue
        try:
            run(c)
            return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
            errors.append(f"{c[0]}: {e}")
    log.warning("%s failed: %s", what, "; ".join(errors) or f"none of {[c[0] for c in cands]} installed")
    raise RuntimeError(f"Can't {what} here (see log)")


# ------------------------------------------------------------------ open things

def open_target(target: str, args: str = ""):
    """Program, file, folder or URL. On Linux a program can be a command name like 'firefox'."""
    target = os.path.expanduser(os.path.expandvars(target.strip().strip('"')))
    if not target:
        raise ValueError("nothing to open")
    if IS_WIN:
        if target.lower().startswith(URL_SCHEMES) or not args:
            os.startfile(target)
        else:
            subprocess.Popen([target] + shlex.split(args, posix=False), cwd=os.path.dirname(target) or None)
        return
    if target.lower().startswith(URL_SCHEMES):
        if have("xdg-open"):
            spawn(["xdg-open", target])
        else:
            webbrowser.open(target)
        return
    if os.path.exists(target) and not (os.path.isfile(target) and os.access(target, os.X_OK)):
        spawn(["xdg-open", target] if have("xdg-open") else ["gio", "open", target])      # file / folder
        return
    if target.endswith(".desktop"):
        spawn(["gtk-launch", os.path.basename(target)[:-8]] if have("gtk-launch") else ["xdg-open", target])
        return
    parts = shlex.split(target) if not os.path.exists(target) else [target]
    if not have(parts[0]) and not os.path.exists(parts[0]):
        raise FileNotFoundError(f"'{parts[0]}' not found")
    spawn(parts + (shlex.split(args) if args else []))


# ------------------------------------------------------------------ session / power

def lock_screen():
    if IS_WIN:
        import ctypes
        ctypes.windll.user32.LockWorkStation()
        return
    first_working([["loginctl", "lock-session"], ["xdg-screensaver", "lock"],
                   ["gnome-screensaver-command", "-l"], ["dm-tool", "lock"], ["xscreensaver-command", "-lock"]],
                  "lock the screen")


def suspend():
    if IS_WIN:
        spawn("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
        return
    first_working([["systemctl", "suspend"], ["loginctl", "suspend"]], "suspend")


def reboot():
    if IS_WIN:
        spawn("shutdown /r /t 0")
        return
    first_working([["systemctl", "reboot"], ["loginctl", "reboot"]], "restart")


def power_off():
    if IS_WIN:
        spawn("shutdown /s /t 0")
        return
    first_working([["systemctl", "poweroff"], ["loginctl", "poweroff"]], "shut down")


def log_out():
    if IS_WIN:
        spawn("shutdown /l")
        return
    sid = os.environ.get("XDG_SESSION_ID")
    cands = []
    if "gnome" in desktop():
        cands.append(["gnome-session-quit", "--logout", "--no-prompt"])
    if "kde" in desktop():
        cands.append(["qdbus", "org.kde.Shutdown", "/Shutdown", "org.kde.Shutdown.logout"])
    if sid:
        cands.append(["loginctl", "terminate-session", sid])
    cands.append(["loginctl", "terminate-user", os.environ.get("USER", "")])
    first_working(cands, "sign out")


def monitor_off():
    if IS_WIN:
        import ctypes
        # WM_SYSCOMMAND / SC_MONITORPOWER / 2 = off (any input turns it back on)
        ctypes.windll.user32.PostMessageW(0xFFFF, 0x0112, 0xF170, 2)
        return
    cands = []
    if x11():
        cands.append(["xset", "dpms", "force", "off"])
    if "kde" in desktop():
        cands.append(["kscreen-doctor", "--dpms", "off"])
    if "gnome" in desktop():
        cands.append(["busctl", "--user", "set-property", "org.gnome.Mutter.DisplayConfig",
                      "/org/gnome/Mutter/DisplayConfig", "org.gnome.Mutter.DisplayConfig", "PowerSaveMode", "i", "1"])
    cands.append(["wlopm", "--off", "*"])
    first_working(cands, "turn the screen off")


def task_manager():
    if IS_WIN:
        from . import winput
        winput.hotkey("ctrl+shift+esc")
        return
    for c in ("gnome-system-monitor", "plasma-systemmonitor", "ksysguard", "xfce4-taskmanager",
              "mate-system-monitor", "lxtask", "resources", "missioncenter"):
        if have(c):
            spawn([c])
            return
    raise RuntimeError("no system monitor app found (e.g. install gnome-system-monitor)")


def screenshot():
    if IS_WIN:
        from . import winput
        winput.hotkey("win+shift+s")
        return
    if "gnome" in desktop() or "kde" in desktop():
        from . import winput
        winput.hotkey("printscreen")          # GNOME and KDE open their screenshot tool on Print
        return
    for c in (["flameshot", "gui"], ["spectacle", "-r"], ["xfce4-screenshooter", "-r"], ["gnome-screenshot", "-i"]):
        if have(c[0]):
            spawn(c)
            return
    from . import winput
    winput.hotkey("printscreen")


def show_desktop():
    from . import winput
    winput.hotkey("win+d")      # Windows, GNOME ("hide all windows" when set), KDE (Meta+D)
