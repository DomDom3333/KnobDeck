"""IR remote: the deck learns codes from any remote control and sends them (TV, amplifier, LED strips, AC...)."""
from knobdeck.plugin import Param, action


@action("ir.send", "Send IR code", group="IR remote", icon="sym:broadcast",
        help="Click 'Learn from remote', point your remote at the deck and press the button you want. "
             "Pronto hex codes (from IR code databases) can be pasted too. Needs firmware 2.0.",
        params=[Param("code", "IR code", "ircode", ""),
                Param("repeat", "Send again (times)", "number", 0,
                      help="Some devices want a code repeated, e.g. volume steps")])
def send(ctx, p):
    if not (p.get("code") or "").strip():
        raise ValueError("no IR code set - learn one in the settings")
    ctx.ir_send(p["code"], repeat=int(p.get("repeat") or 0))
