"""OBS Studio via its built-in WebSocket server (OBS 28+: Tools > WebSocket Server Settings)."""
import time

from knobdeck.plugin import Param, State, action, plugin_settings

plugin_settings("obs", "OBS Studio", [
    Param("host", "Host", "text", "localhost"),
    Param("port", "Port", "number", 4455),
    Param("password", "Password", "secret", "", help="From OBS: Tools > WebSocket Server Settings > Show Connect Info"),
])

_client = {"c": None, "key": None, "retry_at": 0.0}


def _obs(ctx, quiet=False):
    s = ctx.settings("obs")
    key = (s["host"], int(s["port"]), s["password"])
    if _client["c"] is not None and _client["key"] == key:
        return _client["c"]
    if quiet and time.monotonic() < _client["retry_at"]:
        return None                       # OBS was not reachable a moment ago; don't stall state polling
    try:
        import obsws_python as obs
        _client["c"] = obs.ReqClient(host=key[0], port=key[1], password=key[2] or None, timeout=2)
        _client["key"] = key
        return _client["c"]
    except Exception:
        _client["c"] = None
        _client["retry_at"] = time.monotonic() + 10
        if quiet:
            return None
        raise RuntimeError("OBS not reachable")


def _call(ctx, fn, quiet=False):
    c = _obs(ctx, quiet)
    if c is None:
        return None
    try:
        return fn(c)
    except Exception:
        _client["c"] = None               # connection dropped, reconnect next time
        _client["retry_at"] = time.monotonic() + 5
        if quiet:
            return None
        raise


@action("obs.scene", "Switch scene", group="OBS", icon="sym:video",
        params=[Param("scene", "Scene name", "text", "", placeholder="exactly as named in OBS")])
def scene(ctx, p):
    _call(ctx, lambda c: c.set_current_program_scene(p["scene"]))


@scene.state
def scene_state(ctx, p):
    cur = _call(ctx, lambda c: c.get_current_program_scene().current_program_scene_name, quiet=True)
    if cur is None:
        return None
    return State(value="on air" if cur == p["scene"] else "")


@action("obs.record", "Start / stop recording", group="OBS", icon="sym:video")
def record(ctx, p):
    _call(ctx, lambda c: c.toggle_record())


@record.state
def record_state(ctx, p):
    on = _call(ctx, lambda c: c.get_record_status().output_active, quiet=True)
    if on:
        return State(label="Recording", color="#D32F2F", value="REC")
    return None


@action("obs.stream", "Start / stop streaming", group="OBS", icon="sym:upload")
def stream(ctx, p):
    _call(ctx, lambda c: c.toggle_stream())


@stream.state
def stream_state(ctx, p):
    on = _call(ctx, lambda c: c.get_stream_status().output_active, quiet=True)
    if on:
        return State(label="Live", color="#D32F2F", value="LIVE")
    return None


@action("obs.mute", "Mute audio source", group="OBS", icon="sym:audio",
        params=[Param("source", "Audio source", "text", "Mic/Aux")])
def mute(ctx, p):
    _call(ctx, lambda c: c.toggle_input_mute(p["source"]))


@mute.state
def mute_state(ctx, p):
    m = _call(ctx, lambda c: c.get_input_mute(p["source"]).input_muted, quiet=True)
    if m:
        return State(label="Muted", icon="sym:mute", color="#B71C1C", value="muted")
    return None


@action("obs.replay", "Save replay", group="OBS", icon="sym:save")
def replay(ctx, p):
    _call(ctx, lambda c: c.save_replay_buffer())
    ctx.toast("Replay saved", 1200)
