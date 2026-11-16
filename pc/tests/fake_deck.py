"""A fake deck on a pseudo-terminal that speaks the firmware protocol (for testing the PC app on Linux)."""
import base64
import json
import os
import pty
import threading
import time
import tty


class FakeDeck:
    def __init__(self, fw="1.0.0", caps=None):
        self.fw, self.caps = fw, caps
        self.master, slave = pty.openpty()
        tty.setraw(slave)          # no echo / line editing, like a real USB serial port
        self.path = os.ttyname(slave)
        self.rx = []                 # messages received from the PC
        self.icons = {}
        self.files = {}              # name -> (bytes, tag)   (firmware 2.1 file store)
        self.id = "58cf79a1b2c3"
        self.layout_v = "default"
        self._buf = b""
        threading.Thread(target=self._run, daemon=True).start()

    def send(self, msg):
        os.write(self.master, (json.dumps(msg) + "\n").encode())

    def hello(self):
        m = {"t": "hello", "fw": self.fw, "board": "esp32-c3-lcdkit", "v": self.layout_v,
             "icons": sorted(self.icons), "free": 1500000}
        if self.caps is not None:
            m.update(proto=2, caps=self.caps, img_max=128, id=self.id,
                     tpl=["dial", "gauges", "ring", "list", "card", "canvas"])
            if "files" in self.caps:
                m["files"] = {n: {"size": len(d), "tag": t} for n, (d, t) in self.files.items()}
        self.send(m)

    def _run(self):
        os.write(self.master, b"ESP-ROM:esp32c3-api1-20210207\r\nbuild: boot noise\r\n")
        partial = {}
        while True:
            data = os.read(self.master, 65536)
            self._buf += data
            while b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                self.rx.append(m)
                t = m["t"]
                if t == "hello?":
                    self.hello()
                elif t == "layout":
                    self.layout_v = m["v"]
                    self.send({"t": "ack", "what": "layout", "ok": True})
                elif t == "icon":
                    partial.setdefault(m["n"], b"")
                    if m["i"] == 0:
                        partial[m["n"]] = b""
                    partial[m["n"]] += base64.b64decode(m["d"])
                    if m["last"]:
                        self.icons[m["n"]] = partial.pop(m["n"])
                    self.send({"t": "ack", "what": "icon", "n": m["n"], "i": m["i"], "ok": True})
                elif t == "fput":
                    part = partial.setdefault("f:" + m["n"], b"")
                    if m["i"] == 0:
                        part = b""
                    partial["f:" + m["n"]] = part + base64.b64decode(m["d"])
                    if m["last"]:
                        self.files[m["n"]] = (partial.pop("f:" + m["n"]), m.get("tag", ""))
                    self.send({"t": "ack", "what": "fput", "n": m["n"], "i": m["i"], "ok": True})
                elif t == "fget":
                    key = "icon" if "icon" in m else "n"
                    data = self.icons.get(m[key]) if key == "icon" else (self.files.get(m[key]) or (None,))[0]
                    r = {"t": "fdata", key: m[key], "i": m["i"]}
                    if data is None:
                        r["ok"] = False
                    else:
                        c = data[m["i"] * 2048:(m["i"] + 1) * 2048]
                        r.update(ok=True, size=len(data), last=(m["i"] + 1) * 2048 >= len(data),
                                 d=base64.b64encode(c).decode())
                    self.send(r)
                elif t == "ir_learn":
                    self.send({"t": "ir", "raw": [9000, 4500, 560, 560, 560, 1690]})
                elif t == "icon_gc":
                    for k in list(self.icons):
                        if k not in m["keep"]:
                            del self.icons[k]
                    self.send({"t": "ack", "what": "icon_gc", "i": 0, "ok": True})

    def got(self, t, timeout=3.0, **match):
        end = time.time() + timeout
        while time.time() < end:
            for m in self.rx:
                if m.get("t") == t and all(m.get(k) == v for k, v in match.items()):
                    return m
            time.sleep(0.05)
        return None
