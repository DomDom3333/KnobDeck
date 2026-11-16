// Desktop simulator: runs the real UI code (ui.c, deck.c) with LVGL and writes screenshots.
// Usage: ./sim <outdir>   (reads icons from ./icons/<name>.bin)
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "lvgl.h"
#include "cJSON.h"
#include "../main/deck.h"
#include "../main/ui.h"

LV_FONT_DECLARE(kd_font_36)
static uint32_t fake_ms = 1000;
static bool connected = true;
static uint16_t fb[240 * 240];
static const char *outdir = ".";
static deck_t deck;

static uint32_t tick_cb(void) { return fake_ms; }

static void flush_cb(lv_display_t *d, const lv_area_t *a, uint8_t *px)
{
    uint16_t *p = (uint16_t *)px;
    for (int y = a->y1; y <= a->y2; y++)
        for (int x = a->x1; x <= a->x2; x++) fb[y * 240 + x] = *p++;
    lv_display_flush_ready(d);
}

static void hal_send(const char *s) { printf("  -> PC: %s\n", s); }
static bool hal_conn(void) { return connected; }
static uint32_t hal_now(void) { return fake_ms; }
static void hal_bright(int p) { printf("  backlight %d%%\n", p); }

static bool hal_load_icon(const char *name, lv_image_dsc_t *out)
{
    char path[128];
    snprintf(path, sizeof(path), "icons/%s.bin", name);
    FILE *f = fopen(path, "rb");
    if (!f) return false;
    uint8_t hdr[8];
    if (fread(hdr, 1, 8, f) != 8 || memcmp(hdr, "KDI1", 4)) { fclose(f); return false; }
    int w = hdr[4] | hdr[5] << 8, h = hdr[6] | hdr[7] << 8;
    size_t n = (size_t)w * h * 3;
    uint8_t *buf = malloc(n);
    if (fread(buf, 1, n, f) != n) { free(buf); fclose(f); return false; }
    fclose(f);
    memset(out, 0, sizeof(*out));
    out->header.magic = LV_IMAGE_HEADER_MAGIC;
    out->header.cf = LV_COLOR_FORMAT_RGB565A8;
    out->header.w = w;
    out->header.h = h;
    out->header.stride = w * 2;
    out->data_size = n;
    out->data = buf;
    return true;
}

static void hal_free_icon(lv_image_dsc_t *d) { free((void *)d->data); }

static void run(uint32_t ms)
{
    for (uint32_t t = 0; t < ms; t += 5) {
        fake_ms += 5;
        lv_timer_handler();
    }
}

static void shot(const char *name)
{
    run(400);
    lv_obj_invalidate(lv_screen_active());
    lv_refr_now(NULL);
    char path[256];
    snprintf(path, sizeof(path), "%s/%s.ppm", outdir, name);
    FILE *f = fopen(path, "wb");
    fprintf(f, "P6 240 240 255\n");
    for (int i = 0; i < 240 * 240; i++) {
        uint16_t c = fb[i];
        uint8_t rgb[3] = {(uint8_t)((c >> 11) << 3), (uint8_t)(((c >> 5) & 0x3F) << 2), (uint8_t)((c & 0x1F) << 3)};
        fwrite(rgb, 1, 3, f);
    }
    fclose(f);
    printf("[shot] %s\n", name);
}

static void msg(const char *json)
{
    cJSON *m = cJSON_Parse(json);
    printf("  <- PC: %s\n", json);
    const char *t = cJSON_GetStringValue(cJSON_GetObjectItem(m, "t"));
    if (!strcmp(t, "layout")) {
        deck_parse(json, &deck);
        ui_deck_changed();
    } else {
        ui_on_message(t, m);
    }
    cJSON_Delete(m);
}

// input: knob turns, click, long press
static void turn(int d) { ui_input(d, false); run(250); }
static void click(void) { ui_input(0, true); run(60); ui_input(0, false); run(250); }
static void hold(void) { ui_input(0, true); run(800); ui_input(0, false); run(250); }

// Render every built-in symbol icon (white on black) for the PC settings window.
static void dump_symbols(void)
{
    lv_obj_t *scr = lv_screen_active();
    lv_obj_set_style_bg_color(scr, lv_color_black(), 0);
    lv_obj_t *l = lv_label_create(scr);
    lv_obj_set_style_text_font(l, &kd_font_36, 0);
    lv_obj_set_style_text_color(l, lv_color_white(), 0);
    lv_obj_center(l);
    for (int i = 0; i < ui_symbol_count(); i++) {
        const char *glyph, *name = ui_symbol_at(i, &glyph);
        lv_label_set_text(l, glyph);
        lv_obj_center(l);
        char n[64];
        snprintf(n, sizeof(n), "sym_%s", name);
        shot(n);
    }
}

int main(int argc, char **argv)
{
    if (argc > 1) outdir = argv[1];
    if (argc > 2 && !strcmp(argv[2], "symbols")) {
        lv_init();
        lv_tick_set_cb(tick_cb);
        lv_display_t *d = lv_display_create(240, 240);
        static uint16_t buf[240 * 240];
        lv_display_set_buffers(d, buf, NULL, sizeof(buf), LV_DISPLAY_RENDER_MODE_PARTIAL);
        lv_display_set_flush_cb(d, flush_cb);
        dump_symbols();
        return 0;
    }
    lv_init();
    lv_tick_set_cb(tick_cb);
    lv_display_t *d = lv_display_create(240, 240);
    static uint16_t buf[240 * 240];
    lv_display_set_buffers(d, buf, NULL, sizeof(buf), LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_flush_cb(d, flush_cb);

    deck_parse(deck_default_json(), &deck);
    static const ui_hal_t hal = {hal_send, hal_load_icon, hal_free_icon, hal_bright, hal_conn, hal_now};
    ui_init(&hal, &deck);
    shot("01_default");

    // a layout as the PC app would send it, with uploaded image icons
    const char *layout = "{\"t\":\"layout\",\"v\":\"abc\",\"pages\":[{\"id\":\"home\",\"title\":\"Main\",\"tiles\":["
        "{\"id\":\"play\",\"label\":\"Play/Pause\",\"icon\":\"sym:play\",\"color\":\"#1E88E5\"},"
        "{\"id\":\"next\",\"label\":\"Next track\",\"icon\":\"sym:next\",\"color\":\"#1E88E5\"},"
        "{\"id\":\"vol\",\"type\":\"dial\",\"label\":\"Volume\",\"icon\":\"sym:volume\",\"color\":\"#43A047\"},"
        "{\"id\":\"mic\",\"label\":\"Mic\",\"icon\":\"sym:audio\",\"color\":\"#E53935\"},"
        "{\"id\":\"stats\",\"type\":\"stats\",\"label\":\"PC Stats\",\"icon\":\"sym:charge\",\"color\":\"#FB8C00\"},"
        "{\"id\":\"apps\",\"type\":\"folder\",\"target\":\"apps\",\"label\":\"Apps\",\"icon\":\"sym:folder\",\"color\":\"#8E24AA\"},"
        "{\"id\":\"obs\",\"label\":\"OBS Scene\",\"icon\":\"img:obs\",\"color\":\"#37474F\"},"
        "{\"id\":\"voice\",\"type\":\"people\",\"label\":\"Discord\",\"icon\":\"img:discord\",\"color\":\"#5865F2\"}]},"
        "{\"id\":\"apps\",\"title\":\"Apps\",\"tiles\":["
        "{\"id\":\"back\",\"type\":\"back\",\"label\":\"Back\",\"icon\":\"sym:left\",\"color\":\"#455A64\"},"
        "{\"id\":\"chrome\",\"label\":\"Browser\",\"icon\":\"img:globe\",\"color\":\"#0277BD\"},"
        "{\"id\":\"code\",\"label\":\"VS Code\",\"icon\":\"txt:VS\",\"color\":\"#1565C0\"},"
        "null,"
        "{\"id\":\"calc\",\"label\":\"Calculator\",\"icon\":\"sym:list\",\"color\":\"#6D4C41\"},"
        "null,"
        "{\"id\":\"term\",\"label\":\"Terminal\",\"icon\":\"txt:>_\",\"color\":\"#212121\"}]}]}";
    msg(layout);

    msg("{\"t\":\"time\",\"epoch\":1763316000,\"tz\":60}");
    shot("02_layout");

    turn(3);                // -> mic
    msg("{\"t\":\"state\",\"id\":\"mic\",\"label\":\"Mic muted\",\"icon\":\"sym:mute\",\"value\":\"muted\",\"color\":\"#B71C1C\"}");
    shot("03_mic_muted");

    turn(-1);               // -> volume
    click();
    msg("{\"t\":\"dial\",\"id\":\"vol\",\"value\":42,\"min\":0,\"max\":100,\"step\":2,\"text\":\"42%\"}");
    shot("04_dial");
    turn(5);
    shot("05_dial_turned");
    hold();

    turn(2);                // -> stats
    click();
    msg("{\"t\":\"stats\",\"rings\":[{\"label\":\"CPU\",\"value\":23,\"text\":\"23% 54C\"},"
        "{\"label\":\"GPU\",\"value\":61,\"text\":\"61% 67C\"},{\"label\":\"RAM\",\"value\":48,\"text\":\"48%\"}]}");
    shot("06_stats");
    click();

    turn(2);                // -> obs (image icon)
    shot("07_image_icon");
    turn(-1);               // -> apps folder
    click();
    shot("08_folder");
    hold();                 // back

    turn(2);                // apps -> voice tile (slot 7)
    msg("{\"t\":\"state\",\"id\":\"voice\",\"label\":\"4 in voice\",\"value\":\"Anna speaking\",\"color\":\"#2E7D32\"}");
    shot("11_voice_tile");
    click();
    msg("{\"t\":\"people\",\"id\":\"voice\",\"title\":\"Gaming Night\",\"items\":["
        "{\"id\":\"1\",\"name\":\"Anna\",\"icon\":\"img:av1\",\"color\":\"#E91E63\",\"speaking\":true},"
        "{\"id\":\"2\",\"name\":\"Dominik\",\"icon\":\"img:av2\",\"color\":\"#3F51B5\",\"muted\":true},"
        "{\"id\":\"3\",\"name\":\"Max\",\"icon\":\"txt:MA\",\"color\":\"#009688\"},"
        "{\"id\":\"4\",\"name\":\"Lena\",\"icon\":\"txt:LE\",\"color\":\"#FF9800\",\"deaf\":true}]}");
    shot("12_voice_screen");
    turn(1);
    msg("{\"t\":\"people\",\"id\":\"voice\",\"title\":\"Gaming Night\",\"items\":["
        "{\"id\":\"1\",\"name\":\"Anna\",\"icon\":\"img:av1\",\"color\":\"#E91E63\"},"
        "{\"id\":\"2\",\"name\":\"Dominik\",\"icon\":\"img:av2\",\"color\":\"#3F51B5\",\"speaking\":true},"
        "{\"id\":\"3\",\"name\":\"Max\",\"icon\":\"txt:MA\",\"color\":\"#009688\",\"speaking\":true},"
        "{\"id\":\"4\",\"name\":\"Lena\",\"icon\":\"txt:LE\",\"color\":\"#FF9800\",\"deaf\":true}]}");
    shot("13_voice_selected");
    click();
    msg("{\"t\":\"people\",\"id\":\"voice\",\"title\":\"\",\"text\":\"Not in a voice channel\",\"items\":[]}");
    shot("14_voice_empty");
    hold();

    printf("--- back test: PC switches to Apps, then Back tile, then hold\n");
    msg("{\"t\":\"goto\",\"page\":\"apps\"}");
    click();                // back tile is selected on Apps -> returns to home
    msg("{\"t\":\"goto\",\"page\":\"apps\"}");
    msg(layout);      // new layout while in a folder (history is cleared)
    hold();                 // hold -> parent page (home)
    printf("--- end back test\n");


    printf("--- 2.0 generic screens\n");
    // any id works: the PC opens screens without a tile
    msg("{\"t\":\"show\",\"id\":\"spotify.queue\",\"tpl\":\"list\",\"title\":\"Up next\",\"items\":["
        "{\"id\":\"a\",\"text\":\"Bohemian Rhapsody\",\"sub\":\"Queen\",\"icon\":\"sym:spotify\",\"color\":\"#1DB954\"},"
        "{\"id\":\"b\",\"text\":\"Über den Wolken\",\"sub\":\"Reinhard Mey\",\"icon\":\"sym:spotify\",\"color\":\"#1DB954\"},"
        "{\"id\":\"c\",\"text\":\"Déjà vu\",\"sub\":\"Olivia Rodrigo\",\"value\":\"3:35\",\"icon\":\"sym:spotify\",\"color\":\"#1DB954\",\"checked\":true},"
        "{\"id\":\"d\",\"text\":\"Straße nach Süden\",\"sub\":\"Schäfer\",\"icon\":\"sym:spotify\",\"color\":\"#1DB954\"},"
        "{\"id\":\"e\",\"text\":\"Smells Like Teen Spirit\",\"sub\":\"Nirvana\",\"icon\":\"sym:spotify\",\"color\":\"#1DB954\"}]}");
    shot("20_list");
    turn(2);
    shot("21_list_turned");
    click();

    msg("{\"t\":\"show\",\"id\":\"weather\",\"tpl\":\"card\",\"icon\":\"sym:sun\",\"color\":\"#FFB300\","
        "\"title\":\"München 18°C\",\"text\":\"Sonnig, später leicht bewölkt. Wind 12 km/h\",\"footer\":\"updated 14:05\"}");
    shot("22_card");
    msg("{\"t\":\"show\",\"id\":\"render\",\"tpl\":\"card\",\"value\":\"73%\",\"color\":\"#4FC3F7\","
        "\"title\":\"Rendering\",\"text\":\"scene_04.blend · frame 219/300\",\"progress\":73,\"footer\":\"press to cancel\"}");
    shot("23_card_progress");

    msg("{\"t\":\"show\",\"id\":\"net\",\"tpl\":\"canvas\",\"elements\":["
        "{\"id\":\"ring\",\"type\":\"arc\",\"w\":228,\"width\":8,\"value\":64,\"color\":\"#7E57C2\"},"
        "{\"id\":\"ic\",\"type\":\"img\",\"icon\":\"sym:network\",\"y\":-70,\"size\":28,\"color\":\"#7E57C2\"},"
        "{\"id\":\"t\",\"type\":\"label\",\"text\":\"42.7 MB/s\",\"y\":-30,\"size\":28},"
        "{\"id\":\"s\",\"type\":\"label\",\"text\":\"download\",\"y\":-4,\"size\":12,\"color\":\"#9E9E9E\"},"
        "{\"id\":\"ch\",\"type\":\"chart\",\"y\":42,\"w\":150,\"h\":56,\"max\":60,\"color\":\"#7E57C2\","
        "\"points\":[5,12,30,28,41,38,50,44,39,42,47,43]},"
        "{\"id\":\"b\",\"type\":\"bar\",\"y\":86,\"w\":100,\"h\":6,\"value\":30,\"color\":\"#26A69A\"}]}");
    shot("24_canvas");
    msg("{\"t\":\"patch\",\"id\":\"net\",\"set\":[{\"id\":\"t\",\"text\":\"12.1 MB/s\"},{\"id\":\"ch\",\"push\":12},{\"id\":\"ring\",\"value\":20}]}");
    msg("{\"t\":\"patch\",\"id\":\"net\",\"set\":[{\"id\":\"ic\",\"hidden\":true}]}");
    run(50);
    msg("{\"t\":\"patch\",\"id\":\"net\",\"set\":[{\"id\":\"ic\",\"hidden\":false,\"color\":\"#FFB300\"},{\"id\":\"new\",\"type\":\"label\",\"text\":\"added\",\"y\":66,\"size\":12}]}");
    shot("25_canvas_patched");
    turn(1);

    msg("{\"t\":\"show\",\"id\":\"hue\",\"tpl\":\"ring\",\"title\":\"Lights\",\"items\":["
        "{\"id\":\"l1\",\"name\":\"Desk\",\"icon\":\"sym:lightbulb\",\"color\":\"#F9A825\",\"ring\":\"#FFFFFF\",\"status\":\"on · 80%\",\"status_color\":\"#FFD54F\"},"
        "{\"id\":\"l2\",\"name\":\"Ceiling\",\"icon\":\"sym:lightbulb\",\"color\":\"#424242\",\"status\":\"off\",\"status_color\":\"#9E9E9E\"},"
        "{\"id\":\"l3\",\"name\":\"TV strip\",\"icon\":\"sym:tv\",\"color\":\"#AB47BC\",\"badge\":\"sym:magic\",\"badge_color\":\"#7B1FA2\",\"status\":\"scene: Movie\",\"status_color\":\"#CE93D8\"}]}");
    shot("26_ring");
    turn(1);
    click();

    msg("{\"t\":\"show\",\"id\":\"vol2\",\"tpl\":\"dial\",\"label\":\"Spotify\",\"icon\":\"sym:spotify\",\"color\":\"#1DB954\",\"value\":30,\"text\":\"30%\"}");
    shot("27_generic_dial");
    turn(2);
    msg("{\"t\":\"show\",\"id\":\"gpu\",\"tpl\":\"gauges\",\"title\":\"GPU\",\"center\":\"67°C\",\"rings\":["
        "{\"label\":\"Load\",\"value\":88,\"text\":\"88%\",\"color\":\"#76FF03\"},{\"label\":\"VRAM\",\"value\":40,\"text\":\"6.4 GB\"}]}");
    shot("28_gauges");

    msg("{\"t\":\"popup\",\"id\":\"mail\",\"icon\":\"sym:envelope\",\"color\":\"#E53935\",\"title\":\"Neue E-Mail\",\"text\":\"Jürgen: Grüße aus Köln – läuft der Server?\",\"ms\":0}");
    shot("29_popup");
    click();                // dismiss
    msg("{\"t\":\"close\",\"id\":\"gpu\"}");
    shot("30_closed");

    // a "screen" tile: the PC decides what to show
    msg("{\"t\":\"layout\",\"v\":\"s2\",\"pages\":[{\"id\":\"home\",\"title\":\"Main\",\"tiles\":["
        "{\"id\":\"todo\",\"type\":\"screen\",\"label\":\"To-do\",\"icon\":\"sym:check_circle\",\"color\":\"#00897B\"},"
        "{\"id\":\"x\",\"label\":\"Steam\",\"icon\":\"sym:steam\",\"color\":\"#1B2838\"},"
        "{\"id\":\"y\",\"label\":\"Twitch\",\"icon\":\"sym:twitch\",\"color\":\"#9146FF\"},"
        "{\"id\":\"z\",\"label\":\"GitHub\",\"icon\":\"sym:github\",\"color\":\"#333333\"},"
        "{\"id\":\"w\",\"label\":\"Gamepad\",\"icon\":\"sym:gamepad\",\"color\":\"#D84315\"}]}]}");
    shot("31_new_icons");
    click();
    shot("32_screen_tile_loading");
    msg("{\"t\":\"show\",\"id\":\"todo\",\"tpl\":\"list\",\"title\":\"To-do\",\"hold\":\"send\",\"items\":["
        "{\"id\":\"1\",\"text\":\"Einkaufen\",\"icon\":\"sym:cart\",\"checked\":true},"
        "{\"id\":\"2\",\"text\":\"Steuererklärung\",\"icon\":\"sym:briefcase\"}]}");
    shot("33_screen_tile_list");
    hold();                 // hold:"send" -> PC gets it
    msg("{\"t\":\"close\"}");
    msg(layout);
    printf("--- end 2.0\n");

    connected = false;
    run(100);
    click();
    shot("09_offline");
    connected = true;

    run(125000);            // idle -> clock screensaver
    shot("10_clock");
    return 0;
}
