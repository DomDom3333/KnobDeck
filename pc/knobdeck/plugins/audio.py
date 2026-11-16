"""Audio: master volume dial, per-app volume dial, speaker and microphone mute.

Windows: Core Audio via COM (pycaw's interface definitions, our own calls - see _WinEndpoint).
Linux:   PulseAudio / PipeWire (pipewire-pulse) via pulsectl.
"""
import sys
import time

from knobdeck.plugin import DialState, Param, State, action, dial

IS_WIN = sys.platform == "win32"


class Volume:
    """One volume control: get/set 0..100 and mute."""

    def get(self) -> int: ...
    def set(self, v: int): ...
    def muted(self) -> bool: ...
    def set_mute(self, m: bool): ...


# ================================================================== Windows

_cache = {}
_com = {}


def _enumerator():
    import comtypes
    try:
        from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
    except ImportError:
        from pycaw.pycaw import IMMDeviceEnumerator
    if "enum" not in _com:
        clsid = comtypes.GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")     # CLSID_MMDeviceEnumerator
        _com["enum"] = comtypes.CoCreateInstance(clsid, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER)
    return _com["enum"]


class _WinEndpoint(Volume):
    def __init__(self, ev):
        self.ev = ev

    def get(self):
        return round(self.ev.GetMasterVolumeLevelScalar() * 100)

    def set(self, v):
        self.ev.SetMasterVolumeLevelScalar(max(0, min(100, v)) / 100, None)

    def muted(self):
        return bool(self.ev.GetMute())

    def set_mute(self, m):
        self.ev.SetMute(1 if m else 0, None)


class _WinSession(Volume):
    def __init__(self, sv):
        self.sv = sv

    def get(self):
        return round(self.sv.GetMasterVolume() * 100)

    def set(self, v):
        self.sv.SetMasterVolume(max(0, min(100, v)) / 100, None)

    def muted(self):
        return bool(self.sv.GetMute())

    def set_mute(self, m):
        self.sv.SetMute(1 if m else 0, None)


def _win_endpoint(kind: str) -> Volume:
    """Default speakers ('out') or microphone ('in').

    Done by hand instead of with pycaw's helpers: some pycaw versions cast the activated object to
    IAudioEndpointVolume without taking a reference, so Python releases it twice and the app crashes."""
    import comtypes
    from pycaw.pycaw import IAudioEndpointVolume
    try:
        dev = _enumerator().GetDefaultAudioEndpoint(0 if kind == "out" else 1, 1)    # eRender/eCapture, eMultimedia
    except comtypes.COMError:
        dev = None
    if not dev:
        raise RuntimeError("no microphone found" if kind == "in" else "no speakers found")
    unk = dev.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None)
    return _WinEndpoint(unk.QueryInterface(IAudioEndpointVolume))        # proper reference of its own


def _win_sessions(name: str) -> list[Volume]:
    import comtypes
    import psutil
    from pycaw.api.audioclient import ISimpleAudioVolume
    from pycaw.api.audiopolicy import IAudioSessionControl2, IAudioSessionManager2
    dev = _enumerator().GetDefaultAudioEndpoint(0, 1)
    mgr = dev.Activate(IAudioSessionManager2._iid_, comtypes.CLSCTX_ALL, None).QueryInterface(IAudioSessionManager2)
    sessions = mgr.GetSessionEnumerator()
    out = []
    for i in range(sessions.GetCount()):
        ctl = sessions.GetSession(i)
        if not ctl:
            continue
        try:
            pid = ctl.QueryInterface(IAudioSessionControl2).GetProcessId()
            if not pid or _norm(psutil.Process(pid).name()) != name:
                continue
        except (comtypes.COMError, psutil.Error):
            continue
        out.append(_WinSession(ctl.QueryInterface(ISimpleAudioVolume)))
    return out


# ================================================================== Linux (PulseAudio / PipeWire)

_pulse = {"p": None}


def _pa(fn):
    """Run fn(pulse) with a connection, reconnecting once if the sound server restarted."""
    try:
        import pulsectl
    except ImportError:
        raise RuntimeError("pulsectl is missing - run install.sh again")
    for attempt in (1, 2):
        if _pulse["p"] is None:
            try:
                _pulse["p"] = pulsectl.Pulse("knobdeck")
            except Exception as e:
                raise RuntimeError(f"no PulseAudio/PipeWire sound server ({e})")
        try:
            return fn(_pulse["p"])
        except (pulsectl.PulseDisconnected, pulsectl.PulseOperationFailed):
            try:
                _pulse["p"].close()
            except Exception:
                pass
            _pulse["p"] = None
            if attempt == 2:
                raise


class _PaObj(Volume):
    """A sink, source or sink input; looked up fresh on every call (cheap, and follows device changes)."""

    def __init__(self, lookup):
        self.lookup = lookup        # fn(pulse) -> object

    def get(self):
        return _pa(lambda p: round(p.volume_get_all_chans(self.lookup(p)) * 100))

    def set(self, v):
        _pa(lambda p: p.volume_set_all_chans(self.lookup(p), max(0, min(100, v)) / 100))

    def muted(self):
        return _pa(lambda p: bool(self.lookup(p).mute))

    def set_mute(self, m):
        _pa(lambda p: p.mute(self.lookup(p), bool(m)))


def _pa_default(kind: str):
    def lookup(p):
        info = p.server_info()
        if kind == "out":
            return p.get_sink_by_name(info.default_sink_name)
        return p.get_source_by_name(info.default_source_name)
    return lookup


def _pa_sessions(name: str) -> list[Volume]:
    def find(p):
        out = []
        for si in p.sink_input_list():
            props = si.proplist
            names = {_norm(props.get(k, "")) for k in ("application.process.binary", "application.name",
                                                         "application.id", "node.name")}
            if name in names:
                out.append(si.index)
        return out
    return [_PaObj(lambda p, i=i: p.sink_input_info(i)) for i in _pa(find)]


# ================================================================== common

def _norm(s: str) -> str:
    return (s or "").lower().strip().removesuffix(".exe")


def _endpoint(kind: str) -> Volume:
    """Default output ('out') or input ('in'), re-fetched every few seconds so switching headsets is picked up."""
    hit = _cache.get(kind)
    if hit and time.monotonic() - hit[0] < 5:
        return hit[1]
    if IS_WIN:
        v = _win_endpoint(kind)
    elif sys.platform.startswith("linux"):
        v = _PaObj(_pa_default(kind))
        v.get()                      # fail early if there's no sound server
    else:
        raise RuntimeError(f"audio control is not available on {sys.platform}")
    _cache[kind] = (time.monotonic(), v)
    return v


def _sessions(name: str) -> list[Volume]:
    name = _norm(name)
    if IS_WIN:
        return _win_sessions(name)
    if sys.platform.startswith("linux"):
        return _pa_sessions(name)
    raise RuntimeError(f"audio control is not available on {sys.platform}")


def _vol_state(v: Volume, step=2) -> DialState:
    val = v.get()
    return DialState(value=val, step=step, text="Muted" if v.muted() else f"{val}%")


@dial("audio.volume", "System volume", group="Audio", icon="sym:volume",
      params=[Param("step", "Step per click (%)", "number", 2)],
      help="Turn to change the volume, press to mute.")
class SystemVolume:
    def open(self, ctx, p):
        return _vol_state(_endpoint("out"), int(p["step"]))

    def poll(self, ctx, p):
        return self.open(ctx, p)

    def turn(self, ctx, p, delta):
        v = _endpoint("out")
        step = int(p["step"])
        new = max(0, min(100, v.get() + delta * step))
        v.set(new)
        if new > 0 and v.muted():
            v.set_mute(False)
        return _vol_state(v, step)

    def press(self, ctx, p):
        v = _endpoint("out")
        v.set_mute(not v.muted())
        return _vol_state(v, int(p["step"]))


@dial("audio.app_volume", "App volume", group="Audio", icon="sym:volume_mid",
      params=[Param("process", "Program", "text", "spotify.exe" if IS_WIN else "spotify",
                    placeholder="spotify.exe, discord.exe, chrome.exe" if IS_WIN else "spotify, firefox, discord"),
              Param("step", "Step per click (%)", "number", 2)],
      help="Volume of one program (as in the volume mixer). The program must be playing sound once.")
class AppVolume:
    def _state(self, p):
        vols = _sessions(p["process"])
        if not vols:
            return DialState(value=0, text="not running")
        return _vol_state(vols[0], int(p["step"]))

    def open(self, ctx, p):
        return self._state(p)

    def poll(self, ctx, p):
        return self._state(p)

    def turn(self, ctx, p, delta):
        for v in _sessions(p["process"]):
            v.set(v.get() + delta * int(p["step"]))
        return self._state(p)

    def press(self, ctx, p):
        for v in _sessions(p["process"]):
            v.set_mute(not v.muted())
        return self._state(p)


@action("audio.mic_mute", "Microphone mute", group="Audio", icon="sym:audio",
        help="Mutes the default microphone for all programs. The tile turns red while muted.")
def mic_mute(ctx, p):
    v = _endpoint("in")
    v.set_mute(not v.muted())


@mic_mute.state
def mic_state(ctx, p):
    if _endpoint("in").muted():
        return State(label="Mic muted", icon="sym:mute", color="#B71C1C", value="muted")
    return State(value="live")


@action("audio.speaker_mute", "Speaker mute", group="Audio", icon="sym:mute")
def speaker_mute(ctx, p):
    v = _endpoint("out")
    v.set_mute(not v.muted())


@speaker_mute.state
def speaker_state(ctx, p):
    v = _endpoint("out")
    if v.muted():
        return State(label="Muted", icon="sym:mute", color="#B71C1C", value="muted")
    return State(value=f"{v.get()}%")


@action("audio.volume_step", "Volume up / down", group="Audio", icon="sym:plus",
        params=[Param("delta", "Change (%)", "number", 5, help="Negative to turn it down")])
def volume_step(ctx, p):
    v = _endpoint("out")
    new = max(0, min(100, v.get() + int(p["delta"])))
    v.set(new)
    ctx.toast(f"Volume {new}%", 900)
