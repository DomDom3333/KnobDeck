// KnobDeck UI 2.0
// - the tile ring (pages of 8 tiles)
// - generic screens the PC can open at any time: dial, gauges, ring, list, card, canvas
// - popup notifications, toast, clock screensaver
// All screens forward knob input to the PC, so new features only need PC-side code.
#include "ui.h"
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "cJSON.h"

// LVGL >= 9.5 marks lv_obj_add/remove_flag as deprecated; they still work and keep 9.3+ support
#pragma GCC diagnostic ignored "-Wdeprecated-declarations"

// Montserrat with Latin-1 + Latin Extended-A (äöüß éèê łść ...) and ~180 icons; 48 px is digits/ASCII only
LV_FONT_DECLARE(kd_font_12)
LV_FONT_DECLARE(kd_font_14)
LV_FONT_DECLARE(kd_font_16)
LV_FONT_DECLARE(kd_font_20)
LV_FONT_DECLARE(kd_font_28)
LV_FONT_DECLARE(kd_font_36)
LV_FONT_DECLARE(kd_font_48)
#define F12 (&kd_font_12)
#define F14 (&kd_font_14)
#define F16 (&kd_font_16)
#define F20 (&kd_font_20)
#define F28 (&kd_font_28)
#define F36 (&kd_font_36)
#define F48 (&kd_font_48)

#define RING_R       86     // distance of the tile centres from the screen centre
#define SLOT_D       48     // tile diameter
#define SLOT_D_SEL   58     // selected tile diameter
#define LONG_MS      600    // long press
#define FLASH_MS     150    // press feedback
#define ICON_SCALE_SLOT   192   // 48 px icon -> 36 px in a tile (256 = 1:1)
#define ICON_SCALE_CENTER 320   // 48 px icon -> 60 px in the centre
#define RING_MAX     8
#define LIST_MAX     32
#define CANVAS_MAX   32
#define CHART_MAX    64
#define TPL_ICONS    8      // image cache for template screens
#define GREEN        0x3BA55C
#define RED          0xED4245

typedef enum { SCR_DECK, SCR_DIAL, SCR_GAUGES, SCR_RING, SCR_LIST, SCR_CARD, SCR_CANVAS, SCR_CLOCK } screen_t;

typedef struct {
    char name[DECK_ICON_LEN];
    lv_image_dsc_t dsc;
    bool loaded;
} icon_cache_t;

typedef struct {
    char id[DECK_ID_LEN];
    char name[DECK_LABEL_LEN];
    char icon[DECK_ICON_LEN];
    char badge[DECK_ICON_LEN];
    char status[DECK_LABEL_LEN];
    uint32_t color, ring_color, badge_color, status_color;
    bool has_ring;
} ring_item_t;

typedef struct {
    char id[DECK_ID_LEN];
    char text[48];
    char sub[40];
    char icon[DECK_ICON_LEN];
    char value[16];
    uint32_t color;
    bool checked;
} list_item_t;

typedef enum { EL_LABEL, EL_ARC, EL_BAR, EL_IMG, EL_RECT, EL_CHART } el_type_t;

typedef struct {
    char id[16];
    el_type_t type;
    lv_obj_t *obj, *lbl;           // lbl: symbol/text fallback of an image element
    lv_chart_series_t *ser;
    int cache;                     // index into tpl_icons, -1 = none
    bool is_img;                   // img element: picture shown (else the symbol/text label)
} canvas_el_t;

static struct {
    const ui_hal_t *hal;
    deck_t *deck;

    int page, depth, stack[8], sel[DECK_MAX_PAGES];
    char page_id[DECK_ID_LEN];
    screen_t screen, before_clock;

    // which screen context is open: events carry this id
    char ctx[DECK_ID_LEN];
    bool legacy;                   // opened from a dial/stats/people tile: use the 1.x event names
    bool hold_send;                // long press goes to the PC instead of closing the screen

    bool pressed, long_fired, swallow;
    uint32_t press_t0, last_input;

    // deck
    lv_obj_t *scr_deck, *ring_arc, *slots[DECK_SLOTS], *slot_img[DECK_SLOTS], *slot_lbl[DECK_SLOTS];
    lv_obj_t *c_title, *c_img, *c_sym, *c_name, *c_value;
    icon_cache_t icons[DECK_SLOTS];
    icon_cache_t extra;
    int32_t arc_angle;
    int flash_slot;
    uint32_t flash_until;

    // template screens share one image cache
    icon_cache_t tpl_icons[TPL_ICONS];

    // dial
    lv_obj_t *scr_dial, *d_arc, *d_img, *d_sym, *d_value, *d_name, *d_hint;
    int d_val, d_min, d_max, d_step;
    char d_text[DECK_VALUE_LEN], d_label[DECK_LABEL_LEN], d_icon[DECK_ICON_LEN];
    uint32_t d_color;

    // gauges
    lv_obj_t *scr_gauges, *g_arc[3], *g_center, *g_leg[3], *g_title;
    bool g_clock;

    // ring
    lv_obj_t *scr_ring, *r_arc, *r_slots[RING_MAX], *r_img[RING_MAX], *r_lbl[RING_MAX];
    lv_obj_t *r_badge[RING_MAX], *r_badge_lbl[RING_MAX], *r_title, *r_name, *r_status, *r_count;
    ring_item_t ring[RING_MAX];
    int r_n, r_sel;
    int32_t r_arc_angle;
    char r_title_txt[DECK_LABEL_LEN], r_text[DECK_LABEL_LEN];

    // list
    lv_obj_t *scr_list, *l_title, *l_pos, *l_rows[5], *l_row_icon[5], *l_row_img[5], *l_row_txt[5], *l_sub, *l_check;
    list_item_t list[LIST_MAX];
    int l_n, l_sel;
    bool l_select_events;

    // card
    lv_obj_t *scr_card, *k_arc, *k_img, *k_sym, *k_value, *k_title, *k_text, *k_footer;

    // canvas
    lv_obj_t *scr_canvas;
    canvas_el_t els[CANVAS_MAX];
    int el_n;

    // clock
    lv_obj_t *scr_clock, *t_time, *t_date;

    // toast + popup
    lv_obj_t *toast, *toast_lbl;
    uint32_t toast_until;
    lv_obj_t *popup, *pp_img, *pp_sym, *pp_title, *pp_text;
    icon_cache_t pp_icon;
    char pp_id[DECK_ID_LEN];
    uint32_t pp_until;
    bool pp_visible;

    bool connected;
    bool time_known;
    int64_t time_base_ms;
    int bright, dim, idle_s;
    bool dimmed, invert;
    uint32_t last_tick_s;
} ui;

/* ------------------------------------------------------------------ symbols */

static const struct { const char *n, *s; } SYMS[] = {
    {"play", LV_SYMBOL_PLAY}, {"pause", LV_SYMBOL_PAUSE}, {"stop", LV_SYMBOL_STOP},
    {"next", LV_SYMBOL_NEXT}, {"prev", LV_SYMBOL_PREV}, {"volume", LV_SYMBOL_VOLUME_MAX},
    {"volume_mid", LV_SYMBOL_VOLUME_MID}, {"mute", LV_SYMBOL_MUTE}, {"audio", LV_SYMBOL_AUDIO},
    {"video", LV_SYMBOL_VIDEO}, {"image", LV_SYMBOL_IMAGE}, {"home", LV_SYMBOL_HOME},
    {"settings", LV_SYMBOL_SETTINGS}, {"power", LV_SYMBOL_POWER}, {"refresh", LV_SYMBOL_REFRESH},
    {"wifi", LV_SYMBOL_WIFI}, {"bluetooth", LV_SYMBOL_BLUETOOTH}, {"bell", LV_SYMBOL_BELL},
    {"keyboard", LV_SYMBOL_KEYBOARD}, {"list", LV_SYMBOL_LIST}, {"ok", LV_SYMBOL_OK},
    {"close", LV_SYMBOL_CLOSE}, {"plus", LV_SYMBOL_PLUS}, {"minus", LV_SYMBOL_MINUS},
    {"edit", LV_SYMBOL_EDIT}, {"eye", LV_SYMBOL_EYE_OPEN}, {"eye_off", LV_SYMBOL_EYE_CLOSE},
    {"charge", LV_SYMBOL_CHARGE}, {"battery", LV_SYMBOL_BATTERY_FULL}, {"upload", LV_SYMBOL_UPLOAD},
    {"download", LV_SYMBOL_DOWNLOAD}, {"folder", LV_SYMBOL_DIRECTORY}, {"file", LV_SYMBOL_FILE},
    {"save", LV_SYMBOL_SAVE}, {"shuffle", LV_SYMBOL_SHUFFLE}, {"loop", LV_SYMBOL_LOOP},
    {"eject", LV_SYMBOL_EJECT}, {"gps", LV_SYMBOL_GPS}, {"warning", LV_SYMBOL_WARNING},
    {"trash", LV_SYMBOL_TRASH}, {"copy", LV_SYMBOL_COPY}, {"cut", LV_SYMBOL_CUT},
    {"paste", LV_SYMBOL_PASTE}, {"left", LV_SYMBOL_LEFT}, {"right", LV_SYMBOL_RIGHT},
    {"up", LV_SYMBOL_UP}, {"down", LV_SYMBOL_DOWN}, {"call", LV_SYMBOL_CALL},
    {"drive", LV_SYMBOL_DRIVE}, {"sd", LV_SYMBOL_SD_CARD}, {"usb", LV_SYMBOL_USB},
    {"envelope", LV_SYMBOL_ENVELOPE}, {"bars", LV_SYMBOL_BARS}, {"tint", LV_SYMBOL_TINT},
    {"backspace", LV_SYMBOL_BACKSPACE}, {"enter", LV_SYMBOL_NEW_LINE},
    // extra Font Awesome 5 icons (see fonts/)
    {"headphones", "\xEF\x80\xA5"},
    {"mic", "\xEF\x84\xB0"},
    {"mic_off", "\xEF\x84\xB1"},
    {"discord", "\xEF\x8E\x92"},
    {"twitch", "\xEF\x87\xA8"},
    {"spotify", "\xEF\x86\xBC"},
    {"youtube", "\xEF\x85\xA7"},
    {"steam", "\xEF\x86\xB6"},
    {"gamepad", "\xEF\x84\x9B"},
    {"desktop", "\xEF\x84\x88"},
    {"lightbulb", "\xEF\x83\xAB"},
    {"moon", "\xEF\x86\x86"},
    {"sun", "\xEF\x86\x85"},
    {"cloud", "\xEF\x83\x82"},
    {"globe", "\xEF\x82\xAC"},
    {"lock", "\xEF\x80\xA3"},
    {"unlock", "\xEF\x82\x9C"},
    {"user", "\xEF\x80\x87"},
    {"users", "\xEF\x83\x80"},
    {"comment", "\xEF\x81\xB5"},
    {"calendar", "\xEF\x84\xB3"},
    {"clock", "\xEF\x80\x97"},
    {"thermometer", "\xEF\x8B\x89"},
    {"fire", "\xEF\x81\xAD"},
    {"camera", "\xEF\x80\xB0"},
    {"film", "\xEF\x80\x88"},
    {"code", "\xEF\x84\xA1"},
    {"terminal", "\xEF\x84\xA0"},
    {"star", "\xEF\x80\x85"},
    {"heart", "\xEF\x80\x84"},
    {"flag", "\xEF\x80\xA4"},
    {"rocket", "\xEF\x84\xB5"},
    {"coffee", "\xEF\x83\xB4"},
    {"phone_off", "\xEF\x8F\x9D"},
    {"video_off", "\xEF\x93\xA2"},
    {"headset", "\xEF\x96\x90"},
    {"hashtag", "\xEF\x8A\x92"},
    {"plug", "\xEF\x87\xA6"},
    {"server", "\xEF\x88\xB3"},
    {"microchip", "\xEF\x8B\x9B"},
    {"memory", "\xEF\x94\xB8"},
    {"hdd", "\xEF\x82\xA0"},
    {"search", "\xEF\x80\x82"},
    {"link", "\xEF\x83\x81"},
    {"bookmark", "\xEF\x80\xAE"},
    {"tag", "\xEF\x80\xAB"},
    {"chart", "\xEF\x82\x80"},
    {"gauge", "\xEF\x8F\xBD"},
    {"stopwatch", "\xEF\x8B\xB2"},
    {"hourglass", "\xEF\x89\x94"},
    {"bell_off", "\xEF\x87\xB6"},
    {"cart", "\xEF\x81\xBA"},
    {"podcast", "\xEF\x8B\x8E"},
    {"broadcast", "\xEF\x94\x99"},
    {"record", "\xEF\x84\x91"},
    {"chrome", "\xEF\x89\xA8"},
    {"firefox", "\xEF\x89\xA9"},
    {"windows", "\xEF\x85\xBA"},
    {"github", "\xEF\x82\x9B"},
    {"slack", "\xEF\x86\x98"},
    {"telegram", "\xEF\x8B\x86"},
    {"whatsapp", "\xEF\x88\xB2"},
    {"twitter", "\xEF\x82\x99"},
    {"reddit", "\xEF\x86\xA1"},
    {"apple", "\xEF\x85\xB9"},
    {"linux", "\xEF\x85\xBC"},
    {"android", "\xEF\x85\xBB"},
    {"playstation", "\xEF\x8F\x9F"},
    {"xbox", "\xEF\x90\x92"},
    {"sliders", "\xEF\x87\x9E"},
    {"random", "\xEF\x81\xB4"},
    {"redo", "\xEF\x80\x9E"},
    {"undo", "\xEF\x83\xA2"},
    {"expand", "\xEF\x81\xA5"},
    {"compress", "\xEF\x81\xA6"},
    {"tv", "\xEF\x89\xAC"},
    {"mobile", "\xEF\x8F\x8D"},
    {"laptop", "\xEF\x84\x89"},
    {"print", "\xEF\x80\xAF"},
    {"briefcase", "\xEF\x82\xB1"},
    {"gift", "\xEF\x81\xAB"},
    {"trophy", "\xEF\x82\x91"},
    {"paint", "\xEF\x87\xBC"},
    {"magic", "\xEF\x83\x90"},
    {"robot", "\xEF\x95\x84"},
    {"ghost", "\xEF\x9B\xA2"},
    {"skull", "\xEF\x95\x8C"},
    {"dice", "\xEF\x94\xA2"},
    {"crosshairs", "\xEF\x81\x9B"},
    {"bed", "\xEF\x88\xB6"},
    {"car", "\xEF\x86\xB9"},
    {"house", "\xEF\x80\x95"},
    {"snowflake", "\xEF\x8B\x9C"},
    {"drop", "\xEF\x81\x83"},
    {"wind", "\xEF\x9C\xAE"},
    {"umbrella", "\xEF\x83\xA9"},
    {"info", "\xEF\x84\xA9"},
    {"question", "\xEF\x84\xA8"},
    {"exclamation", "\xEF\x84\xAA"},
    {"check_circle", "\xEF\x81\x98"},
    {"x_circle", "\xEF\x81\x97"},
    {"play_circle", "\xEF\x85\x84"},
    {"pause_circle", "\xEF\x8A\x8B"},
    {"stop_circle", "\xEF\x8A\x8D"},
    {"arrows", "\xEF\x82\xB2"},
    {"sync", "\xEF\x80\xA1"},
    {"history", "\xEF\x87\x9A"},
    {"send", "\xEF\x87\x98"},
    {"inbox", "\xEF\x80\x9C"},
    {"fingerprint", "\xEF\x95\xB7"},
    {"key", "\xEF\x82\x84"},
    {"shield", "\xEF\x8F\xAD"},
    {"bug", "\xEF\x86\x88"},
    {"database", "\xEF\x87\x80"},
    {"network", "\xEF\x9B\xBF"},
    {"signal", "\xEF\x80\x92"},
    {"satellite", "\xEF\x9E\xBF"},
};

int ui_symbol_count(void) { return (int)(sizeof(SYMS) / sizeof(SYMS[0])); }

const char *ui_symbol_at(int i, const char **glyph)
{
    if (glyph) *glyph = SYMS[i].s;
    return SYMS[i].n;
}

static const char *sym_lookup(const char *name)
{
    for (size_t i = 0; i < sizeof(SYMS) / sizeof(SYMS[0]); i++) {
        if (!strcmp(SYMS[i].n, name)) return SYMS[i].s;
    }
    return NULL;
}

/* ------------------------------------------------------------------ helpers */

static uint32_t now(void) { return ui.hal->now_ms(); }
static lv_color_t hex(uint32_t c) { return lv_color_hex(c); }

static const lv_font_t *font_for(int size)
{
    if (size <= 12) return F12;
    if (size <= 14) return F14;
    if (size <= 17) return F16;
    if (size <= 24) return F20;
    if (size <= 32) return F28;
    if (size <= 42) return F36;
    return F48;
}

static void copy_txt(char *dst, size_t n, const char *src)
{
    size_t len = src ? strlen(src) : 0;
    if (len >= n) {
        len = n - 1;
        while (len > 0 && ((unsigned char)src[len] & 0xC0) == 0x80) len--;   // don't cut a UTF-8 character
    }
    memmove(dst, src ? src : "", len);
    dst[len] = 0;
}

static lv_obj_t *plain(lv_obj_t *parent)
{
    lv_obj_t *o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_remove_flag(o, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
    return o;
}

static lv_obj_t *mk_screen(void)
{
    lv_obj_t *s = lv_obj_create(NULL);
    lv_obj_remove_style_all(s);
    lv_obj_set_style_bg_color(s, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(s, LV_OPA_COVER, 0);
    lv_obj_remove_flag(s, LV_OBJ_FLAG_SCROLLABLE);
    return s;
}

static lv_obj_t *mk_label(lv_obj_t *parent, const lv_font_t *font, uint32_t color, int x, int y)
{
    lv_obj_t *l = lv_label_create(parent);
    lv_obj_set_style_text_font(l, font, 0);
    lv_obj_set_style_text_color(l, hex(color), 0);
    lv_obj_set_style_text_align(l, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_text(l, "");
    lv_obj_align(l, LV_ALIGN_CENTER, x, y);
    return l;
}

static lv_obj_t *mk_line_label(lv_obj_t *parent, const lv_font_t *font, uint32_t color, int x, int y, int w)
{
    lv_obj_t *l = mk_label(parent, font, color, x, y);
    lv_label_set_long_mode(l, LV_LABEL_LONG_DOT);
    lv_obj_set_size(l, w, lv_font_get_line_height(font));   // one line, "..." when too long
    lv_obj_align(l, LV_ALIGN_CENTER, x, y);
    return l;
}

static lv_obj_t *mk_arc(lv_obj_t *parent, int size, int width)
{
    lv_obj_t *a = lv_arc_create(parent);
    lv_obj_set_size(a, size, size);
    lv_obj_center(a);
    lv_obj_remove_flag(a, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_style_arc_width(a, width, LV_PART_MAIN);
    lv_obj_set_style_arc_width(a, width, LV_PART_INDICATOR);
    lv_obj_set_style_arc_rounded(a, true, LV_PART_INDICATOR);
    lv_obj_set_style_arc_color(a, hex(0x262626), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(a, LV_OPA_TRANSP, LV_PART_KNOB);
    lv_obj_set_style_pad_all(a, 0, LV_PART_KNOB);
    lv_obj_set_style_pad_all(a, 0, LV_PART_MAIN);
    return a;
}

static void send_fmt(const char *fmt, ...)
{
    char buf[200];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (ui.hal->send) ui.hal->send(buf);
}

// generic screen events: {"t":"input","id":<ctx>,"ev":<ev>[,"d":n][,"item":<id>]}
static void send_input(const char *ev, int d, const char *item)
{
    char extra[64] = "";
    if (d) snprintf(extra, sizeof(extra), ",\"d\":%d", d);
    if (item) snprintf(extra + strlen(extra), sizeof(extra) - strlen(extra), ",\"item\":\"%s\"", item);
    send_fmt("{\"t\":\"input\",\"id\":\"%s\",\"ev\":\"%s\"%s}", ui.ctx, ev, extra);
}

static void toast(const char *text, uint32_t ms)
{
    lv_label_set_text(ui.toast_lbl, text);
    lv_obj_remove_flag(ui.toast, LV_OBJ_FLAG_HIDDEN);
    ui.toast_until = now() + ms;
}

static deck_page_t *cur_page(void) { return &ui.deck->pages[ui.page]; }
static int slot_angle(int slot) { return 270 + slot * 45; }   // lv_arc degrees: 0 = 3 o'clock, clockwise

static const char *jstr(const cJSON *m, const char *k)
{
    return cJSON_GetStringValue(cJSON_GetObjectItemCaseSensitive(m, k));
}

static bool jnum(const cJSON *m, const char *k, double *out)
{
    const cJSON *i = cJSON_GetObjectItemCaseSensitive(m, k);
    if (!cJSON_IsNumber(i)) return false;
    *out = i->valuedouble;
    return true;
}

static int jint(const cJSON *m, const char *k, int def)
{
    double v;
    return jnum(m, k, &v) ? (int)v : def;
}

static bool jbool(const cJSON *m, const char *k) { return cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(m, k)); }

static uint32_t jcolor(const cJSON *m, const char *k, uint32_t def) { return deck_parse_color(jstr(m, k), def); }

static bool has(const cJSON *m, const char *k) { return cJSON_GetObjectItemCaseSensitive(m, k) != NULL; }

/* ------------------------------------------------------------------ icons */

static void icon_free(icon_cache_t *c)
{
    if (c->loaded && ui.hal->free_icon) ui.hal->free_icon(&c->dsc);
    memset(c, 0, sizeof(*c));
}

static const lv_image_dsc_t *icon_get(icon_cache_t *c, const char *name)
{
    if (c->loaded && !strcmp(c->name, name)) return &c->dsc;
    icon_free(c);
    if (!ui.hal->load_icon || !ui.hal->load_icon(name, &c->dsc)) return NULL;
    copy_txt(c->name, sizeof(c->name), name);
    c->loaded = true;
    return &c->dsc;
}

// Show an icon spec ("sym:x", "txt:AB", "img:name") on an (image, label) pair.
// `scale` is for 48 px icons; bigger images are scaled to the same displayed size.
static void show_icon(lv_obj_t *img, lv_obj_t *lbl, const char *icon, const char *label, icon_cache_t *cache,
                      const lv_font_t *sym_font, const lv_font_t *txt_font, uint32_t scale)
{
    lv_obj_add_flag(img, LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(lbl, LV_OBJ_FLAG_HIDDEN);
    icon = icon ? icon : "";
    if (!strncmp(icon, "img:", 4)) {
        const lv_image_dsc_t *d = cache ? icon_get(cache, icon + 4) : NULL;
        if (d) {
            lv_image_set_src(img, d);
            lv_image_set_scale(img, d->header.w > 48 ? scale * 48 / d->header.w : scale);
            lv_obj_remove_flag(img, LV_OBJ_FLAG_HIDDEN);
            lv_obj_add_flag(lbl, LV_OBJ_FLAG_HIDDEN);
            return;
        }
        lv_obj_set_style_text_font(lbl, sym_font, 0);
        lv_label_set_text(lbl, LV_SYMBOL_IMAGE);       // icon not on the device (yet)
        return;
    }
    if (!strncmp(icon, "sym:", 4)) {
        const char *s = sym_lookup(icon + 4);
        if (s) {
            lv_obj_set_style_text_font(lbl, sym_font, 0);
            lv_label_set_text(lbl, s);
            return;
        }
    }
    char txt[12] = {0};
    const char *src = !strncmp(icon, "txt:", 4) ? icon + 4 : (label ? label : "");
    copy_txt(txt, !strncmp(icon, "txt:", 4) ? sizeof(txt) : 3, src);
    lv_obj_set_style_text_font(lbl, txt_font, 0);
    lv_label_set_text(lbl, txt);
}

static void free_tpl_icons(void)
{
    for (int i = 0; i < TPL_ICONS; i++) icon_free(&ui.tpl_icons[i]);
}

static void free_deck_icons(void)
{
    for (int i = 0; i < DECK_SLOTS; i++) icon_free(&ui.icons[i]);
    icon_free(&ui.extra);
}

static const char *tile_icon(const deck_tile_t *t) { return t->st_has_icon ? t->st_icon : t->icon; }
static const char *tile_label(const deck_tile_t *t) { return t->st_has_label ? t->st_label : t->label; }
static uint32_t tile_color(const deck_tile_t *t) { return t->st_has_color ? t->st_color : t->color; }

/* ------------------------------------------------------------------ selection arcs (deck + ring) */

static void arc_cb(void *var, int32_t v)
{
    lv_arc_set_angles((lv_obj_t *)var, (v - 20 + 720) % 360, (v + 20 + 720) % 360);
}

static void deck_arc_cb(void *var, int32_t v) { ui.arc_angle = v; arc_cb(var, v); }
static void ring_arc_cb(void *var, int32_t v) { ui.r_arc_angle = v; arc_cb(var, v); }

static void move_arc(lv_obj_t *arc, int32_t *store, lv_anim_exec_xcb_t cb, int pos, bool animate)
{
    int32_t target = slot_angle(pos), from = *store;
    int32_t delta = ((target - from) % 360 + 540) % 360 - 180;    // shortest way round
    if (!animate) {
        cb(arc, target);
        return;
    }
    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, arc);
    lv_anim_set_exec_cb(&a, cb);
    lv_anim_set_values(&a, from, from + delta);
    lv_anim_set_duration(&a, 160);
    lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
    lv_anim_start(&a);
}

/* ------------------------------------------------------------------ deck screen */

static void build_deck(void)
{
    lv_obj_t *s = ui.scr_deck = mk_screen();
    ui.ring_arc = mk_arc(s, 236, 4);
    lv_obj_set_style_arc_opa(ui.ring_arc, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_arc_set_bg_angles(ui.ring_arc, 0, 360);
    for (int i = 0; i < DECK_SLOTS; i++) {
        double a = (-90.0 + i * 45.0) * M_PI / 180.0;
        lv_obj_t *o = ui.slots[i] = plain(s);
        lv_obj_set_style_radius(o, LV_RADIUS_CIRCLE, 0);
        lv_obj_set_style_bg_opa(o, LV_OPA_COVER, 0);
        lv_obj_set_style_border_color(o, lv_color_white(), 0);
        lv_obj_set_size(o, SLOT_D, SLOT_D);
        lv_obj_align(o, LV_ALIGN_CENTER, (int)lround(RING_R * cos(a)), (int)lround(RING_R * sin(a)));
        ui.slot_img[i] = lv_image_create(o);
        lv_obj_center(ui.slot_img[i]);
        ui.slot_lbl[i] = mk_label(o, F20, 0xFFFFFF, 0, 0);
    }
    ui.c_title = mk_line_label(s, F12, 0x9E9E9E, 0, -46, 110);
    ui.c_img = lv_image_create(s);
    lv_obj_align(ui.c_img, LV_ALIGN_CENTER, 0, -14);
    ui.c_sym = mk_label(s, F36, 0xFFFFFF, 0, -14);
    ui.c_name = mk_line_label(s, F16, 0xFFFFFF, 0, 24, 110);
    ui.c_value = mk_line_label(s, F14, 0x80CBC4, 0, 43, 110);
}

static int first_tile(const deck_page_t *p)
{
    for (int i = 0; i < DECK_SLOTS; i++) {
        if (p->tiles[i].type != TILE_EMPTY) return i;
    }
    return -1;
}

static void render_deck(void)
{
    deck_page_t *p = cur_page();
    int sel = ui.sel[ui.page];
    if (sel < 0 || sel >= DECK_SLOTS || p->tiles[sel].type == TILE_EMPTY) sel = ui.sel[ui.page] = first_tile(p);

    for (int i = 0; i < DECK_SLOTS; i++) {
        deck_tile_t *t = &p->tiles[i];
        lv_obj_t *o = ui.slots[i];
        if (t->type == TILE_EMPTY) {
            lv_obj_set_size(o, 10, 10);
            lv_obj_set_style_bg_color(o, hex(0x303030), 0);
            lv_obj_set_style_border_width(o, 0, 0);
            lv_obj_add_flag(ui.slot_img[i], LV_OBJ_FLAG_HIDDEN);
            lv_obj_add_flag(ui.slot_lbl[i], LV_OBJ_FLAG_HIDDEN);
            icon_free(&ui.icons[i]);
            continue;
        }
        bool is_sel = (i == sel);
        bool flashing = (i == ui.flash_slot && now() < ui.flash_until);
        lv_color_t c = hex(tile_color(t));
        lv_obj_set_size(o, is_sel ? SLOT_D_SEL : SLOT_D, is_sel ? SLOT_D_SEL : SLOT_D);
        lv_obj_set_style_bg_color(o, flashing ? lv_color_white() : (is_sel ? c : lv_color_mix(c, lv_color_black(), 150)), 0);
        lv_obj_set_style_border_width(o, is_sel ? 3 : 0, 0);
        lv_obj_set_style_text_opa(ui.slot_lbl[i], is_sel ? LV_OPA_COVER : LV_OPA_70, 0);
        lv_obj_set_style_image_opa(ui.slot_img[i], is_sel ? LV_OPA_COVER : LV_OPA_70, 0);
        show_icon(ui.slot_img[i], ui.slot_lbl[i], tile_icon(t), tile_label(t), &ui.icons[i], F20, F14,
                  is_sel ? ICON_SCALE_SLOT + 32 : ICON_SCALE_SLOT);
    }

    if (!ui.connected) {
        lv_label_set_text(ui.c_title, LV_SYMBOL_USB " PC offline");
        lv_obj_set_style_text_color(ui.c_title, hex(0xEF5350), 0);
    } else {
        lv_label_set_text(ui.c_title, p->title);
        lv_obj_set_style_text_color(ui.c_title, hex(0x9E9E9E), 0);
    }
    if (sel < 0) {
        lv_obj_add_flag(ui.c_img, LV_OBJ_FLAG_HIDDEN);
        lv_label_set_text(ui.c_sym, "");
        lv_label_set_text(ui.c_name, "Empty page");
        lv_label_set_text(ui.c_value, "");
        lv_obj_set_style_arc_opa(ui.ring_arc, LV_OPA_TRANSP, LV_PART_INDICATOR);
        return;
    }
    deck_tile_t *t = &p->tiles[sel];
    lv_obj_set_style_arc_opa(ui.ring_arc, LV_OPA_COVER, LV_PART_INDICATOR);
    lv_obj_set_style_arc_color(ui.ring_arc, hex(tile_color(t)), LV_PART_INDICATOR);
    icon_cache_t *cache = !strcmp(tile_icon(t), t->icon) ? &ui.icons[sel] : &ui.extra;
    show_icon(ui.c_img, ui.c_sym, tile_icon(t), tile_label(t), cache, F36, F28, ICON_SCALE_CENTER);
    lv_obj_set_style_text_color(ui.c_sym, lv_color_mix(hex(tile_color(t)), lv_color_white(), 90), 0);
    lv_label_set_text(ui.c_name, tile_label(t));
    lv_label_set_text(ui.c_value, t->st_value);
}

static void open_page(int idx, bool push)
{
    if (idx < 0 || idx >= ui.deck->page_count) {
        toast("Page not found", 1500);
        return;
    }
    if (push && idx != ui.page) {
        if (ui.depth == 8) {
            memmove(ui.stack, ui.stack + 1, sizeof(ui.stack) - sizeof(ui.stack[0]));
            ui.depth--;
        }
        ui.stack[ui.depth++] = ui.page;
    }
    ui.page = idx;
    copy_txt(ui.page_id, sizeof(ui.page_id), cur_page()->id);
    if (ui.sel[idx] < 0) ui.sel[idx] = first_tile(cur_page());
    free_deck_icons();
    render_deck();
    if (ui.sel[idx] >= 0) move_arc(ui.ring_arc, &ui.arc_angle, deck_arc_cb, ui.sel[idx], false);
    lv_obj_fade_in(ui.scr_deck, 180, 0);
    send_fmt("{\"t\":\"page\",\"id\":\"%s\"}", cur_page()->id);
}

static int parent_page(int idx)
{
    for (int p = 0; p < ui.deck->page_count; p++) {
        if (p == idx) continue;
        for (int s = 0; s < DECK_SLOTS; s++) {
            const deck_tile_t *t = &ui.deck->pages[p].tiles[s];
            if (t->type == TILE_FOLDER && !strcmp(t->target, ui.deck->pages[idx].id)) return p;
        }
    }
    return 0;
}

// Back always leads somewhere: the page we came from, else the folder's parent page, else home.
static void go_back(void)
{
    while (ui.depth > 0) {
        int prev = ui.stack[--ui.depth];
        if (prev != ui.page && prev < ui.deck->page_count) {
            open_page(prev, false);
            return;
        }
    }
    if (ui.page != 0) open_page(parent_page(ui.page), false);
}

/* ------------------------------------------------------------------ screen switching */

static lv_obj_t *screen_obj(screen_t s)
{
    switch (s) {
    case SCR_DIAL:   return ui.scr_dial;
    case SCR_GAUGES: return ui.scr_gauges;
    case SCR_RING:   return ui.scr_ring;
    case SCR_LIST:   return ui.scr_list;
    case SCR_CARD:   return ui.scr_card;
    case SCR_CANVAS: return ui.scr_canvas;
    case SCR_CLOCK:  return ui.scr_clock;
    default:         return ui.scr_deck;
    }
}

static void canvas_clear(void);

// Open a template screen for context `id` (tile id or any id chosen by the PC)
static void open_screen(screen_t s, const char *id, bool legacy)
{
    free_deck_icons();          // RAM for the screen's images
    free_tpl_icons();
    if (ui.screen == SCR_CANVAS && s != SCR_CANVAS) canvas_clear();
    copy_txt(ui.ctx, sizeof(ui.ctx), id);
    ui.legacy = legacy;
    ui.hold_send = false;
    ui.screen = s;
    lv_screen_load_anim(screen_obj(s), LV_SCR_LOAD_ANIM_FADE_IN, 150, 0, false);
}

static void show_deck(void)
{
    free_tpl_icons();
    if (ui.screen == SCR_CANVAS) canvas_clear();
    ui.screen = SCR_DECK;
    ui.ctx[0] = 0;
    render_deck();
    lv_screen_load_anim(ui.scr_deck, LV_SCR_LOAD_ANIM_FADE_IN, 150, 0, false);
    send_fmt("{\"t\":\"screen\",\"name\":\"deck\",\"page\":\"%s\"}", cur_page()->id);
}

/* ------------------------------------------------------------------ dial */

static void build_dial(void)
{
    lv_obj_t *s = ui.scr_dial = mk_screen();
    ui.d_arc = mk_arc(s, 224, 14);
    lv_arc_set_rotation(ui.d_arc, 135);
    lv_arc_set_bg_angles(ui.d_arc, 0, 270);
    ui.d_img = lv_image_create(s);
    lv_obj_align(ui.d_img, LV_ALIGN_CENTER, 0, -50);
    ui.d_sym = mk_label(s, F28, 0xFFFFFF, 0, -50);
    ui.d_value = mk_line_label(s, F48, 0xFFFFFF, 0, 2, 170);
    ui.d_name = mk_line_label(s, F16, 0xBDBDBD, 0, 44, 150);
    ui.d_hint = mk_label(s, F12, 0x616161, 0, 96);
    lv_label_set_text(ui.d_hint, "hold to exit");
}

static void render_dial(void)
{
    lv_obj_set_style_arc_color(ui.d_arc, hex(ui.d_color), LV_PART_INDICATOR);
    lv_arc_set_range(ui.d_arc, ui.d_min, ui.d_max);
    lv_arc_set_value(ui.d_arc, ui.d_val);
    show_icon(ui.d_img, ui.d_sym, ui.d_icon, ui.d_label, &ui.tpl_icons[0], F28, F20, 256);
    lv_obj_set_style_text_color(ui.d_sym, hex(ui.d_color), 0);
    lv_obj_set_style_text_font(ui.d_value, strlen(ui.d_text) > 6 ? F28 : F48, 0);
    if (ui.d_text[0]) {
        lv_label_set_text(ui.d_value, ui.d_text);
    } else {
        lv_label_set_text_fmt(ui.d_value, "%d", ui.d_val);
    }
    lv_label_set_text(ui.d_name, ui.d_label);
}

static void apply_dial(const cJSON *m)
{
    const char *s;
    ui.d_min = jint(m, "min", ui.d_min);
    ui.d_max = jint(m, "max", ui.d_max);
    ui.d_step = jint(m, "step", ui.d_step);
    if (!ui.d_step) ui.d_step = 1;
    ui.d_val = jint(m, "value", ui.d_val);
    copy_txt(ui.d_text, sizeof(ui.d_text), jstr(m, "text"));
    if ((s = jstr(m, "label"))) copy_txt(ui.d_label, sizeof(ui.d_label), s);
    if ((s = jstr(m, "icon"))) copy_txt(ui.d_icon, sizeof(ui.d_icon), s);
    ui.d_color = jcolor(m, "color", ui.d_color);
    render_dial();
}

static void dial_defaults(const char *label, const char *icon, uint32_t color)
{
    ui.d_min = 0;
    ui.d_max = 100;
    ui.d_step = 2;
    ui.d_val = 0;
    copy_txt(ui.d_text, sizeof(ui.d_text), "...");
    copy_txt(ui.d_label, sizeof(ui.d_label), label);
    copy_txt(ui.d_icon, sizeof(ui.d_icon), icon);
    ui.d_color = color;
}

/* ------------------------------------------------------------------ gauges (up to 3 rings) */

static const uint32_t RING_COLORS[3] = {0x4FC3F7, 0xAED581, 0xFFB74D};

static void build_gauges(void)
{
    lv_obj_t *s = ui.scr_gauges = mk_screen();
    for (int i = 0; i < 3; i++) {
        ui.g_arc[i] = mk_arc(s, 234 - i * 30, 12);
        lv_arc_set_rotation(ui.g_arc[i], 270);
        lv_arc_set_bg_angles(ui.g_arc[i], 0, 360);
        lv_arc_set_range(ui.g_arc[i], 0, 100);
        lv_arc_set_value(ui.g_arc[i], 0);
        lv_obj_set_style_arc_color(ui.g_arc[i], hex(RING_COLORS[i]), LV_PART_INDICATOR);
        lv_obj_set_style_arc_color(ui.g_arc[i], lv_color_mix(hex(RING_COLORS[i]), lv_color_black(), 40), LV_PART_MAIN);
        ui.g_leg[i] = mk_line_label(s, F14, RING_COLORS[i], 0, 14 + i * 18, 140);
    }
    ui.g_center = mk_line_label(s, F28, 0xFFFFFF, 0, -20, 130);
    ui.g_title = mk_line_label(s, F12, 0x9E9E9E, 0, -46, 110);
}

static void apply_gauges(const cJSON *m)
{
    const cJSON *rings = cJSON_GetObjectItemCaseSensitive(m, "rings");
    for (int i = 0; i < 3; i++) {
        const cJSON *r = cJSON_GetArrayItem(rings, i);
        if (!r) {
            lv_obj_add_flag(ui.g_arc[i], LV_OBJ_FLAG_HIDDEN);
            lv_label_set_text(ui.g_leg[i], "");
            continue;
        }
        lv_obj_remove_flag(ui.g_arc[i], LV_OBJ_FLAG_HIDDEN);
        double v = 0, mx = 100;
        jnum(r, "value", &v);
        jnum(r, "max", &mx);
        lv_arc_set_range(ui.g_arc[i], 0, mx > 0 ? (int)mx : 100);
        lv_arc_set_value(ui.g_arc[i], (int)v);
        uint32_t c = jcolor(r, "color", RING_COLORS[i]);
        lv_obj_set_style_arc_color(ui.g_arc[i], hex(c), LV_PART_INDICATOR);
        lv_obj_set_style_arc_color(ui.g_arc[i], lv_color_mix(hex(c), lv_color_black(), 40), LV_PART_MAIN);
        lv_obj_set_style_text_color(ui.g_leg[i], hex(c), 0);
        const char *label = jstr(r, "label"), *text = jstr(r, "text");
        lv_label_set_text_fmt(ui.g_leg[i], "%s %s", label ? label : "", text ? text : "");
    }
    const char *s;
    if ((s = jstr(m, "title"))) lv_label_set_text(ui.g_title, s);
    s = jstr(m, "center");
    ui.g_clock = !s;                      // no centre text -> show the clock
    if (s) lv_label_set_text(ui.g_center, s);
}

/* ------------------------------------------------------------------ ring (people, devices, ...) */

static int ring_pos(int i) { return i * (ui.r_n <= 2 ? 4 : ui.r_n <= 4 ? 2 : 1); }

static void build_ring(void)
{
    lv_obj_t *s = ui.scr_ring = mk_screen();
    ui.r_arc = mk_arc(s, 236, 4);
    lv_obj_set_style_arc_opa(ui.r_arc, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_arc_set_bg_angles(ui.r_arc, 0, 360);
    for (int i = 0; i < RING_MAX; i++) {
        lv_obj_t *o = ui.r_slots[i] = plain(s);
        lv_obj_add_flag(o, LV_OBJ_FLAG_OVERFLOW_VISIBLE);
        lv_obj_set_style_radius(o, LV_RADIUS_CIRCLE, 0);
        lv_obj_set_style_bg_opa(o, LV_OPA_COVER, 0);
        lv_obj_set_size(o, SLOT_D, SLOT_D);
        ui.r_img[i] = lv_image_create(o);
        lv_obj_center(ui.r_img[i]);
        ui.r_lbl[i] = mk_label(o, F16, 0xFFFFFF, 0, 0);
        lv_obj_t *b = ui.r_badge[i] = plain(o);
        lv_obj_set_size(b, 20, 20);
        lv_obj_set_style_radius(b, LV_RADIUS_CIRCLE, 0);
        lv_obj_set_style_bg_opa(b, LV_OPA_COVER, 0);
        lv_obj_set_style_border_color(b, lv_color_black(), 0);
        lv_obj_set_style_border_width(b, 2, 0);
        lv_obj_align(b, LV_ALIGN_BOTTOM_RIGHT, 5, 5);
        ui.r_badge_lbl[i] = mk_label(b, F12, 0xFFFFFF, 0, 0);
    }
    ui.r_title = mk_line_label(s, F12, 0x9E9E9E, 0, -46, 110);
    ui.r_name = mk_line_label(s, F20, 0xFFFFFF, 0, -14, 120);
    ui.r_status = mk_line_label(s, F14, GREEN, 0, 12, 140);
    ui.r_count = mk_line_label(s, F12, 0x9E9E9E, 0, 34, 120);
}

static void render_ring(void)
{
    if (ui.r_sel >= ui.r_n) ui.r_sel = ui.r_n ? ui.r_n - 1 : 0;
    for (int i = 0; i < RING_MAX; i++) {
        lv_obj_t *o = ui.r_slots[i];
        if (i >= ui.r_n) {
            lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
            continue;
        }
        ring_item_t *p = &ui.ring[i];
        bool sel = i == ui.r_sel;
        double ang = (-90.0 + ring_pos(i) * 45.0) * M_PI / 180.0;
        lv_obj_align(o, LV_ALIGN_CENTER, (int)lround(RING_R * cos(ang)), (int)lround(RING_R * sin(ang)));
        lv_obj_remove_flag(o, LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_size(o, sel ? SLOT_D_SEL : SLOT_D, sel ? SLOT_D_SEL : SLOT_D);
        lv_obj_set_style_bg_color(o, hex(p->color), 0);
        lv_obj_set_style_border_width(o, p->has_ring ? 4 : 0, 0);
        lv_obj_set_style_border_color(o, hex(p->ring_color), 0);
        show_icon(ui.r_img[i], ui.r_lbl[i], p->icon, p->name, &ui.tpl_icons[i], F20, F16, sel ? 256 : 224);
        if (p->badge[0]) {
            lv_obj_remove_flag(ui.r_badge[i], LV_OBJ_FLAG_HIDDEN);
            lv_obj_set_style_bg_color(ui.r_badge[i], hex(p->badge_color), 0);
            const char *b = p->badge;
            const char *sym = !strncmp(b, "sym:", 4) ? sym_lookup(b + 4) : NULL;
            lv_label_set_text(ui.r_badge_lbl[i], sym ? sym : (!strncmp(b, "txt:", 4) ? b + 4 : b));
        } else {
            lv_obj_add_flag(ui.r_badge[i], LV_OBJ_FLAG_HIDDEN);
        }
    }
    lv_label_set_text(ui.r_title, ui.r_title_txt);
    lv_obj_set_width(ui.r_status, ui.r_n == 0 ? 200 : 124);
    lv_obj_align(ui.r_status, LV_ALIGN_CENTER, 0, 12);
    if (ui.r_n == 0) {
        lv_obj_set_style_arc_opa(ui.r_arc, LV_OPA_TRANSP, LV_PART_INDICATOR);
        lv_label_set_text(ui.r_name, LV_SYMBOL_LIST);
        lv_label_set_text(ui.r_status, ui.r_text[0] ? ui.r_text : "Nothing here");
        lv_obj_set_style_text_color(ui.r_status, hex(0x9E9E9E), 0);
        lv_label_set_text(ui.r_count, "");
        return;
    }
    ring_item_t *p = &ui.ring[ui.r_sel];
    lv_obj_set_style_arc_opa(ui.r_arc, LV_OPA_COVER, LV_PART_INDICATOR);
    lv_obj_set_style_arc_color(ui.r_arc, p->has_ring ? hex(p->ring_color) : lv_color_white(), LV_PART_INDICATOR);
    lv_label_set_text(ui.r_name, p->name);
    lv_label_set_text(ui.r_status, p->status);
    lv_obj_set_style_text_color(ui.r_status, hex(p->status_color), 0);
    if (ui.r_text[0]) {
        lv_label_set_text(ui.r_count, ui.r_text);
    } else {
        lv_label_set_text_fmt(ui.r_count, "%d", ui.r_n);
    }
}

// accepts the generic fields (ring, badge, status) and the 1.x people fields (speaking, muted, deaf)
static void apply_ring(const cJSON *m)
{
    char keep[DECK_ID_LEN] = "";
    if (ui.r_n > 0) copy_txt(keep, sizeof(keep), ui.ring[ui.r_sel].id);
    const char *t;
    if ((t = jstr(m, "title"))) copy_txt(ui.r_title_txt, sizeof(ui.r_title_txt), t);
    copy_txt(ui.r_text, sizeof(ui.r_text), jstr(m, "text"));
    const cJSON *items = cJSON_GetObjectItemCaseSensitive(m, "items");
    int n = 0;
    const cJSON *it;
    cJSON_ArrayForEach(it, items) {
        if (n >= RING_MAX) break;
        ring_item_t *p = &ui.ring[n++];
        memset(p, 0, sizeof(*p));
        copy_txt(p->id, sizeof(p->id), jstr(it, "id"));
        copy_txt(p->name, sizeof(p->name), jstr(it, "name"));
        copy_txt(p->icon, sizeof(p->icon), jstr(it, "icon"));
        p->color = jcolor(it, "color", 0x5865F2);
        bool speaking = jbool(it, "speaking"), muted = jbool(it, "muted"), deaf = jbool(it, "deaf");
        p->has_ring = speaking || has(it, "ring");
        p->ring_color = jcolor(it, "ring", GREEN);
        copy_txt(p->badge, sizeof(p->badge), jstr(it, "badge") ? jstr(it, "badge") : deaf ? "sym:close" : muted ? "sym:mute" : "");
        p->badge_color = jcolor(it, "badge_color", RED);
        const char *st = jstr(it, "status");
        copy_txt(p->status, sizeof(p->status), st ? st : deaf ? "deafened" : muted ? "muted" : speaking ? "speaking" : "");
        p->status_color = jcolor(it, "status_color", (muted || deaf) ? RED : GREEN);
    }
    bool first = ui.r_n == 0;
    ui.r_n = n;
    ui.r_sel = 0;
    for (int i = 0; i < n; i++) {
        if (keep[0] && !strcmp(ui.ring[i].id, keep)) ui.r_sel = i;
    }
    if (jint(m, "sel", -1) >= 0 && jint(m, "sel", -1) < n) ui.r_sel = jint(m, "sel", 0);
    render_ring();
    move_arc(ui.r_arc, &ui.r_arc_angle, ring_arc_cb, ring_pos(ui.r_sel), !first);
}

/* ------------------------------------------------------------------ list (wheel) */

static const int LIST_Y[5] = {-74, -40, 0, 40, 74};

static void build_list(void)
{
    lv_obj_t *s = ui.scr_list = mk_screen();
    ui.l_title = mk_line_label(s, F12, 0x9E9E9E, 0, -102, 120);
    ui.l_pos = mk_label(s, F12, 0x616161, 0, 104);
    lv_obj_t *band = plain(s);                         // highlight behind the selected row
    lv_obj_set_size(band, 214, 46);
    lv_obj_center(band);
    lv_obj_set_style_radius(band, 23, 0);
    lv_obj_set_style_bg_color(band, hex(0x1E2A38), 0);
    lv_obj_set_style_bg_opa(band, LV_OPA_COVER, 0);
    for (int i = 0; i < 5; i++) {
        bool mid = i == 2;
        lv_obj_t *r = ui.l_rows[i] = plain(s);
        lv_obj_set_size(r, mid ? 196 : 176, 34);
        lv_obj_align(r, LV_ALIGN_CENTER, 0, LIST_Y[i] + (mid ? -6 : 0));
        ui.l_row_img[i] = lv_image_create(r);
        lv_obj_align(ui.l_row_img[i], LV_ALIGN_LEFT_MID, 0, 0);
        ui.l_row_icon[i] = lv_label_create(r);
        lv_obj_align(ui.l_row_icon[i], LV_ALIGN_LEFT_MID, 2, 0);
        lv_obj_set_style_text_color(ui.l_row_icon[i], lv_color_white(), 0);
        ui.l_row_txt[i] = lv_label_create(r);
        lv_obj_set_style_text_font(ui.l_row_txt[i], mid ? F20 : F16, 0);
        lv_obj_set_style_text_color(ui.l_row_txt[i], lv_color_white(), 0);
        lv_label_set_long_mode(ui.l_row_txt[i], LV_LABEL_LONG_DOT);
        lv_obj_set_size(ui.l_row_txt[i], mid ? 150 : 136, lv_font_get_line_height(mid ? F20 : F16));
        lv_obj_align(ui.l_row_txt[i], LV_ALIGN_LEFT_MID, 36, 0);
        int dist = i < 2 ? 2 - i : i - 2;
        lv_obj_set_style_opa(r, dist == 0 ? LV_OPA_COVER : dist == 1 ? LV_OPA_60 : LV_OPA_30, 0);
    }
    ui.l_sub = mk_line_label(s, F12, 0x9E9E9E, 0, 15, 170);
    ui.l_check = mk_label(s, F16, GREEN, 92, -6);
}

static void render_list(void)
{
    if (ui.l_sel >= ui.l_n) ui.l_sel = ui.l_n ? ui.l_n - 1 : 0;
    for (int i = 0; i < 5; i++) {
        int idx = ui.l_sel + i - 2;
        lv_obj_t *r = ui.l_rows[i];
        if (idx < 0 || idx >= ui.l_n) {
            lv_obj_add_flag(r, LV_OBJ_FLAG_HIDDEN);
            continue;
        }
        list_item_t *it = &ui.list[idx];
        lv_obj_remove_flag(r, LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_style_text_color(ui.l_row_icon[i], hex(it->color), 0);
        show_icon(ui.l_row_img[i], ui.l_row_icon[i], it->icon[0] ? it->icon : "sym:right", it->text,
                  i == 2 ? &ui.tpl_icons[0] : NULL, i == 2 ? F20 : F16, F14, 160);
        lv_label_set_text(ui.l_row_txt[i], it->text);
    }
    if (ui.l_n == 0) {
        lv_obj_remove_flag(ui.l_rows[2], LV_OBJ_FLAG_HIDDEN);
        lv_label_set_text(ui.l_row_icon[2], "");
        lv_obj_add_flag(ui.l_row_img[2], LV_OBJ_FLAG_HIDDEN);
        lv_label_set_text(ui.l_row_txt[2], "Empty");
        lv_label_set_text(ui.l_sub, "");
        lv_label_set_text(ui.l_pos, "");
        lv_label_set_text(ui.l_check, "");
        return;
    }
    list_item_t *it = &ui.list[ui.l_sel];
    if (it->sub[0] && it->value[0]) {
        lv_label_set_text_fmt(ui.l_sub, "%s  ·  %s", it->sub, it->value);
    } else {
        lv_label_set_text(ui.l_sub, it->sub[0] ? it->sub : it->value);
    }
    lv_label_set_text(ui.l_check, it->checked ? LV_SYMBOL_OK : "");
    lv_label_set_text_fmt(ui.l_pos, "%d / %d", ui.l_sel + 1, ui.l_n);
}

static void apply_list(const cJSON *m)
{
    char keep[DECK_ID_LEN] = "";
    if (ui.l_n > 0) copy_txt(keep, sizeof(keep), ui.list[ui.l_sel].id);
    const char *t;
    if ((t = jstr(m, "title"))) lv_label_set_text(ui.l_title, t);
    ui.l_select_events = jbool(m, "select_events");
    const cJSON *items = cJSON_GetObjectItemCaseSensitive(m, "items");
    int n = 0;
    const cJSON *it;
    cJSON_ArrayForEach(it, items) {
        if (n >= LIST_MAX) break;
        list_item_t *li = &ui.list[n];
        memset(li, 0, sizeof(*li));
        copy_txt(li->id, sizeof(li->id), jstr(it, "id"));
        copy_txt(li->text, sizeof(li->text), jstr(it, "text"));
        copy_txt(li->sub, sizeof(li->sub), jstr(it, "sub"));
        copy_txt(li->icon, sizeof(li->icon), jstr(it, "icon"));
        copy_txt(li->value, sizeof(li->value), jstr(it, "value"));
        li->color = jcolor(it, "color", 0xFFFFFF);
        li->checked = jbool(it, "checked");
        if (!li->id[0]) snprintf(li->id, sizeof(li->id), "%d", n);
        n++;
    }
    ui.l_n = n;
    ui.l_sel = 0;
    for (int i = 0; i < n; i++) {
        if (keep[0] && !strcmp(ui.list[i].id, keep)) ui.l_sel = i;
    }
    int sel = jint(m, "sel", -1);
    if (sel >= 0 && sel < n) ui.l_sel = sel;
    render_list();
}

/* ------------------------------------------------------------------ card */

static void build_card(void)
{
    lv_obj_t *s = ui.scr_card = mk_screen();
    ui.k_arc = mk_arc(s, 234, 6);                 // progress around the rim
    lv_arc_set_rotation(ui.k_arc, 270);
    lv_arc_set_bg_angles(ui.k_arc, 0, 360);
    ui.k_img = lv_image_create(s);
    lv_obj_align(ui.k_img, LV_ALIGN_CENTER, 0, -46);
    ui.k_sym = mk_label(s, F36, 0xFFFFFF, 0, -46);
    ui.k_value = mk_line_label(s, F36, 0xFFFFFF, 0, -46, 170);
    ui.k_title = mk_line_label(s, F20, 0xFFFFFF, 0, 6, 180);
    ui.k_text = mk_label(s, F14, 0xBDBDBD, 0, 44);
    lv_label_set_long_mode(ui.k_text, LV_LABEL_LONG_DOT);
    lv_obj_set_size(ui.k_text, 176, 52);
    ui.k_footer = mk_line_label(s, F12, 0x757575, 0, 92, 120);
}

static void apply_card(const cJSON *m)
{
    uint32_t c = jcolor(m, "color", 0x3D8BFD);
    const char *value = jstr(m, "value"), *icon = jstr(m, "icon");
    if (value && value[0]) {
        lv_obj_add_flag(ui.k_img, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(ui.k_sym, LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(ui.k_value, LV_OBJ_FLAG_HIDDEN);
        lv_label_set_text(ui.k_value, value);
        lv_obj_set_style_text_color(ui.k_value, hex(c), 0);
    } else {
        lv_obj_add_flag(ui.k_value, LV_OBJ_FLAG_HIDDEN);
        show_icon(ui.k_img, ui.k_sym, icon ? icon : "", "", &ui.tpl_icons[0], F36, F28,
                  (uint32_t)jint(m, "icon_scale", 384));
        lv_obj_set_style_text_color(ui.k_sym, hex(c), 0);
    }
    lv_label_set_text(ui.k_title, jstr(m, "title") ? jstr(m, "title") : "");
    lv_label_set_text(ui.k_text, jstr(m, "text") ? jstr(m, "text") : "");
    lv_label_set_text(ui.k_footer, jstr(m, "footer") ? jstr(m, "footer") : "");
    double p, mx = 100;
    if (jnum(m, "progress", &p)) {
        jnum(m, "max", &mx);
        lv_obj_remove_flag(ui.k_arc, LV_OBJ_FLAG_HIDDEN);
        lv_arc_set_range(ui.k_arc, 0, mx > 0 ? (int)mx : 100);
        lv_arc_set_value(ui.k_arc, (int)p);
        lv_obj_set_style_arc_color(ui.k_arc, hex(c), LV_PART_INDICATOR);
    } else {
        lv_obj_add_flag(ui.k_arc, LV_OBJ_FLAG_HIDDEN);
    }
}

/* ------------------------------------------------------------------ canvas (free layout) */

static void canvas_clear(void)
{
    for (int i = 0; i < ui.el_n; i++) {
        if (ui.els[i].obj) lv_obj_delete(ui.els[i].obj);
    }
    memset(ui.els, 0, sizeof(ui.els));
    ui.el_n = 0;
}

static canvas_el_t *el_find(const char *id)
{
    for (int i = 0; i < ui.el_n; i++) {
        if (!strcmp(ui.els[i].id, id)) return &ui.els[i];
    }
    return NULL;
}

// apply any properties present in `m` to element `e`
static void el_apply(canvas_el_t *e, const cJSON *m)
{
    lv_obj_t *o = e->obj;
    double v;
    const char *s;
    int x = jint(m, "x", 0), y = jint(m, "y", 0);
    if (has(m, "x") || has(m, "y")) lv_obj_align(o, LV_ALIGN_CENTER, x, y);
    if (has(m, "w") || has(m, "h")) {
        int w = jint(m, "w", lv_obj_get_width(o)), h = jint(m, "h", e->type == EL_ARC ? w : lv_obj_get_height(o));
        if (e->type == EL_LABEL) {
            lv_obj_set_width(o, w);
            if (has(m, "h")) lv_obj_set_height(o, h);
        } else {
            lv_obj_set_size(o, w, h);
        }
    }
    if (has(m, "hidden")) {
        if (jbool(m, "hidden")) lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
        else lv_obj_remove_flag(o, LV_OBJ_FLAG_HIDDEN);
    }
    if (jnum(m, "opa", &v)) lv_obj_set_style_opa(o, (lv_opa_t)(v * 255 / 100), 0);
    switch (e->type) {
    case EL_LABEL:
        if ((s = jstr(m, "text"))) lv_label_set_text(o, s);
        if (has(m, "size")) lv_obj_set_style_text_font(o, font_for(jint(m, "size", 16)), 0);
        if (has(m, "color")) lv_obj_set_style_text_color(o, hex(jcolor(m, "color", 0xFFFFFF)), 0);
        if ((s = jstr(m, "align"))) {
            lv_obj_set_style_text_align(o, !strcmp(s, "left") ? LV_TEXT_ALIGN_LEFT
                                         : !strcmp(s, "right") ? LV_TEXT_ALIGN_RIGHT : LV_TEXT_ALIGN_CENTER, 0);
        }
        break;
    case EL_ARC:
        if (has(m, "min") || has(m, "max")) lv_arc_set_range(o, jint(m, "min", 0), jint(m, "max", 100));
        if (jnum(m, "value", &v)) lv_arc_set_value(o, (int)v);
        if (has(m, "start") || has(m, "end")) lv_arc_set_bg_angles(o, jint(m, "start", 0), jint(m, "end", 360));
        if (has(m, "rotation")) lv_arc_set_rotation(o, jint(m, "rotation", 0));
        if (has(m, "width")) {
            lv_obj_set_style_arc_width(o, jint(m, "width", 8), LV_PART_MAIN);
            lv_obj_set_style_arc_width(o, jint(m, "width", 8), LV_PART_INDICATOR);
        }
        if (has(m, "color")) lv_obj_set_style_arc_color(o, hex(jcolor(m, "color", 0xFFFFFF)), LV_PART_INDICATOR);
        if (has(m, "bg")) lv_obj_set_style_arc_color(o, hex(jcolor(m, "bg", 0x262626)), LV_PART_MAIN);
        break;
    case EL_BAR:
        if (has(m, "min") || has(m, "max")) lv_bar_set_range(o, jint(m, "min", 0), jint(m, "max", 100));
        if (jnum(m, "value", &v)) lv_bar_set_value(o, (int)v, LV_ANIM_ON);
        if (has(m, "color")) lv_obj_set_style_bg_color(o, hex(jcolor(m, "color", 0xFFFFFF)), LV_PART_INDICATOR);
        if (has(m, "bg")) lv_obj_set_style_bg_color(o, hex(jcolor(m, "bg", 0x262626)), LV_PART_MAIN);
        break;
    case EL_IMG:
        if ((s = jstr(m, "icon"))) {
            int slot = e->cache;
            show_icon(o, e->lbl, s, "", slot >= 0 ? &ui.tpl_icons[slot] : NULL, font_for(jint(m, "size", 28)),
                      font_for(jint(m, "size", 28)), (uint32_t)jint(m, "scale", 256));
            e->is_img = !lv_obj_has_flag(o, LV_OBJ_FLAG_HIDDEN);
        } else if (has(m, "size")) {
            lv_obj_set_style_text_font(e->lbl, font_for(jint(m, "size", 28)), 0);
        }
        if (has(m, "color")) lv_obj_set_style_text_color(e->lbl, hex(jcolor(m, "color", 0xFFFFFF)), 0);
        if (has(m, "x") || has(m, "y")) lv_obj_align(e->lbl, LV_ALIGN_CENTER, x, y);
        if (has(m, "opa")) lv_obj_set_style_opa(e->lbl, lv_obj_get_style_opa(o, 0), 0);
        {   // exactly one of picture / label is visible (unless hidden)
            bool hide = jbool(m, "hidden") || (!has(m, "hidden") && lv_obj_has_flag(e->is_img ? o : e->lbl, LV_OBJ_FLAG_HIDDEN));
            if (hide || !e->is_img) lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN); else lv_obj_remove_flag(o, LV_OBJ_FLAG_HIDDEN);
            if (hide || e->is_img) lv_obj_add_flag(e->lbl, LV_OBJ_FLAG_HIDDEN); else lv_obj_remove_flag(e->lbl, LV_OBJ_FLAG_HIDDEN);
        }
        break;
    case EL_RECT:
        if (has(m, "color")) lv_obj_set_style_bg_color(o, hex(jcolor(m, "color", 0x333333)), 0);
        if (has(m, "radius")) lv_obj_set_style_radius(o, jint(m, "radius", 0), 0);
        if (has(m, "border")) lv_obj_set_style_border_width(o, jint(m, "border", 0), 0);
        if (has(m, "border_color")) lv_obj_set_style_border_color(o, hex(jcolor(m, "border_color", 0xFFFFFF)), 0);
        break;
    case EL_CHART: {
        if (has(m, "min") || has(m, "max")) {
            lv_chart_set_axis_range(o, LV_CHART_AXIS_PRIMARY_Y, jint(m, "min", 0), jint(m, "max", 100));
        }
        if (has(m, "color")) lv_chart_set_series_color(o, e->ser, hex(jcolor(m, "color", 0x4FC3F7)));
        const cJSON *pts = cJSON_GetObjectItemCaseSensitive(m, "points");
        if (cJSON_IsArray(pts)) {
            int n = cJSON_GetArraySize(pts);
            if (n > CHART_MAX) n = CHART_MAX;
            if (n < 2) n = 2;
            lv_chart_set_point_count(o, n);
            lv_chart_set_all_values(o, e->ser, LV_CHART_POINT_NONE);
            const cJSON *p;
            cJSON_ArrayForEach(p, pts) {
                if (cJSON_IsNumber(p)) lv_chart_set_next_value(o, e->ser, (int32_t)p->valuedouble);
            }
        }
        double add;
        if (jnum(m, "push", &add)) lv_chart_set_next_value(o, e->ser, (int32_t)add);   // append one value
        lv_chart_refresh(o);
        break;
    }
    }
}

static void el_create(const cJSON *m)
{
    if (ui.el_n >= CANVAS_MAX) return;
    const char *type = jstr(m, "type");
    if (!type) return;
    canvas_el_t *e = &ui.els[ui.el_n];
    memset(e, 0, sizeof(*e));
    e->cache = -1;
    copy_txt(e->id, sizeof(e->id), jstr(m, "id"));
    lv_obj_t *s = ui.scr_canvas;
    if (!strcmp(type, "label")) {
        e->type = EL_LABEL;
        e->obj = lv_label_create(s);
        lv_obj_set_style_text_font(e->obj, F16, 0);
        lv_obj_set_style_text_color(e->obj, lv_color_white(), 0);
        lv_obj_set_style_text_align(e->obj, LV_TEXT_ALIGN_CENTER, 0);
        lv_label_set_text(e->obj, "");
        if (has(m, "w")) lv_label_set_long_mode(e->obj, jbool(m, "wrap") ? LV_LABEL_LONG_WRAP : LV_LABEL_LONG_DOT);
    } else if (!strcmp(type, "arc")) {
        e->type = EL_ARC;
        e->obj = mk_arc(s, jint(m, "w", 200), jint(m, "width", 10));
        lv_arc_set_bg_angles(e->obj, 0, 360);
        lv_arc_set_rotation(e->obj, 270);
    } else if (!strcmp(type, "bar")) {
        e->type = EL_BAR;
        e->obj = lv_bar_create(s);
        lv_obj_set_size(e->obj, 140, 10);
        lv_obj_set_style_bg_color(e->obj, hex(0x262626), LV_PART_MAIN);
        lv_obj_set_style_bg_color(e->obj, hex(0x4FC3F7), LV_PART_INDICATOR);
    } else if (!strcmp(type, "img")) {
        e->type = EL_IMG;
        e->obj = lv_image_create(s);
        e->lbl = lv_label_create(s);
        lv_obj_set_style_text_color(e->lbl, lv_color_white(), 0);
        for (int i = 0; i < TPL_ICONS; i++) {          // claim a free image slot
            bool used = false;
            for (int k = 0; k < ui.el_n; k++) used |= ui.els[k].cache == i;
            if (!used) { e->cache = i; break; }
        }
    } else if (!strcmp(type, "rect")) {
        e->type = EL_RECT;
        e->obj = plain(s);
        lv_obj_set_size(e->obj, 40, 40);
        lv_obj_set_style_bg_opa(e->obj, LV_OPA_COVER, 0);
        lv_obj_set_style_bg_color(e->obj, hex(0x333333), 0);
    } else if (!strcmp(type, "chart")) {
        e->type = EL_CHART;
        e->obj = lv_chart_create(s);
        lv_obj_set_size(e->obj, 160, 70);
        lv_chart_set_type(e->obj, LV_CHART_TYPE_LINE);
        lv_chart_set_div_line_count(e->obj, 0, 0);
        lv_obj_set_style_bg_opa(e->obj, LV_OPA_TRANSP, 0);
        lv_obj_set_style_border_width(e->obj, 0, 0);
        lv_obj_set_style_pad_all(e->obj, 0, 0);
        lv_obj_set_style_size(e->obj, 0, 0, LV_PART_INDICATOR);
        lv_obj_set_style_line_width(e->obj, 3, LV_PART_ITEMS);
        e->ser = lv_chart_add_series(e->obj, hex(0x4FC3F7), LV_CHART_AXIS_PRIMARY_Y);
    } else {
        return;
    }
    lv_obj_align(e->obj, LV_ALIGN_CENTER, 0, 0);
    if (e->lbl) lv_obj_align(e->lbl, LV_ALIGN_CENTER, 0, 0);
    ui.el_n++;
    el_apply(e, m);
    if (e->type == EL_LABEL && has(m, "w") && !has(m, "h") && !jbool(m, "wrap")) {
        lv_obj_set_height(e->obj, lv_font_get_line_height(lv_obj_get_style_text_font(e->obj, 0)));
    }
}

static void apply_canvas(const cJSON *m)
{
    canvas_clear();
    free_tpl_icons();
    lv_obj_set_style_bg_color(ui.scr_canvas, hex(jcolor(m, "bg", 0x000000)), 0);
    const cJSON *els = cJSON_GetObjectItemCaseSensitive(m, "elements");
    const cJSON *e;
    cJSON_ArrayForEach(e, els) el_create(e);
}

static void apply_patch(const cJSON *m)
{
    const cJSON *set = cJSON_GetObjectItemCaseSensitive(m, "set");
    const cJSON *p;
    cJSON_ArrayForEach(p, set) {
        const char *id = jstr(p, "id");
        canvas_el_t *e = id ? el_find(id) : NULL;
        if (e) el_apply(e, p);
        else el_create(p);                 // unknown id with a "type" adds an element
    }
}

/* ------------------------------------------------------------------ clock (screensaver) */

static void build_clock(void)
{
    lv_obj_t *s = ui.scr_clock = mk_screen();
    ui.t_time = mk_label(s, F48, 0xFFFFFF, 0, -10);
    ui.t_date = mk_label(s, F16, 0x9E9E9E, 0, 34);
}

static bool local_time(struct tm *out)
{
    if (!ui.time_known) return false;
    time_t t = (time_t)((now() + ui.time_base_ms) / 1000);
    gmtime_r(&t, out);
    return true;
}

static void update_clocks(void)
{
    struct tm tm;
    if (!local_time(&tm)) {
        if (ui.g_clock) lv_label_set_text(ui.g_center, "--:--");
        return;
    }
    static const char *WD[] = {"Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"};
    static const char *MO[] = {"Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"};
    if (ui.g_clock) lv_label_set_text_fmt(ui.g_center, "%02d:%02d", tm.tm_hour, tm.tm_min);
    lv_label_set_text_fmt(ui.t_time, "%02d:%02d", tm.tm_hour, tm.tm_min);
    lv_label_set_text_fmt(ui.t_date, "%s %d %s", WD[tm.tm_wday], tm.tm_mday, MO[tm.tm_mon]);
}

/* ------------------------------------------------------------------ popup */

static void build_popup(void)
{
    lv_obj_t *p = ui.popup = plain(lv_layer_top());
    lv_obj_set_size(p, 196, 150);
    lv_obj_center(p);
    lv_obj_set_style_radius(p, 28, 0);
    lv_obj_set_style_bg_color(p, hex(0x1C1E22), 0);
    lv_obj_set_style_bg_opa(p, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(p, 2, 0);
    lv_obj_set_style_shadow_width(p, 0, 0);
    ui.pp_img = lv_image_create(p);
    lv_obj_align(ui.pp_img, LV_ALIGN_TOP_MID, 0, 10);
    ui.pp_sym = lv_label_create(p);
    lv_obj_set_style_text_font(ui.pp_sym, F28, 0);
    lv_obj_align(ui.pp_sym, LV_ALIGN_TOP_MID, 0, 12);
    ui.pp_title = mk_line_label(p, F16, 0xFFFFFF, 0, -2, 176);
    ui.pp_text = mk_label(p, F14, 0xBDBDBD, 0, 36);
    lv_label_set_long_mode(ui.pp_text, LV_LABEL_LONG_DOT);
    lv_obj_set_size(ui.pp_text, 176, 2 * lv_font_get_line_height(F14));
    lv_obj_add_flag(p, LV_OBJ_FLAG_HIDDEN);
}

static void popup_hide(bool by_user)
{
    if (!ui.pp_visible) return;
    ui.pp_visible = false;
    ui.pp_until = 0;
    lv_obj_add_flag(ui.popup, LV_OBJ_FLAG_HIDDEN);
    icon_free(&ui.pp_icon);
    if (by_user) send_fmt("{\"t\":\"input\",\"id\":\"%s\",\"ev\":\"dismiss\"}", ui.pp_id);
}

static void apply_popup(const cJSON *m)
{
    uint32_t c = jcolor(m, "color", 0x3D8BFD);
    copy_txt(ui.pp_id, sizeof(ui.pp_id), jstr(m, "id") ? jstr(m, "id") : "popup");
    lv_obj_set_style_border_color(ui.popup, hex(c), 0);
    show_icon(ui.pp_img, ui.pp_sym, jstr(m, "icon") ? jstr(m, "icon") : "sym:bell", "", &ui.pp_icon, F28, F20, 192);
    lv_obj_set_style_text_color(ui.pp_sym, hex(c), 0);
    lv_label_set_text(ui.pp_title, jstr(m, "title") ? jstr(m, "title") : "");
    lv_label_set_text(ui.pp_text, jstr(m, "text") ? jstr(m, "text") : "");
    int ms = jint(m, "ms", 5000);
    ui.pp_until = ms > 0 ? now() + ms : 0;
    ui.pp_visible = true;
    lv_obj_remove_flag(ui.popup, LV_OBJ_FLAG_HIDDEN);
    lv_obj_fade_in(ui.popup, 150, 0);
    // a popup wakes the screen
    if (ui.dimmed) {
        ui.dimmed = false;
        if (ui.hal->set_brightness) ui.hal->set_brightness(ui.bright);
    }
    ui.last_input = now();
}

/* ------------------------------------------------------------------ opening screens from tiles */

static void open_from_tile(int slot)
{
    deck_tile_t *t = &cur_page()->tiles[slot];
    switch (t->type) {
    case TILE_DIAL:
        dial_defaults(tile_label(t), tile_icon(t), tile_color(t));
        open_screen(SCR_DIAL, t->id, true);
        render_dial();
        send_fmt("{\"t\":\"screen\",\"name\":\"dial\",\"id\":\"%s\"}", t->id);
        break;
    case TILE_STATS:
        lv_label_set_text(ui.g_title, tile_label(t));
        ui.g_clock = true;
        update_clocks();
        open_screen(SCR_GAUGES, t->id, true);
        send_fmt("{\"t\":\"screen\",\"name\":\"stats\",\"id\":\"%s\"}", t->id);
        break;
    case TILE_PEOPLE:
        ui.r_n = 0;
        ui.r_sel = 0;
        copy_txt(ui.r_title_txt, sizeof(ui.r_title_txt), tile_label(t));
        copy_txt(ui.r_text, sizeof(ui.r_text), "Loading...");
        render_ring();
        open_screen(SCR_RING, t->id, true);
        send_fmt("{\"t\":\"screen\",\"name\":\"people\",\"id\":\"%s\"}", t->id);
        break;
    default:   // TILE_SCREEN: a placeholder card until the PC says what to show
        lv_obj_add_flag(ui.k_arc, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(ui.k_value, LV_OBJ_FLAG_HIDDEN);
        show_icon(ui.k_img, ui.k_sym, tile_icon(t), tile_label(t), &ui.tpl_icons[0], F36, F28, 384);
        lv_obj_set_style_text_color(ui.k_sym, hex(tile_color(t)), 0);
        lv_label_set_text(ui.k_title, tile_label(t));
        lv_label_set_text(ui.k_text, "Loading...");
        lv_label_set_text(ui.k_footer, "");
        open_screen(SCR_CARD, t->id, false);
        send_fmt("{\"t\":\"screen\",\"name\":\"screen\",\"id\":\"%s\"}", t->id);
        break;
    }
}

/* ------------------------------------------------------------------ input */

static void on_rotate(int diff)
{
    switch (ui.screen) {
    case SCR_DECK: {
        deck_page_t *p = cur_page();
        int sel = ui.sel[ui.page];
        if (sel < 0) return;
        int dir = diff > 0 ? 1 : -1;
        for (int n = abs(diff); n > 0; n--) {
            for (int k = 1; k <= DECK_SLOTS; k++) {
                int s = ((sel + dir * k) % DECK_SLOTS + DECK_SLOTS) % DECK_SLOTS;
                if (p->tiles[s].type != TILE_EMPTY) { sel = s; break; }
            }
        }
        if (sel != ui.sel[ui.page]) {
            ui.sel[ui.page] = sel;
            render_deck();
            move_arc(ui.ring_arc, &ui.arc_angle, deck_arc_cb, sel, true);
        }
        break;
    }
    case SCR_DIAL: {
        int v = ui.d_val + diff * ui.d_step;
        ui.d_val = v < ui.d_min ? ui.d_min : (v > ui.d_max ? ui.d_max : v);
        ui.d_text[0] = 0;
        render_dial();
        if (ui.legacy) send_fmt("{\"t\":\"dial\",\"id\":\"%s\",\"d\":%d}", ui.ctx, diff);
        else send_input("turn", diff, NULL);
        break;
    }
    case SCR_RING:
        if (ui.r_n > 1) {
            ui.r_sel = ((ui.r_sel + diff) % ui.r_n + ui.r_n) % ui.r_n;
            render_ring();
            move_arc(ui.r_arc, &ui.r_arc_angle, ring_arc_cb, ring_pos(ui.r_sel), true);
            if (!ui.legacy) send_input("select", 0, ui.ring[ui.r_sel].id);
        }
        break;
    case SCR_LIST:
        if (ui.l_n > 0) {
            int s = ui.l_sel + diff;
            s = s < 0 ? 0 : (s >= ui.l_n ? ui.l_n - 1 : s);
            if (s != ui.l_sel) {
                ui.l_sel = s;
                render_list();
                if (ui.l_select_events) send_input("select", 0, ui.list[s].id);
            }
        }
        break;
    case SCR_GAUGES:
    case SCR_CARD:
    case SCR_CANVAS:
        if (!ui.legacy) send_input("turn", diff, NULL);
        break;
    default:
        break;
    }
}

static void on_click(void)
{
    switch (ui.screen) {
    case SCR_DIAL:
        if (ui.legacy) send_fmt("{\"t\":\"dial_press\",\"id\":\"%s\"}", ui.ctx);
        else send_input("press", 0, NULL);
        return;
    case SCR_GAUGES:
        if (ui.legacy) show_deck();
        else send_input("press", 0, NULL);
        return;
    case SCR_RING:
        if (ui.r_n > 0) {
            if (ui.legacy) {
                send_fmt("{\"t\":\"people_press\",\"id\":\"%s\",\"item\":\"%s\"}", ui.ctx, ui.ring[ui.r_sel].id);
            } else {
                send_input("press", 0, ui.ring[ui.r_sel].id);
            }
        }
        return;
    case SCR_LIST:
        if (ui.l_n > 0) send_input("press", 0, ui.list[ui.l_sel].id);
        return;
    case SCR_CARD:
    case SCR_CANVAS:
        send_input("press", 0, NULL);
        return;
    case SCR_DECK:
        break;
    default:
        return;
    }
    int sel = ui.sel[ui.page];
    if (sel < 0) return;
    deck_tile_t *t = &cur_page()->tiles[sel];
    switch (t->type) {
    case TILE_FOLDER:
        open_page(deck_find_page(ui.deck, t->target), true);
        break;
    case TILE_BACK:
        go_back();
        break;
    case TILE_DIAL:
    case TILE_STATS:
    case TILE_PEOPLE:
    case TILE_SCREEN:
        if (!ui.connected && t->type != TILE_STATS) { toast("PC not connected", 1500); break; }
        open_from_tile(sel);
        break;
    default:
        ui.flash_slot = sel;
        ui.flash_until = now() + FLASH_MS;
        render_deck();
        if (!ui.connected) toast("PC not connected", 1500);
        else send_fmt("{\"t\":\"press\",\"id\":\"%s\",\"page\":\"%s\"}", t->id, cur_page()->id);
        break;
    }
}

static void on_long(void)
{
    if (ui.screen == SCR_DECK) {
        if (ui.depth > 0 || ui.page != 0) {
            go_back();
        } else {
            int sel = ui.sel[ui.page];
            if (sel >= 0 && ui.connected) {
                send_fmt("{\"t\":\"long\",\"id\":\"%s\",\"page\":\"%s\"}", cur_page()->tiles[sel].id, cur_page()->id);
            }
        }
    } else if (ui.screen != SCR_CLOCK) {
        if (ui.hold_send) {
            const char *item = ui.screen == SCR_LIST && ui.l_n ? ui.list[ui.l_sel].id
                             : ui.screen == SCR_RING && ui.r_n ? ui.ring[ui.r_sel].id : NULL;
            send_input("hold", 0, item);
        } else {
            show_deck();
        }
    }
}

static void wake(void)
{
    ui.last_input = now();
    if (!ui.dimmed) return;
    ui.dimmed = false;
    if (ui.hal->set_brightness) ui.hal->set_brightness(ui.bright);
    if (ui.screen == SCR_CLOCK) {
        if (ui.before_clock == SCR_GAUGES) {
            ui.screen = SCR_GAUGES;
            lv_screen_load_anim(ui.scr_gauges, LV_SCR_LOAD_ANIM_FADE_IN, 150, 0, false);
        } else {
            show_deck();
        }
    }
}

void ui_input(int diff, bool pressed)
{
    if (ui.invert) diff = -diff;
    if (ui.dimmed && (diff || pressed)) {
        wake();
        if (pressed) ui.swallow = true;     // this press only wakes the screen
        ui.pressed = pressed;
        return;
    }
    if (ui.pp_visible && pressed && !ui.pressed) {   // a press dismisses the popup
        popup_hide(true);
        ui.swallow = true;
        ui.pressed = true;
        ui.press_t0 = now();
        ui.long_fired = false;
        return;
    }
    if (diff || pressed != ui.pressed) ui.last_input = now();
    if (diff) on_rotate(diff);
    if (pressed && !ui.pressed) {
        ui.press_t0 = now();
        ui.long_fired = false;
    } else if (!pressed && ui.pressed) {
        if (!ui.long_fired && !ui.swallow) on_click();
        ui.swallow = false;
    }
    ui.pressed = pressed;
}

static void tick_cb(lv_timer_t *tm)
{
    (void)tm;
    uint32_t t = now();
    if (ui.pressed && !ui.long_fired && !ui.swallow && t - ui.press_t0 >= LONG_MS) {
        ui.long_fired = true;
        on_long();
    }
    if (ui.flash_slot >= 0 && t >= ui.flash_until) {
        ui.flash_slot = -1;
        if (ui.screen == SCR_DECK) render_deck();
    }
    if (ui.toast_until && t >= ui.toast_until) {
        ui.toast_until = 0;
        lv_obj_add_flag(ui.toast, LV_OBJ_FLAG_HIDDEN);
    }
    if (ui.pp_until && t >= ui.pp_until) popup_hide(false);
    bool c = ui.hal->pc_connected ? ui.hal->pc_connected() : false;
    if (c != ui.connected) {
        ui.connected = c;
        if (ui.screen == SCR_DECK) render_deck();
        else if (!c && ui.screen != SCR_CLOCK && ui.screen != SCR_GAUGES) show_deck();
    }
    if (t / 1000 != ui.last_tick_s) {
        ui.last_tick_s = t / 1000;
        update_clocks();
        if (!ui.dimmed && ui.idle_s > 0 && t - ui.last_input > (uint32_t)ui.idle_s * 1000 && !ui.pressed) {
            ui.dimmed = true;
            if (ui.hal->set_brightness) ui.hal->set_brightness(ui.dim);
            if (ui.time_known && (ui.screen == SCR_DECK || ui.screen == SCR_GAUGES)) {
                ui.before_clock = ui.screen;
                ui.screen = SCR_CLOCK;
                lv_screen_load_anim(ui.scr_clock, LV_SCR_LOAD_ANIM_FADE_IN, 400, 0, false);
            }
        }
    }
}

/* ------------------------------------------------------------------ PC messages */

static void msg_state(const cJSON *m)
{
    const char *id = jstr(m, "id");
    int pg, slot;
    deck_tile_t *t = id ? deck_find_tile(ui.deck, id, &pg, &slot) : NULL;
    if (!t) return;
    const char *s;
    if (jbool(m, "clear")) {
        t->st_has_label = t->st_has_icon = t->st_has_color = false;
        t->st_value[0] = 0;
    }
    if ((s = jstr(m, "label"))) { copy_txt(t->st_label, sizeof(t->st_label), s); t->st_has_label = s[0] != 0; }
    if ((s = jstr(m, "icon")))  { copy_txt(t->st_icon, sizeof(t->st_icon), s);  t->st_has_icon = s[0] != 0; }
    if ((s = jstr(m, "color"))) { t->st_has_color = s[0] != 0; t->st_color = deck_parse_color(s, t->color); }
    if ((s = jstr(m, "value"))) copy_txt(t->st_value, sizeof(t->st_value), s);
    if (pg == ui.page && ui.screen == SCR_DECK) render_deck();
}

static screen_t tpl_screen(const char *tpl)
{
    if (!tpl) return SCR_CARD;
    if (!strcmp(tpl, "dial"))   return SCR_DIAL;
    if (!strcmp(tpl, "gauges")) return SCR_GAUGES;
    if (!strcmp(tpl, "ring"))   return SCR_RING;
    if (!strcmp(tpl, "list"))   return SCR_LIST;
    if (!strcmp(tpl, "canvas")) return SCR_CANVAS;
    return SCR_CARD;
}

// {"t":"show","id":..,"tpl":..,...}: open (or update) a template screen
static void msg_show(const cJSON *m)
{
    const char *id = jstr(m, "id");
    if (!id) return;
    screen_t s = tpl_screen(jstr(m, "tpl"));
    bool same = ui.screen == s && !strcmp(ui.ctx, id);
    if (!same) {
        bool keep_legacy = ui.legacy && !strcmp(ui.ctx, id);   // PC answered a 1.x tile with "show"
        if (s == SCR_DIAL) dial_defaults("", "", 0x43A047);
        if (s == SCR_RING) ui.r_n = 0;
        if (s == SCR_LIST) ui.l_n = 0;
        open_screen(s, id, keep_legacy);
    }
    ui.hold_send = jstr(m, "hold") && !strcmp(jstr(m, "hold"), "send");
    switch (s) {
    case SCR_DIAL:   apply_dial(m); break;
    case SCR_GAUGES: apply_gauges(m); update_clocks(); break;
    case SCR_RING:   apply_ring(m); break;
    case SCR_LIST:   apply_list(m); break;
    case SCR_CANVAS:
        if (!same || has(m, "elements")) apply_canvas(m);
        break;
    default:         apply_card(m); break;
    }
    if (!same) ui.last_input = now();
}

void ui_on_message(const char *type, void *msg)
{
    const cJSON *m = (const cJSON *)msg;
    double v;
    const char *id = jstr(m, "id");
    bool mine = id && !strcmp(id, ui.ctx);
    if (!strcmp(type, "state")) {
        msg_state(m);
    } else if (!strcmp(type, "show")) {
        msg_show(m);
    } else if (!strcmp(type, "patch")) {
        if (ui.screen == SCR_CANVAS && mine) apply_patch(m);
    } else if (!strcmp(type, "close")) {
        if (ui.screen != SCR_DECK && ui.screen != SCR_CLOCK && (!id || mine)) show_deck();
    } else if (!strcmp(type, "popup")) {
        apply_popup(m);
    } else if (!strcmp(type, "popup_close")) {
        popup_hide(false);
    } else if (!strcmp(type, "dial")) {              // 1.x
        if (ui.screen == SCR_DIAL && mine) apply_dial(m);
    } else if (!strcmp(type, "stats")) {             // 1.x
        if (ui.screen == SCR_GAUGES) apply_gauges(m);
    } else if (!strcmp(type, "people")) {            // 1.x
        if (ui.screen == SCR_RING && mine) apply_ring(m);
    } else if (!strcmp(type, "toast")) {
        const char *t = jstr(m, "text");
        toast(t ? t : "", jnum(m, "ms", &v) ? (uint32_t)v : 1800);
    } else if (!strcmp(type, "time")) {
        double tz = 0;
        jnum(m, "tz", &tz);
        if (jnum(m, "epoch", &v)) {
            ui.time_base_ms = (int64_t)((v + tz * 60.0) * 1000.0) - (int64_t)now();
            ui.time_known = true;
            update_clocks();
        }
    } else if (!strcmp(type, "cfg")) {
        if (jnum(m, "bright", &v)) ui.bright = (int)v;
        if (jnum(m, "dim", &v)) ui.dim = (int)v;
        if (jnum(m, "idle", &v)) ui.idle_s = (int)v;
        const cJSON *inv = cJSON_GetObjectItemCaseSensitive(m, "invert");
        if (cJSON_IsBool(inv)) ui.invert = cJSON_IsTrue(inv);
        if (!ui.dimmed && ui.hal->set_brightness) ui.hal->set_brightness(ui.bright);
    } else if (!strcmp(type, "wake")) {
        wake();
    } else if (!strcmp(type, "goto")) {
        const char *pid = jstr(m, "page");
        int idx = pid ? deck_find_page(ui.deck, pid) : -1;
        if (idx >= 0) {
            if (ui.screen != SCR_DECK) show_deck();
            if (idx != ui.page) open_page(idx, true);
        }
    }
}

/* ------------------------------------------------------------------ public */

void ui_deck_changed(void)
{
    int keep = deck_find_page(ui.deck, ui.page_id);
    if (keep < 0) keep = 0;
    ui.depth = 0;
    for (int i = 0; i < DECK_MAX_PAGES; i++) ui.sel[i] = -1;
    free_deck_icons();
    ui.page = keep;
    copy_txt(ui.page_id, sizeof(ui.page_id), cur_page()->id);
    ui.sel[keep] = first_tile(cur_page());
    if (ui.screen != SCR_DECK) {
        show_deck();
    } else {
        render_deck();
    }
    if (ui.sel[keep] >= 0) move_arc(ui.ring_arc, &ui.arc_angle, deck_arc_cb, ui.sel[keep], false);
}

void ui_icon_changed(const char *name)
{
    icon_cache_t *all[] = {&ui.extra, &ui.pp_icon};
    for (size_t i = 0; i < 2; i++) {
        if (all[i]->loaded && !strcmp(all[i]->name, name)) icon_free(all[i]);
    }
    for (int i = 0; i < DECK_SLOTS; i++) {
        if (ui.icons[i].loaded && !strcmp(ui.icons[i].name, name)) icon_free(&ui.icons[i]);
    }
    for (int i = 0; i < TPL_ICONS; i++) {
        if (ui.tpl_icons[i].loaded && !strcmp(ui.tpl_icons[i].name, name)) icon_free(&ui.tpl_icons[i]);
    }
    switch (ui.screen) {
    case SCR_DECK: render_deck(); break;
    case SCR_DIAL: render_dial(); break;
    case SCR_RING: render_ring(); break;
    case SCR_LIST: render_list(); break;
    default: break;      // card/canvas: the PC re-sends the screen after uploading
    }
}

void ui_init(const ui_hal_t *hal, deck_t *deck)
{
    memset(&ui, 0, sizeof(ui));
    ui.hal = hal;
    ui.deck = deck;
    ui.flash_slot = -1;
    ui.bright = 80;
    ui.dim = 8;
    ui.idle_s = 120;
    ui.g_clock = true;
    for (int i = 0; i < DECK_MAX_PAGES; i++) ui.sel[i] = -1;

    build_deck();
    build_dial();
    build_gauges();
    build_ring();
    build_list();
    build_card();
    ui.scr_canvas = mk_screen();
    build_clock();
    build_popup();

    ui.toast = plain(lv_layer_top());
    lv_obj_set_style_bg_color(ui.toast, hex(0x212121), 0);
    lv_obj_set_style_bg_opa(ui.toast, LV_OPA_90, 0);
    lv_obj_set_style_radius(ui.toast, 12, 0);
    lv_obj_set_style_pad_hor(ui.toast, 12, 0);
    lv_obj_set_style_pad_ver(ui.toast, 6, 0);
    lv_obj_set_style_border_color(ui.toast, hex(0x424242), 0);
    lv_obj_set_style_border_width(ui.toast, 1, 0);
    lv_obj_set_size(ui.toast, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
    lv_obj_align(ui.toast, LV_ALIGN_BOTTOM_MID, 0, -30);
    ui.toast_lbl = mk_label(ui.toast, F14, 0xFFFFFF, 0, 0);
    lv_obj_add_flag(ui.toast, LV_OBJ_FLAG_HIDDEN);

    ui.last_input = now();
    ui.page = 0;
    copy_txt(ui.page_id, sizeof(ui.page_id), cur_page()->id);
    ui.sel[0] = first_tile(cur_page());
    ui.screen = SCR_DECK;
    ui.connected = hal->pc_connected ? hal->pc_connected() : false;
    render_deck();
    if (ui.sel[0] >= 0) move_arc(ui.ring_arc, &ui.arc_angle, deck_arc_cb, ui.sel[0], false);
    lv_screen_load(ui.scr_deck);
    if (hal->set_brightness) hal->set_brightness(ui.bright);
    lv_timer_create(tick_cb, 20, NULL);
}
