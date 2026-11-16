"""Minimal fake of the Discord desktop app's local RPC (IPC socket) for tests."""
import json
import os
import socket
import struct
import threading
import time


class FakeDiscord:
    def __init__(self, folder):
        self.path = os.path.join(folder, "discord-ipc-0")
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        self.srv.listen(1)
        self.conn = None
        self.received = []
        self.subs = set()
        self.mute = self.deaf = False
        self.channel = {"id": "900", "name": "Gaming Night", "voice_states": [
            self._vs("1", "dominik", "Dominik", None),
            self._vs("2", "anna", "Anna", "abc123"),
            self._vs("3", "max", None, None, self_mute=True),
        ]}
        threading.Thread(target=self._run, daemon=True).start()

    @staticmethod
    def _vs(uid, username, global_name, avatar, self_mute=False):
        return {"nick": None, "mute": False, "volume": 100,
                "voice_state": {"mute": False, "deaf": False, "self_mute": self_mute, "self_deaf": False, "suppress": False},
                "user": {"id": uid, "username": username, "global_name": global_name, "avatar": avatar}}

    def _send(self, op, payload):
        data = json.dumps(payload).encode()
        self.conn.sendall(struct.pack("<II", op, len(data)) + data)

    def dispatch(self, evt, data):
        self._send(1, {"cmd": "DISPATCH", "evt": evt, "data": data})

    def _recv(self):
        hdr = b""
        while len(hdr) < 8:
            c = self.conn.recv(8 - len(hdr))
            if not c:
                raise ConnectionError
            hdr += c
        op, n = struct.unpack("<II", hdr)
        body = b""
        while len(body) < n:
            body += self.conn.recv(n - len(body))
        return op, json.loads(body)

    def _run(self):
        self.conn, _ = self.srv.accept()
        while True:
            op, m = self._recv()
            self.received.append(m)
            if op == 0:
                self.dispatch("READY", {"v": 1, "user": {"id": "1", "username": "dominik"}})
                continue
            cmd, nonce, args = m.get("cmd"), m.get("nonce"), m.get("args") or {}
            reply = {}
            if cmd == "AUTHORIZE":
                reply = {"code": "the-code"}
            elif cmd == "AUTHENTICATE":
                if args.get("access_token") != "tok":
                    self._send(1, {"cmd": cmd, "evt": "ERROR", "nonce": nonce, "data": {"code": 4009, "message": "Invalid token"}})
                    continue
                reply = {"user": {"id": "1", "username": "dominik", "global_name": "Dominik"}}
            elif cmd == "SUBSCRIBE":
                self.subs.add((m.get("evt"), args.get("channel_id")))
            elif cmd == "GET_VOICE_SETTINGS":
                reply = {"mute": self.mute, "deaf": self.deaf}
            elif cmd == "GET_SELECTED_VOICE_CHANNEL":
                reply = self.channel
            elif cmd == "SET_VOICE_SETTINGS":
                self.mute = args.get("mute", self.mute)
                self.deaf = args.get("deaf", self.deaf)
                reply = {"mute": self.mute, "deaf": self.deaf}
            self._send(1, {"cmd": cmd, "evt": m.get("evt"), "nonce": nonce, "data": reply})
            if cmd == "SET_VOICE_SETTINGS":
                time.sleep(0.05)
                self.dispatch("VOICE_SETTINGS_UPDATE", {"mute": self.mute, "deaf": self.deaf})
