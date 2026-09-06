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
#define GD32_UART_BAUD 921600UL
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
#ifndef DIAG_MINIMAL
    display_init();
#endif

#ifndef DIAG_MINIMAL
    /* Draw Startup Screen */
    display_fill_screen(COLOR_BLACK);
    display_draw_string(40, 30, "HONEYWELL HTRAM", COLOR_GREEN, COLOR_BLACK);
    display_draw_string(24, 50, "CUSTOM GD32 FIRMWARE", COLOR_WHITE, COLOR_BLACK);
    display_draw_string(56, 70, "VERSION 1.0.0", COLOR_YELLOW, COLOR_BLACK);

    display_draw_string(32, 110, "INITIALIZING...", COLOR_GRAY, COLOR_BLACK);

    /* 3. Initialize Sensors (SHT30 + CRIR M1 CO2) */
    sensors_init();

#endif

    /* The backlight comes up dark (display_init leaves PWM duty at 0) and
     * nothing in normal operation ever raised it -- only the ESP did, via
     * CMD_SET_BACKLIGHT. So every GD32 reset left a black screen until someone
     * touched the brightness in Home Assistant, which is exactly what a reflash
     * looks like from the outside: "the display died". Come up lit. */
#ifndef DIAG_MINIMAL
    display_set_backlight(100);
#endif

    /* 4. Initialize Communication Protocol on USART1 */
    protocol_init(GD32_UART_BAUD);

    /* 5. Startup Chirp & Send Hello Handshake */
#ifndef DIAG_MINIMAL
    periph_beep_blocking(2304, 50);
#endif
    protocol_send_hello();
#ifndef DIAG_MINIMAL
    display_fill_rect(20, 105, 200, 25, COLOR_BLACK);
    display_draw_string(32, 110, "SENSORS ONLINE", COLOR_CYAN, COLOR_BLACK);
#endif

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
#ifndef DIAG_MINIMAL
    /* Green LED ON indicating ready */
    periph_set_leds(0, 0, 1, 100);
#endif

#ifdef DIAG_MINIMAL
    /* Nothing but the link and the battery: no sensors, no display, no LEDs,
     * no buzzer, no button. Every pin except PB3/PF7 and USART1 stays in its
     * reset state, so if the cell still refuses to charge here, nothing this
     * firmware does is responsible. */
    while (1) {
        uint32_t now = periph_millis();
        protocol_process_rx();

        if (now - last_telemetry_ms >= 5000) {
            last_telemetry_ms = now;
            periph_read_battery(&batt_mv, &is_usb_present, &is_charging);

            uint8_t status = 0;
            if (is_usb_present) status |= STATUS_FLAG_USB_PRESENT;
            if (is_charging) status |= STATUS_FLAG_CHARGING;
            protocol_send_telemetry(co2_ppm, temp_001c, hum_001pct, batt_mv, status);
            protocol_send_hello();
        }
        delay_ms(5);
    }
    return 0;
}
#else
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
            
            /* STANDBY MODE ENTRY -- mirrors what the factory image actually
             * does while it charges, read live over SWD (§6.5d):
             *
             *   PB3  low       ESP32 rail off. PB3 gates the ESP, NOT PF7 --
             *                  the factory sits with PF7 high and the ESP dead.
             *   PF7  stays up  panel keeps its power, so the charge screen works
             *   PC15 released  the factory stops driving the DC-DC latch here,
             *                  and this is the prime suspect for why the cell
             *                  never charges under our firmware
             *   PB11 stays up  the factory leaves the 5V boost enabled
             */
            GPIOB_BC = (1 << 3);             /* ESP32 rail off */
            GPIOB_BC = (1 << 9);             /* CO2 sensor power off */
            gpio_cfg_in(GPIOC_BASE, 15, 0);  /* release the DC-DC latch */
            periph_set_leds(0, 0, 0, 0);
            GPIOA_BC = (1 << 1);             /* VLED off */

            protocol_set_external_display(0); /* we own the panel again */
            display_fill_screen(COLOR_BLACK);
            display_set_backlight(12);        /* dim: legible up close, cheap */

            /* Wait for button release */
            while (periph_read_button()) delay_ms(10);

            uint32_t wakeup_ticks = 0;
            uint32_t last_draw_ms = 0;
            uint8_t first_draw = 1;
            /* Standby has to stay a low-power state, so the panel is only lit
             * for a while after entry or after a tap -- long enough to read,
             * short enough not to drain the cell overnight. */
            uint32_t lit_until_ms = periph_millis() + 30000;
            uint8_t lit = 1;
            uint8_t last_usb = gpio_get(GPIOC_BASE, 13) ? 1 : 0;

            while (1) {
                uint32_t sb_now = periph_millis();

                /* Plugging power in lights the screen, like the factory. It has
                 * no interrupt on PC13 either -- EXTI is armed only on the
                 * button -- so this is a poll, same as theirs. */
                uint8_t usb_now = gpio_get(GPIOC_BASE, 13) ? 1 : 0;
                if (usb_now != last_usb) {
                    last_usb = usb_now;
                    if (!lit) display_set_backlight(12);
                    lit = 1;
                    first_draw = 1;
                    lit_until_ms = sb_now + 30000;
                }

                if (lit && (int32_t)(sb_now - lit_until_ms) >= 0) {
                    lit = 0;
                    display_set_backlight(0);
                    display_fill_screen(COLOR_BLACK);
                }

                if (lit && (first_draw || sb_now - last_draw_ms >= 2000)) {
                    first_draw = 0;
                    last_draw_ms = sb_now;

                    uint16_t mv = 0;
                    uint8_t usb = 0, chrg = 0;
                    periph_read_battery(&mv, &usb, &chrg);

                    /* Percentage: same piecewise curve the ESP uses, coarse. */
                    int pct;
                    if (mv <= 3200) pct = 0;
                    else if (mv >= 4200) pct = 100;
                    else if (mv < 3550) pct = (mv - 3200) * 20 / 350;
                    else if (mv < 3720) pct = 20 + (mv - 3550) * 30 / 170;
                    else if (mv < 3950) pct = 50 + (mv - 3720) * 34 / 230;
                    else pct = 84 + (mv - 3950) * 16 / 250;

                    uint16_t fg = chrg ? COLOR_GREEN : (pct < 20 ? COLOR_ORANGE : COLOR_WHITE);

                    /* Battery outline, 140x60 at (50,80), 3 px border. */
                    display_fill_rect(50, 80, 140, 3, fg);
                    display_fill_rect(50, 137, 140, 3, fg);
                    display_fill_rect(50, 80, 3, 60, fg);
                    display_fill_rect(187, 80, 3, 60, fg);
                    display_fill_rect(190, 97, 6, 26, fg);   /* nub */

                    /* Fill proportional to charge, rest cleared. */
                    uint8_t w = (uint8_t)((132 * pct) / 100);
                    if (w > 132) w = 132;
                    if (w) display_fill_rect(54, 84, w, 52, fg);
                    if (w < 132) display_fill_rect(54 + w, 84, 132 - w, 52, COLOR_BLACK);

                    char buf[16];
                    char line[24];
                    int i = 0;
                    int_to_str(pct, buf, 0);
                    while (buf[i]) { line[i] = buf[i]; i++; }
                    line[i++] = '%';
                    line[i] = 0;
                    display_fill_rect(0, 152, 240, 12, COLOR_BLACK);
                    display_draw_string(104, 152, line, fg, COLOR_BLACK);

                    /* Voltage only matters while something is feeding it. */
                    display_fill_rect(0, 172, 240, 12, COLOR_BLACK);
                    if (usb) {
                        int j = 0;
                        int_to_str(mv, buf, 0);
                        while (buf[j]) { line[j] = buf[j]; j++; }
                        line[j++] = ' ';
                        line[j++] = 'm';
                        line[j++] = 'V';
                        line[j] = 0;
                        display_draw_string(84, 172, line, COLOR_GRAY, COLOR_BLACK);
                    }

                    display_fill_rect(0, 192, 240, 12, COLOR_BLACK);
                    const char *st = chrg ? "CHARGING" : (usb ? "USB" : "BATTERY");
                    uint8_t len = 0;
                    while (st[len]) len++;
                    display_draw_string((uint8_t)(120 - len * 4), 192, st,
                                        chrg ? COLOR_GREEN : COLOR_GRAY, COLOR_BLACK);
                }

                if (periph_read_button()) {
                    if (!wakeup_ticks && !lit) {   /* a tap wakes the panel only */
                        lit = 1;
                        first_draw = 1;
                        display_set_backlight(12);
                    }
                    wakeup_ticks++;
                    lit_until_ms = sb_now + 30000;
                    if (wakeup_ticks > 400) { /* 2 seconds */
                        /* The reset restores PB3/PC15 through periph_init(). */
                        *(volatile uint32_t *)0xE000ED0C = (0x5FA << 16) | (1 << 2);
                        while (1);
                    }
                } else {
                    wakeup_ticks = 0;
                }
                delay_ms(5);
            }
        }

        /* SHT30 & Battery Poll (every 30000ms).
         * The conversion wait belongs to the loop, not to the driver: blocking
         * 40 ms here would overrun the RX ring at 921600 (22 ms of slack). */
        static uint32_t sht_started_ms = 0;
        static uint8_t sht_pending = 0;

        /* The sensor rail stays up. Duty-cycling it looked attractive while we
         * thought the charger was starved of current, but the NDIR needs far
         * longer than a couple of seconds to give a real number -- with a short
         * window it just reports 0 -- and the actual cause of the charging
         * failure turned out to be PB2, not consumption. */
        if (!sht_pending && now - last_sht_ms >= 30000) {
            last_sht_ms = now;

            periph_read_battery(&batt_mv, &is_usb_present, &is_charging);

            if (sensors_sht30_start() == 0) {
                sht_pending = 1;
                sht_started_ms = now ? now : 1;
            } else {
                sensor_err = 1;
            }
        }

        if (sht_pending && (now - sht_started_ms >= SHT30_CONVERSION_MS)) {
            sht_pending = 0;
            sensor_err = (sensors_sht30_fetch(&temp_001c, &hum_001pct) != 0) ? 1 : 0;
        }

        /* CO2 is a Modbus exchange with per-byte timeouts, up to 300 ms on the
         * first byte -- past what the RX ring absorbs at 921600, so the ESP is
         * asked to hold the pixel stream across it. */
        if (now - last_co2_ms >= 30000) {
            last_co2_ms = now;

            protocol_send_flow(0);
            uint32_t drain_start = periph_millis();
            while (periph_millis() - drain_start < 8)
                protocol_process_rx();

            uint8_t wm = 0;
            if (sensors_poll_co2(&co2_ppm, &wm) == 0) {
                warmup = wm;
            }

            protocol_send_flow(1);
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
#endif /* DIAG_MINIMAL */
