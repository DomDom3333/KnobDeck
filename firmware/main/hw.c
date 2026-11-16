// KnobDeck hardware services: RGB LED (WS2812 on GPIO8), speaker (PDM on GPIO3), IR send + learn (RMT)
#include "hw.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "driver/rmt_rx.h"
#include "driver/rmt_tx.h"
#include "led_strip.h"
#include "esp_codec_dev.h"
#include "cJSON.h"
#include "bsp/esp-bsp.h"
#include "deck.h"

static const char *TAG = "hw";
static hw_send_fn s_send;

static int jint(const cJSON *m, const char *k, int def)
{
    const cJSON *i = cJSON_GetObjectItemCaseSensitive(m, k);
    return cJSON_IsNumber(i) ? (int)i->valuedouble : def;
}

/* ------------------------------------------------------------------ RGB LED */

typedef enum { FX_SOLID, FX_BLINK, FX_PULSE, FX_RAINBOW } led_fx_t;

static led_strip_handle_t s_strip;
static esp_timer_handle_t s_led_timer;
static struct {
    uint8_t r, g, b;
    int bright;            // 0..100
    led_fx_t fx;
    int period_ms;
    int64_t t0_us, until_us;   // until: 0 = forever
} s_led;

static void led_write(int r, int g, int b)
{
    if (!s_strip) return;
    led_strip_set_pixel(s_strip, 0, r, g, b);
    led_strip_refresh(s_strip);
}

static void hsv(int h, uint8_t *r, uint8_t *g, uint8_t *b)
{
    h %= 360;
    int x = 255 * (60 - abs(h % 120 - 60)) / 60;   // rising/falling edge
    int rgb[6][3] = {{255, x, 0}, {x, 255, 0}, {0, 255, x}, {0, x, 255}, {x, 0, 255}, {255, 0, x}};
    *r = rgb[h / 60][0];
    *g = rgb[h / 60][1];
    *b = rgb[h / 60][2];
}

static void led_tick(void *arg)
{
    (void)arg;
    int64_t t = esp_timer_get_time();
    if (s_led.until_us && t >= s_led.until_us) {
        esp_timer_stop(s_led_timer);
        led_write(0, 0, 0);
        return;
    }
    int period = s_led.period_ms > 50 ? s_led.period_ms : 1000;
    int phase = (int)(((t - s_led.t0_us) / 1000) % period);
    int k = s_led.bright;                  // 0..100
    uint8_t r = s_led.r, g = s_led.g, b = s_led.b;
    switch (s_led.fx) {
    case FX_BLINK:
        if (phase >= period / 2) k = 0;
        break;
    case FX_PULSE:
        k = (int)(k * (0.5 - 0.5 * cos(2 * M_PI * phase / period)));
        break;
    case FX_RAINBOW:
        hsv(phase * 360 / period, &r, &g, &b);
        break;
    default:
        break;
    }
    led_write(r * k / 100, g * k / 100, b * k / 100);
}

// {"t":"led","color":"#RRGGBB","effect":"solid|blink|pulse|rainbow|off","period":ms,"bright":0-100,"ms":duration}
static void led_msg(const cJSON *m)
{
    if (!s_strip) return;
    esp_timer_stop(s_led_timer);
    const char *fx = cJSON_GetStringValue(cJSON_GetObjectItemCaseSensitive(m, "effect"));
    uint32_t c = deck_parse_color(cJSON_GetStringValue(cJSON_GetObjectItemCaseSensitive(m, "color")), 0xFFFFFF);
    s_led.r = c >> 16;
    s_led.g = (c >> 8) & 0xFF;
    s_led.b = c & 0xFF;
    s_led.bright = jint(m, "bright", 30);
    if (s_led.bright < 0) s_led.bright = 0;
    if (s_led.bright > 100) s_led.bright = 100;
    s_led.period_ms = jint(m, "period", 1000);
    s_led.fx = !fx ? FX_SOLID : !strcmp(fx, "blink") ? FX_BLINK : !strcmp(fx, "pulse") ? FX_PULSE
             : !strcmp(fx, "rainbow") ? FX_RAINBOW : FX_SOLID;
    if ((fx && !strcmp(fx, "off")) || c == 0) {
        led_write(0, 0, 0);
        return;
    }
    s_led.t0_us = esp_timer_get_time();
    int ms = jint(m, "ms", 0);
    s_led.until_us = ms > 0 ? s_led.t0_us + (int64_t)ms * 1000 : 0;
    led_tick(NULL);
    if (s_led.fx != FX_SOLID || ms > 0) esp_timer_start_periodic(s_led_timer, 20 * 1000);
}

static void led_init(void)
{
    led_strip_config_t cfg = {
        .strip_gpio_num = BSP_LED_RGB_IO,
        .max_leds = 1,
        .led_model = LED_MODEL_WS2812,
        .color_component_format = LED_STRIP_COLOR_COMPONENT_FMT_GRB,
    };
    led_strip_rmt_config_t rmt = {
        .clk_src = RMT_CLK_SRC_DEFAULT,
        .resolution_hz = 10 * 1000 * 1000,
        .mem_block_symbols = 48,
    };
    if (led_strip_new_rmt_device(&cfg, &rmt, &s_strip) != ESP_OK) {
        ESP_LOGW(TAG, "RGB LED not available");
        s_strip = NULL;
        return;
    }
    led_write(0, 0, 0);
    const esp_timer_create_args_t ta = {.callback = led_tick, .name = "led"};
    esp_timer_create(&ta, &s_led_timer);
}

/* ------------------------------------------------------------------ speaker */

#define SR          22050
#define MAX_TONES   32

typedef struct {
    int n, vol;
    uint16_t freq[MAX_TONES], ms[MAX_TONES];
} tones_t;

static QueueHandle_t s_tone_q;
static esp_codec_dev_handle_t s_spk;
static bool s_click;

static void play(const tones_t *t)
{
    if (!s_spk) {
        s_spk = bsp_audio_codec_speaker_init();
        if (!s_spk) {
            ESP_LOGW(TAG, "speaker init failed");
            return;
        }
    }
    esp_codec_dev_sample_info_t fs = {.sample_rate = SR, .channel = 1, .bits_per_sample = 16};
    if (esp_codec_dev_open(s_spk, &fs) != ESP_CODEC_DEV_OK) return;
    static int16_t buf[256];
    int amp = 120 * t->vol;                    // vol 0..100 -> up to ~12000 (square-ish wave is loud)
    for (int i = 0; i < t->n; i++) {
        int total = SR * t->ms[i] / 1000, done = 0;
        double ph = 0, step = 2 * M_PI * t->freq[i] / SR;
        int fade = SR / 200;                   // 5 ms fade in/out against clicks
        while (done < total) {
            int n = total - done > 256 ? 256 : total - done;
            for (int k = 0; k < n; k++, done++) {
                double env = 1.0;
                if (done < fade) env = (double)done / fade;
                if (total - done < fade) env = (double)(total - done) / fade;
                buf[k] = t->freq[i] ? (int16_t)(amp * env * sin(ph)) : 0;   // freq 0 = pause
                ph += step;
            }
            esp_codec_dev_write(s_spk, buf, n * 2);
        }
    }
    memset(buf, 0, sizeof(buf));
    esp_codec_dev_write(s_spk, buf, sizeof(buf));
    esp_codec_dev_close(s_spk);
}

static void tone_task(void *arg)
{
    (void)arg;
    tones_t *t;
    for (;;) {
        if (xQueueReceive(s_tone_q, &t, portMAX_DELAY)) {
            play(t);
            free(t);
        }
    }
}

static void queue_tones(tones_t *t)
{
    if (!t->n || xQueueSend(s_tone_q, &t, 0) != pdTRUE) free(t);
}

static void preset(tones_t *t, const char *name)
{
    static const struct { const char *n; uint16_t f[4], ms[4]; int c; } P[] = {
        {"click",  {2600},             {12},            1},
        {"ok",     {880, 1320},        {70, 110},       2},
        {"error",  {330, 0, 330},      {150, 60, 220},  3},
        {"notify", {1047, 1319, 1568}, {90, 90, 160},   3},
        {"alarm",  {1760, 0, 1760, 0}, {120, 80, 120, 80}, 4},
    };
    for (size_t i = 0; i < sizeof(P) / sizeof(P[0]); i++) {
        if (!strcmp(P[i].n, name)) {
            for (int k = 0; k < P[i].c; k++) {
                t->freq[t->n] = P[i].f[k];
                t->ms[t->n++] = P[i].ms[k];
            }
        }
    }
}

// {"t":"beep","freq":880,"ms":100} | {"t":"beep","tones":[[880,100],[0,50],[1320,120]]} | {"t":"beep","sound":"notify"}
static void beep_msg(const cJSON *m)
{
    tones_t *t = calloc(1, sizeof(*t));
    if (!t) return;
    t->vol = jint(m, "vol", 60);
    const char *snd = cJSON_GetStringValue(cJSON_GetObjectItemCaseSensitive(m, "sound"));
    if (snd) preset(t, snd);
    const cJSON *tones = cJSON_GetObjectItemCaseSensitive(m, "tones"), *it;
    cJSON_ArrayForEach(it, tones) {
        if (t->n >= MAX_TONES) break;
        const cJSON *a = cJSON_GetArrayItem(it, 0), *b = cJSON_GetArrayItem(it, 1);
        if (!cJSON_IsNumber(a) || !cJSON_IsNumber(b)) continue;
        t->freq[t->n] = (uint16_t)a->valuedouble;
        t->ms[t->n++] = (uint16_t)(b->valuedouble > 5000 ? 5000 : b->valuedouble);
    }
    if (!t->n && jint(m, "freq", 0) > 0) {
        t->freq[0] = jint(m, "freq", 880);
        t->ms[0] = jint(m, "ms", 100);
        t->n = 1;
    }
    queue_tones(t);
}

void hw_click(void)
{
    if (!s_click || !s_tone_q || uxQueueMessagesWaiting(s_tone_q)) return;
    tones_t *t = calloc(1, sizeof(*t));
    if (!t) return;
    t->vol = 25;
    preset(t, "click");
    queue_tones(t);
}

/* ------------------------------------------------------------------ IR */

#define IR_RES_HZ     1000000       // 1 tick = 1 us
#define IR_MAX_EDGES  512
#define IR_RX_SYMS    96

typedef enum { IR_SEND, IR_LEARN } ir_op_t;
typedef struct {
    ir_op_t op;
    int khz, timeout_ms, n;
    uint16_t us[];                // mark, space, mark, ...
} ir_job_t;

static QueueHandle_t s_ir_q, s_ir_rx_done;
static int s_ir_tx_pin = 4, s_ir_rx_pin = 4;
static rmt_channel_handle_t s_ir_tx, s_ir_rx;
static rmt_encoder_handle_t s_ir_enc;
static int s_ir_khz;
static volatile bool s_ir_reopen;

static void ir_close(void)
{
    if (s_ir_tx) {
        rmt_disable(s_ir_tx);
        rmt_del_channel(s_ir_tx);
        s_ir_tx = NULL;
    }
    if (s_ir_rx) {
        rmt_disable(s_ir_rx);
        rmt_del_channel(s_ir_rx);
        s_ir_rx = NULL;
    }
}

static bool ir_tx_open(int khz)
{
    if (s_ir_tx && khz == s_ir_khz) return true;
    ir_close();
    rmt_tx_channel_config_t cfg = {
        .gpio_num = s_ir_tx_pin,
        .clk_src = RMT_CLK_SRC_DEFAULT,
        .resolution_hz = IR_RES_HZ,
        .mem_block_symbols = 48,
        .trans_queue_depth = 2,
    };
    if (rmt_new_tx_channel(&cfg, &s_ir_tx) != ESP_OK) return false;
    rmt_carrier_config_t car = {.frequency_hz = khz * 1000, .duty_cycle = 0.33};
    rmt_apply_carrier(s_ir_tx, &car);
    if (!s_ir_enc) {
        rmt_copy_encoder_config_t ec = {};
        rmt_new_copy_encoder(&ec, &s_ir_enc);
    }
    rmt_enable(s_ir_tx);
    s_ir_khz = khz;
    return true;
}

static bool IRAM_ATTR ir_rx_cb(rmt_channel_handle_t ch, const rmt_rx_done_event_data_t *ev, void *ctx)
{
    BaseType_t woken = pdFALSE;
    xQueueSendFromISR((QueueHandle_t)ctx, ev, &woken);
    return woken == pdTRUE;
}

static bool ir_rx_open(void)
{
    if (s_ir_rx) return true;
    ir_close();
    rmt_rx_channel_config_t cfg = {
        .gpio_num = s_ir_rx_pin,
        .clk_src = RMT_CLK_SRC_DEFAULT,
        .resolution_hz = IR_RES_HZ,
        .mem_block_symbols = IR_RX_SYMS,
    };
    if (rmt_new_rx_channel(&cfg, &s_ir_rx) != ESP_OK) return false;
    rmt_rx_event_callbacks_t cbs = {.on_recv_done = ir_rx_cb};
    rmt_rx_register_event_callbacks(s_ir_rx, &cbs, s_ir_rx_done);
    rmt_enable(s_ir_rx);
    return true;
}

static void ir_send(const ir_job_t *j)
{
    if (!ir_tx_open(j->khz)) {
        s_send("{\"t\":\"toast\",\"text\":\"IR send failed\"}");
        return;
    }
    int nsym = (j->n + 1) / 2;
    rmt_symbol_word_t *sym = calloc(nsym, sizeof(*sym));
    if (!sym) return;
    for (int i = 0; i < nsym; i++) {
        sym[i].level0 = 1;
        sym[i].duration0 = j->us[2 * i] > 32767 ? 32767 : j->us[2 * i];
        sym[i].level1 = 0;
        uint32_t sp = 2 * i + 1 < j->n ? j->us[2 * i + 1] : 1000;
        sym[i].duration1 = sp > 32767 ? 32767 : sp;
    }
    rmt_transmit_config_t tc = {.loop_count = 0};
    rmt_transmit(s_ir_tx, s_ir_enc, sym, nsym * sizeof(*sym), &tc);
    rmt_tx_wait_all_done(s_ir_tx, 1000);
    free(sym);
}

static void ir_learn(int timeout_ms)
{
    static rmt_symbol_word_t rx[IR_RX_SYMS * 2];
    if (!ir_rx_open()) {
        s_send("{\"t\":\"ir\",\"error\":\"rx\"}");
        return;
    }
    xQueueReset(s_ir_rx_done);
    rmt_receive_config_t rc = {.signal_range_min_ns = 1250, .signal_range_max_ns = 12 * 1000 * 1000};
    rmt_receive(s_ir_rx, rx, sizeof(rx), &rc);
    rmt_rx_done_event_data_t ev;
    int64_t end = esp_timer_get_time() + (int64_t)timeout_ms * 1000;
    for (;;) {
        int left = (int)((end - esp_timer_get_time()) / 1000);
        if (left <= 0 || xQueueReceive(s_ir_rx_done, &ev, pdMS_TO_TICKS(left)) != pdTRUE) {
            ir_close();         // stops the pending receive
            s_send("{\"t\":\"ir\",\"timeout\":true}");
            return;
        }
        if (ev.num_symbols >= 4) break;           // ignore glitches, keep listening
        rmt_receive(s_ir_rx, rx, sizeof(rx), &rc);
    }
    cJSON *m = cJSON_CreateObject();
    cJSON_AddStringToObject(m, "t", "ir");
    cJSON *raw = cJSON_AddArrayToObject(m, "raw");
    for (size_t i = 0; i < ev.num_symbols; i++) {
        if (ev.received_symbols[i].duration0) cJSON_AddItemToArray(raw, cJSON_CreateNumber(ev.received_symbols[i].duration0));
        if (ev.received_symbols[i].duration1) cJSON_AddItemToArray(raw, cJSON_CreateNumber(ev.received_symbols[i].duration1));
    }
    char *s = cJSON_PrintUnformatted(m);
    if (s) {
        s_send(s);
        cJSON_free(s);
    }
    cJSON_Delete(m);
}

static void ir_task(void *arg)
{
    (void)arg;
    ir_job_t *j;
    for (;;) {
        if (xQueueReceive(s_ir_q, &j, portMAX_DELAY)) {
            if (s_ir_reopen) {
                s_ir_reopen = false;
                ir_close();
            }
            if (j->op == IR_SEND) ir_send(j);
            else ir_learn(j->timeout_ms);
            free(j);
        }
    }
}

// {"t":"ir","raw":[9000,4500,560,560,...],"khz":38,"repeat":0}  |  {"t":"ir_learn","ms":10000}
static void ir_msg(const cJSON *m, bool learn)
{
    if (learn) {
        ir_job_t *j = calloc(1, sizeof(*j));
        if (!j) return;
        j->op = IR_LEARN;
        j->timeout_ms = jint(m, "ms", 10000);
        if (xQueueSend(s_ir_q, &j, 0) != pdTRUE) free(j);
        return;
    }
    const cJSON *raw = cJSON_GetObjectItemCaseSensitive(m, "raw");
    int n = cJSON_GetArraySize(raw);
    if (n <= 0 || n > IR_MAX_EDGES) return;
    int repeat = jint(m, "repeat", 0);
    if (repeat > 5) repeat = 5;
    for (int r = 0; r <= repeat; r++) {
        ir_job_t *j = calloc(1, sizeof(*j) + n * sizeof(uint16_t));
        if (!j) return;
        j->op = IR_SEND;
        j->khz = jint(m, "khz", 38);
        if (j->khz < 20 || j->khz > 60) j->khz = 38;
        const cJSON *it;
        cJSON_ArrayForEach(it, raw) {
            double v = cJSON_IsNumber(it) ? it->valuedouble : 0;
            j->us[j->n++] = (uint16_t)(v < 0 ? -v : (v > 65535 ? 65535 : v));   // accept +/- notation
        }
        if (xQueueSend(s_ir_q, &j, 0) != pdTRUE) free(j);
    }
}

/* ------------------------------------------------------------------ public */

bool hw_on_message(const char *type, void *msg)
{
    const cJSON *m = (const cJSON *)msg;
    if (!strcmp(type, "led")) {
        led_msg(m);
    } else if (!strcmp(type, "beep")) {
        beep_msg(m);
    } else if (!strcmp(type, "ir")) {
        ir_msg(m, false);
    } else if (!strcmp(type, "ir_learn")) {
        ir_msg(m, true);
    } else if (!strcmp(type, "cfg")) {
        // shared with the UI: only pick our fields, let the UI handle the rest
        int tx = jint(m, "ir_pin", s_ir_tx_pin), rx = jint(m, "ir_rx_pin", jint(m, "ir_pin", s_ir_rx_pin));
        if (tx != s_ir_tx_pin || rx != s_ir_rx_pin) {
            if (tx >= 0 && tx <= 21 && rx >= 0 && rx <= 21) {
                s_ir_tx_pin = tx;
                s_ir_rx_pin = rx;
                s_ir_reopen = true;     // the IR task re-creates its channels on next use
            }
        }
        const cJSON *c = cJSON_GetObjectItemCaseSensitive(m, "click");
        if (cJSON_IsBool(c)) s_click = cJSON_IsTrue(c);
        return false;
    } else {
        return false;
    }
    return true;
}

void hw_init(hw_send_fn send)
{
    s_send = send;
    led_init();
    s_tone_q = xQueueCreate(4, sizeof(tones_t *));
    xTaskCreate(tone_task, "tones", 4096, NULL, 3, NULL);
    s_ir_q = xQueueCreate(6, sizeof(ir_job_t *));
    s_ir_rx_done = xQueueCreate(1, sizeof(rmt_rx_done_event_data_t));
    xTaskCreate(ir_task, "ir", 4096, NULL, 4, NULL);
}
