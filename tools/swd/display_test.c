/*
 * HTRAM GD32F150 ST7789 Display Bench Probe — Runs entirely from SRAM (0x20000000)
 *
 * Does NOT touch or erase Flash memory (Maintains RDP Level 1 protection).
 *
 * ST7789 Display Mapping (Reversed from factory firmware 0x080043E8, 0x08005180):
 *   - PB12: RES (Reset, active LOW)
 *   - PB13: SCK (Clock, idle HIGH)
 *   - PB14: CS  (Chip Select, active LOW)
 *   - PB15: SDA (MOSI Data, 9-bit mode: D/C bit + 8 data bits)
 *   - PB8:  Backlight (Active-HIGH / TIMER15_CH0 PWM)
 *
 * Power Rails:
 *   - PC15 = 1: System DC-DC Latch
 *   - PB3  = 1: Peripheral Power Rail
 *   - PA1  = 1: VLED Switch
 *
 * Telemetry:
 *   - USART1 TX on PA2 @ 115200 8N1 -> Pico GP5 / Debugprobe
 *   - Button SW1 on PA0: cycle patterns
 *   - Buzzer on PB0: short feedback chirp
 */

#include <stdint.h>

/* ── RCU Registers ── */
#define RCU_BASE      0x40021000
#define RCU_AHBEN     (*(volatile uint32_t *)(RCU_BASE + 0x14))
#define RCU_APB2EN    (*(volatile uint32_t *)(RCU_BASE + 0x18))
#define RCU_APB1EN    (*(volatile uint32_t *)(RCU_BASE + 0x1C))

/* ── GPIO Registers ── */
#define GPIOA_BASE    0x48000000
#define GPIOB_BASE    0x48000400
#define GPIOC_BASE    0x48000800

#define GPIO_CTL(b)   (*(volatile uint32_t *)((b) + 0x00))
#define GPIO_OMD(b)   (*(volatile uint32_t *)((b) + 0x04))
#define GPIO_OSPD(b)  (*(volatile uint32_t *)((b) + 0x08))
#define GPIO_PUD(b)   (*(volatile uint32_t *)((b) + 0x0C))
#define GPIO_ISTAT(b) (*(volatile uint32_t *)((b) + 0x10))
#define GPIO_OCTL(b)  (*(volatile uint32_t *)((b) + 0x14))
#define GPIO_BOP(b)   (*(volatile uint32_t *)((b) + 0x18))
#define GPIO_AFSEL0(b)(*(volatile uint32_t *)((b) + 0x20))
#define GPIO_AFSEL1(b)(*(volatile uint32_t *)((b) + 0x24))
#define GPIO_BC(b)    (*(volatile uint32_t *)((b) + 0x28))

#define GPIOA_BOP     GPIO_BOP(GPIOA_BASE)
#define GPIOB_BOP     GPIO_BOP(GPIOB_BASE)
#define GPIOC_BOP     GPIO_BOP(GPIOC_BASE)

#define GPIOA_BC      GPIO_BC(GPIOA_BASE)
#define GPIOB_BC      GPIO_BC(GPIOB_BASE)
#define GPIOC_BC      GPIO_BC(GPIOC_BASE)

/* ── USART1 Registers (115200 8N1 on PA2) ── */
#define USART1_BASE   0x40004400
#define USART1_CTL0   (*(volatile uint32_t *)(USART1_BASE + 0x00))
#define USART1_BAUD   (*(volatile uint32_t *)(USART1_BASE + 0x0C))
#define USART1_STAT   (*(volatile uint32_t *)(USART1_BASE + 0x1C))
#define USART1_TDATA  (*(volatile uint32_t *)(USART1_BASE + 0x28))

#define UEN   (1 << 0)
#define REN   (1 << 2)
#define TEN   (1 << 3)
#define TBE   (1 << 7)

/* ── RGB565 Colors ── */
#define COLOR_BLACK   0x0000
#define COLOR_WHITE   0xFFFF
#define COLOR_RED     0xF800
#define COLOR_GREEN   0x07E0
#define COLOR_BLUE    0x001F
#define COLOR_YELLOW  0xFFE0
#define COLOR_CYAN    0x07FF
#define COLOR_MAGENTA 0xF81F
#define COLOR_ORANGE  0xFD20
#define COLOR_GRAY    0x8410

/* ── Delays ── */
static inline void delay_cycles(uint32_t n)
{
    while (n--) {
        __asm__ volatile("");
    }
}

static void delay_ms(uint32_t ms)
{
    while (ms--) {
        /* ~2000 cycles for 1ms @ 8MHz IRC8M */
        delay_cycles(2000);
    }
}

/* ── UART Output ── */
static void uart_putc(uint8_t c)
{
    while (!(USART1_STAT & TBE))
        ;
    USART1_TDATA = c;
}

static void uart_puts(const char *s)
{
    while (*s)
        uart_putc((uint8_t)*s++);
}

static void print_dec(uint32_t n)
{
    if (n == 0) {
        uart_putc('0');
        return;
    }
    char buf[12];
    int i = 0;
    while (n > 0) {
        buf[i++] = '0' + (n % 10);
        n /= 10;
    }
    while (i > 0) {
        uart_putc(buf[--i]);
    }
}

static void print_hex8(uint8_t val)
{
    const char hex[] = "0123456789ABCDEF";
    uart_putc(hex[(val >> 4) & 0x0F]);
    uart_putc(hex[val & 0x0F]);
}

/* ── GPIO Helpers ── */
static void gpio_cfg_out(uint32_t base, int pin)
{
    uint32_t ctl = GPIO_CTL(base);
    ctl &= ~(3 << (pin * 2));
    ctl |= (1 << (pin * 2)); /* 01 = Output */
    GPIO_CTL(base) = ctl;

    GPIO_OMD(base) &= ~(1 << pin); /* Push-Pull */

    uint32_t ospd = GPIO_OSPD(base);
    ospd &= ~(3 << (pin * 2));
    ospd |= (3 << (pin * 2)); /* 50MHz */
    GPIO_OSPD(base) = ospd;

    uint32_t pud = GPIO_PUD(base);
    pud &= ~(3 << (pin * 2)); /* No pull */
    GPIO_PUD(base) = pud;
}

static void gpio_cfg_in_pull(uint32_t base, int pin, int pull_up)
{
    uint32_t ctl = GPIO_CTL(base);
    ctl &= ~(3 << (pin * 2)); /* 00 = Input */
    GPIO_CTL(base) = ctl;

    uint32_t pud = GPIO_PUD(base);
    pud &= ~(3 << (pin * 2));
    pud |= ((pull_up ? 1 : 2) << (pin * 2));
    GPIO_PUD(base) = pud;
}

static inline int gpio_read_pin(uint32_t base, int pin)
{
    return (GPIO_ISTAT(base) & (1 << pin)) ? 1 : 0;
}

/* ── Buzzer Chirp on PB0 ── */
static void buzzer_chirp(uint32_t duration_ms)
{
    /* Toggle PB0 ~2.3 kHz (period ~434 us, half-period ~217 us) */
    uint32_t cycles = (duration_ms * 1000) / 434;
    for (uint32_t i = 0; i < cycles; i++) {
        GPIOB_BOP = (1 << 0);
        delay_cycles(430);
        GPIOB_BC = (1 << 0);
        delay_cycles(430);
    }
}

/* ── Front LEDs ── */
static void set_leds(int g, int o, int r)
{
    if (g) GPIOC_BOP = (1 << 14); else GPIOC_BC = (1 << 14);
    if (o) GPIOB_BOP = (1 << 4);  else GPIOB_BC = (1 << 4);
    if (r) GPIOB_BOP = (1 << 5);  else GPIOB_BC = (1 << 5);
}

/* ── ST7789 3-Wire 9-Bit SPI Driver ── */

static void lcd_send_cmd(uint8_t cmd)
{
    GPIOB_BC = (1 << 14); /* CS = 0 */

    /* 9th bit: D/C = 0 (Command) */
    GPIOB_BC = (1 << 15); /* SDA = 0 */
    GPIOB_BC = (1 << 13); /* SCK = 0 */
    __asm__ volatile("nop");
    GPIOB_BOP = (1 << 13); /* SCK = 1 */
    __asm__ volatile("nop");

    /* 8 command bits (MSB first) */
    for (int i = 0; i < 8; i++) {
        if (cmd & 0x80) {
            GPIOB_BOP = (1 << 15);
        } else {
            GPIOB_BC = (1 << 15);
        }
        cmd <<= 1;
        GPIOB_BC = (1 << 13);
        __asm__ volatile("nop");
        GPIOB_BOP = (1 << 13);
        __asm__ volatile("nop");
    }

    GPIOB_BOP = (1 << 14); /* CS = 1 */
}

static void lcd_send_data(uint8_t data)
{
    GPIOB_BC = (1 << 14); /* CS = 0 */

    /* 9th bit: D/C = 1 (Data) */
    GPIOB_BOP = (1 << 15); /* SDA = 1 */
    GPIOB_BC = (1 << 13);  /* SCK = 0 */
    __asm__ volatile("nop");
    GPIOB_BOP = (1 << 13);  /* SCK = 1 */
    __asm__ volatile("nop");

    /* 8 data bits (MSB first) */
    for (int i = 0; i < 8; i++) {
        if (data & 0x80) {
            GPIOB_BOP = (1 << 15);
        } else {
            GPIOB_BC = (1 << 15);
        }
        data <<= 1;
        GPIOB_BC = (1 << 13);
        __asm__ volatile("nop");
        GPIOB_BOP = (1 << 13);
        __asm__ volatile("nop");
    }

    GPIOB_BOP = (1 << 14); /* CS = 1 */
}

/* Fast 16-bit pixel send (matches factory function 0x0800523E) */
static void lcd_send_pixel(uint16_t color)
{
    uint8_t b1 = (uint8_t)(color >> 8);
    uint8_t b2 = (uint8_t)(color & 0xFF);

    GPIOB_BC = (1 << 14); /* CS = 0 */

    /* Byte 1: D/C = 1 */
    GPIOB_BOP = (1 << 15);
    GPIOB_BC = (1 << 13);
    GPIOB_BOP = (1 << 13);
    for (int i = 0; i < 8; i++) {
        if (b1 & 0x80) GPIOB_BOP = (1 << 15); else GPIOB_BC = (1 << 15);
        b1 <<= 1;
        GPIOB_BC = (1 << 13);
        GPIOB_BOP = (1 << 13);
    }

    /* Byte 2: D/C = 1 */
    GPIOB_BOP = (1 << 15);
    GPIOB_BC = (1 << 13);
    GPIOB_BOP = (1 << 13);
    for (int i = 0; i < 8; i++) {
        if (b2 & 0x80) GPIOB_BOP = (1 << 15); else GPIOB_BC = (1 << 15);
        b2 <<= 1;
        GPIOB_BC = (1 << 13);
        GPIOB_BOP = (1 << 13);
    }

    GPIOB_BOP = (1 << 14); /* CS = 1 */
}

/* Address window set (CASET + RASET + RAMWR) */
static void lcd_set_window(uint16_t x0, uint16_t y0, uint16_t x1, uint16_t y1)
{
    lcd_send_cmd(0x2A); /* CASET */
    lcd_send_data((uint8_t)(x0 >> 8));
    lcd_send_data((uint8_t)(x0 & 0xFF));
    lcd_send_data((uint8_t)(x1 >> 8));
    lcd_send_data((uint8_t)(x1 & 0xFF));

    lcd_send_cmd(0x2B); /* RASET */
    lcd_send_data((uint8_t)(y0 >> 8));
    lcd_send_data((uint8_t)(y0 & 0xFF));
    lcd_send_data((uint8_t)(y1 >> 8));
    lcd_send_data((uint8_t)(y1 & 0xFF));

    lcd_send_cmd(0x2C); /* RAMWR */
}

/* Fill rectangular area */
static void lcd_fill_rect(uint16_t x0, uint16_t y0, uint16_t x1, uint16_t y1, uint16_t color)
{
    if (x0 > 239) x0 = 239;
    if (x1 > 239) x1 = 239;
    if (y0 > 239) y0 = 239;
    if (y1 > 239) y1 = 239;
    if (x0 > x1 || y0 > y1) return;

    lcd_set_window(x0, y0, x1, y1);
    uint32_t count = (uint32_t)(x1 - x0 + 1) * (uint32_t)(y1 - y0 + 1);
    for (uint32_t i = 0; i < count; i++) {
        lcd_send_pixel(color);
    }
}

/* Fill full 240x240 screen */
static void lcd_fill_screen(uint16_t color)
{
    lcd_fill_rect(0, 0, 239, 239, color);
}

/* ST7789 Complete Initialization Sequence */
static void lcd_init(void)
{
    uart_puts("[LCD] Hardware Reset (PB12)...\r\n");
    GPIOB_BC = (1 << 12); /* RES LOW */
    delay_ms(150);
    GPIOB_BOP = (1 << 12); /* RES HIGH */
    delay_ms(150);

    uart_puts("[LCD] Software Reset (0x01) & Sleep Out (0x11)...\r\n");
    lcd_send_cmd(0x01); /* SWRESET */
    delay_ms(150);

    lcd_send_cmd(0x11); /* SLPOUT */
    delay_ms(200);

    uart_puts("[LCD] Programming ST7789 Registers from Factory ROM...\r\n");

    /* Porch control: B2 1F 1F 00 33 33 */
    lcd_send_cmd(0xB2);
    lcd_send_data(0x1F);
    lcd_send_data(0x1F);
    lcd_send_data(0x00);
    lcd_send_data(0x33);
    lcd_send_data(0x33);

    /* Tearing Effect ON: 35 00 */
    lcd_send_cmd(0x35);
    lcd_send_data(0x00);

    /* Memory Access Control: 36 00 (Standard orientation, RGB) */
    lcd_send_cmd(0x36);
    lcd_send_data(0x00);

    /* Interface Pixel Format: 3A 05 (16-bit RGB565) */
    lcd_send_cmd(0x3A);
    lcd_send_data(0x05);

    /* Gate Control: B7 02 */
    lcd_send_cmd(0xB7);
    lcd_send_data(0x02);

    /* VCOM Setting: BB 31 */
    lcd_send_cmd(0xBB);
    lcd_send_data(0x31);

    /* LCM Control: C0 2C */
    lcd_send_cmd(0xC0);
    lcd_send_data(0x2C);

    /* VDV and VRH Command Enable: C2 01 */
    lcd_send_cmd(0xC2);
    lcd_send_data(0x01);

    /* VRH Set: C3 19 */
    lcd_send_cmd(0xC3);
    lcd_send_data(0x19);

    /* VDV Set: C4 20 */
    lcd_send_cmd(0xC4);
    lcd_send_data(0x20);

    /* Frame Rate Control in Normal Mode: C6 13 */
    lcd_send_cmd(0xC6);
    lcd_send_data(0x13);

    /* Power Control 1: D0 A4 A1 */
    lcd_send_cmd(0xD0);
    lcd_send_data(0xA4);
    lcd_send_data(0xA1);

    /* Vendor Register D6: A1 */
    lcd_send_cmd(0xD6);
    lcd_send_data(0xA1);

    /* Positive Voltage Gamma: E0 */
    lcd_send_cmd(0xE0);
    lcd_send_data(0xF0);
    lcd_send_data(0x04);
    lcd_send_data(0x0A);
    lcd_send_data(0x09);
    lcd_send_data(0x0A);
    lcd_send_data(0x27);
    lcd_send_data(0x2C);
    lcd_send_data(0x43);
    lcd_send_data(0x42);
    lcd_send_data(0x38);
    lcd_send_data(0x13);
    lcd_send_data(0x13);
    lcd_send_data(0x27);
    lcd_send_data(0x2B);

    /* Negative Voltage Gamma: E1 */
    lcd_send_cmd(0xE1);
    lcd_send_data(0xF0);
    lcd_send_data(0x05);
    lcd_send_data(0x08);
    lcd_send_data(0x0A);
    lcd_send_data(0x08);
    lcd_send_data(0x04);
    lcd_send_data(0x2C);
    lcd_send_data(0x43);
    lcd_send_data(0x41);
    lcd_send_data(0x3A);
    lcd_send_data(0x16);
    lcd_send_data(0x16);
    lcd_send_data(0x28);
    lcd_send_data(0x2C);

    /* Gate / Vendor Register E4 */
    lcd_send_cmd(0xE4);
    lcd_send_data(0x1D);
    lcd_send_data(0x00);
    lcd_send_data(0x00);

    /* Display Inversion ON: 21 */
    lcd_send_cmd(0x21);

    uart_puts("[LCD] Clearing frame to Black (0x0000)...\r\n");
    lcd_fill_screen(COLOR_BLACK);

    uart_puts("[LCD] Display ON (0x29)...\r\n");
    lcd_send_cmd(0x29);
    delay_ms(120);

    uart_puts("[LCD] Backlight ON (PB8 = 1)...\r\n");
    GPIOB_BOP = (1 << 8); /* Backlight ON */
    delay_ms(50);
}

/* Draw Test Pattern */
static void draw_pattern(int pattern)
{
    switch (pattern) {
    case 0:
        /* Quad Colors + Center Box */
        uart_puts("[PATTERN 0] Quadrants (Red/Green/Blue/Yellow) + Center White\r\n");
        set_leds(1, 1, 1);
        lcd_fill_rect(0,   0,   119, 119, COLOR_RED);
        lcd_fill_rect(120, 0,   239, 119, COLOR_GREEN);
        lcd_fill_rect(0,   120, 119, 239, COLOR_BLUE);
        lcd_fill_rect(120, 120, 239, 239, COLOR_YELLOW);
        /* Center box */
        lcd_fill_rect(80, 80, 159, 159, COLOR_WHITE);
        /* 1-pixel border test around 240x240 boundary */
        lcd_fill_rect(0, 0, 239, 3, COLOR_WHITE);     /* Top */
        lcd_fill_rect(0, 236, 239, 239, COLOR_WHITE); /* Bottom */
        lcd_fill_rect(0, 0, 3, 239, COLOR_WHITE);     /* Left */
        lcd_fill_rect(236, 0, 239, 239, COLOR_WHITE); /* Right */
        break;

    case 1:
        /* Solid RED */
        uart_puts("[PATTERN 1] Solid RED (0xF800)\r\n");
        set_leds(0, 0, 1);
        lcd_fill_screen(COLOR_RED);
        break;

    case 2:
        /* Solid GREEN */
        uart_puts("[PATTERN 2] Solid GREEN (0x07E0)\r\n");
        set_leds(1, 0, 0);
        lcd_fill_screen(COLOR_GREEN);
        break;

    case 3:
        /* Solid BLUE */
        uart_puts("[PATTERN 3] Solid BLUE (0x001F)\r\n");
        set_leds(0, 1, 0);
        lcd_fill_screen(COLOR_BLUE);
        break;

    case 4:
        /* Solid WHITE */
        uart_puts("[PATTERN 4] Solid WHITE (0xFFFF)\r\n");
        set_leds(1, 1, 1);
        lcd_fill_screen(COLOR_WHITE);
        break;

    case 5:
        /* Horizontal Color Bars */
        uart_puts("[PATTERN 5] 8 Color Bars (White, Yellow, Cyan, Green, Magenta, Red, Blue, Black)\r\n");
        set_leds(1, 1, 0);
        uint16_t bars[8] = {
            COLOR_WHITE, COLOR_YELLOW, COLOR_CYAN, COLOR_GREEN,
            COLOR_MAGENTA, COLOR_RED, COLOR_BLUE, COLOR_BLACK
        };
        for (int b = 0; b < 8; b++) {
            uint16_t y0 = b * 30;
            uint16_t y1 = y0 + 29;
            lcd_fill_rect(0, y0, 239, y1, bars[b]);
        }
        break;
    }
}

/* ── Main Entry ── */
void main(void)
{
    /* 1. Enable Clocks */
    RCU_AHBEN  |= (1 << 17) | (1 << 18) | (1 << 19); /* GPIOA, GPIOB, GPIOC */
    RCU_APB1EN |= (1 << 17); /* USART1 */

    /* 2. System Power Rails */
    gpio_cfg_out(GPIOC_BASE, 15);
    GPIOC_BOP = (1 << 15); /* PC15 = 1: Main DC-DC latch */

    gpio_cfg_out(GPIOB_BASE, 3);
    GPIOB_BOP = (1 << 3);  /* PB3 = 1: Peripheral power rail */

    gpio_cfg_out(GPIOA_BASE, 1);
    GPIOA_BOP = (1 << 1);  /* PA1 = 1: VLED Switch */

    /* 3. Front LEDs & Buzzer */
    gpio_cfg_out(GPIOC_BASE, 14); /* 2x Green LEDs */
    gpio_cfg_out(GPIOB_BASE, 4);  /* 1x Yellow/Orange LED */
    gpio_cfg_out(GPIOB_BASE, 5);  /* 2x Red LEDs */
    gpio_cfg_out(GPIOB_BASE, 0);  /* Buzzer */
    GPIOB_BC = (1 << 0);
    set_leds(1, 0, 0); /* Green ON initially */

    /* 4. Button SW1 on PA0 (Active HIGH) */
    gpio_cfg_in_pull(GPIOA_BASE, 0, 0); /* Pulldown */

    /* 5. USART1 on PA2 (TX) @ 115200 8N1 */
    uint32_t ctl_a = GPIO_CTL(GPIOA_BASE);
    ctl_a &= ~(3 << (2 * 2));
    ctl_a |= (2 << (2 * 2)); /* PA2: AF */
    GPIO_CTL(GPIOA_BASE) = ctl_a;

    GPIO_AFSEL0(GPIOA_BASE) &= ~(0x0F << (2 * 4));
    GPIO_AFSEL0(GPIOA_BASE) |= (1 << (2 * 4)); /* AF1 = USART1_TX */

    USART1_CTL0 = 0;
    USART1_BAUD = 0x0045; /* 8MHz / 115200 = 69 (0x45) */
    USART1_CTL0 = UEN | TEN;

    delay_ms(20);

    uart_puts("\r\n\r\n");
    uart_puts("====================================================\r\n");
    uart_puts("  Honeywell HTRAM ST7789 Display Live Bench Probe   \r\n");
    uart_puts("  MCU: GD32F150C8T6 | SRAM Execution (0x20000000)   \r\n");
    uart_puts("====================================================\r\n");
    uart_puts("[INFO] Initializing display GPIO pins (PB8, PB12..PB15)...\r\n");

    /* 6. Configure Display GPIOs */
    gpio_cfg_out(GPIOB_BASE, 8);  /* Backlight (Active-HIGH) */
    gpio_cfg_out(GPIOB_BASE, 12); /* RES (Active-LOW) */
    gpio_cfg_out(GPIOB_BASE, 13); /* SCK */
    gpio_cfg_out(GPIOB_BASE, 14); /* CS (Active-LOW) */
    gpio_cfg_out(GPIOB_BASE, 15); /* SDA */

    /* Default idle states */
    GPIOB_BC = (1 << 8);   /* Backlight OFF during init */
    GPIOB_BOP = (1 << 12); /* RES HIGH */
    GPIOB_BOP = (1 << 13); /* SCK HIGH */
    GPIOB_BOP = (1 << 14); /* CS HIGH */
    GPIOB_BOP = (1 << 15); /* SDA HIGH */

    buzzer_chirp(60); /* Startup chirp */

    /* 7. Initialize ST7789 Display */
    lcd_init();

    uart_puts("[LCD] Init complete! Display active.\r\n");

    /* 8. Main Display Pattern Loop */
    int current_pattern = 0;
    const int NUM_PATTERNS = 6;
    draw_pattern(current_pattern);

    uart_puts("\r\n[INFO] Test running! Press front button SW1 (PA0) to cycle patterns.\r\n");
    uart_puts("[INFO] Auto-cycling every 4 seconds if button not pressed.\r\n\r\n");

    int button_prev = 0;
    uint32_t auto_timer = 0;

    while (1) {
        int btn = gpio_read_pin(GPIOA_BASE, 0);

        /* Button press event (rising edge) */
        if (btn && !button_prev) {
            buzzer_chirp(30);
            current_pattern = (current_pattern + 1) % NUM_PATTERNS;
            uart_puts("[SW1 PRESS] Switched to pattern #");
            print_dec(current_pattern);
            uart_puts("\r\n");
            draw_pattern(current_pattern);
            auto_timer = 0;
        }
        button_prev = btn;

        /* Auto-cycle timer (~4 seconds: 80 loops x 50ms) */
        delay_ms(50);
        auto_timer++;
        if (auto_timer >= 80) {
            auto_timer = 0;
            current_pattern = (current_pattern + 1) % NUM_PATTERNS;
            uart_puts("[AUTO-CYCLE] Switched to pattern #");
            print_dec(current_pattern);
            uart_puts("\r\n");
            draw_pattern(current_pattern);
        }
    }
}
