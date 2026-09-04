#include "periph.h"
#include "gd32f150.h"

void periph_init(void)
{
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

    /* 3. Buzzer (PB0) */
    gpio_cfg_out_pp(GPIOB_BASE, 0);
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
    delay_cycles(2000);

    /* Reset Calibration */
    ADC_CTL1 |= (1 << 3);
    uint32_t to = 100000;
    while ((ADC_CTL1 & (1 << 3)) && --to) ;

    /* Start Calibration */
    ADC_CTL1 |= (1 << 2);
    to = 100000;
    while ((ADC_CTL1 & (1 << 2)) && --to) ;
}

void periph_set_leds(uint8_t red, uint8_t yellow, uint8_t green, uint8_t brightness)
{
    /* VLED Anode Switch */
    if (red || yellow || green || brightness > 0) {
        GPIOA_BOP = (1 << 1);
    } else {
        GPIOA_BC = (1 << 1);
    }

    if (green)  GPIOC_BOP = (1 << 14); else GPIOC_BC = (1 << 14);
    if (yellow) GPIOB_BOP = (1 << 4);  else GPIOB_BC = (1 << 4);
    if (red)    GPIOB_BOP = (1 << 5);  else GPIOB_BC = (1 << 5);
}

void periph_beep(uint16_t freq_hz, uint16_t duration_ms)
{
    if (freq_hz == 0) freq_hz = 2304; /* Default resonant frequency */
    if (duration_ms == 0) return;

    /* Period in us = 1,000,000 / freq */
    uint32_t period_us = 1000000UL / freq_hz;
    uint32_t half_period = period_us / 2;
    uint32_t cycles = ((uint32_t)duration_ms * 1000UL) / period_us;

    /* Loop iterations for half-period (~4 loops per us @ 8MHz) */
    uint32_t delay_cnt = half_period * 2;

    for (uint32_t i = 0; i < cycles; i++) {
        GPIOB_BOP = (1 << 0);
        delay_cycles(delay_cnt);
        GPIOB_BC = (1 << 0);
        delay_cycles(delay_cnt);
    }
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

    /* 2. De-assert and reset peripherals */
    RCU_APB1RST = 0xFFFFFFFF;
    RCU_APB1RST = 0;
    RCU_APB2RST = 0xFFFFFFFF;
    RCU_APB2RST = 0;

    /* 3. Remap system memory to 0x00000000 or jump directly to ROM loader at 0x1FFFEC00 */
    uint32_t boot_addr = 0x1FFFEC00;
    uint32_t msp_val = *(volatile uint32_t *)(boot_addr);
    uint32_t pc_val  = *(volatile uint32_t *)(boot_addr + 4);

    SCB_VTOR = boot_addr;

    __asm__ volatile(
        "msr msp, %0\n"
        "bx  %1\n"
        :
        : "r"(msp_val), "r"(pc_val)
        :
    );

    while (1) {
        /* Should not be reached */
    }
}
