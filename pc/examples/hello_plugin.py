r"""Example KnobDeck plugin. Copy into %APPDATA%\KnobDeck\plugins\ and restart KnobDeck.

It adds three entries to the action list in the settings window:
  - "Say hello"        (an action with a text field and a live counter on the tile)
  - "Brightness dial"  (a dial; here it just keeps a number - wire it to anything you like)
  - "Clock rings"      (a stats source: hour / minute / second rings)
"""
import time

from knobdeck.plugin import DialState, Param, Ring, State, action, dial, stats

counter = {"n": 0}


@action("example.hello", "Say hello", group="Examples", icon="sym:bell",
        params=[Param("text", "Message", "text", "Hello from the PC!")])
def hello(ctx, p):
    counter["n"] += 1
    ctx.toast(p["text"])


@hello.state
def hello_state(ctx, p):
    # shown under the label in the middle of the deck; colour turns green after 5 presses
    return State(value=f"pressed {counter['n']}x", color="#2E7D32" if counter["n"] >= 5 else None)


@dial("example.number", "Number dial", group="Examples", icon="sym:settings",
      params=[Param("max", "Maximum", "number", 50)])
class NumberDial:
    value = 10

    def open(self, ctx, p):
        return DialState(self.value, 0, int(p["max"]), 1, f"{self.value}")

    def turn(self, ctx, p, delta):
        self.value = max(0, min(int(p["max"]), self.value + delta))
        return self.open(ctx, p)

    def press(self, ctx, p):
        self.value = 0
        return self.open(ctx, p)


@stats("example.clock", "Clock rings", group="Examples", icon="sym:loop")
def clock(ctx, p):
    t = time.localtime()
    return [Ring("H", t.tm_hour, f"{t.tm_hour}", max=24, color="#EF5350"),
            Ring("M", t.tm_min, f"{t.tm_min}", max=60, color="#66BB6A"),
            Ring("S", t.tm_sec, f"{t.tm_sec}", max=60, color="#42A5F5")]
