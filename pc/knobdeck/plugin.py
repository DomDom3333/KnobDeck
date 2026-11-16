"""Plugin API.

A plugin is a Python file that registers actions with decorators. Built-in plugins live in
knobdeck/plugins/, your own go into %APPDATA%\\KnobDeck\\plugins\\ and are loaded at start-up.

    from knobdeck.plugin import action, dial, stats, Param, State, DialState

    @action("hello.say", "Say hello", group="Examples",
            params=[Param("text", "Text", default="Hi!")])
    def say(ctx, p):
        ctx.toast(p["text"])                      # shows a message on the deck

    # optional live state for tiles using this action (polled about once a second)
    @say.state
    def say_state(ctx, p):
        return State(value="ok")

Kinds of entries:
  action  - runs when the tile is pressed
  screen  - (deck firmware 2.x) a full screen the plugin draws itself: list, card, canvas, ring, dial or
            gauges; gets every knob turn/press. See PLUGINS.md and plugins/demo_screens.py.
  dial    - full-screen dial on the deck; open/turn/press return a DialState
  stats   - feeds the stats screen; returns a list of up to 3 Ring()s
  people  - feeds the people screen (e.g. who is in a voice chat); get() returns People

Every plugin can also use ctx.popup(), ctx.led(), ctx.beep(), ctx.ir_send(), ctx.image() and ctx.show()
(screens without a tile) - see Context in app.py.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import logging
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("knobdeck.plugin")


@dataclasses.dataclass
class Param:
    key: str
    label: str
    type: str = "text"          # text | number | select | bool | hotkey | file | folder | secret | page | textarea
    default: Any = ""
    options: list | None = None  # for select: ["a", "b"] or [["value", "Label"], ...]
    placeholder: str = ""
    help: str = ""

    def to_json(self):
        return dataclasses.asdict(self)


@dataclasses.dataclass
class State:
    """Live look of a tile. Fields left as None are not changed; "" resets to the configured value."""
    label: str | None = None
    icon: str | None = None
    color: str | None = None
    value: str | None = None


@dataclasses.dataclass
class DialState:
    value: int = 0
    min: int = 0
    max: int = 100
    step: int = 2
    text: str = ""


@dataclasses.dataclass
class Ring:
    label: str
    value: float
    text: str = ""
    max: float = 100
    color: str | None = None


@dataclasses.dataclass
class Person:
    id: str
    name: str
    icon: str = ""               # "txt:AB" / "sym:..."; leave empty and set avatar_url for a picture
    avatar_url: str = ""         # downloaded, cropped round and sent to the deck by the app
    color: str = "#5865F2"
    speaking: bool = False
    muted: bool = False
    deaf: bool = False


@dataclasses.dataclass
class People:
    title: str = ""
    items: list = dataclasses.field(default_factory=list)    # list[Person], max 8 shown
    text: str = ""               # small line under the name (e.g. "Not in a voice channel" when empty)


@dataclasses.dataclass
class Event:
    """Knob input on a screen opened by a plugin.
    type:  "turn"    knob turned, `delta` = steps (+ clockwise)   - dial, card, canvas, gauges
           "press"   knob pressed (`item` = selected list/ring item, if any)
           "select"  selection moved on a list (only with select_events=True) or ring, `item` = new item
           "hold"    long press (only when the screen was shown with hold="send"; otherwise it closes the screen)
           "dismiss" a popup was closed with the knob
           "close"   the screen was left (long press, other screen, PC closed it, deck disconnected)"""
    type: str
    delta: int = 0
    item: str = ""


def list_item(id, text, sub="", icon="", color=None, value="", checked=False) -> dict:
    """An entry for screen.list(). icon: "sym:..", "txt:AB" or ctx.image(...)"""
    d = {"id": str(id), "text": text, "sub": sub, "icon": icon, "value": value, "checked": bool(checked)}
    if color:
        d["color"] = color
    return d


def ring_item(id, name, icon="", color="#5865F2", ring=None, badge="", badge_color=None, status="",
              status_color=None) -> dict:
    """An entry for screen.ring() (max 8): a circle with an icon/picture, optional coloured ring
    (e.g. "speaking") and a small badge symbol in the corner."""
    d = {"id": str(id), "name": name, "icon": icon, "color": color, "badge": badge, "status": status}
    for k, v in (("ring", ring), ("badge_color", badge_color), ("status_color", status_color)):
        if v:
            d[k] = v
    return d


# canvas elements: positions are relative to the screen centre (240x240 round display, x/y -120..120)
def label(id, text="", x=0, y=0, size=16, color="#FFFFFF", w=None, wrap=False, align="center") -> dict:
    d = {"id": id, "type": "label", "text": text, "x": x, "y": y, "size": size, "color": color, "align": align}
    if w:
        d.update(w=w, wrap=wrap)
    return d


def arc(id, value=0, x=0, y=0, w=200, width=10, min=0, max=100, start=0, end=360, rotation=270,
        color="#4FC3F7", bg="#262626") -> dict:
    return {"id": id, "type": "arc", "value": value, "x": x, "y": y, "w": w, "h": w, "width": width, "min": min,
            "max": max, "start": start, "end": end, "rotation": rotation, "color": color, "bg": bg}


def bar(id, value=0, x=0, y=0, w=140, h=10, min=0, max=100, color="#4FC3F7", bg="#262626") -> dict:
    return {"id": id, "type": "bar", "value": value, "x": x, "y": y, "w": w, "h": h, "min": min, "max": max,
            "color": color, "bg": bg}


def image(id, icon, x=0, y=0, size=28, color="#FFFFFF", scale=256) -> dict:
    """icon: "sym:..", "txt:.." (drawn with font `size`) or ctx.image(...) (scale 256 = 1:1)"""
    return {"id": id, "type": "img", "icon": icon, "x": x, "y": y, "size": size, "color": color, "scale": scale}


def rect(id, x=0, y=0, w=40, h=40, color="#333333", radius=0, border=0, border_color="#FFFFFF") -> dict:
    return {"id": id, "type": "rect", "x": x, "y": y, "w": w, "h": h, "color": color, "radius": radius,
            "border": border, "border_color": border_color}


def chart(id, points=(), x=0, y=0, w=160, h=70, min=0, max=100, color="#4FC3F7") -> dict:
    """Line chart, up to 64 points. Append live values with screen.patch({"id": .., "push": value})."""
    return {"id": id, "type": "chart", "points": list(points), "x": x, "y": y, "w": w, "h": h, "min": min,
            "max": max, "color": color}


class Entry:
    def __init__(self, kind: str, id: str, name: str, group: str, params, icon: str, fn, help: str = ""):
        self.kind = kind
        self.id = id
        self.name = name
        self.group = group
        self.params: list[Param] = list(params or [])
        self.icon = icon
        self.help = help
        self.fn = fn
        self.state_fn: Callable | None = None
        self.plugin = ""

    # decorator to attach a state provider:  @my_action.state
    def state(self, fn):
        self.state_fn = fn
        return fn

    def __call__(self, *a, **kw):
        return self.fn(*a, **kw)

    def defaults(self) -> dict:
        return {p.key: p.default for p in self.params}

    def to_json(self):
        return {
            "kind": self.kind, "id": self.id, "name": self.name, "group": self.group, "icon": self.icon,
            "help": self.help, "params": [p.to_json() for p in self.params], "has_state": self.state_fn is not None,
            "plugin": self.plugin,
        }


class Registry:
    def __init__(self):
        self.entries: dict[str, Entry] = {}
        self.settings: dict[str, dict] = {}    # plugin id -> {"name", "params": [Param]}
        self.errors: list[str] = []
        self._current_plugin = ""

    def add(self, e: Entry):
        e.plugin = self._current_plugin
        if e.id in self.entries:
            log.warning("action %s registered twice, keeping the last one", e.id)
        self.entries[e.id] = e
        return e

    def get(self, id: str) -> Entry | None:
        return self.entries.get(id)

    def catalog(self):
        return {
            "entries": [e.to_json() for e in sorted(self.entries.values(), key=lambda e: (e.group, e.name))],
            "settings": {k: {"name": v["name"], "help": v.get("help", ""), "params": [p.to_json() for p in v["params"]]}
                         for k, v in self.settings.items()},
            "errors": self.errors,
        }

    def load_file(self, path: Path, module_name: str):
        self._current_plugin = path.stem
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = mod
            spec.loader.exec_module(mod)
        except Exception:
            msg = f"plugin {path.name} failed to load:\n{traceback.format_exc(limit=3)}"
            log.error(msg)
            self.errors.append(msg)
        finally:
            self._current_plugin = ""

    def load_dir(self, folder: Path, prefix: str):
        if not folder.is_dir():
            return
        for f in sorted(folder.glob("*.py")):
            if not f.name.startswith("_"):
                self.load_file(f, f"{prefix}.{f.stem}")


REGISTRY = Registry()


def _register(kind, id, name, group, params, icon, help):
    def deco(fn):
        return REGISTRY.add(Entry(kind, id, name, group, params, icon, fn, help))
    return deco


def action(id: str, name: str, group: str = "Other", params=None, icon: str = "", help: str = ""):
    """fn(ctx, params) -> None"""
    return _register("action", id, name, group, params, icon, help)


def dial(id: str, name: str, group: str = "Other", params=None, icon: str = "", help: str = ""):
    """Decorate a class with open(ctx,p)->DialState, turn(ctx,p,delta)->DialState, press(ctx,p)->DialState,
    and optionally poll(ctx,p)->DialState (called twice a second while the dial is open)."""
    def deco(cls):
        e = REGISTRY.add(Entry("dial", id, name, group, params, icon, cls(), help))
        return e
    return deco


def screen(id: str, name: str, group: str = "Other", params=None, icon: str = "", help: str = ""):
    """Decorate a class with open(ctx, p, scr) - draw something with scr.list()/card()/canvas()/ring()/dial()/
    gauges() - and optionally event(ctx, p, scr, ev: Event), poll(ctx, p, scr) (once a second while open) and
    state(ctx, p) -> State for the tile. Needs deck firmware 2.x."""
    def deco(cls):
        return REGISTRY.add(Entry("screen", id, name, group, params, icon, cls(), help))
    return deco


def stats(id: str, name: str, group: str = "Stats", params=None, icon: str = "", help: str = ""):
    """fn(ctx, params) -> list[Ring] (max 3), called once a second while the stats screen is open."""
    return _register("stats", id, name, group, params, icon, help)


def people(id: str, name: str, group: str = "Other", params=None, icon: str = "", help: str = ""):
    """Decorate a class with get(ctx,p)->People, optionally press(ctx,p,item_id) (knob pressed on a person)
    and state(ctx,p)->State (look of the tile on the ring). Call ctx.notify() when something changed."""
    def deco(cls):
        return REGISTRY.add(Entry("people", id, name, group, params, icon, cls(), help))
    return deco


def plugin_settings(plugin_id: str, name: str, params: list[Param], status: Callable | None = None, help: str = ""):
    """Global settings for a plugin (shown in the settings window), read with ctx.settings(plugin_id).
    status(ctx) -> str is shown under the settings (e.g. "Connected as ...")."""
    REGISTRY.settings[plugin_id] = {"name": name, "params": params, "status": status, "help": help}
