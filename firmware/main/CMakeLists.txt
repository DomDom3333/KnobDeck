idf_component_register(
    SRCS "main.c" "ui.c" "deck.c" "hw.c"
         "fonts/kd_font_12.c" "fonts/kd_font_14.c" "fonts/kd_font_16.c" "fonts/kd_font_20.c"
         "fonts/kd_font_28.c" "fonts/kd_font_36.c" "fonts/kd_font_48.c"
    INCLUDE_DIRS "."
    REQUIRES json esp_driver_rmt esp_driver_i2s led_strip esp_codec_dev mbedtls esp_driver_usb_serial_jtag esp_timer spiffs esp32_c3_lcdkit
)
