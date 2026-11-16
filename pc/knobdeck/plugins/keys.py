"""Keyboard shortcuts and text."""
from knobdeck import winput
from knobdeck.plugin import Param, action


@action("keys.hotkey", "Send hotkey", group="Keyboard", icon="sym:keyboard",
        params=[Param("keys", "Keys", "hotkey", "ctrl+c", placeholder="ctrl+shift+m",
                      help="Combine with +. Several in a row: separate with commas, e.g. ctrl+c, alt+tab, ctrl+v. "
                           "Names: ctrl shift alt win (= Super/Meta on Linux) enter esc tab space f1-f24 up down left "
                           "right home end pageup pagedown delete printscreen playpause volumeup volumedown")])
def hotkey(ctx, p):
    winput.hotkey_sequence(p["keys"])


@action("keys.type", "Type text", group="Keyboard", icon="sym:edit",
        params=[Param("text", "Text", "textarea", "", placeholder="Text to type (any language)")])
def type_text(ctx, p):
    winput.type_text(p["text"])
