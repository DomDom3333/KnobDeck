// KnobDeck firmware for the Espressif ESP32-C3-LCDkit
// - round 240x240 GC9A01 display + rotary encoder with push (via the official BSP)
// - talks to the KnobDeck PC app over the USB port (USB Serial/JTAG), one JSON message per line
#include <dirent.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_spiffs.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "esp_mac.h"
#include "driver/gpio.h"
#include "driver/usb_serial_jtag.h"
#include "mbedtls/base64.h"
#include "cJSON.h"
#include "bsp/esp-bsp.h"
#include "deck.h"
#include "ui.h"
#include "hw.h"

#define FW_VERSION   "2.1.0"
#define PROTO        2
#define ICON_MAX     128                 // largest image side (px)
#define HEAP_RESERVE (40 * 1024)        // never let images eat the last of the RAM
#define MOUNT        BSP_SPIFFS_MOUNT_POINT
#define LAYOUT_PATH  MOUNT "/layout.json"
#define ICON_PREFIX  "i_"
#define FILE_PREFIX  "f_"                // general files from the PC (e.g. its full configuration)
#define FCHUNK       2048                // bytes per fdata reply
#define RX_LINE_MAX     (24 * 1024)
#define ALIVE_US     (6 * 1000 * 1000)   // PC pings every 2 s

static const char *TAG = "knobdeck";
static deck_t s_deck;
static volatile int64_t s_last_rx_us = -ALIVE_US;
static SemaphoreHandle_t s_tx_lock;
static lv_indev_read_cb_t s_orig_read;
static QueueHandle_t s_btn_q;          // debounced press/release edges of the knob button
static bool s_btn_down;

/* ------------------------------------------------------------------ USB link */

static void send_line(const char *s)
{
    if (!usb_serial_jtag_is_connected()) return;
    xSemaphoreTake(s_tx_lock, portMAX_DELAY);
    usb_serial_jtag_write_bytes(s, strlen(s), pdMS_TO_TICKS(30));
    usb_serial_jtag_write_bytes("\n", 1, pdMS_TO_TICKS(30));
    xSemaphoreGive(s_tx_lock);
}

static void send_json(cJSON *m)
{
    char *s = cJSON_PrintUnformatted(m);
    if (s) {
        send_line(s);
        cJSON_free(s);
    }
    cJSON_Delete(m);
}

static void add_file_list(cJSON *m);

static void send_hello(void)
{
    cJSON *m = cJSON_CreateObject();
    cJSON_AddStringToObject(m, "t", "hello");
    cJSON_AddStringToObject(m, "fw", FW_VERSION);
    cJSON_AddStringToObject(m, "board", "esp32-c3-lcdkit");
    cJSON_AddStringToObject(m, "v", s_deck.version);
    cJSON *icons = cJSON_AddArrayToObject(m, "icons");
    DIR *dir = opendir(MOUNT);
    if (dir) {
        struct dirent *e;
        while ((e = readdir(dir))) {
            if (!strncmp(e->d_name, ICON_PREFIX, 2)) cJSON_AddItemToArray(icons, cJSON_CreateString(e->d_name + 2));
        }
        closedir(dir);
    }
    size_t total = 0, used = 0;
    esp_spiffs_info(CONFIG_BSP_SPIFFS_PARTITION_LABEL, &total, &used);
    cJSON_AddNumberToObject(m, "free", (double)(total - used));
    // what this firmware can do, so PC plugins can adapt instead of needing new firmware
    cJSON_AddNumberToObject(m, "proto", PROTO);
    static const char *CAPS[] = {"screens", "popup", "led", "beep", "ir", "ir_learn", "utf8", "click", "files"};
    cJSON *caps = cJSON_AddArrayToObject(m, "caps");
    for (size_t i = 0; i < sizeof(CAPS) / sizeof(CAPS[0]); i++) cJSON_AddItemToArray(caps, cJSON_CreateString(CAPS[i]));
    static const char *TPL[] = {"dial", "gauges", "ring", "list", "card", "canvas"};
    cJSON *tpl = cJSON_AddArrayToObject(m, "tpl");
    for (size_t i = 0; i < sizeof(TPL) / sizeof(TPL[0]); i++) cJSON_AddItemToArray(tpl, cJSON_CreateString(TPL[i]));
    cJSON_AddNumberToObject(m, "img_max", ICON_MAX);
    cJSON_AddNumberToObject(m, "syms", ui_symbol_count());
    cJSON_AddNumberToObject(m, "heap", (double)esp_get_free_heap_size());
    uint8_t mac[6] = {0};
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    char id[13];
    snprintf(id, sizeof(id), "%02x%02x%02x%02x%02x%02x", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    cJSON_AddStringToObject(m, "id", id);
    add_file_list(m);
    send_json(m);
}

static void send_ack(const char *what, const char *name, int seq, bool ok)
{
    cJSON *m = cJSON_CreateObject();
    cJSON_AddStringToObject(m, "t", "ack");
    cJSON_AddStringToObject(m, "what", what);
    if (name) cJSON_AddStringToObject(m, "n", name);
    if (seq >= 0) cJSON_AddNumberToObject(m, "i", seq);
    cJSON_AddBoolToObject(m, "ok", ok);
    send_json(m);
}

/* ------------------------------------------------------------------ storage */

static bool valid_icon_name(const char *n)
{
    size_t len = n ? strlen(n) : 0;
    if (len == 0 || len > 16) return false;
    for (size_t i = 0; i < len; i++) {
        char c = n[i];
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'z') || c == '_' || c == '-')) return false;
    }
    return true;
}

static void icon_path(char *out, size_t n, const char *name)
{
    snprintf(out, n, "%.16s/" ICON_PREFIX "%.16s", MOUNT, name);
}

static bool hal_load_icon(const char *name, lv_image_dsc_t *out)
{
    if (!valid_icon_name(name)) return false;
    char path[48];
    icon_path(path, sizeof(path), name);
    FILE *f = fopen(path, "rb");
    if (!f) return false;
    uint8_t hdr[8];
    bool ok = fread(hdr, 1, 8, f) == 8 && !memcmp(hdr, "KDI1", 4);
    int w = hdr[4] | hdr[5] << 8, h = hdr[6] | hdr[7] << 8;
    ok = ok && w > 0 && h > 0 && w <= ICON_MAX && h <= ICON_MAX;
    size_t len = (size_t)w * h * 3;
    ok = ok && heap_caps_get_largest_free_block(MALLOC_CAP_8BIT) > len + HEAP_RESERVE;
    uint8_t *buf = ok ? malloc(len) : NULL;
    ok = buf && fread(buf, 1, len, f) == len;
    fclose(f);
    if (!ok) {
        free(buf);
        return false;
    }
    memset(out, 0, sizeof(*out));
    out->header.magic = LV_IMAGE_HEADER_MAGIC;
    out->header.cf = LV_COLOR_FORMAT_RGB565A8;
    out->header.w = w;
    out->header.h = h;
    out->header.stride = w * 2;
    out->data_size = len;
    out->data = buf;
    return true;
}

static void hal_free_icon(lv_image_dsc_t *d)
{
    free((void *)d->data);
    d->data = NULL;
}

static bool save_file(const char *path, const char *data, size_t len)
{
    char tmp[64];
    snprintf(tmp, sizeof(tmp), "%.60s~", path);
    FILE *f = fopen(tmp, "wb");
    if (!f) return false;
    bool ok = fwrite(data, 1, len, f) == len;
    fclose(f);
    if (!ok) return false;
    remove(path);
    return rename(tmp, path) == 0;
}

static char *read_file(const char *path)
{
    struct stat st;
    if (stat(path, &st) || st.st_size <= 0 || st.st_size > RX_LINE_MAX) return NULL;
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    char *buf = malloc(st.st_size + 1);
    if (buf && fread(buf, 1, st.st_size, f) == (size_t)st.st_size) {
        buf[st.st_size] = 0;
    } else {
        free(buf);
        buf = NULL;
    }
    fclose(f);
    return buf;
}

/* ------------------------------------------------------------------ messages from the PC */

static void handle_layout(const char *line, size_t len)
{
    deck_t *tmp = malloc(sizeof(deck_t));
    bool ok = tmp && deck_parse(line, tmp);
    if (ok) {
        save_file(LAYOUT_PATH, line, len);
        bsp_display_lock(0);
        memcpy(&s_deck, tmp, sizeof(s_deck));
        ui_deck_changed();
        bsp_display_unlock();
        ESP_LOGI(TAG, "layout %s: %d pages", s_deck.version, s_deck.page_count);
    }
    free(tmp);
    send_ack("layout", NULL, -1, ok);
}

static void handle_icon(cJSON *m)
{
    const char *name = cJSON_GetStringValue(cJSON_GetObjectItem(m, "n"));
    const char *b64 = cJSON_GetStringValue(cJSON_GetObjectItem(m, "d"));
    const cJSON *seq = cJSON_GetObjectItem(m, "i");
    int i = cJSON_IsNumber(seq) ? seq->valueint : -1;
    bool last = cJSON_IsTrue(cJSON_GetObjectItem(m, "last"));
    if (!valid_icon_name(name) || !b64 || i < 0) {
        send_ack("icon", name, i, false);
        return;
    }
    static uint8_t raw[2048];
    size_t n = 0;
    bool ok = mbedtls_base64_decode(raw, sizeof(raw), &n, (const uint8_t *)b64, strlen(b64)) == 0;
    char path[48], part[64];
    icon_path(path, sizeof(path), name);
    snprintf(part, sizeof(part), "%.60s~", path);
    if (ok) {
        FILE *f = fopen(part, i == 0 ? "wb" : "ab");
        ok = f && fwrite(raw, 1, n, f) == n;
        if (f) fclose(f);
    }
    if (ok && last) {
        remove(path);
        ok = rename(part, path) == 0;
        bsp_display_lock(0);
        ui_icon_changed(name);
        bsp_display_unlock();
    }
    send_ack("icon", name, i, ok);
}

static void handle_icon_gc(cJSON *m)
{
    const cJSON *keep = cJSON_GetObjectItem(m, "keep");
    DIR *dir = opendir(MOUNT);
    if (!dir) return;
    static char names[128][20];
    int count = 0;
    struct dirent *e;
    while ((e = readdir(dir)) && count < 128) {
        if (strncmp(e->d_name, ICON_PREFIX, 2)) continue;
        bool kept = false;
        const cJSON *k;
        cJSON_ArrayForEach(k, keep) {
            if (cJSON_IsString(k) && !strcmp(k->valuestring, e->d_name + 2)) kept = true;
        }
        if (!kept) snprintf(names[count++], sizeof(names[0]), "%.16s", e->d_name + 2);
    }
    closedir(dir);
    char path[48];
    for (int i = 0; i < count; i++) {
        icon_path(path, sizeof(path), names[i]);
        remove(path);
    }
    send_ack("icon_gc", NULL, count, true);
}

/* ------------------------------------------------------------------ file store */

static void file_path(char *out, size_t n, const char *name, const char *suffix)
{
    snprintf(out, n, "%.16s/" FILE_PREFIX "%.16s%.4s", MOUNT, name, suffix);
}

// "files":{"<name>":{"size":1234,"tag":"<tag>"},...}
static void add_file_list(cJSON *m)
{
    cJSON *files = cJSON_AddObjectToObject(m, "files");
    DIR *dir = opendir(MOUNT);
    if (!dir) return;
    struct dirent *e;
    while ((e = readdir(dir))) {
        const char *n = e->d_name;
        size_t len = strlen(n);
        if (strncmp(n, FILE_PREFIX, 2) || len < 3 || len > 18 || strchr(n, '.') || n[len - 1] == '~') continue;
        char path[48], tag[64] = "";
        struct stat st;
        file_path(path, sizeof(path), n + 2, "");
        if (stat(path, &st)) continue;
        file_path(path, sizeof(path), n + 2, ".tag");
        FILE *f = fopen(path, "rb");
        if (f) {
            size_t r = fread(tag, 1, sizeof(tag) - 1, f);
            tag[r] = 0;
            fclose(f);
        }
        cJSON *o = cJSON_AddObjectToObject(files, n + 2);
        cJSON_AddNumberToObject(o, "size", (double)st.st_size);
        cJSON_AddStringToObject(o, "tag", tag);
    }
    closedir(dir);
}

// {"t":"fput","n":"config","i":0,"last":false,"d":"<base64>","tag":"<tag, with the last chunk>"}
static void handle_fput(cJSON *m)
{
    const char *name = cJSON_GetStringValue(cJSON_GetObjectItem(m, "n"));
    const char *b64 = cJSON_GetStringValue(cJSON_GetObjectItem(m, "d"));
    const char *tag = cJSON_GetStringValue(cJSON_GetObjectItem(m, "tag"));
    const cJSON *seq = cJSON_GetObjectItem(m, "i");
    int i = cJSON_IsNumber(seq) ? seq->valueint : -1;
    bool last = cJSON_IsTrue(cJSON_GetObjectItem(m, "last"));
    if (!valid_icon_name(name) || !b64 || i < 0) {
        send_ack("fput", name, i, false);
        return;
    }
    static uint8_t raw[2048];
    size_t n = 0;
    bool ok = mbedtls_base64_decode(raw, sizeof(raw), &n, (const uint8_t *)b64, strlen(b64)) == 0;
    char path[48], part[48];
    file_path(path, sizeof(path), name, "");
    file_path(part, sizeof(part), name, "~");
    if (ok) {
        FILE *f = fopen(part, i == 0 ? "wb" : "ab");
        ok = f && fwrite(raw, 1, n, f) == n;
        if (f) fclose(f);
    }
    if (ok && last) {
        remove(path);
        ok = rename(part, path) == 0;
        file_path(part, sizeof(part), name, ".tag");
        FILE *f = fopen(part, "wb");
        if (f) {
            if (tag) fwrite(tag, 1, strnlen(tag, 63), f);
            fclose(f);
        }
    }
    send_ack("fput", name, i, ok);
}

// {"t":"fget","n":"config","i":0}  or  {"t":"fget","icon":"<name>","i":0}
// -> {"t":"fdata","n":"<name>","i":0,"size":12345,"last":false,"d":"<base64>"}  (one chunk per request)
static void handle_fget(cJSON *m)
{
    const char *name = cJSON_GetStringValue(cJSON_GetObjectItem(m, "n"));
    const char *icon = cJSON_GetStringValue(cJSON_GetObjectItem(m, "icon"));
    const cJSON *seq = cJSON_GetObjectItem(m, "i");
    int i = cJSON_IsNumber(seq) ? seq->valueint : 0;
    const char *which = icon ? icon : name;
    cJSON *r = cJSON_CreateObject();
    cJSON_AddStringToObject(r, "t", "fdata");
    cJSON_AddStringToObject(r, icon ? "icon" : "n", which ? which : "");
    cJSON_AddNumberToObject(r, "i", i);
    char path[48];
    FILE *f = NULL;
    if (valid_icon_name(which) && i >= 0) {
        if (icon) icon_path(path, sizeof(path), which);
        else file_path(path, sizeof(path), which, "");
        f = fopen(path, "rb");
    }
    if (!f) {
        cJSON_AddBoolToObject(r, "ok", false);
        send_json(r);
        return;
    }
    static uint8_t raw[FCHUNK];
    static char b64[FCHUNK * 4 / 3 + 8];
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, (long)i * FCHUNK, SEEK_SET);
    size_t n = fread(raw, 1, FCHUNK, f);
    fclose(f);
    size_t out = 0;
    mbedtls_base64_encode((unsigned char *)b64, sizeof(b64), &out, raw, n);
    b64[out] = 0;
    cJSON_AddBoolToObject(r, "ok", true);
    cJSON_AddNumberToObject(r, "size", (double)size);
    cJSON_AddBoolToObject(r, "last", (long)i * FCHUNK + (long)n >= size);
    cJSON_AddStringToObject(r, "d", b64);
    send_json(r);
}

static void handle_line(char *line, size_t len)
{
    while (len && (line[len - 1] == '\r' || line[len - 1] == ' ')) line[--len] = 0;
    if (len < 2 || line[0] != '{') return;
    s_last_rx_us = esp_timer_get_time();

    cJSON *m = cJSON_ParseWithLength(line, len);
    if (!m) return;
    const char *t = cJSON_GetStringValue(cJSON_GetObjectItem(m, "t"));
    if (!t) {
        cJSON_Delete(m);
        return;
    }
    if (!strcmp(t, "layout")) {
        cJSON_Delete(m);            // parse again into the deck model; keep peak RAM low
        handle_layout(line, len);
        return;
    }
    if (!strcmp(t, "hello?")) {
        send_hello();
    } else if (!strcmp(t, "ping")) {
        // keep-alive only
    } else if (!strcmp(t, "icon")) {
        handle_icon(m);
    } else if (!strcmp(t, "icon_gc")) {
        handle_icon_gc(m);
    } else if (!strcmp(t, "fput")) {
        handle_fput(m);
    } else if (!strcmp(t, "fget")) {
        handle_fget(m);
    } else if (!strcmp(t, "reboot")) {
        cJSON_Delete(m);
        vTaskDelay(pdMS_TO_TICKS(100));
        esp_restart();
    } else if (!hw_on_message(t, m)) {
        bsp_display_lock(0);
        ui_on_message(t, m);
        bsp_display_unlock();
    }
    cJSON_Delete(m);
}

static void rx_task(void *arg)
{
    char *line = malloc(RX_LINE_MAX);
    size_t len = 0;
    bool overflow = false, was_connected = false;
    uint8_t chunk[256];
    for (;;) {
        bool c = usb_serial_jtag_is_connected();
        if (c && !was_connected) send_hello();          // cable plugged in / PC woke up
        was_connected = c;

        int n = usb_serial_jtag_read_bytes(chunk, sizeof(chunk), pdMS_TO_TICKS(20));
        for (int i = 0; i < n; i++) {
            char ch = (char)chunk[i];
            if (ch == '\n') {
                if (!overflow) {
                    line[len] = 0;
                    handle_line(line, len);
                }
                len = 0;
                overflow = false;
            } else if (len < RX_LINE_MAX - 1) {
                line[len++] = ch;
            } else {
                overflow = true;
            }
        }
    }
}

/* ------------------------------------------------------------------ HAL for the UI */

static bool hal_connected(void)
{
    return usb_serial_jtag_is_connected() && esp_timer_get_time() - s_last_rx_us < ALIVE_US;
}

static uint32_t hal_now(void) { return (uint32_t)(esp_timer_get_time() / 1000); }

static void hal_brightness(int pct) { bsp_display_brightness_set(pct); }

// The BSP feeds the knob into LVGL as an encoder; we take the turns from there and drive the UI ourselves.
// The button is sampled separately: LVGL only sees its level when it gets around to reading, so a quick
// click during a screen redraw could be missed. Every debounced edge is queued instead.
static void encoder_read(lv_indev_t *indev, lv_indev_data_t *data)
{
    s_orig_read(indev, data);
    if (data->enc_diff) {
        ui_input(data->enc_diff, s_btn_down);
        hw_click();
    }
    data->enc_diff = 0;
    data->state = LV_INDEV_STATE_RELEASED;
}

static void button_sample(void *arg)        // esp_timer, every 5 ms
{
    static int stable = 0, count = 0;
    int level = gpio_get_level(BSP_ENCODER_PRESS) == 0;      // active low
    if (level == stable) {
        count = 0;
        return;
    }
    if (++count >= 3) {                       // 15 ms steady -> real edge
        stable = level;
        count = 0;
        bool pressed = level;
        xQueueSend(s_btn_q, &pressed, 0);
    }
}

static void button_drain(lv_timer_t *t)     // LVGL task: feed the edges in order
{
    bool pressed;
    while (xQueueReceive(s_btn_q, &pressed, 0) == pdTRUE) {
        s_btn_down = pressed;
        ui_input(0, pressed);
    }
}

static const ui_hal_t s_hal = {
    .send = send_line,
    .load_icon = hal_load_icon,
    .free_icon = hal_free_icon,
    .set_brightness = hal_brightness,
    .pc_connected = hal_connected,
    .now_ms = hal_now,
};

void app_main(void)
{
    s_tx_lock = xSemaphoreCreateMutex();

    usb_serial_jtag_driver_config_t usb_cfg = {.rx_buffer_size = 4096, .tx_buffer_size = 4096};
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&usb_cfg));

    hw_init(send_line);
    if (bsp_spiffs_mount() != ESP_OK) ESP_LOGE(TAG, "storage not mounted");

    char *saved = read_file(LAYOUT_PATH);
    if (!saved || !deck_parse(saved, &s_deck)) deck_parse(deck_default_json(), &s_deck);
    free(saved);

    lv_display_t *disp = bsp_display_start();
    (void)disp;
    bsp_display_backlight_on();

    bsp_display_lock(0);
    ui_init(&s_hal, &s_deck);
    lv_indev_t *enc = bsp_display_get_input_dev();
    s_orig_read = lv_indev_get_read_cb(enc);
    lv_indev_set_read_cb(enc, encoder_read);
    s_btn_q = xQueueCreate(16, sizeof(bool));
    lv_timer_create(button_drain, 10, NULL);
    bsp_display_unlock();
    const esp_timer_create_args_t btn_timer = {.callback = button_sample, .name = "knob_btn"};
    esp_timer_handle_t th;
    ESP_ERROR_CHECK(esp_timer_create(&btn_timer, &th));
    ESP_ERROR_CHECK(esp_timer_start_periodic(th, 5000));

    xTaskCreate(rx_task, "usb_rx", 6144, NULL, 4, NULL);
    ESP_LOGI(TAG, "KnobDeck %s ready, free heap %u", FW_VERSION, (unsigned)esp_get_free_heap_size());
}
