"""Local settings server (127.0.0.1 only) + JSON API used by the settings window."""
from __future__ import annotations

import json
import logging
import mimetypes
import secrets
import subprocess
import sys
import threading
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__, autostart, firmware
from . import sync as cfgsync
from .config import data_dir, normalize
from .device import find_ports
from .iconconv import icon_hash, kdi_from_bytes, kdi_to_png
from .plugin import REGISTRY

log = logging.getLogger("knobdeck.web")
STATIC = Path(__file__).parent / "static"
TOKEN = secrets.token_urlsafe(24)
SYMBOLS = ["play", "pause", "stop", "next", "prev", "volume", "volume_mid", "mute", "audio", "video", "image",
           "home", "settings", "power", "refresh", "wifi", "bluetooth", "bell", "keyboard", "list", "ok", "close",
           "plus", "minus", "edit", "eye", "eye_off", "charge", "battery", "upload", "download", "folder", "file",
           "save", "shuffle", "loop", "eject", "gps", "warning", "trash", "copy", "cut", "paste", "left", "right",
           "up", "down", "call", "drive", "sd", "usb", "envelope", "bars", "tint", "backspace", "enter"]
# extra icons in firmware 2.x
SYMBOLS2 = (Path(__file__).parent / "static" / "sym2.txt").read_text(encoding="utf-8").split()
MAX_UPLOAD = 8 * 1024 * 1024


def make_handler(app, port_ref):
    class H(BaseHTTPRequestHandler):
        server_version = "KnobDeck"

        def log_message(self, fmt, *args):
            log.debug(fmt, *args)

        # ---------------------------------------------------------- helpers
        def _host_ok(self):
            # blocks DNS-rebinding: only accept requests addressed to localhost
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def _auth(self, query=None):
            tok = self.headers.get("X-KD-Token") or (query or {}).get("k", [""])[0]
            return self._host_ok() and secrets.compare_digest(tok, TOKEN)

        def _send(self, code, body: bytes, ctype="application/json", cache=False):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj).encode())

        def _body(self) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_UPLOAD:
                raise ValueError("too large")
            return self.rfile.read(n)

        # ---------------------------------------------------------- GET
        def do_GET(self):
            url = urlparse(self.path)
            q = parse_qs(url.query)
            path = url.path
            if not self._host_ok():
                return self._send(403, b"forbidden", "text/plain")
            if path in ("/", "/index.html"):
                html = (STATIC / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", TOKEN)
                return self._send(200, html.encode(), "text/html; charset=utf-8")
            if path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            if path == "/api/ping":
                return self._json({"app": "knobdeck", "version": __version__})
            if path.startswith("/static/"):
                f = (STATIC / path[len("/static/"):]).resolve()
                if STATIC.resolve() in f.parents and f.is_file():
                    ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                    return self._send(200, f.read_bytes(), ctype, cache=f.suffix == ".png")
                return self._send(404, b"not found", "text/plain")
            if not self._auth(q):
                return self._send(403, b"forbidden", "text/plain")
            if path == "/api/state":
                return self._json(state(app))
            if path.startswith("/api/icon/") and path.endswith(".png"):
                h = path[len("/api/icon/"):-4]
                f = data_dir() / "icons" / f"{h}.kdi"
                if h.isalnum() and f.exists():
                    return self._send(200, kdi_to_png(f.read_bytes()), "image/png", cache=True)
                return self._send(404, b"not found", "text/plain")
            return self._send(404, b"not found", "text/plain")

        # ---------------------------------------------------------- POST
        def do_POST(self):
            url = urlparse(self.path)
            if not self._auth():
                return self._send(403, b"forbidden", "text/plain")
            try:
                body = self._body()
                if url.path == "/api/icon":
                    kdi = kdi_from_bytes(body)
                    h = icon_hash(kdi)
                    (data_dir() / "icons" / f"{h}.kdi").write_bytes(kdi)
                    return self._json({"hash": h})
                data = json.loads(body or b"{}")
                if url.path == "/api/config":
                    app.save_config(data["config"])
                    return self._json({"ok": True, "config": app.store.data})
                if url.path == "/api/test":
                    msg = app.call_in_worker(app.test_tile, data.get("tile") or {})
                    return self._json({"ok": not msg or msg.startswith("OK"), "message": msg})
                if url.path == "/api/reconnect":
                    app.device.reconnect()
                    return self._json({"ok": True})
                if url.path == "/api/autostart":
                    if "mode" in data:
                        autostart.set_mode(str(data["mode"]))
                    else:
                        autostart.set_enabled(bool(data.get("enabled")))
                    return self._json({"ok": True, "enabled": autostart.is_enabled(), "mode": autostart.get_mode(),
                                       "note": autostart.note()})
                if url.path == "/api/firmware":
                    if app.fw_status.get("state") == "running":
                        return self._json({"ok": False, "error": "update already running"})
                    app.fw_status.update(state="running", progress=0, message="Starting...")
                    threading.Thread(target=firmware.flash, args=(app,), name="flash", daemon=True).start()
                    return self._json({"ok": True})
                if url.path == "/api/sync":
                    return self._json(sync_action(app, data.get("action")))
                if url.path == "/api/ir_learn":
                    raw = app.ir_learn(float(data.get("timeout") or 10))
                    return self._json({"ok": bool(raw), "raw": raw or [],
                                       "error": "" if raw else "Nothing received - point the remote at the deck"})
                if url.path == "/api/ir_send":
                    app.ctx.ir_send(data.get("code") or "", repeat=int(data.get("repeat") or 0))
                    return self._json({"ok": True})
                if url.path == "/api/preview":
                    # show a page on the deck while editing it
                    app.device.send({"t": "goto", "page": data.get("page", "home")})
                    return self._json({"ok": True})
            except Exception as e:
                log.exception("api %s failed", url.path)
                return self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 400)
            return self._send(404, b"not found", "text/plain")

    return H


def state(app) -> dict:
    d = app.device
    return {
        "version": __version__,
        "config": app.store.data,
        "config_rev": (app.store.data.get("rev") or {}).get("id") or "",
        "config_msg": app.config_msg,
        "sync": cfgsync.status(app),
        "catalog": REGISTRY.catalog(),
        "symbols": SYMBOLS + (SYMBOLS2 if (not d.info or "utf8" in (d.info.get("caps") or [])) else []),
        "symbols_v2": SYMBOLS2,
        "firmware": {"bundled": (firmware.bundled() or ("",))[0], "update": firmware.newer_available(d.info.get("fw", "")),
                     "status": app.fw_status, "esptool": firmware.esptool_available()},
        "device": {"connected": d.connected, "port": d.port, "error": d.error, "info": {k: v for k, v in d.info.items() if k != "icons"},
                   "ports": find_ports()},
        "autostart": autostart.is_enabled(),
        "autostart_label": autostart.label(),
        "autostart_mode": autostart.get_mode(),
        "autostart_modes": autostart.modes(),
        "autostart_note": autostart.note(),
        "platform": "windows" if sys.platform == "win32" else "linux" if sys.platform.startswith("linux") else sys.platform,
        "input_status": _input_status(app),
        "autostart_supported": autostart.supported(),
        "last_error": app.last_error,
        "plugin_status": plugin_status(app),
        "data_dir": str(data_dir()),
    }


def sync_action(app, action: str) -> dict:
    if not app.device.connected:
        return {"ok": False, "error": "Deck not connected"}
    if "files" not in (app.device.info.get("caps") or []):
        return {"ok": False, "error": "Needs deck firmware 2.1 - update it above"}
    with app.sync_lock:
        if action == "pull":
            ok = cfgsync.pull(app, "loaded from the deck")
        elif action == "push":
            ok = cfgsync.push(app)
        elif action == "undo":
            b = cfgsync.latest_backup()
            if not b:
                return {"ok": False, "error": "No backup"}
            cfg = json.loads(b.read_text(encoding="utf-8"))
            cfgsync.backup(app.store.data, "before-undo")
            app.store.save(cfg, touch=True)
            ok = cfgsync.push(app)
            app.config_event(f"Settings restored from {b.name}")
        else:
            return {"ok": False, "error": "unknown action"}
    if ok:
        threading.Thread(target=app.on_connect, name="sync", daemon=True).start()   # layout, icons, cfg
    return {"ok": ok, "error": "" if ok else "Failed - see the log"}


def _input_status(app) -> str:
    if not sys.platform.startswith("linux"):
        return ""
    if "input" not in app.__dict__.setdefault("_cache", {}):
        from . import winput
        try:
            app._cache["input"] = winput.status()       # opening /dev/uinput once is enough
        except Exception as e:
            app._cache["input"] = str(e)
    return app._cache["input"]


def plugin_status(app) -> dict:
    out = {}
    for pid, spec in REGISTRY.settings.items():
        fn = spec.get("status")
        if fn:
            try:
                out[pid] = str(fn(app.ctx))
            except Exception as e:
                out[pid] = f"{type(e).__name__}: {e}"
    return out


def start_server(app, port: int) -> ThreadingHTTPServer:
    port_ref = {}
    for p in (port, 0):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", p), make_handler(app, port_ref))
            break
        except OSError:
            continue
    srv.daemon_threads = True
    port_ref["port"] = srv.server_port
    threading.Thread(target=srv.serve_forever, name="web", daemon=True).start()
    log.info("settings at http://127.0.0.1:%d/", srv.server_port)
    return srv


def already_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=0.5) as r:
            return json.load(r).get("app") == "knobdeck"
    except Exception:
        return False


def open_settings(port: int):
    url = f"http://127.0.0.1:{port}/"
    try:
        import webview  # noqa: F401  (pywebview available -> own window in a separate process)
        exe = sys.executable
        if exe.lower().endswith("python.exe"):
            exe = exe[:-10] + "pythonw.exe"
        subprocess.Popen([exe, "-m", "knobdeck.window", url], cwd=str(Path(__file__).parent.parent))
    except Exception:
        webbrowser.open(url)
