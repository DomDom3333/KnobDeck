"""Actions that control the deck itself."""
from knobdeck.plugin import Param, action


@action("deck.goto", "Go to page", group="Deck", icon="sym:right",
        params=[Param("page", "Page", "page", "home")])
def goto(ctx, p):
    ctx.goto(p["page"])


@action("deck.toast", "Show message", group="Deck", icon="sym:bell",
        params=[Param("text", "Message", "text", "Hello!")])
def toast(ctx, p):
    ctx.toast(p["text"])


@action("deck.popup", "Show popup", group="Deck", icon="sym:bell",
        help="A notification over whatever the deck shows; press the knob to close it. Needs firmware 2.0.",
        params=[Param("title", "Title", "text", "Hello!"), Param("text", "Text", "text", ""),
                Param("icon", "Icon (sym:name)", "text", "sym:bell"),
                Param("sound", "Sound", "select", "notify",
                      options=[["", "None"], "notify", "ok", "error", "alarm", "click"]),
                Param("seconds", "Close after (s, 0 = when pressed)", "number", 5)])
def popup(ctx, p):
    ctx.popup(p["title"], p["text"], icon=p.get("icon") or "sym:bell", ms=int(float(p.get("seconds") or 0) * 1000),
              sound=p.get("sound") or None)


@action("deck.led", "Set LED", group="Deck", icon="sym:lightbulb",
        help="The RGB LED on the deck's board. Needs firmware 2.0.",
        params=[Param("color", "Colour (#RRGGBB)", "text", "#00FF00"),
                Param("effect", "Effect", "select", "solid", options=["solid", "blink", "pulse", "rainbow", "off"]),
                Param("bright", "Brightness %", "number", 30),
                Param("seconds", "Switch off after (s, 0 = stay on)", "number", 0)])
def led(ctx, p):
    ctx.led(p.get("color") or "#FFFFFF", p.get("effect") or "solid", bright=int(p.get("bright") or 30),
            ms=int(float(p.get("seconds") or 0) * 1000))


@action("deck.beep", "Play sound", group="Deck", icon="sym:audio",
        help="Plays a sound on the deck's speaker. Needs firmware 2.0.",
        params=[Param("sound", "Sound", "select", "notify", options=["notify", "ok", "error", "alarm", "click"]),
                Param("volume", "Volume %", "number", 60)])
def beep(ctx, p):
    ctx.beep(p.get("sound") or "notify", vol=int(p.get("volume") or 60))
