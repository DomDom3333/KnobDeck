"""Screens a plugin draws on the deck (firmware 2.x): list, card, canvas, ring, dial, gauges.

A Screen is a handle for one screen id. show() opens it on the deck (or updates it when it's already open);
knob input comes back as Event()s to the screen's handler.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from .plugin import Event, Ring

if TYPE_CHECKING:
    from .app import App

log = logging.getLogger("knobdeck.screens")

TEXT_KEYS = {"text", "title", "sub", "name", "status", "label", "footer", "value", "center"}
TEMPLATES = ("list", "card", "canvas", "ring", "dial", "gauges")


class Screen:
    def __init__(self, app: "App", id: str, on_event: Callable[[Event], None] | None = None):
        self.app = app
        self.id = str(id)[:23]
        self.on_event = on_event       # fn(Event); for tile screens the plugin's event() is used
        self.tpl: str | None = None
        self._last: dict | None = None

    # ------------------------------------------------------------ state
    @property
    def is_open(self) -> bool:
        """True while this screen is what the deck shows."""
        return self.app.active_screen is self

    # ------------------------------------------------------------ templates
    def list(self, items: list, title: str = "", sel: int | None = None, select_events: bool = False,
             hold: str | None = None):
        """A scrolling list (max 32 items, see plugin.list_item). Turning moves the selection on the deck
        itself; pressing sends Event("press", item=<id>)."""
        return self.show("list", title=title, items=list(items)[:32], sel=sel, select_events=select_events, hold=hold)

    def card(self, title: str = "", text: str = "", icon="", value: str = "", color: str | None = None,
             progress: float | None = None, max: float = 100, footer: str = "", hold: str | None = None,
             icon_scale: int | None = None):
        """Big icon (or big `value` text), title, up to 3 lines of text, optional progress ring on the rim."""
        return self.show("card", title=title, text=text, icon=icon, value=value, color=color,
                         progress=progress, max=max if progress is not None else None, footer=footer, hold=hold,
                         icon_scale=icon_scale)

    def dial(self, value: int, min: int = 0, max: int = 100, step: int = 1, text: str = "", label: str = "",
             icon="", color: str | None = None):
        """A value ring. The deck moves the value right away when turned (by `step`) and sends Event("turn")."""
        return self.show("dial", value=int(value), min=int(min), max=int(max), step=int(step), text=text,
                         label=label, icon=icon, color=color)

    def gauges(self, rings: list, title: str = "", center: str | None = None, hold: str | None = None):
        """Up to 3 concentric rings (plugin.Ring or dicts). Without `center` the clock is shown in the middle."""
        out = []
        for r in rings[:3]:
            if isinstance(r, Ring):
                r = {"label": r.label, "value": float(r.value), "max": float(r.max), "text": r.text,
                     **({"color": r.color} if r.color else {})}
            out.append(r)
        return self.show("gauges", rings=out, title=title, center=center, hold=hold)

    def ring(self, items: list, title: str = "", text: str = "", sel: int | None = None, hold: str | None = None):
        """Up to 8 circles around the edge (people, devices, lights ... see plugin.ring_item)."""
        return self.show("ring", items=list(items)[:8], title=title, text=text, sel=sel, hold=hold)

    def canvas(self, elements: list, bg: str = "#000000", hold: str | None = None):
        """Free layout from label/arc/bar/image/rect/chart elements (see plugin.py). Change elements later
        with patch() instead of re-sending everything."""
        self._last = None
        return self.show("canvas", elements=list(elements)[:32], bg=bg, hold=hold)

    def patch(self, *changes: dict):
        """Change canvas elements by id: patch({"id": "speed", "text": "12 MB/s"}, {"id": "chart", "push": 12}).
        A change with an unknown id and a "type" adds a new element."""
        if not self.is_open or self.tpl != "canvas":
            return False
        return self.app.device.send({"t": "patch", "id": self.id, "set": [self._prep(c) for c in changes]})

    def close(self):
        """Back to the tiles (only if this screen is showing)."""
        if self.is_open:
            self.app.device.send({"t": "close", "id": self.id})
            self.app.screen_left(self)

    # ------------------------------------------------------------ generic
    def show(self, tpl: str, **fields):
        if tpl not in TEMPLATES:
            raise ValueError(f"unknown screen template {tpl!r}")
        if not self.app.has_cap("screens"):
            self.app.ctx.toast("Needs deck firmware 2.0")
            return False
        msg = {"t": "show", "id": self.id, "tpl": tpl}
        msg.update(self._prep({k: v for k, v in fields.items() if v is not None}))
        if self.is_open and msg == self._last:
            return True                      # nothing changed (poll loops can call show() every second)
        self.tpl = tpl
        self._last = msg
        self.app.screen_shown(self)
        return self.app.device.send(msg)

    def _prep(self, obj, key: str = ""):
        """Deck-safe text, colours as #RRGGBB, images uploaded."""
        if isinstance(obj, dict):
            return {k: self._prep(v, k) for k, v in obj.items() if v is not None}
        if isinstance(obj, (list, tuple)):
            return [self._prep(v, key) for v in obj]
        if key in ("icon", "badge") and obj and not isinstance(obj, str):
            return self.app.image(obj)        # PIL image, path or bytes
        if key in TEXT_KEYS and isinstance(obj, str):
            return self.app.text(obj, 300)
        return obj

    def _event(self, ev: Event):
        if self.on_event:
            self.on_event(ev)


class TileScreen(Screen):
    """A screen opened from a "screen" tile: events go to the plugin's event(ctx, p, scr, ev)."""

    def __init__(self, app: "App", tile: dict):
        super().__init__(app, tile["id"])
        self.tile = tile

    def _event(self, ev: Event):
        e, params = self.app.entry_for(self.tile)
        if e and e.kind == "screen" and hasattr(e.fn, "event"):
            e.fn.event(self.app.ctx, params, self, ev)
