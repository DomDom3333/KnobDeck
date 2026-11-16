# Writing KnobDeck plugins

A plugin is one Python file. Put it into `%APPDATA%\KnobDeck\plugins\` (Linux: `~/.config/knobdeck/plugins/`)
and restart KnobDeck (tray > Quit, then
start it again). Its actions appear in the settings window under their group. Load errors are shown in the
*Plugins* tab.

The built-in plugins in `pc/knobdeck/plugins/` use the same API and are good templates
(`screens.py` for screens, `audio.py` for dials and live state, `obs.py` for plugin settings, `discord.py` for
background connections, `ir.py` for IR). `pc/examples/` has two complete example plugins.

**You never need new firmware for a plugin.** Deck firmware 2.x has a fixed set of building blocks - six screen
templates, popups, the RGB LED, the speaker, IR send/learn, pictures up to 128×128 and text with accents - and
sends every knob turn and press to the PC. Everything else happens in Python.

## Actions

```python
from knobdeck.plugin import action, Param, State

@action("myplugin.say", "Say something", group="My stuff", icon="sym:bell",
        params=[Param("text", "Text", "text", "Hello!")],
        help="Shown under the action in the settings window.")
def say(ctx, p):
    ctx.toast(p["text"])
```

* `id` must be unique; keep it stable (tiles store it).
* `p` holds the tile's parameter values (defaults filled in).
* Raise an exception to show an error: the deck shows it as a message.
* Parameter types: `text`, `textarea`, `number`, `bool`, `select` (with `options=[...]` or `[[value, label], ...]`),
  `hotkey` (with a *Record* button), `file`, `folder`, `secret` (password field), `page` (choose a deck page).

### Live state

Attach a state function to show something on the tile. It's called about once a second (keep it fast):

```python
@say.state
def say_state(ctx, p):
    return State(label="Busy", color="#D32F2F", value="3 left", icon="sym:warning")   # None = no change
```

`label` replaces the tile label, `value` is shown under it in the middle of the deck, `color` replaces the
tile colour, `icon` the icon (`sym:...`, `txt:...`). Return `None` to show the tile as configured.

## Screens

A screen tile opens a full screen that your plugin draws and that gets every knob event. Pick one of six
templates and call it again whenever something changes (unchanged content isn't re-sent, so calling it every
second is fine):

```python
from knobdeck.plugin import screen, list_item, State

@screen("myplugin.rooms", "Rooms", group="My stuff", icon="sym:house")
class Rooms:
    def open(self, ctx, p, scr):                 # tile pressed
        scr.list([list_item("kitchen", "Küche", sub="21 °C", icon="sym:lightbulb", color="#FFB300"),
                  list_item("living", "Wohnzimmer", sub="off", icon="sym:lightbulb")], title="Licht")

    def event(self, ctx, p, scr, ev):            # ev.type: turn / press / select / hold / close
        if ev.type == "press":                   # ev.item = id of the selected list item
            scr.card(title=ev.item, text="switched on", icon="sym:check_circle", color="#43A047")

    def poll(self, ctx, p, scr):                 # optional: once a second while open
        ...

    def state(self, ctx, p):                     # optional: look of the tile, once a second
        return State(value="2 on")
```

| Template | Call | Knob |
|---|---|---|
| list | `scr.list(items, title="", sel=None, select_events=False)` – up to 32 `list_item(id, text, sub, icon, color, value, checked)` | turning scrolls on the deck; `press` with `ev.item` |
| card | `scr.card(title, text, icon=…, value="", color, progress=None, max=100, footer="")` – big icon *or* big value text, 2–3 lines of text, optional progress ring | `turn` (`ev.delta`), `press` |
| dial | `scr.dial(value, min, max, step, text, label, icon, color)` | the deck moves the value itself and sends `turn`; `press` |
| gauges | `scr.gauges([Ring(...), ...], title, center=None)` – up to 3 rings, clock in the middle unless `center` | `turn`, `press` |
| ring | `scr.ring(items, title, text)` – up to 8 `ring_item(id, name, icon, color, ring=…, badge="sym:mute", status=…)` around the edge (people, lights, devices) | turning selects (`select`), `press` with `ev.item` |
| canvas | `scr.canvas([label(...), arc(...), bar(...), image(...), rect(...), chart(...)])` – free layout, then `scr.patch({"id": …, …})` to change single elements | `turn`, `press` |

* Every template accepts `hold="send"`: a long press then arrives as `ev.type == "hold"` instead of closing the screen.
* `scr.close()` goes back to the tiles; `ev.type == "close"` tells you the user left (long press, other screen,
  unplugged).
* Canvas coordinates are offsets from the centre of the 240×240 round screen. `chart()` takes up to 64 points;
  `scr.patch({"id": "graph", "push": 42})` appends one. See `screens.network` in `plugins/screens.py`.
* Icons: `"sym:<name>"` (173 symbols – see the icon picker), `"txt:AB"`, or any picture: pass a file path, URL,
  bytes or PIL image as `icon=` and it's converted and uploaded automatically (or call `ctx.image(src, size)`
  yourself; up to 128 px).

### Screens without a tile

Anything can open a screen, e.g. an action or a background thread reacting to an event:

```python
def on_event(ev):
    if ev.type == "press":
        ctx.close("call")
ctx.show("call", "card", on_event=on_event, title="Incoming call", text="Anna", icon="sym:phone_off", color="#43A047")
```

`ctx.screen(id, on_event)` returns the handle without showing anything, so you can use `.list()`, `.canvas()` ...

## Popups, LED, sound, IR

```python
ctx.popup("New mail", "Jürgen: Grüße aus Köln", icon="sym:envelope", color="#E53935", ms=0,   # 0 = until pressed
          sound="notify", on_dismiss=lambda: ctx.led_off())
ctx.led("#FF0000", "pulse", period=1500, bright=40)          # solid / blink / pulse / rainbow / off, ms=… auto-off
ctx.beep("ok")                                               # click / ok / error / notify / alarm
ctx.beep(tones=[(880, 100), (0, 50), (1320, 150)], vol=50)   # (Hz, ms), 0 Hz = pause
raw = ctx.ir_learn(timeout=10)                               # point a remote at the deck -> [µs, µs, ...] or None
ctx.ir_send(raw)                                             # or a Pronto hex string "0000 006D ..."
```

`ctx.has("ir")`, `ctx.caps` and `ctx.firmware` tell you what the connected deck can do. On firmware 1.x popups
fall back to a toast, LED/beep do nothing and screens show a hint to update.

## Dials (simple)

The older, simpler tile types below still work; for anything new a screen is more flexible.

```python
from knobdeck.plugin import dial, DialState

@dial("myplugin.level", "Level", group="My stuff", icon="sym:settings")
class Level:
    value = 50
    def open(self, ctx, p):            # dial screen opened
        return DialState(self.value, min=0, max=100, step=5, text=f"{self.value}%")
    def turn(self, ctx, p, delta):     # delta = knob detents, negative = left (fast turns are merged)
        self.value = max(0, min(100, self.value + delta * 5))
        return self.open(ctx, p)
    def press(self, ctx, p):           # optional: knob pressed
        return self.open(ctx, p)
    def poll(self, ctx, p):            # optional: called twice a second, e.g. to follow outside changes
        return self.open(ctx, p)
```

## Stats

```python
from knobdeck.plugin import stats, Ring

@stats("myplugin.rings", "My rings", group="My stuff")
def rings(ctx, p):
    return [Ring("CPU", 42, "42%"), Ring("Fan", 1200, "1200rpm", max=3000, color="#FFB74D")]   # up to 3
```

## People (voice chat and similar)

```python
from knobdeck.plugin import people, People, Person, State

@people("myplugin.room", "My room", group="My stuff", icon="sym:call")
class Room:
    def get(self, ctx, p):             # called when the screen opens, every second, and after ctx.notify()
        return People(title="Room 1", items=[
            Person(id="a", name="Anna", avatar_url="https://.../anna.png", speaking=True),
            Person(id="b", name="Ben", icon="txt:BE", muted=True)])
    def press(self, ctx, p, item_id):  # optional: knob pressed on a person
        ...
    def state(self, ctx, p):           # optional: how the tile looks on the ring
        return State(label="2 here")
```

Call `ctx.notify()` from any thread when something changes (e.g. someone starts speaking): the deck is
updated within about 0.1 s. Avatars from `avatar_url` are downloaded, cropped round and stored on the deck by
the app. See `plugins/discord.py`.

## Plugin settings

Global settings (shown in the *Plugins* tab), read with `ctx.settings("<plugin id>")`. An optional
`status=fn(ctx) -> str` is shown in the card (refreshed every 2 s):

```python
from knobdeck.plugin import plugin_settings, Param
plugin_settings("myplugin", "My plugin", [Param("api_key", "API key", "secret", "")])
```

## The `ctx` object

| | |
|---|---|
| `ctx.toast(text, ms=1800)` | message on the deck |
| `ctx.goto(page_id)` | switch the deck to a page |
| `ctx.settings(plugin_id)` | plugin settings dict (with defaults) |
| `ctx.refresh()` | poll all tile states now |
| `ctx.notify()` | thread-safe "something changed": refreshes tile states and the people screen shortly |
| `ctx.show(id, tpl, on_event=None, **fields)` / `ctx.screen(id, on_event)` | screens without a tile |
| `ctx.close(id=None)` | close a screen |
| `ctx.popup(title, text, icon, color, ms, id, on_dismiss, sound)` / `ctx.popup_close()` | notification |
| `ctx.led(color, effect, period, bright, ms)` / `ctx.led_off()` | RGB LED |
| `ctx.beep(sound, freq, ms, tones, vol)` | speaker |
| `ctx.ir_send(code, khz=38, repeat=0)` / `ctx.ir_learn(timeout)` | infrared |
| `ctx.image(src, size=48, circle=False)` | upload a picture, returns `"img:<name>"` |
| `ctx.has(cap)`, `ctx.caps`, `ctx.firmware` | what the deck supports |
| `ctx.log` | a `logging.Logger` (goes to `knobdeck.log` in the settings folder) |

Text can use accents and symbols like € – … (firmware 2.x); for 1.x decks it's transliterated automatically.

All plugin functions run on one worker thread, one at a time, so you don't need locks; avoid blocking for
long (start a thread for slow work). COM is initialised on that thread (pycaw etc. work directly).
Helpers you can reuse (Windows and Linux): `knobdeck.winput.hotkey("ctrl+shift+m")`, `winput.press("playpause")`,
`winput.type_text("...")`, and in `knobdeck.osutil`: `open_target("firefox" / path / URL)`, `lock_screen()`,
`suspend()`, `monitor_off()`, `IS_WIN` / `IS_LINUX`, `wayland()`. COM (Windows audio) is only set up on Windows;
check `sys.platform` before using Windows-only libraries so your plugin still loads on Linux.
