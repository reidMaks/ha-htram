#include "periph.h"
#include "gd32f150.h"

static void buzzer_init(void);

static void system_clock_config(void)
{
    /* Enable IRC8M */
    RCU_CTL |= (1 << 0); /* IRC8MEN */
    while ((RCU_CTL & (1 << 1)) == 0); /* Wait for IRC8MSTB */

    /* Flash latency: 2 wait states for 72MHz */
    uint32_t fmc_ws = *(volatile uint32_t *)0x40022000;
    fmc_ws &= ~7U;
    fmc_ws |= 2U;
    *(volatile uint32_t *)0x40022000 = fmc_ws;

    /* AHB, APB2 prescalers = /1, APB1 prescaler = /2 */
    RCU_CFG0 &= ~(0x3FF0U);
    RCU_CFG0 |= (4U << 8); /* APB1PSC = 100 (/2) */

    /* Config PLL: SRC = IRC8M/2, MUL = 18 */
    /* Clear PLLSEL (bit 16), PLLMF[3:0] (bits 21:18), PLLMF4 (bit 27) */
    RCU_CFG0 &= ~((1U << 16) | (0xFU << 18) | (1U << 27));
    /* Set PLLMF to 18 (bit 27 = 1, bits 21:18 = 0001) */
    RCU_CFG0 |= (1U << 18) | (1U << 27);

    /* Enable PLL */
    RCU_CTL |= (1U << 24); /* PLLEN */
    while ((RCU_CTL & (1U << 25)) == 0); /* Wait for PLLSTB */

    /* Select PLL as system clock */
    RCU_CFG0 = (RCU_CFG0 & ~3U) | 2U;

    /* Wait until PLL is used as system clock */
    while ((RCU_CFG0 & (3U << 2)) != (2U << 2));
}

void periph_init(void)
{
    /* Configure system clock to 72MHz via PLL */
    system_clock_config();

    /* Enable GPIO Clocks */
    RCU_AHBEN |= RCU_AHBEN_PAEN | RCU_AHBEN_PBEN | RCU_AHBEN_PCEN;

    /* 1. System Power Latch Rails */
    gpio_cfg_out_pp(GPIOC_BASE, 15);
    GPIOC_BOP = (1 << 15); /* PC15 = 1 (Main DC-DC Latch) */

    gpio_cfg_out_pp(GPIOB_BASE, 3);
    GPIOB_BOP = (1 << 3);  /* PB3 = 1 (Peripheral Power Rail) */

    gpio_cfg_out_pp(GPIOA_BASE, 1);
    GPIOA_BOP = (1 << 1);  /* PA1 = 1 (VLED Switch Enable) */

    /* PF7 = Master peripheral power enable (display panel + ESP32 rail).
     * The factory firmware drives PF7 HIGH to power the system on; ours never
     * enabled GPIOF at all. Masked until the first true cold boot: the display
     * and ESP had been living off the factory-latched rail, which survived our
     * pyocd resets but not a battery pull. Confirmed by reading the live
     * factory GPIO state in the on state -- RCU_AHBEN PFEN set, PF7 output HIGH.
     * The GD32 domain is always powered from the battery; PF7 gates the rest. */
    RCU_AHBEN |= RCU_AHBEN_PFEN;
    gpio_cfg_out_pp(GPIOF_BASE, 7);
    GPIO_BOP(GPIOF_BASE) = (1 << 7);
    delay_ms(20); /* let the peripheral rail settle before display/ESP init */

    /* 2. Front LEDs (PC14 Green, PB4 Yellow, PB5 Red) */
    gpio_cfg_out_pp(GPIOC_BASE, 14);
    gpio_cfg_out_pp(GPIOB_BASE, 4);
    gpio_cfg_out_pp(GPIOB_BASE, 5);
    periph_set_leds(0, 0, 0, 0);

    /* 3. Buzzer (PB0 = TIMER2_CH2 PWM tone) */
    buzzer_init();
    GPIOB_BC = (1 << 0);

    /* 4. Button SW1 (PA0, Active-HIGH) */
    gpio_cfg_in(GPIOA_BASE, 0, 2); /* Pulldown */

    /* 5. USB / Charging Detect (PA15 with pullup, PC13 input) */
    gpio_cfg_in(GPIOA_BASE, 15, 1); /* Pullup */
    gpio_cfg_in(GPIOC_BASE, 13, 0); /* Float */

    /* 6. ADC Initialization for Battery (PB1 / Ch9, confirmed ADC_IN9 in the
     *    GD32F150xx datasheet §pinout) */
    RCU_APB2EN |= RCU_APB2EN_ADCEN;
    *(volatile uint32_t *)0x40021004 |= 0x8000; /* RCU_CFG0 APB2/6 */
    *(volatile uint32_t *)0x40021030 |= 0x0100; /* RCU_CFG2 ADC clock source */

    /* PB2 is driven HIGH once and then left alone. It gates neither the SHT30
     * nor the battery divider: bench probe tools/swd/battery_test.c reads the
     * SHT30 with valid CRC at PB2=0, and channel 9 returns the same count
     * (2589 vs 2590) either way. The datasheet gives PB2 no analog function at
     * all. HIGH matches the factory init; toggling it per reading only risked
     * disturbing the bus for nothing. */
    gpio_cfg_out_pp(GPIOB_BASE, 2);
    GPIOB_BOP = (1 << 2);

    /* PB1 as Analog: Mode 11, No pull */
    uint32_t ctl_b = GPIO_CTL(GPIOB_BASE);
    ctl_b |= (3U << (1 * 2));
    GPIO_CTL(GPIOB_BASE) = ctl_b;
    GPIO_PUD(GPIOB_BASE) &= ~(3U << (1 * 2));

    ADC_RSQ0 = 0; /* Sequence length = 1 */
    ADC_RSQ2 = 9; /* Channel 9 (PB1) */
    ADC_SAMPT1 = (7 << (9 * 3)); /* 239.5 cycles */

    /* Software trigger for the regular sequence: ETSRC = 0b111 (SWRCST) with
     * ETERC enabled. Asserting SWRCST alone left EOC unset on the first
     * conversion after calibration (see battery_test.c test 2), which made the
     * very first battery reading stale. */
    ADC_CTL1 = (ADC_CTL1 & ~(7U << 17)) | (7U << 17) | (1U << 20);

    /* Power ON ADC */
    ADC_CTL1 |= 1;
    delay_us(500);

    /* Reset Calibration */
    ADC_CTL1 |= (1 << 3);
    uint32_t to = 100000;
    while ((ADC_CTL1 & (1 << 3)) && --to) ;

    /* Start Calibration */
    ADC_CTL1 |= (1 << 2);
    to = 100000;
    while ((ADC_CTL1 & (1 << 2)) && --to) ;
}

/* Last commanded LED state, bit0=red bit1=yellow bit2=green. Mirrored to the
 * ESP over telemetry so Home Assistant reflects what the GD32 actually shows. */
static volatile uint8_t g_led_state;

void periph_set_leds(uint8_t red, uint8_t yellow, uint8_t green, uint8_t brightness)
{
    (void)brightness; /* hardware has no LED dimming; on/off only */

    g_led_state = (uint8_t)((red ? 1 : 0) | (yellow ? 2 : 0) | (green ? 4 : 0));

    /* VLED Anode Switch */
    if (red || yellow || green) {
        GPIOA_BOP = (1 << 1);
    } else {
        GPIOA_BC = (1 << 1);
    }

    if (green)  GPIOC_BOP = (1 << 14); else GPIOC_BC = (1 << 14);
    if (yellow) GPIOB_BOP = (1 << 4);  else GPIOB_BC = (1 << 4);
    if (red)    GPIOB_BOP = (1 << 5);  else GPIOB_BC = (1 << 5);
}

uint8_t periph_get_led_state(void)
{
    return g_led_state;
}

/* ── Buzzer: PB0 = TIMER2_CH2 (AF1). Tone via PWM, non-blocking melody player.
 * Timer ticks at 1 MHz so a note of f Hz uses CAR = 1e6/f, 50% duty. ── */
#define BUZZER_TICK_HZ 1000000u
#define MELODY_MAX     96u

static uint16_t mel_freq[MELODY_MAX];
static uint16_t mel_dur[MELODY_MAX];
static volatile uint8_t mel_count;
static volatile uint8_t mel_idx;
static volatile uint8_t mel_active;
static volatile uint32_t mel_now_ms;
static volatile uint32_t mel_note_end_ms;

static void buzzer_tone(uint16_t freq)
{
    if (freq == 0) {
        TIMER2_CH2CV = 0; /* rest: 0% duty -> silent */
        return;
    }
    uint32_t car = BUZZER_TICK_HZ / freq;
    if (car < 2) car = 2;
    if (car > 65536u) car = 65536u;
    TIMER2_CAR = car - 1u;
    TIMER2_CH2CV = car / 2u; /* 50% duty square wave */
    TIMER2_SWEVG = 1u;       /* UPG: latch CAR/CH2CV, restart period */
}

static void buzzer_init(void)
{
    RCU_APB1EN |= RCU_APB1EN_TIMER2EN;

    /* PB0: alternate-function mode, push-pull, high speed, AF1 = TIMER2_CH2 */
    uint32_t ctl = GPIO_CTL(GPIOB_BASE);
    ctl &= ~(3u << (0 * 2));
    ctl |= (2u << (0 * 2));            /* 10 = AF */
    GPIO_CTL(GPIOB_BASE) = ctl;
    GPIO_OMD(GPIOB_BASE) &= ~(1u << 0);
    GPIO_OSPD(GPIOB_BASE) |= (3u << (0 * 2));
    uint32_t af = GPIO_AFSEL0(GPIOB_BASE);
    af &= ~(0xFu << 0);               /* pin 0 -> AFSEL0[3:0] */
    af |= (1u << 0);                  /* AF1 */
    GPIO_AFSEL0(GPIOB_BASE) = af;

    TIMER2_PSC = (SYSTEM_CLOCK_HZ / BUZZER_TICK_HZ) - 1u; /* 1 MHz tick */
    TIMER2_CAR = 999;                 /* placeholder */
    TIMER2_CH2CV = 0;                 /* silent */
    TIMER2_CHCTL1 = (6u << 4) | (1u << 3); /* CH2 PWM mode 0 + compare preload */
    TIMER2_CHCTL2 = (1u << 8);        /* CH2EN, active-high (CH2P=0) */
    TIMER2_SWEVG = 1u;
    TIMER2_CTL0 = (1u << 7) | (1u << 0); /* ARSE | CEN */
}

void periph_buzzer_tick(uint32_t now_ms)
{
    mel_now_ms = now_ms;
    if (!mel_active) return;
    if ((int32_t)(now_ms - mel_note_end_ms) >= 0) {
        mel_idx++;
        if (mel_idx >= mel_count) {
            buzzer_tone(0);
            mel_active = 0;
            return;
        }
        buzzer_tone(mel_freq[mel_idx]); /* freq 0 => rest */
        mel_note_end_ms = now_ms + mel_dur[mel_idx];
    }
}

void periph_play_melody(const uint8_t *notes4, uint8_t count)
{
    if (count == 0) {
        buzzer_tone(0);
        mel_active = 0;
        return;
    }
    if (count > MELODY_MAX) count = MELODY_MAX;
    for (uint8_t i = 0; i < count; i++) {
        mel_freq[i] = (uint16_t)notes4[i * 4] | ((uint16_t)notes4[i * 4 + 1] << 8);
        mel_dur[i] = (uint16_t)notes4[i * 4 + 2] | ((uint16_t)notes4[i * 4 + 3] << 8);
    }
    mel_count = count;
    mel_idx = 0;
    mel_active = 1;
    buzzer_tone(mel_freq[0]);
    mel_note_end_ms = mel_now_ms + mel_dur[0];
}

void periph_beep(uint16_t freq_hz, uint16_t duration_ms)
{
    if (duration_ms == 0) return;
    mel_freq[0] = (freq_hz == 0) ? 2304 : freq_hz;
    mel_dur[0] = duration_ms;
    mel_count = 1;
    mel_idx = 0;
    mel_active = 1;
    buzzer_tone(mel_freq[0]);
    mel_note_end_ms = mel_now_ms + duration_ms;
}

void periph_beep_blocking(uint16_t freq_hz, uint16_t duration_ms)
{
    if (duration_ms == 0) return;
    if (freq_hz == 0) freq_hz = 2304;
    mel_active = 0; /* cancel any melody in progress */
    buzzer_tone(freq_hz);
    delay_ms(duration_ms);
    buzzer_tone(0);
}

int periph_read_button(void)
{
    return gpio_get(GPIOA_BASE, 0);
}

int periph_read_battery(uint16_t *batt_mv, uint8_t *is_usb_present, uint8_t *is_charging)
{
    /* PC13: 1 = USB 5V VBUS present, 0 = on battery */
    int usb = gpio_get(GPIOC_BASE, 13);

    /* PA15: Charger status (active only when USB 5V is present) */
    int chrg_pin = gpio_get(GPIOA_BASE, 15);

    if (is_usb_present) {
        *is_usb_present = usb ? 1 : 0;
    }
    if (is_charging) {
        *is_charging = (usb && chrg_pin) ? 1 : 0;
    }

    /* Start conversion on Channel 9 (PB1) */
    ADC_STAT = 0; /* Clear EOC */
    ADC_RSQ0 = 0;
    ADC_RSQ2 = 9;
    ADC_CTL1 |= (1 << 22); /* SWRCST */

    uint32_t to = 200000;
    while (!(ADC_STAT & (1 << 1)) && --to) ;
    if (!to) {
        return -1; /* conversion never completed; leave *batt_mv untouched */
    }

    uint16_t raw = (uint16_t)(ADC_RDATA & 0xFFFF);

    /* Factory formula: mV = (raw * 3275) >> 11. Validated on the bench against
     * the reference unit's own factory telemetry: raw 2588 -> 4138 mV here vs
     * 4143 mV reported over MQTT by a device on the same charger. Deriving the
     * scale from Vrefint instead lands at 4176 mV, so the factory constant --
     * not a nominal 2:1 divider at 3.3 V -- is the accurate one. */
    uint32_t mv = ((uint32_t)raw * 3275UL) >> 11;
    if (batt_mv) {
        *batt_mv = (uint16_t)mv;
    }
    return 0;
}

void watchdog_init(void)
{
    /* FWDGT runs from independent IRC40K (~40 kHz) */
    FWDGT_CTL = FWDGT_KEY_ENABLE;
    FWDGT_CTL = FWDGT_KEY_ACCESS;

    /* Prescaler 64: 40kHz / 64 = 625 Hz (1.6 ms per tick) */
    FWDGT_PSC = 4;

    /* Reload: 1875 ticks * 1.6ms = ~3.0 seconds timeout */
    FWDGT_RLD = 1875;

    FWDGT_CTL = FWDGT_KEY_RELOAD;
}

void watchdog_kick(void)
{
    FWDGT_CTL = FWDGT_KEY_RELOAD;
}

void system_enter_bootloader(void)
{
    /* 1. Disable all interrupts */
    __asm__ volatile("cpsid i");

    /* 2. Disable SysTick */
    *(volatile uint32_t *)0xE000E010 = 0;
    *(volatile uint32_t *)0xE000E014 = 0;
    *(volatile uint32_t *)0xE000E018 = 0;

    /* 3. Disable all NVIC interrupts and clear pending flags */
    *(volatile uint32_t *)0xE000E180 = 0xFFFFFFFF;
    *(volatile uint32_t *)0xE000E280 = 0xFFFFFFFF;

    /* 4. Reset clock to internal 8MHz IRC8M, disable PLL and reset all prescalers */
    RCU_CTL |= (1 << 0);
    while ((RCU_CTL & (1 << 1)) == 0);
    RCU_CFG0 &= ~3U;
    while ((RCU_CFG0 & (3U << 2)) != 0);
    RCU_CTL &= ~(1 << 24); /* Disable PLL */
    while ((RCU_CTL & (1 << 25)) != 0);
    RCU_CFG0 = 0x00000000; /* Factory default: AHB=/1, APB1=/1 (8MHz), APB2=/1 */

    /* Reset Flash latency to 0 wait states */
    *(volatile uint32_t *)0x40022000 &= ~7U;

    /* 5. Reset and disable APB1 and APB2 peripherals */
    RCU_APB1RST = 0xFFFFFFFF;
    RCU_APB1RST = 0;
    RCU_APB2RST = 0xFFFFFFFF;
    RCU_APB2RST = 0;
    RCU_APB1EN = 0;
    RCU_APB2EN = 0;

    /* 6. Hold power latches and power CO2 sensor so PA10 (USART0_RX) stays IDLE HIGH (3.3V) */
    RCU_AHBEN |= (1 << 17) | (1 << 18) | (1 << 19); /* GPIOA, GPIOB, GPIOC */

    /* PC15 = 1 (Main DC-DC) */
    uint32_t ctl_c = GPIO_CTL(GPIOC_BASE);
    ctl_c &= ~(3U << (15 * 2));
    ctl_c |=  (1U << (15 * 2));
    GPIO_CTL(GPIOC_BASE) = ctl_c;
    GPIO_BOP(GPIOC_BASE) = (1 << 15);

    /* PB3 = 1 (Periph), PB9 = 1 (CO2 Pwr), PB11 = 1 (5V Boost) */
    uint32_t ctl_b = GPIO_CTL(GPIOB_BASE);
    ctl_b &= ~((3U << (3 * 2)) | (3U << (9 * 2)) | (3U << (11 * 2)));
    ctl_b |=  ((1U << (3 * 2)) | (1U << (9 * 2)) | (1U << (11 * 2)));
    GPIO_CTL(GPIOB_BASE) = ctl_b;
    GPIO_BOP(GPIOB_BASE) = (1 << 3) | (1 << 9) | (1 << 11);

    /* 7. Set Vector Table to System Memory */
    uint32_t boot_addr = 0x1FFFEC00;
    SCB_VTOR = boot_addr;

    /* 7. Read stack pointer and reset handler from bootloader vector */
    uint32_t msp_val = *(volatile uint32_t *)(boot_addr);
    uint32_t pc_val  = *(volatile uint32_t *)(boot_addr + 4);

    /* 8. Load MSP, re-enable interrupts (PRIMASK=0) and jump to bootloader */
    __asm__ volatile(
        "msr msp, %0\n"
        "cpsie i\n"
        "bx  %1\n"
        :
        : "r"(msp_val), "r"(pc_val)
        :
    );

    while (1) {
        /* Should not be reached */
    }
}
