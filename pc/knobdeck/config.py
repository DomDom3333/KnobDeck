"""Configuration: pages/tiles, device options and plugin settings, stored as JSON in the user's app-data folder."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import sys
import unicodedata
from pathlib import Path

SLOTS = 8
TILE_TYPES = ("action", "folder", "dial", "stats", "back", "people", "screen")


def data_dir() -> Path:
    if os.environ.get("KNOBDECK_HOME"):
        d = Path(os.environ["KNOBDECK_HOME"])
    elif sys.platform == "win32":
        d = Path(os.environ.get("APPDATA", Path.home())) / "KnobDeck"
    else:
        d = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "knobdeck"
    (d / "icons").mkdir(parents=True, exist_ok=True)
    (d / "plugins").mkdir(parents=True, exist_ok=True)
    return d


def new_id(prefix="t") -> str:
    return f"{prefix}{secrets.token_hex(3)}"


def _tile(id, label, icon, color, type="action", action="", params=None, **kw):
    t = {"id": id, "type": type, "label": label, "icon": icon, "color": color, "action": action, "params": params or {}}
    t.update(kw)
    return t


def _windows_apps():
    return [
        _tile("explorer", "Explorer", "sym:folder", "#F9A825", action="launch.open", params={"target": "explorer.exe"}),
        _tile("calc", "Calculator", "sym:list", "#6D4C41", action="launch.open", params={"target": "calc.exe"}),
        None, None, None,
        _tile("taskmgr", "Task Mgr", "sym:bars", "#00897B", action="keys.hotkey", params={"keys": "ctrl+shift+esc"}),
    ]


def _linux_apps():
    return [
        _tile("files", "Files", "sym:folder", "#F9A825", action="launch.open", params={"target": "~"}),
        _tile("calc", "Calculator", "sym:list", "#6D4C41", action="launch.open", params={"target": "gnome-calculator"}),
        None, None, None,
        _tile("taskmgr", "System monitor", "sym:bars", "#00897B", action="system.taskmgr"),
    ]


def default_config() -> dict:
    return {
        "version": 1,
        "device": {"port": "auto", "brightness": 80, "dim": 8, "idle": 120, "invert": False, "click": False, "ir_pin": 4},
        "plugins": {},
        "pages": [
            {"id": "home", "title": "KnobDeck", "tiles": [
                _tile("play", "Play/Pause", "sym:play", "#1E88E5", action="media.play_pause"),
                _tile("next", "Next", "sym:next", "#1E88E5", action="media.next"),
                _tile("vol", "Volume", "sym:volume", "#43A047", type="dial", action="audio.volume"),
                _tile("mic", "Mic", "sym:audio", "#E53935", action="audio.mic_mute"),
                _tile("stats", "PC Stats", "sym:charge", "#FB8C00", type="stats", action="stats.system"),
                _tile("apps", "Apps", "sym:folder", "#8E24AA", type="folder", target="apps"),
                _tile("lock", "Lock PC", "sym:power", "#546E7A", action="system.lock"),
                _tile("prev", "Previous", "sym:prev", "#1E88E5", action="media.prev"),
            ]},
            {"id": "apps", "title": "Apps", "tiles": [
                _tile("back", "Back", "sym:left", "#455A64", type="back"),
                _tile("browser", "Browser", "sym:gps", "#0277BD", action="launch.open",
                      params={"target": "https://www.google.com"}),
                *(_windows_apps() if sys.platform == "win32" else _linux_apps()),
            ]},
        ],
    }


def normalize(cfg: dict) -> dict:
    """Fill gaps and fix obvious mistakes so the rest of the app can rely on the shape."""
    base = default_config()
    out = copy.deepcopy(cfg) if isinstance(cfg, dict) else base
    out.setdefault("version", 1)
    dev = out.setdefault("device", {})
    for k, v in base["device"].items():
        dev.setdefault(k, v)
    out.setdefault("plugins", {})
    pages = out.get("pages")
    if not isinstance(pages, list) or not pages:
        out["pages"] = base["pages"]
        pages = out["pages"]
    seen_ids = set()
    for i, p in enumerate(pages):
        p.setdefault("id", new_id("p"))
        p.setdefault("title", f"Page {i + 1}")
        tiles = list(p.get("tiles") or [])[:SLOTS]
        tiles += [None] * (SLOTS - len(tiles))
        for j, t in enumerate(tiles):
            if not isinstance(t, dict) or t.get("type") == "empty":
                tiles[j] = None
                continue
            if t.get("type") not in TILE_TYPES:
                t["type"] = "action"
            if not t.get("id") or t["id"] in seen_ids:
                t["id"] = new_id()
            seen_ids.add(t["id"])
            t.setdefault("label", "")
            t.setdefault("icon", "")
            t.setdefault("color", "#3949AB")
            t.setdefault("action", "")
            t.setdefault("params", {})
        p["tiles"] = tiles
    return out


class ConfigStore:
    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "config.json"
        self.data = self.load()

    def load(self) -> dict:
        try:
            cfg = normalize(json.loads(self.path.read_text(encoding="utf-8")))
            if not cfg.get("rev") and cfg["pages"] != normalize(default_config())["pages"]:
                # settings from KnobDeck < 2.1 (no revisions yet): they're the user's own, mark them as such
                cfg["rev"] = {"id": secrets.token_hex(6), "time": round(self.path.stat().st_mtime, 3)}
            return cfg
        except FileNotFoundError:
            cfg = normalize(default_config())
            self.save(cfg)
            return cfg
        except Exception:
            # keep a copy of a broken file instead of silently losing it
            backup = self.path.with_suffix(".broken.json")
            try:
                self.path.replace(backup)
            except OSError:
                pass
            return normalize(default_config())

    def save(self, cfg: dict | None = None, touch: bool = False):
        """touch=True: the user changed something - new revision, so it gets synced to the deck."""
        if cfg is not None:
            self.data = normalize(cfg)
        if touch:
            import time
            self.data["rev"] = {"id": secrets.token_hex(6), "time": round(time.time(), 3)}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def find_tile(self, tile_id: str):
        for p in self.data["pages"]:
            for t in p["tiles"]:
                if t and t["id"] == tile_id:
                    return p, t
        return None, None


# ---------------------------------------------------------------- device layout

_TRANSLIT = {"ß": "ss", "ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "€": "EUR", "°": "", "–": "-", "—": "-"}


def ascii_text(s: str, limit: int = 31) -> str:
    """For decks with firmware 1.x, whose fonts only cover ASCII: transliterate the rest (Grüße -> Gruesse)."""
    s = "".join(_TRANSLIT.get(c, c) for c in str(s or ""))
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[\x00-\x1f]", "", s)
    return s[:limit]


# characters the 2.x firmware fonts have (Latin-1, Latin Extended-A and common punctuation)
_EXTRA = set("\u2010\u2012\u2013\u2014\u2015\u2018\u2019\u201a\u201c\u201d\u201e\u2020\u2021\u2022"
             "\u2026\u2030\u2039\u203a\u20ac\u2122\u2190\u2191\u2192\u2193\u2212")


def _deck_char(c: str) -> str:
    o = ord(c)
    if 0x20 <= o <= 0x7E or 0xA0 <= o <= 0x17F or c in _EXTRA or 0xF000 <= o <= 0xF8FF:   # F000+: icon glyphs
        return c
    if o < 0x20:
        return " " if c in "\n\t" else ""
    return unicodedata.normalize("NFKD", c).encode("ascii", "ignore").decode("ascii")


def deck_text(s, limit: int = 31, utf8: bool = True) -> str:
    """Text as the deck can show it. `limit` is in bytes (the firmware's buffers); never cuts a character."""
    if not utf8:
        return ascii_text(s, limit)
    out, size = [], 0
    for c in str(s if s is not None else ""):
        c = _deck_char(c)
        n = len(c.encode("utf-8"))
        if size + n > limit:
            break
        out.append(c)
        size += n
    return "".join(out)


def device_layout(cfg: dict, utf8: bool = False) -> dict:
    def txt(s, n=31):
        return deck_text(s, n, utf8)
    pages = []
    for p in cfg["pages"]:
        tiles = []
        for slot, t in enumerate(p["tiles"]):
            if not t:
                continue
            tiles.append({
                "slot": slot, "id": t["id"], "type": t["type"], "label": txt(t.get("label")),
                "icon": ascii_text(t.get("icon") or "", 23), "color": t.get("color") or "#3949AB",
                **({"target": t["target"]} if t["type"] == "folder" and t.get("target") else {}),
            })
        pages.append({"id": p["id"], "title": txt(p.get("title")), "tiles": tiles})
    body = json.dumps(pages, sort_keys=True, separators=(",", ":"))
    return {"t": "layout", "v": hashlib.sha1(body.encode()).hexdigest()[:12], "pages": pages}


def used_icons(cfg: dict) -> set[str]:
    out = set()
    for p in cfg["pages"]:
        for t in p["tiles"]:
            if t and str(t.get("icon", "")).startswith("img:"):
                out.add(t["icon"][4:])
    return out
