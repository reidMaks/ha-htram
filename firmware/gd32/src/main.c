/*
 * Honeywell HTRAM Custom GD32F150 Firmware
 *
 * Implements I/O Coprocessor architecture according to docs/CUSTOM_FIRMWARE_SPEC.md:
 *   - ST7789 display rasterizer & 3-wire 9-bit SPI driver
 *   - Honeywell CRIR M1 CO2 sensor acquisition (USART0 Modbus RTU)
 *   - Sensirion SHT30 T/H sensor acquisition (I2C bitbang)
 *   - Front LEDs, button SW1, buzzer & system power latches
 *   - Binary protocol with CRC-16-CCITT to ESP32 on USART1
 *   - Watchdog & OTA ROM-bootloader jump
 */

#include "gd32f150.h"
#include "protocol_engine.h"
#include "display.h"
#include "sensors.h"
#include "periph.h"

#ifndef GD32_UART_BAUD
#define GD32_UART_BAUD 115200UL
#endif


/* Format integer to string */
static void int_to_str(int32_t val, char *buf, int is_signed)
{
    int i = 0;
    if (is_signed && val < 0) {
        buf[i++] = '-';
        val = -val;
    }
    if (val == 0) {
        buf[i++] = '0';
        buf[i] = '\0';
        return;
    }
    char tmp[12];
    int t = 0;
    while (val > 0) {
        tmp[t++] = '0' + (val % 10);
        val /= 10;
    }
    while (t > 0) {
        buf[i++] = tmp[--t];
    }
    buf[i] = '\0';
}

/* Format 0.01 fixed point: e.g. 2350 -> "23.5" */
static void format_fixed1(int32_t val, char *buf, int is_signed)
{
    int i = 0;
    if (is_signed && val < 0) {
        buf[i++] = '-';
        val = -val;
    }
    int32_t int_part = val / 100;
    int32_t dec_part = (val % 100) / 10;

    int_to_str(int_part, &buf[i], 0);
    while (buf[i]) i++;
    buf[i++] = '.';
    buf[i++] = '0' + (char)dec_part;
    buf[i] = '\0';
}

int main(void)
{
    /* 1. Initialize Board Peripherals & Power Latches */
    periph_init();

    /* 2. Initialize ST7789 Color Display */
    display_init();

    /* Draw Startup Screen */
    display_fill_screen(COLOR_BLACK);
    display_draw_string(40, 30, "HONEYWELL HTRAM", COLOR_GREEN, COLOR_BLACK);
    display_draw_string(24, 50, "CUSTOM GD32 FIRMWARE", COLOR_WHITE, COLOR_BLACK);
    display_draw_string(56, 70, "VERSION 1.0.0", COLOR_YELLOW, COLOR_BLACK);

    display_draw_string(32, 110, "INITIALIZING...", COLOR_GRAY, COLOR_BLACK);

    /* 3. Initialize Sensors (SHT30 + CRIR M1 CO2) */
    sensors_init();

    /* 4. Initialize Communication Protocol on USART1 */
    protocol_init(GD32_UART_BAUD);

    /* 5. Startup Chirp & Send Hello Handshake */
    periph_beep_blocking(2304, 50);
    protocol_send_hello();

    display_fill_rect(20, 105, 200, 25, COLOR_BLACK);
    display_draw_string(32, 110, "SENSORS ONLINE", COLOR_CYAN, COLOR_BLACK);

    /* Live Status Loop Variables */
    int16_t temp_001c = 0;
    uint16_t hum_001pct = 0;
    uint16_t co2_ppm = 0;
    uint16_t batt_mv = 0;
    uint8_t is_usb_present = 0;
    uint8_t is_charging = 0;
    uint8_t warmup = 1;
    uint8_t sensor_err = 0;

    uint32_t last_sht_ms = 0;
    uint32_t last_co2_ms = 0;
    uint32_t last_telemetry_ms = 0;
    uint32_t last_ui_ms = 0;
    uint32_t btn_hold_start_ms = 0;
    /* Green LED ON indicating ready */
    periph_set_leds(0, 0, 1, 100);

    while (1) {
        uint32_t now = periph_millis();

        /* Process all incoming packets from ESP32 */
        protocol_process_rx();

        /* Advance non-blocking buzzer/melody playback (synchronized to SysTick wall-clock) */
        periph_buzzer_tick(now);

        /* Poll Button SW1 with ~15ms software debounce filter */
        int raw_btn = periph_read_button();
        static int debounced_btn = 0;
        static uint8_t debounce_count = 0;

        if (raw_btn != debounced_btn) {
            debounce_count++;
            if (debounce_count >= 3) {
                debounced_btn = raw_btn;
                debounce_count = 0;

                if (debounced_btn) {
                    /* 0 -> 1: Button Pressed */
                    btn_hold_start_ms = now ? now : 1;
                    periph_beep(2304, 20); /* press feedback chirp */
                    protocol_send_button_event(1, 0);
                } else {
                    /* 1 -> 0: Button Released */
                    uint32_t dur = (btn_hold_start_ms && now >= btn_hold_start_ms) ? (now - btn_hold_start_ms) : 0;
                    if (dur > 65535) dur = 65535;
                    btn_hold_start_ms = 0;
                    protocol_send_button_event(0, (uint16_t)dur);
                }
            }
        } else {
            debounce_count = 0;
        }

        if (debounced_btn && btn_hold_start_ms && (now - btn_hold_start_ms >= 3000)) {
            periph_beep_blocking(2000, 100);
            
            /* STANDBY MODE ENTRY */
            display_fill_screen(COLOR_BLACK);
            delay_ms(100);

            /* Kill Peripherals */
            display_set_backlight(0); /* Backlight OFF (PWM duty 0; PB8 is AF) */
            GPIOB_BC = (1 << 11) | (1 << 9); /* Sensor Power OFF */
            periph_set_leds(0, 0, 0, 0); /* LEDs OFF (this correctly sets data pins LOW) */
            GPIOA_BC = (1 << 1); /* VLED OFF */
            *(volatile uint32_t *)(GPIOF_BASE + 0x14) = (1 << 7); /* GPIOF_BC = PF7 OFF */
            GPIOB_BC = (1 << 3); /* PB3 OFF */

            /* Wait for button release */
            while(periph_read_button()) delay_ms(10);

            /* Wait for next long press to wake up */
            uint32_t wakeup_ticks = 0;
            while(1) {
                if (periph_read_button()) {
                    wakeup_ticks++;
                    if (wakeup_ticks > 400) { /* 2 seconds */
                        /* Hard Reset MCU */
                        *(volatile uint32_t *)0xE000ED0C = (0x5FA << 16) | (1 << 2);
                        while(1);
                    }
                } else {
                    wakeup_ticks = 0;
                }
                delay_ms(5);
            }
        }

        /* SHT30 & Battery Poll (every 30000ms) */
        if (now - last_sht_ms >= 30000) {
            last_sht_ms = now;

            /* Read actual battery voltage & USB charging state */
            periph_read_battery(&batt_mv, &is_usb_present, &is_charging);

            /* Read SHT30 with thermal compensation */
            if (sensors_read_sht30(&temp_001c, &hum_001pct) != 0) {
                sensor_err = 1;
            } else {
                sensor_err = 0;
            }
        }

        /* CRIR M1 CO2 Poll (every 30000ms) */
        if (now - last_co2_ms >= 30000) {
            last_co2_ms = now;
            uint8_t wm = 0;
            if (sensors_poll_co2(&co2_ppm, &wm) == 0) {
                warmup = wm;
            }
        }

        /* Send Uplink Telemetry (every 30000ms) */
        if (now - last_telemetry_ms >= 30000) {
            last_telemetry_ms = now;

            uint8_t status = 0;
            if (is_usb_present) status |= STATUS_FLAG_USB_PRESENT;
            if (is_charging)    status |= STATUS_FLAG_CHARGING;
            if (warmup || co2_ppm == 0) status |= STATUS_FLAG_WARMUP;
            if (sensor_err) status |= STATUS_FLAG_SENSOR_ERR;
            if (debounced_btn) status |= STATUS_FLAG_BUTTON_PRESSED;

            uint8_t leds = periph_get_led_state();
            if (leds & 1) status |= STATUS_FLAG_LED_RED;
            if (leds & 2) status |= STATUS_FLAG_LED_YELLOW;
            if (leds & 4) status |= STATUS_FLAG_LED_GREEN;

            /* Re-announce firmware version each cycle: the ESP32 is powered by
             * our PF7 rail and boots *after* the one-shot hello at init, so it
             * would otherwise never learn GD32_FW_VERSION. */
            protocol_send_hello();
            protocol_send_telemetry(co2_ppm, temp_001c, hum_001pct, batt_mv, status);
        }

        /* Update Local Screen (every 1000ms) - only if ESP32 hasn't taken over */
        if (!protocol_is_external_display_active() && (now - last_ui_ms >= 1000)) {
            last_ui_ms = now;

            char buf[32];

            /* CO2 Line */
            display_fill_rect(20, 140, 200, 16, COLOR_BLACK);
            display_draw_string(24, 142, "CO2 :", COLOR_WHITE, COLOR_BLACK);
            if (warmup || co2_ppm == 0) {
                display_draw_string(80, 142, "WARMING UP...", COLOR_ORANGE, COLOR_BLACK);
            } else {
                int_to_str(co2_ppm, buf, 0);
                display_draw_string(80, 142, buf, COLOR_GREEN, COLOR_BLACK);
                int l = 0; while (buf[l]) l++;
                display_draw_string(80 + l * 8 + 8, 142, "PPM", COLOR_WHITE, COLOR_BLACK);
            }

            /* Temp Line */
            display_fill_rect(20, 165, 200, 16, COLOR_BLACK);
            display_draw_string(24, 167, "TEMP:", COLOR_WHITE, COLOR_BLACK);
            format_fixed1(temp_001c, buf, 1);
            display_draw_string(80, 167, buf, COLOR_CYAN, COLOR_BLACK);
            int l = 0; while (buf[l]) l++;
            display_draw_string(80 + l * 8 + 8, 167, "C", COLOR_WHITE, COLOR_BLACK);

            /* Humidity Line */
            display_fill_rect(20, 190, 200, 16, COLOR_BLACK);
            display_draw_string(24, 192, "HUM :", COLOR_WHITE, COLOR_BLACK);
            format_fixed1(hum_001pct, buf, 0);
            display_draw_string(80, 192, buf, COLOR_CYAN, COLOR_BLACK);
            l = 0; while (buf[l]) l++;
            display_draw_string(80 + l * 8 + 8, 192, "%RH", COLOR_WHITE, COLOR_BLACK);

            /* Battery Line */
            display_fill_rect(20, 215, 200, 16, COLOR_BLACK);
            display_draw_string(24, 217, "BATT:", COLOR_WHITE, COLOR_BLACK);
            int_to_str(batt_mv, buf, 0);
            display_draw_string(80, 217, buf, COLOR_YELLOW, COLOR_BLACK);
            int l_b = 0; while (buf[l_b]) l_b++;
            display_draw_string(80 + l_b * 8 + 4, 217, "MV", COLOR_WHITE, COLOR_BLACK);
            if (is_charging) {
                display_draw_string(160, 217, "[CHRG]", COLOR_GREEN, COLOR_BLACK);
            } else if (is_usb_present) {
                display_draw_string(160, 217, "[USB ]", COLOR_CYAN, COLOR_BLACK);
            } else {
                display_draw_string(160, 217, "[BATT]", COLOR_YELLOW, COLOR_BLACK);
            }
        }

        /* 5ms delay per loop */
        delay_ms(5);
    }

    return 0;
}
