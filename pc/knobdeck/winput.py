"""Keyboard input.

Windows: SendInput (no extra packages, no admin rights needed).
Linux:   a virtual keyboard via /dev/uinput (works on X11 and Wayland; install.sh sets up access), falling back
         to xdotool (X11) or ydotool. Typing text uses xdotool / wtype / ydotool, or the clipboard + Ctrl+V.

Key names are the same everywhere: 'ctrl+shift+m', 'win+d' (win = super/meta key), 'f13', 'playpause' ...
On Linux, letters and digits mean the key at that position of a US keyboard (that's also how the settings window
records hotkeys), so 'ctrl+z' is the same physical key on every layout.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import struct
import subprocess
import sys
import threading
import time

IS_WIN = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")

# ------------------------------------------------------------------ Windows virtual-key codes

VK = {
    "ctrl": 0x11, "control": 0x11, "lctrl": 0xA2, "rctrl": 0xA3,
    "shift": 0x10, "lshift": 0xA0, "rshift": 0xA1,
    "alt": 0x12, "menu": 0x12, "lalt": 0xA4, "ralt": 0xA5, "altgr": 0xA5,
    "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C, "cmd": 0x5B, "super": 0x5B, "meta": 0x5B,
    "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "ins": 0x2D,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22, "pgdn": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "printscreen": 0x2C, "prtsc": 0x2C, "print": 0x2C, "pause": 0x13, "capslock": 0x14, "numlock": 0x90,
    "scrolllock": 0x91, "apps": 0x5D, "contextmenu": 0x5D,
    "volumemute": 0xAD, "volumedown": 0xAE, "volumeup": 0xAF,
    "nexttrack": 0xB0, "prevtrack": 0xB1, "stop": 0xB2, "playpause": 0xB3,
    "plus": 0xBB, "comma": 0xBC, "minus": 0xBD, "period": 0xBE,
    **{f"f{i}": 0x6F + i for i in range(1, 25)},
    **{f"num{i}": 0x60 + i for i in range(10)},
}
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5C, 0x5D, 0xA3, 0xA5,
            0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3, 0x2C, 0x90}

# ------------------------------------------------------------------ Linux key codes (linux/input-event-codes.h)

KEY = {
    "esc": 1, "escape": 1, "minus": 12, "-": 12, "equal": 13, "=": 13, "plus": 13, "backspace": 14, "tab": 15,
    "[": 26, "]": 27, "enter": 28, "return": 28, "ctrl": 29, "control": 29, "lctrl": 29, ";": 39, "'": 40,
    "`": 41, "shift": 42, "lshift": 42, "\\": 43, ",": 51, "comma": 51, ".": 52, "period": 52, "/": 53,
    "rshift": 54, "alt": 56, "lalt": 56, "menu": 56, "space": 57, " ": 57, "capslock": 58, "numlock": 69,
    "scrolllock": 70, "rctrl": 97, "printscreen": 99, "prtsc": 99, "print": 99, "ralt": 100, "altgr": 100,
    "home": 102, "up": 103, "pageup": 104, "pgup": 104, "left": 105, "right": 106, "end": 107, "down": 108,
    "pagedown": 109, "pgdn": 109, "insert": 110, "ins": 110, "delete": 111, "del": 111,
    "volumemute": 113, "volumedown": 114, "volumeup": 115, "pause": 119,
    "win": 125, "lwin": 125, "super": 125, "meta": 125, "cmd": 125, "rwin": 126, "apps": 127, "contextmenu": 127,
    "nexttrack": 163, "playpause": 164, "prevtrack": 165, "stop": 166,
    **{c: code for code, c in zip(range(16, 26), "qwertyuiop")},
    **{c: code for code, c in zip(range(30, 39), "asdfghjkl")},
    **{c: code for code, c in zip(range(44, 51), "zxcvbnm")},
    **{str(d): 2 + (d - 1) % 10 for d in range(10)},
    **{f"f{i}": 58 + i for i in range(1, 11)}, "f11": 87, "f12": 88,
    **{f"f{i}": 170 + i for i in range(13, 25)},
    **{f"num{i}": c for i, c in enumerate((82, 79, 80, 81, 75, 76, 77, 71, 72, 73))},
}
MODIFIERS = {29, 42, 54, 56, 97, 100, 125, 126}

# X keysyms for the xdotool fallback
_XSYM = {"ctrl": "ctrl", "control": "ctrl", "shift": "shift", "alt": "alt", "win": "super", "super": "super",
         "meta": "super", "cmd": "super", "enter": "Return", "return": "Return", "esc": "Escape",
         "escape": "Escape", "tab": "Tab", "space": "space", "backspace": "BackSpace", "delete": "Delete",
         "del": "Delete", "insert": "Insert", "home": "Home", "end": "End", "pageup": "Prior", "pagedown": "Next",
         "left": "Left", "right": "Right", "up": "Up", "down": "Down", "printscreen": "Print", "print": "Print",
         "pause": "Pause", "capslock": "Caps_Lock", "volumemute": "XF86AudioMute",
         "volumedown": "XF86AudioLowerVolume", "volumeup": "XF86AudioRaiseVolume", "playpause": "XF86AudioPlay",
         "nexttrack": "XF86AudioNext", "prevtrack": "XF86AudioPrev", "stop": "XF86AudioStop",
         "minus": "minus", "plus": "plus", "comma": "comma", "period": "period", "numlock": "Num_Lock",
         "scrolllock": "Scroll_Lock", "apps": "Menu", "contextmenu": "Menu", "altgr": "ISO_Level3_Shift",
         "lctrl": "Control_L", "rctrl": "Control_R", "lshift": "Shift_L", "rshift": "Shift_R", "lalt": "Alt_L",
         "ralt": "Alt_R", "lwin": "Super_L", "rwin": "Super_R", "ins": "Insert", "pgup": "Prior", "pgdn": "Next",
         "prtsc": "Print", "menu": "alt",
         **{f"f{i}": f"F{i}" for i in range(1, 25)}, **{f"num{i}": f"KP_{i}" for i in range(10)}}


def _names(combo: str) -> list[str]:
    names = [p for p in combo.lower().replace(" ", "").split("+") if p]
    if not names:
        raise ValueError("no keys given")
    return names


# ================================================================== Windows

if IS_WIN:
    from ctypes import wintypes

    ULONG_PTR = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    _user32.VkKeyScanW.argtypes = (wintypes.WCHAR,)
    _user32.VkKeyScanW.restype = ctypes.c_short


def _key_event(vk: int, up: bool = False, scan: int = 0, unicode: bool = False):
    flags = 0
    if unicode:
        flags |= 0x0004                        # KEYEVENTF_UNICODE
    elif vk in EXTENDED:
        flags |= 0x0001                        # KEYEVENTF_EXTENDEDKEY
    if up:
        flags |= 0x0002                        # KEYEVENTF_KEYUP
    inp = INPUT(type=1)
    inp.ki = KEYBDINPUT(vk if not unicode else 0, scan, flags, 0, 0)
    return inp


def _send(events):
    arr = (INPUT * len(events))(*events)
    _user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))


def parse_combo(combo: str) -> list[int]:
    """Windows virtual-key codes of a combo."""
    keys = []
    for part in _names(combo):
        if part in VK:
            keys.append(VK[part])
        elif len(part) == 1:
            r = _user32.VkKeyScanW(part) if IS_WIN else -1
            if r == -1:
                raise ValueError(f"unknown key '{part}'")
            keys.append(r & 0xFF)
        else:
            raise ValueError(f"unknown key '{part}'")
    return keys


def _linux_codes(combo: str) -> list[int]:
    codes = []
    for part in _names(combo):
        if part not in KEY:
            raise ValueError(f"unknown key '{part}'")
        codes.append(KEY[part])
    return codes


# ================================================================== Linux: uinput virtual keyboard

class _UInput:
    EV_SYN, EV_KEY, SYN_REPORT = 0, 1, 0
    UI_SET_EVBIT, UI_SET_KEYBIT = 0x40045564, 0x40045565
    UI_DEV_SETUP, UI_DEV_CREATE = 0x405C5503, 0x5501

    def __init__(self):
        import fcntl
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        try:
            fcntl.ioctl(self.fd, self.UI_SET_EVBIT, self.EV_KEY)
            for code in range(1, 256):
                fcntl.ioctl(self.fd, self.UI_SET_KEYBIT, code)
            name = b"KnobDeck virtual keyboard"
            try:
                # struct uinput_setup: input_id (bustype, vendor, product, version), name[80], ff_effects_max
                fcntl.ioctl(self.fd, self.UI_DEV_SETUP, struct.pack("HHHH80sI", 0x03, 0x303A, 0x4B44, 1, name, 0))
            except OSError:
                # kernels < 4.5: struct uinput_user_dev
                os.write(self.fd, struct.pack("80sHHHHI", name, 0x03, 0x303A, 0x4B44, 1, 0) + bytes(4 * 64 * 4))
            fcntl.ioctl(self.fd, self.UI_DEV_CREATE)
        except Exception:
            os.close(self.fd)
            raise
        time.sleep(0.3)          # give the desktop a moment to pick up the new keyboard

    def _emit(self, type_, code, value):
        now = time.time()
        sec = int(now)
        os.write(self.fd, struct.pack("llHHi", sec, int((now - sec) * 1e6), type_, code, value))

    def keys(self, down: list[int], up: list[int]):
        for c in down:
            self._emit(self.EV_KEY, c, 1)
            self._emit(self.EV_SYN, self.SYN_REPORT, 0)
            time.sleep(0.008)
        for c in up:
            self._emit(self.EV_KEY, c, 0)
            self._emit(self.EV_SYN, self.SYN_REPORT, 0)
            time.sleep(0.008)


_uinput: dict = {"dev": None, "error": None}
_lock = threading.Lock()


def _get_uinput():
    if _uinput["dev"] is None and _uinput["error"] is None:
        try:
            _uinput["dev"] = _UInput()
        except OSError as e:
            _uinput["error"] = e
    return _uinput["dev"]


def _wayland() -> bool:
    return os.environ.get("XDG_SESSION_TYPE") == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))


def _x11() -> bool:
    return not _wayland() and bool(os.environ.get("DISPLAY"))


def _run(cmd: list[str], input_text: str | None = None):
    subprocess.run(cmd, check=True, timeout=15, capture_output=True, input=input_text.encode() if input_text else None)


def _no_backend() -> RuntimeError:
    err = _uinput["error"]
    why = f" ({err.strerror})" if isinstance(err, OSError) and err.strerror else ""
    return RuntimeError("keyboard input needs access to /dev/uinput" + why +
                        " - run install.sh once more (it adds a udev rule), then log out and in again")


def _linux_combo(combo: str):
    codes = _linux_codes(combo)
    with _lock:
        dev = _get_uinput()
        if dev:
            dev.keys(codes, list(reversed(codes)))
            return
    if _x11() and shutil.which("xdotool"):
        syms = [_XSYM.get(n, n) for n in _names(combo)]
        _run(["xdotool", "key", "--clearmodifiers", "+".join(syms)])
        return
    if shutil.which("ydotool"):
        _run(["ydotool", "key"] + [f"{c}:1" for c in codes] + [f"{c}:0" for c in reversed(codes)])
        return
    raise _no_backend()


def _clipboard_paste(text: str) -> bool:
    if _wayland() and shutil.which("wl-copy"):
        _run(["wl-copy"], text)
    elif shutil.which("xclip"):
        _run(["xclip", "-selection", "clipboard"], text)
    elif shutil.which("xsel"):
        _run(["xsel", "--clipboard", "--input"], text)
    else:
        return False
    time.sleep(0.1)
    _linux_combo("ctrl+v")
    return True


def _linux_type(text: str):
    if _x11() and shutil.which("xdotool"):
        _run(["xdotool", "type", "--clearmodifiers", "--delay", "8", "--", text])
        return
    if _wayland() and shutil.which("wtype"):
        try:
            _run(["wtype", "--", text])        # wlroots, KDE; GNOME doesn't allow it
            return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
    if shutil.which("ydotool"):
        try:
            _run(["ydotool", "type", "--", text])
            return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
    if _clipboard_paste(text):
        return
    raise RuntimeError("typing text needs xdotool (X11), wtype or wl-clipboard (Wayland) - see README")


# ================================================================== public API

def _unsupported():
    return RuntimeError(f"keyboard input is not available on {sys.platform}")


def press_vk(vk: int):
    """Windows virtual-key code (kept for older plugins); prefer press('playpause')."""
    if IS_WIN:
        _send([_key_event(vk), _key_event(vk, up=True)])
        return
    name = next((n for n, v in VK.items() if v == vk and n in KEY), None)
    if not name:
        raise ValueError(f"key code {vk:#x} has no Linux equivalent")
    press(name)


def press(name: str):
    """One key by name: 'playpause', 'volumeup', 'f13', 'enter' ..."""
    hotkey(name)


def hotkey(combo: str):
    """'ctrl+shift+m', 'win+d', 'f13', 'alt+tab' ..."""
    if IS_WIN:
        keys = parse_combo(combo)
        _send([_key_event(k) for k in keys] + [_key_event(k, up=True) for k in reversed(keys)])
    elif IS_LINUX:
        _linux_combo(combo)
    else:
        raise _unsupported()


def hotkey_sequence(spec: str, gap: float = 0.05):
    """Several combos separated by commas: 'ctrl+c, alt+tab, ctrl+v'."""
    for i, combo in enumerate(c for c in spec.split(",") if c.strip()):
        if i:
            time.sleep(gap)
        hotkey(combo)


def type_text(text: str):
    if IS_LINUX:
        if text:
            _linux_type(text.replace("\r\n", "\n"))
        return
    if not IS_WIN:
        raise _unsupported()
    events = []
    for ch in text.replace("\r\n", "\n"):
        if ch == "\n":
            events += [_key_event(0x0D), _key_event(0x0D, up=True)]
            continue
        unit = ch.encode("utf-16-le")             # characters outside the BMP become two UTF-16 units
        for i in range(0, len(unit), 2):
            code = int.from_bytes(unit[i:i + 2], "little")
            events += [_key_event(0, scan=code, unicode=True), _key_event(0, up=True, scan=code, unicode=True)]
    if events:
        _send(events)


def status() -> str:
    """How keys are sent on this computer (shown in the settings window)."""
    if IS_WIN:
        return "Windows SendInput"
    if not IS_LINUX:
        return "not available"
    with _lock:
        if _get_uinput():
            return "virtual keyboard (/dev/uinput)"
    if _x11() and shutil.which("xdotool"):
        return "xdotool (X11)"
    if shutil.which("ydotool"):
        return "ydotool"
    return str(_no_backend())
