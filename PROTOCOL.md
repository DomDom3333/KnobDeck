# KnobDeck USB protocol (version 2)

Transport: the ESP32-C3 USB Serial/JTAG port (VID `303A`, PID `1001`), baud rate irrelevant.
One JSON object per line (`\n`). Lines that don't start with `{` are ignored (ROM boot messages).
Max line length on the deck: 24 KB.

Text is UTF-8. Firmware 2.x shows Latin-1 + Latin Extended-A (äöüß éèê łść ...), common punctuation (– — “ ” … € ™ ←↑→↓)
and the icon glyphs; firmware 1.x shows ASCII only (the PC app transliterates for it). Text that's too long for its
place is cut with "...".

The PC must not toggle DTR/RTS when opening the port (it resets the chip).

The idea of version 2: the firmware has a fixed set of building blocks (screens, popup, LED, sound, IR) and
forwards every knob input to the PC, so new features are PC-side plugins and don't need new firmware.
Check `caps` in `hello` before using something.

## Deck -> PC

| Message | When |
|---|---|
| `{"t":"hello","fw":"2.1.0","board":"esp32-c3-lcdkit","v":"<layout version>","icons":["<hash>",...],"free":1234567,"proto":2,"caps":["screens","popup","led","beep","ir","ir_learn","utf8","click","files"],"tpl":["dial","gauges","ring","list","card","canvas"],"img_max":128,"syms":173,"heap":150000,"id":"58cf79a1b2c3","files":{"config":{"size":5321,"tag":"a1b2c3d4e5f6"}}}` | answer to `hello?`, and when a USB host appears. `proto`, `caps` ... are missing on 1.x |
| `{"t":"press","id":"<tile>","page":"<page>"}` | action tile pressed |
| `{"t":"long","id":"<tile>","page":"home"}` | knob held on the home page |
| `{"t":"page","id":"<page>"}` | another page is shown |
| `{"t":"screen","name":"screen","id":"<tile>"}` | a `screen` tile was pressed. The deck shows a "Loading..." card; the PC answers with `show` |
| `{"t":"screen","name":"deck","page":"<page>"}` | back on the tile ring (long press, `close`, PC disconnected) |
| `{"t":"input","id":"<screen>","ev":"turn","d":-3}` | knob turned on a dial / card / canvas / gauges screen (detents, signed) |
| `{"t":"input","id":"<screen>","ev":"press","item":"<item>"}` | knob pressed; `item` = selected list/ring item (if any) |
| `{"t":"input","id":"<screen>","ev":"select","item":"<item>"}` | selection moved on a ring, or a list shown with `"select_events":true` |
| `{"t":"input","id":"<screen>","ev":"hold","item":"<item>"}` | long press on a screen shown with `"hold":"send"` (otherwise a long press closes the screen) |
| `{"t":"input","id":"<popup>","ev":"dismiss"}` | a popup was closed with the knob |
| `{"t":"ir","raw":[9000,4500,560,560,...]}` / `{"t":"ir","timeout":true}` | result of `ir_learn` (µs, mark first) |
| `{"t":"fdata","n":"config","i":0,"ok":true,"size":5321,"last":true,"d":"<base64>"}` | answer to `fget` (one chunk of ≤2048 bytes; `"icon":"<name>"` instead of `n` for images); `ok:false` if missing |
| `{"t":"ack","what":"layout|icon|icon_gc|fput","n":"<name>","i":0,"ok":true}` | after the matching PC message |

Legacy (1.x tiles, still supported): `{"t":"screen","name":"dial|stats|people","id":"<tile>"}`, `{"t":"dial","id","d"}`,
`{"t":"dial_press","id"}`, `{"t":"people_press","id","item"}`.

## PC -> deck

| Message | Effect |
|---|---|
| `{"t":"hello?"}` | deck answers `hello` |
| `{"t":"ping"}` | keep-alive; the deck shows "PC offline" after 6 s without any message |
| `{"t":"layout","v":"<version>","pages":[...]}` | replace all pages, stored in flash (see below) |
| `{"t":"icon","n":"<name>","i":0,"last":false,"d":"<base64>"}` | image upload in chunks (<=1024 bytes raw each), wait for each `ack` |
| `{"t":"icon_gc","keep":["<name>",...]}` | delete all other images |
| `{"t":"fput","n":"config","i":0,"last":false,"d":"<base64>","tag":"<with the last chunk>"}` | store a file on the deck (2.1+), chunks of ≤1024 bytes, wait for each `ack`. Listed in `hello` with its tag |
| `{"t":"fget","n":"config","i":0}` / `{"t":"fget","icon":"<name>","i":0}` | read chunk `i` of a stored file / image (2.1+) |
| `{"t":"state","id":"<tile>","clear":true,"label":"Muted","icon":"sym:mute","color":"#B71C1C","value":"muted"}` | live look of a tile; `clear` resets to the layout first; `""` resets one field |
| `{"t":"show","id":"<screen>","tpl":"<template>",...}` | open a screen (or update it if it's already open). Any id works; tile screens use the tile id. See below |
| `{"t":"patch","id":"<screen>","set":[{"id":"<element>",...},...]}` | change canvas elements (only while that canvas is open) |
| `{"t":"close","id":"<screen>"}` | back to the tiles (only if that screen is open; without `id`: any) |
| `{"t":"popup","id":"mail","title":"New mail","text":"...","icon":"sym:envelope","color":"#E53935","ms":5000}` | notification over everything; `ms:0` = until pressed. Wakes the screen |
| `{"t":"popup_close"}` | hide the popup |
| `{"t":"led","color":"#FF0000","effect":"solid|blink|pulse|rainbow|off","period":1000,"bright":30,"ms":0}` | RGB LED; `ms>0` switches it off after that time |
| `{"t":"beep","sound":"click|ok|error|notify|alarm","vol":60}` | preset sound |
| `{"t":"beep","freq":880,"ms":100}` / `{"t":"beep","tones":[[880,100],[0,50],[1320,150]]}` | tone / melody (freq 0 = pause, max 32 tones, 5 s each) |
| `{"t":"ir","raw":[9000,4500,560,560,...],"khz":38,"repeat":0}` | send an IR code (µs, mark first, max 512 values) |
| `{"t":"ir_learn","ms":10000}` | listen for a remote; answers with `ir` |
| `{"t":"toast","text":"Saved","ms":1500}` | short message at the bottom of the screen |
| `{"t":"time","epoch":1763316000,"tz":60}` | wall clock (UTC seconds + offset in minutes) for the clock/screensaver |
| `{"t":"cfg","bright":80,"dim":8,"idle":120,"invert":false,"click":false,"ir_pin":4,"ir_rx_pin":4}` | brightness %, dimmed %, seconds until dimming (0 = never), knob direction, click sound on turning, IR GPIOs |
| `{"t":"wake"}` | undim the screen |
| `{"t":"goto","page":"<page>"}` | show a page |
| `{"t":"reboot"}` | restart the deck |

Legacy (1.x, still supported): `dial`, `stats` and `people` messages for the matching tile types.

## Settings on the deck (2.1+)

The PC app stores its full configuration as file `config` (`"KDC1"` + zlib-compressed JSON) with the
configuration's revision id as tag. `hello` lists it, so the app can tell on connect whether the deck or the PC has
the newer settings (see `pc/knobdeck/sync.py`). `id` (the chip's MAC address) tells decks apart.

## Screens (`show`)

Common fields: `id` (≤23 chars), `tpl`, `hold` (`"send"` = long press becomes an `input`/`hold` event instead of
closing the screen). Icons everywhere: `sym:<name>`, `txt:<letters>` or `img:<name>` (an uploaded image, up to 128×128).

| `tpl` | Fields | Knob |
|---|---|---|
| `list` | `title`, `items:[{"id","text","sub","icon","color","value","checked"}]` (max 32), `sel` (index), `select_events` | turn scrolls (on the deck), press → `press` + item |
| `card` | `icon` or `value` (big text), `title`, `text` (2–3 lines), `footer`, `color`, `progress`+`max` (ring on the rim), `icon_scale` (256 = 1:1) | `turn`, `press` |
| `dial` | `value`, `min`, `max`, `step`, `text`, `label`, `icon`, `color` | turn moves the value on the deck and sends `turn`; `press` |
| `gauges` | `rings:[{"label","value","max","text","color"}]` (max 3), `title`, `center` (no `center` = clock) | `turn`, `press` |
| `ring` | `title`, `text`, `items:[{"id","name","icon","color","ring","badge","badge_color","status","status_color"}]` (max 8), `sel` | turn selects (`select`), press → `press` + item |
| `canvas` | `bg`, `elements:[...]` (max 32, see below) | `turn`, `press` |

Canvas elements (`x`/`y` = offset of the element's centre from the screen centre, the display is 240×240 round):

| `type` | Fields |
|---|---|
| `label` | `text`, `size` (12/14/16/20/28/36/48), `color`, `w` (+ `wrap`), `align` |
| `arc` | `value`, `min`, `max`, `w` (diameter), `width`, `start`, `end`, `rotation` (degrees, 0 = 3 o'clock), `color`, `bg` |
| `bar` | `value`, `min`, `max`, `w`, `h`, `color`, `bg` |
| `img` | `icon`, `size` (for symbols/text), `scale` (images, 256 = 1:1), `color` |
| `rect` | `w`, `h`, `color`, `radius`, `border`, `border_color` |
| `chart` | line chart: `points:[...]` (max 64), `min`, `max`, `w`, `h`, `color`; in `patch`: `push` appends one value |

All elements: `id`, `x`, `y`, `hidden`, `opa` (0–100). `patch` changes only the fields given; an unknown `id` with a
`type` adds an element.

## Layout

```json
{"t":"layout","v":"a1b2c3","pages":[
  {"id":"home","title":"Main","tiles":[
    {"slot":0,"id":"play","type":"action","label":"Play/Pause","icon":"sym:play","color":"#1E88E5"},
    {"slot":1,"id":"timer","type":"screen","label":"Timer","icon":"sym:hourglass","color":"#00897B"},
    {"slot":2,"id":"vol","type":"dial","label":"Volume","icon":"sym:volume","color":"#43A047"},
    {"slot":5,"id":"apps","type":"folder","target":"apps","label":"Apps","icon":"sym:folder","color":"#8E24AA"}
  ]},
  {"id":"apps","title":"Apps","tiles":[{"slot":0,"id":"back","type":"back","label":"Back","icon":"sym:left"}]}
]}
```

* The first page is the home page. Max 16 pages, 8 slots each (0 = top, clockwise). Missing slots are empty.
* `type`: `action`, `screen`, `folder`, `dial`, `stats`, `people`, `back`.
* `icon`: `sym:<name>` (built-in symbols, see `SYMS` in `firmware/main/ui.c` – 56 in 1.x, 173 in 2.x),
  `txt:<up to 5 letters>`, or `img:<name>` for an uploaded image.
* Ids: up to 23 characters; labels up to 31 bytes; image names up to 16 (`[a-z0-9_-]`).

## Image format (KDI1)

`"KDI1"`, width (u16 LE), height (u16 LE), then width*height RGB565 pixels (u16 LE), then width*height alpha bytes.
Tile icons are 48×48 (6920 bytes). Firmware 2.x loads up to 128×128 (`img_max` in `hello`; 1.x: 64×64) as long as
enough RAM is free. Images are shown scaled to the size of the place they're used in.
