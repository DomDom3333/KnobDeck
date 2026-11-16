"""Media control: play/pause, next, previous, stop.

Windows: media keys (Spotify, browsers, VLC ... anything that listens to them).
Linux: MPRIS via playerctl when installed (talks to the player directly), otherwise media keys.
"""
from knobdeck import osutil, winput
from knobdeck.plugin import action


def _media(key: str, mpris: str):
    if osutil.IS_LINUX and osutil.have("playerctl"):
        try:
            osutil.run(["playerctl", mpris])
            return
        except Exception:
            pass                     # no player running that speaks MPRIS: try the media key
    winput.press(key)


@action("media.play_pause", "Play / Pause", group="Media", icon="sym:play")
def play_pause(ctx, p):
    _media("playpause", "play-pause")


@action("media.next", "Next track", group="Media", icon="sym:next")
def next_track(ctx, p):
    _media("nexttrack", "next")


@action("media.prev", "Previous track", group="Media", icon="sym:prev")
def prev_track(ctx, p):
    _media("prevtrack", "previous")


@action("media.stop", "Stop", group="Media", icon="sym:stop")
def stop(ctx, p):
    _media("stop", "stop")
