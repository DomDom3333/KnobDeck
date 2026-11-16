r"""Example screen plugin (needs deck firmware 2.x). Copy into %APPDATA%\KnobDeck\plugins\ and restart KnobDeck,
then add a tile with type "Screen" and pick "Stopwatch".

Shows how to
  - draw a free layout (canvas) and change single elements with patch()
  - react to the knob: press = start/stop, turn = lap list, hold = reset (hold="send" keeps the screen open)
  - use the speaker and the LED
"""
import time

from knobdeck.plugin import State, arc, label, list_item, screen


def fmt(s):
    m, s = divmod(s, 60)
    return f"{int(m):02d}:{s:05.2f}"


@screen("example.stopwatch", "Stopwatch", group="Examples", icon="sym:stopwatch")
class Stopwatch:
    def __init__(self):
        self.t0 = None          # time.time() when started (None = stopped)
        self.acc = 0.0          # seconds counted before the last start
        self.laps = []
        self.view = "watch"

    def elapsed(self):
        return self.acc + (time.time() - self.t0 if self.t0 else 0)

    def open(self, ctx, p, scr):
        self.view = "watch"
        self.draw(scr)

    def draw(self, scr):
        if self.view == "laps":
            items = [list_item(str(i), f"Lap {i + 1}: {fmt(t)}", icon="sym:flag") for i, t in enumerate(self.laps)]
            scr.list(items or [list_item("none", "No laps yet")], title="Laps", hold="send")
            return
        e = self.elapsed()
        scr.canvas([
            arc("sec", value=int(e % 60), max=60, w=224, width=10, color="#00ACC1"),
            label("time", fmt(e), y=-8, size=36),
            label("hint", "running" if self.t0 else "press to start", y=30, size=14,
                  color="#00E676" if self.t0 else "#9E9E9E"),
            label("laps", f"{len(self.laps)} laps · turn to see", y=78, size=12, color="#757575"),
        ], hold="send")

    def poll(self, ctx, p, scr):
        if self.view == "watch" and self.t0:
            e = self.elapsed()
            scr.patch({"id": "time", "text": fmt(e)}, {"id": "sec", "value": int(e % 60)})

    def event(self, ctx, p, scr, ev):
        if ev.type == "press" and self.view == "watch":
            if self.t0:
                self.acc, self.t0 = self.elapsed(), None
                self.laps.append(self.acc)
                ctx.beep(freq=660, ms=60)
                ctx.led_off()
            else:
                self.t0 = time.time()
                ctx.beep(freq=990, ms=60)
                ctx.led("#00ACC1", "pulse", period=2000, bright=20)
        elif ev.type == "turn":
            self.view = "laps" if ev.delta > 0 else "watch"
        elif ev.type == "press":            # on the lap list: back to the watch
            self.view = "watch"
        elif ev.type == "hold":             # reset
            self.t0, self.acc, self.laps, self.view = None, 0.0, [], "watch"
            ctx.beep("ok")
            ctx.led_off()
        elif ev.type == "close":
            return
        self.draw(scr)

    def state(self, ctx, p):
        return State(value=fmt(self.elapsed()) if self.t0 or self.acc else "")
