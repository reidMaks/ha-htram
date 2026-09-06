#include "display.h"
#include "gd32f150.h"

/*
 * ST7789 Pinout (GPIOB):
 *   PB12: RES (Reset, Active-LOW)
 *   PB13: SCK (Clock, Idle-HIGH)
 *   PB14: CS  (Chip Select, Active-LOW)
 *   PB15: SDA (MOSI Data, 9-bit mode: D/C + 8 data bits)
 *   PB8:  Backlight (Active-HIGH)
 */

#define LCD_RES_LOW()   GPIOB_BC = (1 << 12)
#define LCD_RES_HIGH()  GPIOB_BOP = (1 << 12)

#define LCD_CS_LOW()    GPIOB_BC = (1 << 14)
#define LCD_CS_HIGH()   GPIOB_BOP = (1 << 14)

#define LCD_SCK_LOW()   GPIOB_BC = (1 << 13)
#define LCD_SCK_HIGH()  GPIOB_BOP = (1 << 13)

#define LCD_SDA_LOW()   GPIOB_BC = (1 << 15)
#define LCD_SDA_HIGH()  GPIOB_BOP = (1 << 15)

#define LCD_SCK_PULSE() do { LCD_SCK_LOW(); LCD_SCK_HIGH(); } while (0)

/*
 * Orientation. The panel is mounted upside down in the case, so the frame has
 * to be turned 180 degrees. Doing it here in MADCTL (MY|MX) rather than in
 * LVGL on the ESP means the GD32's own drawing -- the boot screen and the
 * standby charge indicator -- comes out the right way up too, and the ESP
 * stops paying for a software rotation of every flushed rectangle.
 *
 * MY reverses the row counter, so a 240x240 panel bonded to the top of the
 * controller's 320-row RAM moves to the far end of it and every row address
 * needs LCD_ROW_OFFSET added. 80 = 320 - 240. If the panel turns out to be a
 * true 240x240 controller the offset is 0 and the picture is simply shifted;
 * that is the one thing to look at on the first flash.
 */
#define LCD_MADCTL      0xC0    /* MY | MX -- 180 degrees, RGB order */
#define LCD_COL_OFFSET  0
#define LCD_ROW_OFFSET  80

static void lcd_send_cmd(uint8_t cmd)
{
    LCD_CS_LOW();

    /* 9th bit: D/C = 0 (Command) */
    LCD_SDA_LOW();
    LCD_SCK_PULSE();

    /* 8 bits data (MSB first) */
    for (int i = 0; i < 8; i++) {
        if (cmd & 0x80) LCD_SDA_HIGH(); else LCD_SDA_LOW();
        cmd <<= 1;
        LCD_SCK_PULSE();
    }

    LCD_CS_HIGH();
}

static void lcd_send_data(uint8_t data)
{
    LCD_CS_LOW();

    /* 9th bit: D/C = 1 (Data) */
    LCD_SDA_HIGH();
    LCD_SCK_PULSE();

    /* 8 bits data (MSB first) */
    for (int i = 0; i < 8; i++) {
        if (data & 0x80) LCD_SDA_HIGH(); else LCD_SDA_LOW();
        data <<= 1;
        LCD_SCK_PULSE();
    }

    LCD_CS_HIGH();
}

static inline void lcd_send_pixel_raw(uint16_t color)
{
    uint8_t b1 = (uint8_t)(color >> 8);
    uint8_t b2 = (uint8_t)(color & 0xFF);

    /* Byte 1: D/C = 1 */
    LCD_SDA_HIGH();
    LCD_SCK_PULSE();
    for (int i = 0; i < 8; i++) {
        if (b1 & 0x80) LCD_SDA_HIGH(); else LCD_SDA_LOW();
        b1 <<= 1;
        LCD_SCK_PULSE();
    }

    /* Byte 2: D/C = 1 */
    LCD_SDA_HIGH();
    LCD_SCK_PULSE();
    for (int i = 0; i < 8; i++) {
        if (b2 & 0x80) LCD_SDA_HIGH(); else LCD_SDA_LOW();
        b2 <<= 1;
        LCD_SCK_PULSE();
    }
}

void display_send_pixel(uint16_t color)
{
    LCD_CS_LOW();
    lcd_send_pixel_raw(color);
    LCD_CS_HIGH();
}

void display_start_pixels(uint8_t x, uint8_t y, uint8_t w, uint8_t h)
{
    display_set_window(x, y, w, h);
    LCD_CS_LOW();
}

void display_send_pixel_stream(uint16_t color)
{
    lcd_send_pixel_raw(color);
}

void display_end_pixels(void)
{
    LCD_CS_HIGH();
}

void display_set_window(uint8_t x, uint8_t y, uint8_t w, uint8_t h)
{
    uint16_t x0 = x;
    uint16_t x1 = x + w - 1;
    uint16_t y0 = y;
    uint16_t y1 = y + h - 1;

    if (x1 > 239) x1 = 239;
    if (y1 > 239) y1 = 239;

    x0 += LCD_COL_OFFSET; x1 += LCD_COL_OFFSET;
    y0 += LCD_ROW_OFFSET; y1 += LCD_ROW_OFFSET;

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

void display_fill_rect(uint8_t x, uint8_t y, uint8_t w, uint8_t h, uint16_t color)
{
    if (w == 0 || h == 0) return;
    display_set_window(x, y, w, h);
    uint32_t count = (uint32_t)w * (uint32_t)h;
    LCD_CS_LOW();
    while (count--) {
        lcd_send_pixel_raw(color);
    }
    LCD_CS_HIGH();
}

void display_fill_screen(uint16_t color)
{
    display_fill_rect(0, 0, 240, 240, color);
}

/* Backlight PWM: PB8 -> TIMER15_CH0 (AF2). 100 duty steps map directly to the
 * 0..100 brightness sent over the protocol; ~1 kHz regardless of core clock. */
#define BL_PWM_STEPS 100u

static void backlight_pwm_init(void)
{
    RCU_APB2EN |= RCU_APB2EN_TIMER15EN;

    /* PB8: alternate-function mode, push-pull, high speed, AF2 = TIMER15_CH0 */
    uint32_t ctl = GPIO_CTL(GPIOB_BASE);
    ctl &= ~(3u << (8 * 2));
    ctl |= (2u << (8 * 2));               /* 10 = AF */
    GPIO_CTL(GPIOB_BASE) = ctl;
    GPIO_OMD(GPIOB_BASE) &= ~(1u << 8);   /* push-pull */
    GPIO_OSPD(GPIOB_BASE) |= (3u << (8 * 2));
    uint32_t af = GPIO_AFSEL1(GPIOB_BASE);
    af &= ~(0xFu << 0);                   /* pin 8 -> AFSEL1[3:0] */
    af |= (2u << 0);                      /* AF2 */
    GPIO_AFSEL1(GPIOB_BASE) = af;

    TIMER15_PSC = (SYSTEM_CLOCK_HZ / (1000u * BL_PWM_STEPS)) - 1u; /* ~1 kHz */
    TIMER15_CAR = BL_PWM_STEPS - 1u;      /* period = 100 counts */
    TIMER15_CH0CV = 0;                    /* start dark */

    /* Channel 0: PWM mode 0 (CH0COMCTL=110) + compare preload (CH0COMSEN) */
    TIMER15_CHCTL0 = (6u << 4) | (1u << 3);
    /* Enable CH0 output, active-high (CH0P=0) */
    TIMER15_CHCTL2 = (1u << 0);
    /* Advanced timer requires primary output enable */
    TIMER15_CCHP = (1u << 15);            /* POEN */
    TIMER15_SWEVG = (1u << 0);            /* UPG: latch PSC/CAR/preload */
    TIMER15_CTL0 = (1u << 7) | (1u << 0); /* ARSE | CEN */
}

void display_set_backlight(uint8_t brightness)
{
    if (brightness > BL_PWM_STEPS) brightness = BL_PWM_STEPS;
    TIMER15_CH0CV = brightness;           /* 0 = off, 100 = full */
}

void display_init(void)
{
    /* Enable GPIOB clock */
    RCU_AHBEN |= RCU_AHBEN_PBEN;

    /* Configure display GPIO pins (PB12, PB13, PB14, PB15); PB8 backlight is
     * driven by TIMER15_CH0 PWM (set up below) rather than a plain GPIO. */
    backlight_pwm_init();              /* Backlight (PB8, PWM, starts at 0) */
    gpio_cfg_out_pp(GPIOB_BASE, 12);  /* RES */
    gpio_cfg_out_pp(GPIOB_BASE, 13);  /* SCK */
    gpio_cfg_out_pp(GPIOB_BASE, 14);  /* CS */
    gpio_cfg_out_pp(GPIOB_BASE, 15);  /* SDA */

    /* Default idle pin states (backlight already dark: PWM duty 0) */
    LCD_RES_HIGH();
    LCD_SCK_HIGH();
    LCD_CS_HIGH();
    LCD_SDA_HIGH();

    /* 1. Hardware Reset */
    LCD_RES_LOW();
    delay_ms(150);
    LCD_RES_HIGH();
    delay_ms(150);

    /* 2. Software Reset & Wakeup */
    lcd_send_cmd(0x01); /* SWRESET */
    delay_ms(150);

    lcd_send_cmd(0x11); /* SLPOUT */
    delay_ms(200);

    /* 3. Panel Parameters (Matching Factory ROM) */
    lcd_send_cmd(0xB2); /* PORCTRL */
    lcd_send_data(0x1F);
    lcd_send_data(0x1F);
    lcd_send_data(0x00);
    lcd_send_data(0x33);
    lcd_send_data(0x33);

    lcd_send_cmd(0x35); /* TEON */
    lcd_send_data(0x00);

    lcd_send_cmd(0x36); /* MADCTL -- see LCD_MADCTL above */
    lcd_send_data(LCD_MADCTL);

    lcd_send_cmd(0x3A); /* COLMOD (16-bit RGB565) */
    lcd_send_data(0x05);

    lcd_send_cmd(0xB7); /* GCTRL */
    lcd_send_data(0x02);

    lcd_send_cmd(0xBB); /* VCOMS */
    lcd_send_data(0x31);

    lcd_send_cmd(0xC0); /* LCMCTRL */
    lcd_send_data(0x2C);

    lcd_send_cmd(0xC2); /* VDVVRHEN */
    lcd_send_data(0x01);

    lcd_send_cmd(0xC3); /* VRHS */
    lcd_send_data(0x19);

    lcd_send_cmd(0xC4); /* VDVS */
    lcd_send_data(0x20);

    lcd_send_cmd(0xC6); /* FRCTRL2 */
    lcd_send_data(0x13);

    lcd_send_cmd(0xD0); /* PWCTRL1 */
    lcd_send_data(0xA4);
    lcd_send_data(0xA1);

    lcd_send_cmd(0xD6);
    lcd_send_data(0xA1);

    /* Positive Voltage Gamma */
    lcd_send_cmd(0xE0);
    lcd_send_data(0xF0); lcd_send_data(0x04); lcd_send_data(0x0A); lcd_send_data(0x09);
    lcd_send_data(0x0A); lcd_send_data(0x27); lcd_send_data(0x2C); lcd_send_data(0x43);
    lcd_send_data(0x42); lcd_send_data(0x38); lcd_send_data(0x13); lcd_send_data(0x13);
    lcd_send_data(0x27); lcd_send_data(0x2B);

    /* Negative Voltage Gamma */
    lcd_send_cmd(0xE1);
    lcd_send_data(0xF0); lcd_send_data(0x05); lcd_send_data(0x08); lcd_send_data(0x0A);
    lcd_send_data(0x08); lcd_send_data(0x04); lcd_send_data(0x2C); lcd_send_data(0x43);
    lcd_send_data(0x41); lcd_send_data(0x3A); lcd_send_data(0x16); lcd_send_data(0x16);
    lcd_send_data(0x28); lcd_send_data(0x2C);

    lcd_send_cmd(0xE4);
    lcd_send_data(0x1D);
    lcd_send_data(0x00);
    lcd_send_data(0x00);

    lcd_send_cmd(0x21); /* INVON (Inversion ON for IPS) */

    /* Clear screen to black before turning on display */
    display_fill_screen(COLOR_BLACK);

    /* 4. Display ON */
    lcd_send_cmd(0x29); /* DISPON */
    delay_ms(120);

    /* 5. Backlight ON */
    display_set_backlight(100);
}

/* ── Minimal 8x12 Font for Status Display ── */
static const uint8_t font8x12[][12] = {
    [' ' - ' '] = {0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00},
    ['.' - ' '] = {0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x30,0x30,0x00},
    [':' - ' '] = {0x00,0x00,0x00,0x30,0x30,0x00,0x00,0x30,0x30,0x00,0x00,0x00},
    ['-' - ' '] = {0x00,0x00,0x00,0x00,0x00,0x7E,0x7E,0x00,0x00,0x00,0x00,0x00},
    ['+' - ' '] = {0x00,0x00,0x18,0x18,0x18,0xFF,0xFF,0x18,0x18,0x18,0x00,0x00},
    ['/' - ' '] = {0x00,0x06,0x0C,0x18,0x30,0x60,0xC0,0x80,0x00,0x00,0x00,0x00},
    ['0' - ' '] = {0x3C,0x66,0x6E,0x7E,0x76,0x66,0x66,0x66,0x66,0x3C,0x00,0x00},
    ['1' - ' '] = {0x18,0x38,0x18,0x18,0x18,0x18,0x18,0x18,0x18,0x7E,0x00,0x00},
    ['2' - ' '] = {0x3C,0x66,0x06,0x06,0x0C,0x18,0x30,0x60,0x66,0x7E,0x00,0x00},
    ['3' - ' '] = {0x3C,0x66,0x06,0x06,0x1C,0x06,0x06,0x06,0x66,0x3C,0x00,0x00},
    ['4' - ' '] = {0x0C,0x1C,0x3C,0x6C,0xCC,0xFE,0x0C,0x0C,0x0C,0x1E,0x00,0x00},
    ['5' - ' '] = {0x7E,0x60,0x60,0x7C,0x66,0x06,0x06,0x06,0x66,0x3C,0x00,0x00},
    ['6' - ' '] = {0x3C,0x66,0x60,0x7C,0x66,0x66,0x66,0x66,0x66,0x3C,0x00,0x00},
    ['7' - ' '] = {0x7E,0x66,0x06,0x0C,0x18,0x18,0x30,0x30,0x30,0x30,0x00,0x00},
    ['8' - ' '] = {0x3C,0x66,0x66,0x66,0x3C,0x66,0x66,0x66,0x66,0x3C,0x00,0x00},
    ['9' - ' '] = {0x3C,0x66,0x66,0x66,0x66,0x3E,0x06,0x06,0x66,0x3C,0x00,0x00},
    ['A' - ' '] = {0x18,0x3C,0x66,0x66,0x66,0x7E,0x66,0x66,0x66,0x66,0x00,0x00},
    ['B' - ' '] = {0x7C,0x66,0x66,0x66,0x7C,0x66,0x66,0x66,0x66,0x7C,0x00,0x00},
    ['C' - ' '] = {0x3C,0x66,0x60,0x60,0x60,0x60,0x60,0x60,0x66,0x3C,0x00,0x00},
    ['D' - ' '] = {0x78,0x6C,0x66,0x66,0x66,0x66,0x66,0x66,0x6C,0x78,0x00,0x00},
    ['E' - ' '] = {0x7E,0x60,0x60,0x60,0x7C,0x60,0x60,0x60,0x60,0x7E,0x00,0x00},
    ['F' - ' '] = {0x7E,0x60,0x60,0x60,0x7C,0x60,0x60,0x60,0x60,0x60,0x00,0x00},
    ['G' - ' '] = {0x3C,0x66,0x60,0x60,0x6E,0x66,0x66,0x66,0x66,0x3C,0x00,0x00},
    ['H' - ' '] = {0x66,0x66,0x66,0x66,0x7E,0x66,0x66,0x66,0x66,0x66,0x00,0x00},
    ['I' - ' '] = {0x3C,0x18,0x18,0x18,0x18,0x18,0x18,0x18,0x18,0x3C,0x00,0x00},
    ['M' - ' '] = {0x63,0x77,0x7F,0x6B,0x63,0x63,0x63,0x63,0x63,0x63,0x00,0x00},
    ['N' - ' '] = {0x66,0x76,0x7E,0x7E,0x6E,0x66,0x66,0x66,0x66,0x66,0x00,0x00},
    ['O' - ' '] = {0x3C,0x66,0x66,0x66,0x66,0x66,0x66,0x66,0x66,0x3C,0x00,0x00},
    ['P' - ' '] = {0x7C,0x66,0x66,0x66,0x7C,0x60,0x60,0x60,0x60,0x60,0x00,0x00},
    ['R' - ' '] = {0x7C,0x66,0x66,0x66,0x7C,0x6E,0x66,0x66,0x66,0x66,0x00,0x00},
    ['S' - ' '] = {0x3C,0x66,0x60,0x30,0x1C,0x06,0x06,0x06,0x66,0x3C,0x00,0x00},
    ['T' - ' '] = {0x7E,0x5A,0x18,0x18,0x18,0x18,0x18,0x18,0x18,0x3C,0x00,0x00},
    ['U' - ' '] = {0x66,0x66,0x66,0x66,0x66,0x66,0x66,0x66,0x66,0x3C,0x00,0x00},
    ['V' - ' '] = {0x66,0x66,0x66,0x66,0x66,0x66,0x3C,0x3C,0x18,0x18,0x00,0x00},
    ['W' - ' '] = {0x63,0x63,0x63,0x63,0x6B,0x6B,0x7F,0x36,0x36,0x22,0x00,0x00},
    ['Y' - ' '] = {0x66,0x66,0x66,0x66,0x3C,0x18,0x18,0x18,0x18,0x3C,0x00,0x00},
};

static void display_draw_char(uint8_t x, uint8_t y, char c, uint16_t color, uint16_t bg)
{
    if (x > 232 || y > 228) return;
    const uint8_t *glyph;
    if (c >= ' ' && (size_t)(c - ' ') < (sizeof(font8x12) / sizeof(font8x12[0]))) {
        glyph = font8x12[c - ' '];
    } else {
        glyph = font8x12[0];
    }

    display_set_window(x, y, 8, 12);
    LCD_CS_LOW();
    for (int row = 0; row < 12; row++) {
        uint8_t line = glyph[row];
        for (int col = 7; col >= 0; col--) {
            if (line & (1 << col)) {
                lcd_send_pixel_raw(color);
            } else {
                lcd_send_pixel_raw(bg);
            }
        }
    }
    LCD_CS_HIGH();
}

void display_draw_string(uint8_t x, uint8_t y, const char *s, uint16_t color, uint16_t bg)
{
    while (*s && x <= 232) {
        display_draw_char(x, y, *s++, color, bg);
        x += 8;
    }
}
