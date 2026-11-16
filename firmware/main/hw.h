// KnobDeck hardware services the PC can use: RGB LED, speaker (beeps / tones), IR send + learn
#pragma once
#include <stdbool.h>

typedef void (*hw_send_fn)(const char *line);

void hw_init(hw_send_fn send);

// Handles "led", "beep", "ir", "ir_learn" and the "ir_pin" field of "cfg".
// msg is a cJSON*; returns true if the message was for the hardware layer.
bool hw_on_message(const char *type, void *msg);

// Short click sound for knob feedback (no-op unless enabled with cfg {"click":true})
void hw_click(void);
