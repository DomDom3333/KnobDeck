#!/bin/sh
# Builds the desktop simulator and renders screenshots of the deck UI into ./out/sheet.png
#   LVGL_DIR=path/to/lvgl (v9.3+)   CJSON_DIR=path/to/cJSON (folder with cJSON.c, e.g. $IDF_PATH/components/json/cJSON)
set -e
: "${LVGL_DIR:?set LVGL_DIR to an LVGL 9.3+ checkout}"
: "${CJSON_DIR:?set CJSON_DIR to the folder containing cJSON.c}"
cd "$(dirname "$0")"
mkdir -p build out
find "$LVGL_DIR/src" -name "*.c" | while read -r f; do
  o="build/$(echo "$f" | md5sum | cut -c1-12).o"
  [ -f "$o" ] || echo "$f $o"
done | xargs -r -P 8 -n 2 sh -c 'gcc -c -O1 -w -DLV_CONF_INCLUDE_SIMPLE -I. -I"$LVGL_DIR" "$0" -o "$1"'
gcc -O1 -Wall -DLV_CONF_INCLUDE_SIMPLE -I. -I"$LVGL_DIR" -I"$CJSON_DIR" -I../main \
    main_sim.c ../main/ui.c ../main/deck.c ../main/fonts/*.c "$CJSON_DIR/cJSON.c" build/*.o -lm -o sim
./sim out
python3 sheet.py out out/sheet.png
echo "-> out/sheet.png"
