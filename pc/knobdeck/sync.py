"""Keep the full configuration on the deck too (firmware 2.1+), so it travels with the deck.

On every connect:
  * fresh PC (settings never edited)               -> load the deck's settings
  * only the PC changed since the last sync         -> store them on the deck
  * only the deck changed (edited on another PC)    -> load them from the deck
  * both changed                                     -> the newer one wins; the other is kept in backups/
Every time settings are loaded from the deck, the PC's previous settings are saved to backups/ first.

Not stored on the deck: passwords / secrets (plugin parameters of type "secret"), the USB port setting, Discord
login tokens and other files of individual plugins. Those stay on each PC.
"""
from __future__ import annotations

import copy
import json
import logging
import time
import zlib
from typing import TYPE_CHECKING

from .config import data_dir, normalize, used_icons
from .plugin import REGISTRY

if TYPE_CHECKING:
    from .app import App

log = logging.getLogger("knobdeck.sync")
FILE = "config"
FORMAT = 1
LOCAL_ONLY_DEVICE_KEYS = ("port",)
MAX_BACKUPS = 20


# ------------------------------------------------------------------ packing

def _secret_keys():
    plugin = {pid: {p.key for p in spec["params"] if p.type == "secret"} for pid, spec in REGISTRY.settings.items()}
    action = {e.id: {p.key for p in e.params if p.type == "secret"} for e in REGISTRY.entries.values()}
    return plugin, action


def _strip(cfg: dict) -> dict:
    out = copy.deepcopy(cfg)
    plugin_secret, action_secret = _secret_keys()
    for pid, keys in plugin_secret.items():
        for k in keys:
            out.get("plugins", {}).get(pid, {}).pop(k, None)
    for page in out.get("pages", []):
        for t in page.get("tiles", []):
            for holder in (t, (t or {}).get("long") or {}):
                if holder and holder.get("action") in action_secret:
                    for k in action_secret[holder["action"]]:
                        (holder.get("params") or {}).pop(k, None)
    for k in LOCAL_ONLY_DEVICE_KEYS:
        out.get("device", {}).pop(k, None)
    return out


def _merge_local(remote: dict, local: dict) -> dict:
    """Keep this PC's secrets and port setting in a config that came from the deck."""
    out = copy.deepcopy(remote)
    plugin_secret, action_secret = _secret_keys()
    for pid, keys in plugin_secret.items():
        for k in keys:
            v = local.get("plugins", {}).get(pid, {}).get(k)
            if v:
                out.setdefault("plugins", {}).setdefault(pid, {})[k] = v
    local_tiles = {t["id"]: t for p in local.get("pages", []) for t in p.get("tiles", []) if t}
    for page in out.get("pages", []):
        for t in page.get("tiles", []):
            lt = local_tiles.get((t or {}).get("id"))
            if not t or not lt or lt.get("action") != t.get("action"):
                continue
            for k in action_secret.get(t.get("action"), ()):
                if (lt.get("params") or {}).get(k):
                    t.setdefault("params", {})[k] = lt["params"][k]
    for k in LOCAL_ONLY_DEVICE_KEYS:
        if k in local.get("device", {}):
            out.setdefault("device", {})[k] = local["device"][k]
    return out


def pack(cfg: dict) -> bytes:
    body = json.dumps({"format": FORMAT, "saved": time.time(), "config": _strip(cfg)},
                      separators=(",", ":"), ensure_ascii=False).encode()
    return b"KDC1" + zlib.compress(body, 9)


def unpack(data: bytes) -> dict:
    if not data.startswith(b"KDC1"):
        raise ValueError("not a KnobDeck configuration")
    body = json.loads(zlib.decompress(data[4:]))
    if body.get("format", 0) > FORMAT:
        raise ValueError("the deck's settings were saved by a newer KnobDeck - update this app")
    return body["config"]


# ------------------------------------------------------------------ bookkeeping

def _state_file():
    return data_dir() / "sync.json"


def _load_state() -> dict:
    try:
        return json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(st: dict):
    _state_file().write_text(json.dumps(st, indent=1), encoding="utf-8")


def backup(cfg: dict, why: str) -> str:
    d = data_dir() / "backups"
    d.mkdir(exist_ok=True)
    name = time.strftime("config-%Y%m%d-%H%M%S") + f"-{why}.json"
    (d / name).write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    files = sorted(d.glob("config-*.json"))
    for old in files[:-MAX_BACKUPS]:
        old.unlink(missing_ok=True)
    return name


def latest_backup():
    files = sorted((data_dir() / "backups").glob("config-*.json")) if (data_dir() / "backups").exists() else []
    return files[-1] if files else None


# ------------------------------------------------------------------ the sync

def _tag(cfg: dict) -> str | None:
    return (cfg.get("rev") or {}).get("id")


def _time(cfg: dict) -> float:
    return float((cfg.get("rev") or {}).get("time") or 0)


def push(app: "App") -> bool:
    cfg = app.store.data
    tag = _tag(cfg)
    if not tag:
        app.store.save(touch=True)           # give the config a revision so the deck can tell versions apart
        cfg, tag = app.store.data, _tag(app.store.data)
    if not app.device.put_file(FILE, pack(cfg), tag):
        return False
    info = app.device.info
    info.setdefault("files", {})[FILE] = {"tag": tag}
    _record(info.get("id"), tag)
    log.info("settings stored on the deck (%s)", tag)
    return True


def pull(app: "App", reason: str = "loaded from the deck") -> bool:
    data = app.device.get_file(FILE)
    if not data:
        log.warning("could not read the settings from the deck")
        return False
    remote = unpack(data)
    local = app.store.data
    backup(local, "before-load")                # always keep a copy of what this PC had
    cfg = normalize(_merge_local(remote, local))
    _fetch_icons(app, cfg)
    app.store.save(cfg)                         # keeps the deck's revision -> in sync
    _record(app.device.info.get("id"), _tag(cfg))
    app.config_event(f"Settings {reason}")
    log.info("settings %s (%s)", reason, _tag(cfg))
    return True


def _fetch_icons(app: "App", cfg: dict):
    have = set(app.device.info.get("icons") or [])
    for h in sorted(used_icons(cfg)):
        f = data_dir() / "icons" / f"{h}.kdi"
        if f.exists() or h not in have:
            continue
        data = app.device.get_file(icon=h)
        if data and data.startswith(b"KDI1"):
            f.write_bytes(data)


def _record(deck_id, tag):
    if not deck_id or not tag:
        return
    st = _load_state()
    st[deck_id] = {"tag": tag, "time": time.time()}
    _save_state(st)


def status(app: "App") -> dict:
    info = app.device.info
    deck_file = (info.get("files") or {}).get(FILE)
    supported = "files" in (info.get("caps") or [])
    last = _load_state().get(info.get("id") or "", {})
    return {
        "supported": supported,
        "deck_has": bool(deck_file),
        "in_sync": bool(deck_file and deck_file.get("tag") == _tag(app.store.data)),
        "last": last.get("time"),
        "backup": latest_backup().name if latest_backup() else "",
    }


def sync_on_connect(app: "App") -> str:
    """Returns what happened: '', 'pushed', 'pulled', 'conflict-pushed', 'conflict-pulled'."""
    info = app.device.info
    if "files" not in (info.get("caps") or []):
        return ""
    deck_id = info.get("id")
    deck_file = (info.get("files") or {}).get(FILE)
    deck_tag = (deck_file or {}).get("tag") or None
    local = app.store.data
    local_tag = _tag(local)
    last = _load_state().get(deck_id or "", {}).get("tag")

    if deck_tag and deck_tag == local_tag:
        _record(deck_id, local_tag)
        return ""
    if not deck_tag:
        return "pushed" if local_tag and push(app) else ""
    if not local_tag:                                   # this PC never had its own settings
        return "pulled" if pull(app, "loaded from the deck") else ""
    if last == local_tag:                               # only the deck changed
        return "pulled" if pull(app, "updated from the deck") else ""
    if last == deck_tag:                                # only the PC changed
        return "pushed" if push(app) else ""
    # both changed (or this deck was never synced with this PC): newer wins, the other is kept
    data = app.device.get_file(FILE)
    try:
        remote = unpack(data) if data else None
    except ValueError as e:
        log.warning("deck settings unreadable: %s", e)
        remote = None
    if remote is not None and _time(remote) > _time(local):
        return "conflict-pulled" if pull(app, "loaded from the deck (they were newer; yours are in backups)") else ""
    if remote is not None:
        backup(remote, "deck-before-overwrite")
    return "conflict-pushed" if push(app) else ""
