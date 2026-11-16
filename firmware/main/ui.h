// KnobDeck - LVGL user interface (round 240x240, rotary knob + push)
// Platform independent: hardware is reached only through ui_hal_t, so the same code runs in the
// desktop simulator (sim/) that renders screenshots.
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "lvgl.h"
#include "deck.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    void (*send)(const char *json_line);                  // send one JSON message to the PC (no newline)
    bool (*load_icon)(const char *name, lv_image_dsc_t *out); // load "img:<name>" icon into RAM
    void (*free_icon)(lv_image_dsc_t *dsc);
    void (*set_brightness)(int percent);
    bool (*pc_connected)(void);
    uint32_t (*now_ms)(void);
} ui_hal_t;

// All ui_* functions must be called with the LVGL lock held.
void ui_init(const ui_hal_t *hal, deck_t *deck);
void ui_deck_changed(void);                 // deck contents replaced (new layout)
void ui_icon_changed(const char *name);     // icon file (re)written
void ui_input(int diff, bool pressed);      // raw knob/button state, called from the indev read callback
void ui_on_message(const char *type, void *cjson_msg); // PC -> device UI messages (cJSON*)

// built-in symbol icons ("sym:<name>")
int ui_symbol_count(void);
const char *ui_symbol_at(int i, const char **glyph);

#ifdef __cplusplus
}
#endif
