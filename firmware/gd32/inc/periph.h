#ifndef PERIPH_H
#define PERIPH_H

#include <stdint.h>

void periph_init(void);
void periph_set_leds(uint8_t red, uint8_t yellow, uint8_t green, uint8_t brightness);
uint8_t periph_get_led_state(void); /* bit0=red bit1=yellow bit2=green */
void periph_beep(uint16_t freq_hz, uint16_t duration_ms);
int periph_read_button(void);
int periph_read_battery(uint16_t *batt_mv, uint8_t *is_usb_present, uint8_t *is_charging);

void watchdog_init(void);
void watchdog_kick(void);

void system_enter_bootloader(void);

#endif /* PERIPH_H */
