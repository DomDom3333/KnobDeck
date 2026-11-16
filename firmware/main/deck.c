#include "deck.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cJSON.h"

static void copy_str(char *dst, size_t n, const cJSON *item)
{
    dst[0] = 0;
    if (cJSON_IsString(item) && item->valuestring) {
        strncpy(dst, item->valuestring, n - 1);
        dst[n - 1] = 0;
    }
}

uint32_t deck_parse_color(const char *s, uint32_t fallback)
{
    if (!s) return fallback;
    if (*s == '#') s++;
    if (strlen(s) != 6) return fallback;
    char *end = NULL;
    unsigned long v = strtoul(s, &end, 16);
    if (!end || *end) return fallback;
    return (uint32_t)v;
}

static tile_type_t parse_type(const char *s)
{
    if (!s) return TILE_ACTION;
    if (!strcmp(s, "folder")) return TILE_FOLDER;
    if (!strcmp(s, "dial"))   return TILE_DIAL;
    if (!strcmp(s, "stats"))  return TILE_STATS;
    if (!strcmp(s, "back"))   return TILE_BACK;
    if (!strcmp(s, "people")) return TILE_PEOPLE;
    if (!strcmp(s, "screen")) return TILE_SCREEN;
    if (!strcmp(s, "empty"))  return TILE_EMPTY;
    return TILE_ACTION;
}

bool deck_parse(const char *json, deck_t *out)
{
    cJSON *root = cJSON_Parse(json);
    if (!root) return false;
    const cJSON *pages = cJSON_GetObjectItemCaseSensitive(root, "pages");
    if (!cJSON_IsArray(pages) || cJSON_GetArraySize(pages) == 0) {
        cJSON_Delete(root);
        return false;
    }
    memset(out, 0, sizeof(*out));
    copy_str(out->version, sizeof(out->version), cJSON_GetObjectItemCaseSensitive(root, "v"));

    const cJSON *pg;
    cJSON_ArrayForEach(pg, pages) {
        if (out->page_count >= DECK_MAX_PAGES) break;
        deck_page_t *p = &out->pages[out->page_count];
        copy_str(p->id, sizeof(p->id), cJSON_GetObjectItemCaseSensitive(pg, "id"));
        copy_str(p->title, sizeof(p->title), cJSON_GetObjectItemCaseSensitive(pg, "title"));
        if (!p->id[0]) snprintf(p->id, sizeof(p->id), "p%d", out->page_count);

        const cJSON *tiles = cJSON_GetObjectItemCaseSensitive(pg, "tiles");
        int slot = 0;
        const cJSON *t;
        cJSON_ArrayForEach(t, tiles) {
            // tiles may carry an explicit "slot" (0..7, 0 = top, clockwise); otherwise array order
            const cJSON *js = cJSON_GetObjectItemCaseSensitive(t, "slot");
            int s = cJSON_IsNumber(js) ? js->valueint : slot;
            slot++;
            if (s < 0 || s >= DECK_SLOTS || cJSON_IsNull(t)) continue;
            deck_tile_t *dt = &p->tiles[s];
            dt->type = parse_type(cJSON_GetStringValue(cJSON_GetObjectItemCaseSensitive(t, "type")));
            copy_str(dt->id, sizeof(dt->id), cJSON_GetObjectItemCaseSensitive(t, "id"));
            copy_str(dt->label, sizeof(dt->label), cJSON_GetObjectItemCaseSensitive(t, "label"));
            copy_str(dt->icon, sizeof(dt->icon), cJSON_GetObjectItemCaseSensitive(t, "icon"));
            copy_str(dt->target, sizeof(dt->target), cJSON_GetObjectItemCaseSensitive(t, "target"));
            dt->color = deck_parse_color(cJSON_GetStringValue(cJSON_GetObjectItemCaseSensitive(t, "color")), 0x3949AB);
            if (!dt->id[0]) {
                char tmp[DECK_ID_LEN];
                snprintf(tmp, sizeof(tmp), "%.16s.%d", p->id, s);
                strcpy(dt->id, tmp);
            }
        }
        out->page_count++;
    }
    cJSON_Delete(root);
    return out->page_count > 0;
}

int deck_find_page(const deck_t *d, const char *id)
{
    for (int i = 0; i < d->page_count; i++) {
        if (!strcmp(d->pages[i].id, id)) return i;
    }
    return -1;
}

deck_tile_t *deck_find_tile(deck_t *d, const char *id, int *page_out, int *slot_out)
{
    for (int p = 0; p < d->page_count; p++) {
        for (int s = 0; s < DECK_SLOTS; s++) {
            deck_tile_t *t = &d->pages[p].tiles[s];
            if (t->type != TILE_EMPTY && !strcmp(t->id, id)) {
                if (page_out) *page_out = p;
                if (slot_out) *slot_out = s;
                return t;
            }
        }
    }
    return NULL;
}

// Same layout as the PC app's starter config, so the deck looks sensible before the first sync.
const char *deck_default_json(void)
{
    return "{\"t\":\"layout\",\"v\":\"default\",\"pages\":[{\"id\":\"home\",\"title\":\"KnobDeck\",\"tiles\":["
           "{\"id\":\"play\",\"label\":\"Play/Pause\",\"icon\":\"sym:play\",\"color\":\"#1E88E5\"},"
           "{\"id\":\"next\",\"label\":\"Next\",\"icon\":\"sym:next\",\"color\":\"#1E88E5\"},"
           "{\"id\":\"vol\",\"type\":\"dial\",\"label\":\"Volume\",\"icon\":\"sym:volume\",\"color\":\"#43A047\"},"
           "{\"id\":\"mic\",\"label\":\"Mic\",\"icon\":\"sym:audio\",\"color\":\"#E53935\"},"
           "{\"id\":\"stats\",\"type\":\"stats\",\"label\":\"PC Stats\",\"icon\":\"sym:charge\",\"color\":\"#FB8C00\"},"
           "{\"id\":\"apps\",\"type\":\"folder\",\"target\":\"apps\",\"label\":\"Apps\",\"icon\":\"sym:folder\",\"color\":\"#8E24AA\"},"
           "{\"id\":\"lock\",\"label\":\"Lock PC\",\"icon\":\"sym:power\",\"color\":\"#546E7A\"},"
           "{\"id\":\"prev\",\"label\":\"Previous\",\"icon\":\"sym:prev\",\"color\":\"#1E88E5\"}]},"
           "{\"id\":\"apps\",\"title\":\"Apps\",\"tiles\":["
           "{\"id\":\"back\",\"type\":\"back\",\"label\":\"Back\",\"icon\":\"sym:left\",\"color\":\"#455A64\"}]}]}";
}
