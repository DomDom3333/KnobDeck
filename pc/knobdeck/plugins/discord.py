"""Discord: who is in your voice channel and who is speaking, plus mute / deafen.

Uses the Discord desktop app's local RPC interface (the one StreamKit and overlays use). Discord only lets an
application you registered yourself use it - see README, "Discord setup". Discord shows a one-time
"Authorize" prompt in the app; after that it reconnects by itself.
"""
from __future__ import annotations

import json
import os
import queue
import socket
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from knobdeck.config import data_dir
from knobdeck.plugin import Param, People, Person, State, action, people, plugin_settings

SCOPES = ["rpc", "rpc.voice.read", "rpc.voice.write", "identify"]
OP_HANDSHAKE, OP_FRAME, OP_CLOSE, OP_PING, OP_PONG = 0, 1, 2, 3, 4
TOKEN_FILE = "discord_token.json"
UA = "KnobDeck (https://github.com/, 1.1)"
SPEAK_GREEN = "#3BA55C"


class RPCError(Exception):
    pass


# ---------------------------------------------------------------------------- transport

class _Pipe:
    """Discord IPC: Windows named pipe or Unix socket. Non-blocking reads so one thread can read and write."""

    def __init__(self):
        self.f = self.sock = None
        self.buf = b""
        if sys.platform == "win32":
            import ctypes
            import msvcrt
            from ctypes import wintypes
            self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._k32.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                                ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
            self._k32.PeekNamedPipe.restype = wintypes.BOOL
            for i in range(10):
                try:
                    self.f = open(rf"\\?\pipe\discord-ipc-{i}", "r+b", buffering=0)
                    self._h = msvcrt.get_osfhandle(self.f.fileno())
                    self._dw = wintypes.DWORD
                    self._byref = ctypes.byref
                    return
                except OSError:
                    continue
        else:
            bases = [os.environ.get(k) for k in ("XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP")] + ["/tmp"]
            for base in filter(None, bases):
                for sub in ("", "app/com.discordapp.Discord", "snap.discord", ".flatpak/com.discordapp.Discord/xdg-run"):
                    for i in range(10):
                        path = os.path.join(base, sub, f"discord-ipc-{i}")
                        if not os.path.exists(path):
                            continue
                        try:
                            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                            s.connect(path)
                            s.setblocking(False)
                            self.sock = s
                            return
                        except OSError:
                            continue
        raise ConnectionError("Discord is not running")

    def send(self, op: int, payload: dict):
        data = json.dumps(payload).encode()
        frame = struct.pack("<II", op, len(data)) + data
        if self.f:
            self.f.write(frame)
        else:
            self.sock.setblocking(True)
            try:
                self.sock.sendall(frame)
            finally:
                self.sock.setblocking(False)

    def _available(self) -> bytes:
        if self.f:
            n = self._dw(0)
            if not self._k32.PeekNamedPipe(self._h, None, 0, None, self._byref(n), None):
                raise ConnectionError("Discord closed the connection")
            return self.f.read(n.value) if n.value else b""
        try:
            data = self.sock.recv(65536)
        except BlockingIOError:
            return b""
        if not data:
            raise ConnectionError("Discord closed the connection")
        return data

    def recv(self):
        """Returns (op, payload) or None if no complete frame is waiting."""
        self.buf += self._available()
        if len(self.buf) < 8:
            return None
        op, n = struct.unpack("<II", self.buf[:8])
        if len(self.buf) < 8 + n:
            return None
        payload = json.loads(self.buf[8:8 + n] or b"{}")
        self.buf = self.buf[8 + n:]
        return op, payload

    def close(self):
        for x in (self.f, self.sock):
            try:
                if x:
                    x.close()
            except OSError:
                pass


# ---------------------------------------------------------------------------- client

class DiscordClient:
    def __init__(self):
        self.lock = threading.RLock()
        self.status = "Not set up"
        self.user = None
        self.channel = None                 # {"id", "name"} or None
        self.members: dict[str, dict] = {}  # user id -> {"name","avatar_url","speaking","muted","deaf"}
        self.self_mute = self.self_deaf = False
        self.ctx = None
        self.settings = {}
        self._out: queue.Queue = queue.Queue()
        self._waiting: dict[str, queue.Queue] = {}
        self._thread = None
        self._pipe: _Pipe | None = None
        self._order: list[str] = []
        self._retry_in = 5

    # ---- called from the app's worker thread ------------------------------------
    def ensure(self, ctx):
        self.ctx = ctx
        self.settings = ctx.settings("discord")
        if not self.settings.get("client_id") or not self.settings.get("client_secret"):
            self.status = "Enter the Client ID and Client Secret of your Discord application (see README), then press Save"
            return False
        if not self._thread or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="discord", daemon=True)
            self._thread.start()
        return self.user is not None

    def command(self, cmd: str, args: dict | None = None, evt: str | None = None, timeout=5.0):
        """Send a command through the client thread and wait for the answer."""
        nonce = str(uuid.uuid4())
        q: queue.Queue = queue.Queue()
        self._waiting[nonce] = q
        msg = {"cmd": cmd, "args": args or {}, "nonce": nonce}
        if evt:
            msg["evt"] = evt
        self._out.put(msg)
        try:
            res = q.get(timeout=timeout)
        except queue.Empty:
            raise RPCError(f"Discord did not answer {cmd}")
        finally:
            self._waiting.pop(nonce, None)
        if res.get("evt") == "ERROR":
            raise RPCError(res.get("data", {}).get("message", "Discord error"))
        return res.get("data")

    # ---- client thread -----------------------------------------------------------
    def _run(self):
        while True:
            self._retry_in = 5
            try:
                self._session()
            except Exception as e:      # Discord closed, not running, auth failed...
                with self.lock:
                    self.user = None
                    self.channel = None
                    self.members.clear()
                    if not str(self.status).startswith(("Discord said", "Login failed")):
                        self.status = f"Not connected: {e}"
                self._notify()
            finally:
                if self._pipe:
                    self._pipe.close()
                    self._pipe = None
            time.sleep(self._retry_in)

    def _pump(self, until_nonce=None, timeout=10.0):
        """Send queued commands and dispatch incoming frames; optionally wait for one reply."""
        end = time.monotonic() + timeout
        while True:
            while not self._out.empty():
                self._pipe.send(OP_FRAME, self._out.get_nowait())
            got = self._pipe.recv()
            if got is None:
                if until_nonce is None:
                    return None
                if time.monotonic() > end:
                    raise RPCError("Discord did not answer")
                time.sleep(0.02)
                continue
            op, data = got
            if op == OP_PING:
                self._pipe.send(OP_PONG, data)
            elif op == OP_CLOSE:
                raise ConnectionError(data.get("message", "closed by Discord"))
            elif op == OP_FRAME:
                nonce = data.get("nonce")
                if until_nonce and nonce == until_nonce:
                    return data
                if nonce and nonce in self._waiting:
                    self._waiting[nonce].put(data)
                elif data.get("cmd") == "DISPATCH":
                    self._event(data.get("evt"), data.get("data") or {})

    def _call(self, cmd, args=None, evt=None, timeout=10.0):
        nonce = str(uuid.uuid4())
        msg = {"cmd": cmd, "args": args or {}, "nonce": nonce}
        if evt:
            msg["evt"] = evt
        self._pipe.send(OP_FRAME, msg)
        res = self._pump(until_nonce=nonce, timeout=timeout)
        if res.get("evt") == "ERROR":
            raise RPCError(res.get("data", {}).get("message", "error"))
        return res.get("data") or {}

    def _session(self):
        cid = str(self.settings["client_id"]).strip()
        self.status = "Connecting to Discord..."
        self._pipe = _Pipe()
        self._pipe.send(OP_HANDSHAKE, {"v": 1, "client_id": cid})
        end = time.monotonic() + 10
        while True:                                      # wait for READY
            got = self._pipe.recv()
            if got:
                op, data = got
                if op == OP_CLOSE:
                    raise ConnectionError(data.get("message", "handshake refused - check the Client ID"))
                if data.get("evt") == "READY":
                    break
            elif time.monotonic() > end:
                raise ConnectionError("no answer from Discord")
            time.sleep(0.02)

        token = self._load_token(cid)
        auth = None
        if token:
            try:
                auth = self._call("AUTHENTICATE", {"access_token": token["access_token"]})
            except RPCError:
                token = self._refresh(cid, token)
                if token:
                    try:
                        auth = self._call("AUTHENTICATE", {"access_token": token["access_token"]})
                    except RPCError:
                        auth = None
        if not auth:
            self.status = "Click 'Authorize' in the Discord window to let KnobDeck see your voice channel"
            self._notify()
            try:
                code = self._call("AUTHORIZE", {"client_id": cid, "scopes": SCOPES}, timeout=180)["code"]
            except RPCError as e:
                self.status = f"Discord said: {e} - asking again in a minute"
                self._retry_in = 60          # don't pop the prompt up again right away
                raise
            token = self._exchange(cid, {"grant_type": "authorization_code", "code": code})
            auth = self._call("AUTHENTICATE", {"access_token": token["access_token"]})

        u = auth.get("user") or {}
        with self.lock:
            self.user = u
            self.status = f"Connected as {u.get('global_name') or u.get('username', '?')}"
        for evt in ("VOICE_CHANNEL_SELECT", "VOICE_SETTINGS_UPDATE"):
            self._call("SUBSCRIBE", evt=evt)
        vs = self._call("GET_VOICE_SETTINGS")
        with self.lock:
            self.self_mute, self.self_deaf = bool(vs.get("mute")), bool(vs.get("deaf"))
        self._load_channel()
        while True:
            self._pump()
            time.sleep(0.02)

    def _load_channel(self):
        old = self.channel
        if old:
            for evt in ("VOICE_STATE_CREATE", "VOICE_STATE_UPDATE", "VOICE_STATE_DELETE", "SPEAKING_START", "SPEAKING_STOP"):
                try:
                    self._call("UNSUBSCRIBE", {"channel_id": old["id"]}, evt=evt)
                except RPCError:
                    pass
        ch = self._call("GET_SELECTED_VOICE_CHANNEL")
        with self.lock:
            self.members.clear()
            self._order = []
            if not ch or not ch.get("id"):
                self.channel = None
            else:
                self.channel = {"id": ch["id"], "name": ch.get("name") or "Voice"}
                for v in ch.get("voice_states") or []:
                    self._apply_state(v)
        if self.channel:
            for evt in ("VOICE_STATE_CREATE", "VOICE_STATE_UPDATE", "VOICE_STATE_DELETE", "SPEAKING_START", "SPEAKING_STOP"):
                self._call("SUBSCRIBE", {"channel_id": self.channel["id"]}, evt=evt)
        self._notify()

    def _apply_state(self, v: dict):
        u = v.get("user") or {}
        uid = str(u.get("id") or "")
        if not uid:
            return
        vs = v.get("voice_state") or {}
        if u.get("avatar"):
            avatar = f"https://cdn.discordapp.com/avatars/{uid}/{u['avatar']}.png?size=64"
        else:
            avatar = f"https://cdn.discordapp.com/embed/avatars/{(int(uid) >> 22) % 6}.png"
        m = self.members.get(uid, {"speaking": False})
        m.update(name=v.get("nick") or u.get("global_name") or u.get("username") or "?", avatar_url=avatar,
                 muted=bool(vs.get("mute") or vs.get("self_mute") or vs.get("suppress") or v.get("mute")),
                 deaf=bool(vs.get("deaf") or vs.get("self_deaf")), local_mute=bool(v.get("mute")))
        self.members[uid] = m
        if uid not in self._order:
            self._order.append(uid)

    def _event(self, evt: str, data: dict):
        if evt == "VOICE_CHANNEL_SELECT":
            self._load_channel()
            return
        with self.lock:
            if evt in ("VOICE_STATE_CREATE", "VOICE_STATE_UPDATE"):
                self._apply_state(data)
            elif evt == "VOICE_STATE_DELETE":
                uid = str((data.get("user") or {}).get("id"))
                self.members.pop(uid, None)
                if uid in self._order:
                    self._order.remove(uid)
            elif evt in ("SPEAKING_START", "SPEAKING_STOP"):
                uid = str(data.get("user_id"))
                if uid in self.members:
                    self.members[uid]["speaking"] = evt == "SPEAKING_START"
            elif evt == "VOICE_SETTINGS_UPDATE":
                self.self_mute, self.self_deaf = bool(data.get("mute")), bool(data.get("deaf"))
        self._notify()

    def _notify(self):
        if self.ctx:
            self.ctx.notify()

    # ---- OAuth -------------------------------------------------------------------
    def _token_path(self):
        return data_dir() / TOKEN_FILE

    def _load_token(self, cid):
        try:
            t = json.loads(self._token_path().read_text())
            return t if t.get("client_id") == cid else None
        except (OSError, ValueError):
            return None

    def _exchange(self, cid, form: dict) -> dict:
        body = dict(form, client_id=cid, client_secret=str(self.settings["client_secret"]).strip(),
                    redirect_uri=self.settings.get("redirect_uri") or "http://localhost")
        try:
            tok = self._post_token(body)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:200]
            self.status = f"Login failed ({e.code}): {detail} - check Client Secret and Redirect"
            raise RPCError(self.status)
        tok["client_id"] = cid
        tok["expires_at"] = time.time() + tok.get("expires_in", 0)
        self._token_path().write_text(json.dumps(tok))
        return tok

    @staticmethod
    def _post_token(body: dict) -> dict:
        req = urllib.request.Request("https://discord.com/api/v10/oauth2/token",
                                     data=urllib.parse.urlencode(body).encode(),
                                     headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)

    def _refresh(self, cid, token):
        if not token.get("refresh_token"):
            return None
        try:
            return self._exchange(cid, {"grant_type": "refresh_token", "refresh_token": token["refresh_token"]})
        except Exception:
            return None

    # ---- snapshot for the deck ---------------------------------------------------
    def snapshot(self):
        with self.lock:
            return (dict(self.channel) if self.channel else None,
                    [dict(self.members[u], id=u) for u in self._order if u in self.members],
                    self.self_mute, self.self_deaf, (self.user or {}).get("id"))


CLIENT = DiscordClient()


def _status(ctx):
    CLIENT.ensure(ctx)
    return CLIENT.status


plugin_settings("discord", "Discord", [
    Param("client_id", "Client ID", "text", "", placeholder="from discord.com/developers > your app > OAuth2"),
    Param("client_secret", "Client Secret", "secret", ""),
    Param("redirect_uri", "Redirect", "text", "http://localhost",
          help="Must also be added under OAuth2 > Redirects in your Discord application."),
], status=_status, help="Shows who is in your voice channel and who is speaking. One-time setup: see README, 'Discord setup'.")


def _need(ctx):
    if not CLIENT.ensure(ctx):
        raise RuntimeError(CLIENT.status if len(CLIENT.status) < 40 else "Discord not connected")


@people("discord.voice", "Voice channel", group="Discord", icon="sym:call",
        help="Everyone in your current voice channel; a green ring shows who is speaking. "
             "Press on yourself to mute/unmute, on someone else to mute them just for you.")
class Voice:
    def get(self, ctx, p):
        if not CLIENT.ensure(ctx):
            return People(title="Discord", text="Not connected - see settings")
        ch, members, *_ = CLIENT.snapshot()
        if not ch:
            return People(title="Discord", text="Not in a voice channel")
        return People(title=ch["name"], items=[
            Person(id=m["id"], name=m["name"], avatar_url=m["avatar_url"], speaking=m["speaking"] and not m["muted"],
                   muted=m["muted"], deaf=m["deaf"]) for m in members])

    def press(self, ctx, p, item):
        _need(ctx)
        ch, members, self_mute, _, me = CLIENT.snapshot()
        if item == me:
            CLIENT.command("SET_VOICE_SETTINGS", {"mute": not self_mute})
            return
        m = next((m for m in members if m["id"] == item), None)
        if m:
            CLIENT.command("SET_USER_VOICE_SETTINGS", {"user_id": item, "mute": not m.get("local_mute")})
            ctx.toast(f"{m['name']} {'unmuted' if m.get('local_mute') else 'muted'} for you", 1500)

    def state(self, ctx, p):
        if not CLIENT.ensure(ctx):
            return None
        ch, members, *_ = CLIENT.snapshot()
        if not ch:
            return State(value="not in voice")
        talking = [m["name"] for m in members if m["speaking"] and not m["muted"]]
        return State(label=f"{len(members)} in voice", value=(f"{talking[0]} speaking" if talking else ch["name"]),
                     color=SPEAK_GREEN if talking else None)


@action("discord.mute", "Mute microphone (Discord)", group="Discord", icon="sym:audio")
def mute(ctx, p):
    _need(ctx)
    CLIENT.command("SET_VOICE_SETTINGS", {"mute": not CLIENT.snapshot()[2]})


@mute.state
def mute_state(ctx, p):
    if CLIENT.ensure(ctx) and CLIENT.snapshot()[2]:
        return State(label="Muted", icon="sym:mute", color="#B71C1C", value="muted")
    return None


@action("discord.deafen", "Deafen (Discord)", group="Discord", icon="sym:volume")
def deafen(ctx, p):
    _need(ctx)
    CLIENT.command("SET_VOICE_SETTINGS", {"deaf": not CLIENT.snapshot()[3]})


@deafen.state
def deafen_state(ctx, p):
    if CLIENT.ensure(ctx) and CLIENT.snapshot()[3]:
        return State(label="Deafened", icon="sym:mute", color="#B71C1C", value="deafened")
    return None


@action("discord.leave", "Leave voice channel", group="Discord", icon="sym:close")
def leave(ctx, p):
    _need(ctx)
    CLIENT.command("SELECT_VOICE_CHANNEL", {"channel_id": None, "force": True})
