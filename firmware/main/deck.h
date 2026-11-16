// KnobDeck - deck model (pages of up to 8 tiles arranged in a ring)
// Platform independent: used by the firmware and by the desktop simulator.
#pragma once
#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define DECK_SLOTS      8
#define DECK_MAX_PAGES  16
#define DECK_ID_LEN     24
#define DECK_LABEL_LEN  32
#define DECK_ICON_LEN   24
#define DECK_VALUE_LEN  20
#define DECK_VER_LEN    20

typedef enum {
    TILE_EMPTY = 0,
    TILE_ACTION,    // pressed -> PC runs the configured action
    TILE_FOLDER,    // opens another page (handled on the device)
    TILE_DIAL,      // full-screen dial; knob turns are sent to the PC (e.g. volume)
    TILE_STATS,     // full-screen PC stats (rings + lines) pushed by the PC
    TILE_BACK,      // goes back one page
    TILE_PEOPLE,    // full-screen ring of people (e.g. Discord voice channel) pushed by the PC
    TILE_SCREEN,    // generic: the PC decides what to show (list, card, canvas, ...)
} tile_type_t;

typedef struct {
    tile_type_t type;
    char id[DECK_ID_LEN];
    char label[DECK_LABEL_LEN];
    char icon[DECK_ICON_LEN];      // "sym:play", "txt:OBS" or "img:<hash>"
    uint32_t color;                // 0xRRGGBB
    char target[DECK_ID_LEN];      // folder target page id
    // live state pushed by the PC (e.g. mic muted -> red)
    bool st_has_label, st_has_icon, st_has_color;
    char st_label[DECK_LABEL_LEN];
    char st_icon[DECK_ICON_LEN];
    char st_value[DECK_VALUE_LEN];
    uint32_t st_color;
} deck_tile_t;

typedef struct {
    char id[DECK_ID_LEN];
    char title[DECK_LABEL_LEN];
    deck_tile_t tiles[DECK_SLOTS];
} deck_page_t;

typedef struct {
    char version[DECK_VER_LEN];
    int page_count;
    deck_page_t pages[DECK_MAX_PAGES];
} deck_t;

// Parse a layout message/file: {"t":"layout","v":"..","pages":[{"id","title","tiles":[...]}]}
bool deck_parse(const char *json, deck_t *out);
// Built-in layout shown before the PC app has sent one.
const char *deck_default_json(void);

int deck_find_page(const deck_t *d, const char *id);
deck_tile_t *deck_find_tile(deck_t *d, const char *id, int *page_out, int *slot_out);
uint32_t deck_parse_color(const char *s, uint32_t fallback);

#ifdef __cplusplus
}
#endif
