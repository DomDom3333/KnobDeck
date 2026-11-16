"""USB link to the deck: newline-delimited JSON over the ESP32-C3's USB Serial/JTAG port."""
from __future__ import annotations

import base64
import json
import logging
import errno
import queue
import sys
import threading
import time
from typing import Callable

import serial
import serial.tools.list_ports

log = logging.getLogger("knobdeck.device")

ESPRESSIF_VID = 0x303A
USB_JTAG_PID = 0x1001
CHUNK = 1024


def find_ports() -> list[dict]:
    out = []
    for p in serial.tools.list_ports.comports():
        out.append({
            "device": p.device, "description": p.description or "",
            "deck": p.vid == ESPRESSIF_VID and p.pid == USB_JTAG_PID,
        })
    return out


def auto_port() -> str | None:
    for p in find_ports():
        if p["deck"]:
            return p["device"]
    return None


class Device:
    """Keeps a connection open (reconnecting as needed) and hands every incoming message to on_message."""

    def __init__(self, on_message: Callable[[dict], None], on_connect: Callable[[], None],
                 on_disconnect: Callable[[], None], port_setting: Callable[[], str]):
        self.on_message = on_message
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.port_setting = port_setting
        self.ser: serial.Serial | None = None
        self.port: str | None = None
        self.info: dict = {}
        self.error = ""                         # why the port can't be opened (shown in the settings window)
        self._wlock = threading.Lock()
        self._ulock = threading.Lock()          # one upload at a time (acks are matched in order)
        self._acks: queue.Queue = queue.Queue()
        self._replies: queue.Queue = queue.Queue()     # fdata answers
        self._stop = threading.Event()
        self._reconnect = threading.Event()
        self._paused = threading.Event()        # port released (e.g. for a firmware update)
        self._thread = threading.Thread(target=self._run, name="device", daemon=True)

    # ------------------------------------------------------------ public
    @property
    def connected(self) -> bool:
        return self.ser is not None and bool(self.info)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._close()

    def reconnect(self):
        self._reconnect.set()

    def pause(self):
        """Close the port and leave it alone until resume() (another program needs it)."""
        self._paused.set()
        self._reconnect.set()
        for _ in range(50):
            if not self.ser:
                break
            time.sleep(0.05)
        self._close()

    def resume(self):
        self._paused.clear()

    def send(self, msg: dict) -> bool:
        ser = self.ser
        if not ser:
            return False
        data = (json.dumps(msg, separators=(",", ":"), ensure_ascii=True) + "\n").encode()
        try:
            with self._wlock:
                ser.write(data)
            return True
        except (serial.SerialException, OSError) as e:
            log.warning("write failed: %s", e)
            self._close()
            return False

    def upload_icon(self, name: str, data: bytes) -> bool:
        with self._ulock:
            return self._upload(name, data)

    def _upload(self, name: str, data: bytes) -> bool:
        chunks = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)] or [b""]
        for i, c in enumerate(chunks):
            self._drain_acks()
            ok = self.send({"t": "icon", "n": name, "i": i, "last": i == len(chunks) - 1,
                            "d": base64.b64encode(c).decode()})
            if not ok or not self._wait_ack("icon", name, i):
                log.warning("icon %s upload failed at chunk %d", name, i)
                return False
        return True

    def put_file(self, name: str, data: bytes, tag: str = "") -> bool:
        """Store a file on the deck (firmware 2.1+)."""
        with self._ulock:
            chunks = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)] or [b""]
            for i, c in enumerate(chunks):
                self._drain_acks()
                last = i == len(chunks) - 1
                msg = {"t": "fput", "n": name, "i": i, "last": last, "d": base64.b64encode(c).decode()}
                if last:
                    msg["tag"] = tag
                if not self.send(msg) or not self._wait_ack("fput", name, i, 5.0):
                    log.warning("storing %s on the deck failed at chunk %d", name, i)
                    return False
            return True

    def get_file(self, name: str | None = None, icon: str | None = None, timeout: float = 5.0) -> bytes | None:
        """Read a file (or an image with icon=...) back from the deck (firmware 2.1+)."""
        key, val = ("icon", icon) if icon else ("n", name)
        out = bytearray()
        with self._ulock:
            for i in range(4096):
                while not self._replies.empty():
                    self._replies.get_nowait()
                if not self.send({"t": "fget", key: val, "i": i}):
                    return None
                end = time.monotonic() + timeout
                r = None
                while time.monotonic() < end:
                    try:
                        m = self._replies.get(timeout=max(0.01, end - time.monotonic()))
                    except queue.Empty:
                        break
                    if m.get(key) == val and m.get("i") == i:
                        r = m
                        break
                if not r or not r.get("ok"):
                    return None
                out += base64.b64decode(r.get("d") or "")
                if r.get("last"):
                    return bytes(out)
        return None

    def wait_ack(self, what: str, timeout=3.0) -> bool:
        return self._wait_ack(what, None, None, timeout)

    # ------------------------------------------------------------ internals
    def _drain_acks(self):
        while not self._acks.empty():
            self._acks.get_nowait()

    def _wait_ack(self, what, name, seq, timeout=3.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                a = self._acks.get(timeout=max(0.01, end - time.monotonic()))
            except queue.Empty:
                break
            if a.get("what") == what and (name is None or a.get("n") == name) and (seq is None or a.get("i") == seq):
                return bool(a.get("ok"))
        return False

    def _open(self) -> bool:
        setting = (self.port_setting() or "auto").strip()
        port = auto_port() if setting.lower() == "auto" else setting
        if not port:
            self.error = ""
            return False
        try:
            s = serial.Serial()
            s.port = port
            s.baudrate = 115200
            s.timeout = 0.2
            s.write_timeout = 2
            # Never toggle DTR/RTS: on the ESP32-C3 USB port "RTS on, DTR off" resets the chip.
            # Windows opens with both off. Linux switches both on when the port is opened (one request, so no
            # reset); leaving them on avoids passing through the reset state when pyserial changes them one by one.
            s.dtr = s.rts = sys.platform.startswith("linux")
            s.open()
        except (serial.SerialException, OSError) as e:
            msg = str(e)
            if getattr(e, "errno", None) == errno.EACCES or "Permission denied" in msg:
                self.error = (f"No permission to open {port}. Run install.sh (adds a udev rule), then unplug and "
                              f"replug the deck" if sys.platform.startswith("linux") else f"Access denied: {port}")
            elif "busy" in msg.lower() or getattr(e, "errno", None) == errno.EBUSY or "PermissionError" in msg:
                self.error = f"{port} is in use by another program (serial monitor, flasher ...)"
            else:
                self.error = f"Can't open {port}: {msg}"
            if self.error != getattr(self, "_logged", ""):
                log.warning(self.error)
                self._logged = self.error
            return False
        self.error = ""
        self.ser, self.port, self.info = s, port, {}
        log.info("opened %s", port)
        self.send({"t": "hello?"})
        return True

    def _close(self):
        s, self.ser = self.ser, None
        was = bool(self.info)
        self.info = {}
        if s:
            try:
                s.close()
            except Exception:
                pass
            if was:
                self.on_disconnect()

    def _run(self):
        buf = b""
        last_ping = last_hello = 0.0
        while not self._stop.is_set():
            if self._reconnect.is_set():
                self._reconnect.clear()
                self._close()
            if self._paused.is_set():
                self._close()
                self._stop.wait(0.3)
                continue
            if not self.ser:
                if not self._open():
                    self._stop.wait(2.0)
                    continue
                buf = b""
                last_hello = time.monotonic()
            now = time.monotonic()
            if now - last_ping > 2.0:
                last_ping = now
                self.send({"t": "ping"})
            if not self.info and now - last_hello > 3.0:     # deck didn't answer yet (e.g. still booting)
                last_hello = now
                self.send({"t": "hello?"})
            try:
                # return as soon as anything arrives (read(4096) would wait for the full timeout)
                chunk = self.ser.read(max(1, self.ser.in_waiting)) if self.ser else b""
            except (serial.SerialException, OSError, TypeError, AttributeError):
                self._close()
                continue
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line.startswith(b"{"):
                    continue                 # boot messages from the ROM etc.
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                self._dispatch(msg)

    def _dispatch(self, msg: dict):
        t = msg.get("t")
        if t == "ack":
            self._acks.put(msg)
            return
        if t == "fdata":
            self._replies.put(msg)
            return
        if t == "hello":
            # first answer after opening, or the deck restarted: (re)sync layout, icons and settings
            self.info = msg
            threading.Thread(target=self.on_connect, name="sync", daemon=True).start()
            return
        try:
            self.on_message(msg)
        except Exception:
            log.exception("handling %s failed", msg)
