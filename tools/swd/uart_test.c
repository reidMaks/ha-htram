/*
 * HTRAM GD32F150 Hardware Bench Probe — Runs entirely from SRAM (0x20000000)
 *
 * Does NOT touch or erase Flash memory.
 *
 * Hardware Map Verification:
 *   - Power Rails: PC15 (Main Latch), PB3 (Periph Latch), PA1 (VLED/Periph Gate)
 *   - Front Panel LEDs: PA4, PA5, PA7 (+ PA8 test)
 *   - Button SW1: PA6 (with internal Pull-Up, plus PB4/PB5/PC14 monitoring)
 *   - UART Link: PA2 (USART1 TX @ 115200 8N1) -> Pico GP5 / ESP32 GPIO16
 */

#include <stdint.h>

/* ── RCU (Reset and Clock Unit) ── */
#define RCU_BASE      0x40021000
#define RCU_AHBEN     (*(volatile uint32_t *)(RCU_BASE + 0x14))
#define RCU_APB2EN    (*(volatile uint32_t *)(RCU_BASE + 0x18))
#define RCU_APB1EN    (*(volatile uint32_t *)(RCU_BASE + 0x1C))

/* ── GPIO Base Addresses ── */
#define GPIOA_BASE    0x48000000
#define GPIOB_BASE    0x48000400
#define GPIOC_BASE    0x48000800
#define GPIOF_BASE    0x48001400

/* ── GPIO Registers (GD32F150 layout) ── */
#define GPIO_CTL(b)   (*(volatile uint32_t *)((b) + 0x00))  /* Mode: 00=In, 01=Out, 10=AF, 11=Analog */
#define GPIO_OMD(b)   (*(volatile uint32_t *)((b) + 0x04))  /* Type: 0=PushPull, 1=OpenDrain          */
#define GPIO_OSPD(b)  (*(volatile uint32_t *)((b) + 0x08))  /* Speed: 00=2M, 01=10M, 11=50M          */
#define GPIO_PUD(b)   (*(volatile uint32_t *)((b) + 0x0C))  /* Pull: 00=None, 01=PullUp, 10=PullDown  */
#define GPIO_ISTAT(b) (*(volatile uint32_t *)((b) + 0x10))  /* Input status                           */
#define GPIO_OCTL(b)  (*(volatile uint32_t *)((b) + 0x14))  /* Output data                            */
#define GPIO_BOP(b)   (*(volatile uint32_t *)((b) + 0x18))  /* Bit operate: low16=set, high16=reset   */
#define GPIO_AFSEL0(b)(*(volatile uint32_t *)((b) + 0x20))  /* Alternate function 0..7               */
#define GPIO_AFSEL1(b)(*(volatile uint32_t *)((b) + 0x24))  /* Alternate function 8..15              */
#define GPIO_BC(b)    (*(volatile uint32_t *)((b) + 0x28))  /* Bit clear (low 16)                     */

/* ── USART0 (Honeywell CRIR M1 CO2 Sensor Link on PA9/PA10) ── */
#define USART0_BASE   0x40013800
#define USART0_CTL0   (*(volatile uint32_t *)(USART0_BASE + 0x00))
#define USART0_BAUD   (*(volatile uint32_t *)(USART0_BASE + 0x0C))
#define USART0_STAT   (*(volatile uint32_t *)(USART0_BASE + 0x1C))
#define USART0_TDATA  (*(volatile uint32_t *)(USART0_BASE + 0x28))
#define USART0_RDATA  (*(volatile uint32_t *)(USART0_BASE + 0x24))

/* ── USART1 (Debug / ESP32 Link on PA2/PA3) ── */
#define USART1_BASE   0x40004400
#define USART1_CTL0   (*(volatile uint32_t *)(USART1_BASE + 0x00))
#define USART1_BAUD   (*(volatile uint32_t *)(USART1_BASE + 0x0C))
#define USART1_STAT   (*(volatile uint32_t *)(USART1_BASE + 0x1C))
#define USART1_TDATA  (*(volatile uint32_t *)(USART1_BASE + 0x28))

#define UEN   (1 << 0)
#define REN   (1 << 2)
#define TEN   (1 << 3)
#define RBNE  (1 << 5)
#define TC    (1 << 6)
#define TBE   (1 << 7)


/* ── Helpers ── */

static inline void delay_cycles(uint32_t n)
{
    while (n--) {
        __asm__ volatile("");
    }
}

static void delay_ms(uint32_t ms)
{
    while (ms--) {
        /* ~8000 cycles for 1ms @ 8MHz IRC8M */
        delay_cycles(2000);
    }
}

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
    while (n) {
        buf[i++] = '0' + (n % 10);
        n /= 10;
    }
    while (i--) {
        uart_putc(buf[i]);
    }
}

static void print_hex8(uint8_t b)
{
    const char hex[] = "0123456789ABCDEF";
    uart_putc(hex[(b >> 4) & 0xF]);
    uart_putc(hex[b & 0xF]);
}

static void gpio_config_output(uint32_t base, int pin)
{
    /* Output mode: 01 */
    uint32_t ctl = GPIO_CTL(base);
    ctl &= ~(3U << (pin * 2));
    ctl |=  (1U << (pin * 2));
    GPIO_CTL(base) = ctl;

    /* Push-Pull: 0 */
    GPIO_OMD(base) &= ~(1U << pin);

    /* Speed: 50 MHz (11) */
    uint32_t ospd = GPIO_OSPD(base);
    ospd &= ~(3U << (pin * 2));
    ospd |=  (3U << (pin * 2));
    GPIO_OSPD(base) = ospd;
}

static void gpio_config_input_pullup(uint32_t base, int pin)
{
    /* Input mode: 00 */
    uint32_t ctl = GPIO_CTL(base);
    ctl &= ~(3U << (pin * 2));
    GPIO_CTL(base) = ctl;

    /* Pull-Up: 01 */
    uint32_t pud = GPIO_PUD(base);
    pud &= ~(3U << (pin * 2));
    pud |=  (1U << (pin * 2));
    GPIO_PUD(base) = pud;
}

static inline int gpio_read_pin(uint32_t base, int pin)
{
    return (GPIO_ISTAT(base) & (1U << pin)) ? 1 : 0;
}

/* ── I2C Bitbang on PB6 (SCL) and PB7 (SDA) — Matches Factory Routine ── */
#define SCL_PIN  6
#define SDA_PIN  7

static inline void i2c_delay(void)
{
    delay_cycles(40); /* ~5us @ 8MHz = ~100kHz I2C */
}

static inline void sda_mode_input(void)
{
    uint32_t ctl = GPIO_CTL(GPIOB_BASE);
    ctl &= ~(3U << (SDA_PIN * 2));
    GPIO_CTL(GPIOB_BASE) = ctl;
}

static inline void sda_mode_output(void)
{
    uint32_t ctl = GPIO_CTL(GPIOB_BASE);
    ctl &= ~(3U << (SDA_PIN * 2));
    ctl |=  (1U << (SDA_PIN * 2));
    GPIO_CTL(GPIOB_BASE) = ctl;
}

static void i2c_init(void)
{
    /* Enable GPIOB clock */
    RCU_AHBEN |= (1 << 18);

    /* SCL (PB6): Output Open-Drain, 50MHz */
    gpio_config_output(GPIOB_BASE, SCL_PIN);
    GPIO_OMD(GPIOB_BASE) |= (1U << SCL_PIN);

    /* SDA (PB7): Output Open-Drain, 50MHz */
    gpio_config_output(GPIOB_BASE, SDA_PIN);
    GPIO_OMD(GPIOB_BASE) |= (1U << SDA_PIN);

    /* Pull-ups on both lines */
    uint32_t pud = GPIO_PUD(GPIOB_BASE);
    pud &= ~((3U << (SCL_PIN * 2)) | (3U << (SDA_PIN * 2)));
    pud |=  ((1U << (SCL_PIN * 2)) | (1U << (SDA_PIN * 2)));
    GPIO_PUD(GPIOB_BASE) = pud;

    /* Bus Idle: SCL = 1, SDA in Input (Hi-Z) */
    GPIO_BOP(GPIOB_BASE) = (1 << SCL_PIN);
    sda_mode_input();
    i2c_delay();
}

static void i2c_start(void)
{
    sda_mode_output();
    GPIO_BOP(GPIOB_BASE) = (1 << SDA_PIN);
    GPIO_BOP(GPIOB_BASE) = (1 << SCL_PIN);
    i2c_delay();
    GPIO_BC(GPIOB_BASE)  = (1 << SDA_PIN); /* SDA falls while SCL is high */
    i2c_delay();
    GPIO_BC(GPIOB_BASE)  = (1 << SCL_PIN); /* SCL falls */
    i2c_delay();
}

static void i2c_stop(void)
{
    sda_mode_output();
    GPIO_BC(GPIOB_BASE)  = (1 << SDA_PIN);
    i2c_delay();
    GPIO_BOP(GPIOB_BASE) = (1 << SCL_PIN);
    i2c_delay();
    GPIO_BOP(GPIOB_BASE) = (1 << SDA_PIN); /* SDA rises while SCL is high */
    i2c_delay();
    sda_mode_input();
}

static int i2c_write_byte(uint8_t byte)
{
    sda_mode_output();
    for (int i = 7; i >= 0; i--) {
        GPIO_BC(GPIOB_BASE) = (1 << SCL_PIN);
        i2c_delay();
        if (byte & (1 << i))
            GPIO_BOP(GPIOB_BASE) = (1 << SDA_PIN);
        else
            GPIO_BC(GPIOB_BASE)  = (1 << SDA_PIN);
        GPIO_BOP(GPIOB_BASE) = (1 << SCL_PIN);
        i2c_delay();
    }
    GPIO_BC(GPIOB_BASE) = (1 << SCL_PIN);
    i2c_delay();

    /* Read ACK: release SDA to input mode */
    sda_mode_input();
    GPIO_BOP(GPIOB_BASE) = (1 << SCL_PIN);
    i2c_delay();
    int ack = (gpio_read_pin(GPIOB_BASE, SDA_PIN) == 0) ? 1 : 0;
    GPIO_BC(GPIOB_BASE) = (1 << SCL_PIN);
    i2c_delay();

    return ack;
}

static uint8_t i2c_read_byte(int ack)
{
    uint8_t byte = 0;
    sda_mode_input();
    for (int i = 7; i >= 0; i--) {
        GPIO_BOP(GPIOB_BASE) = (1 << SCL_PIN);
        i2c_delay();
        if (gpio_read_pin(GPIOB_BASE, SDA_PIN))
            byte |= (1 << i);
        GPIO_BC(GPIOB_BASE) = (1 << SCL_PIN);
        i2c_delay();
    }

    /* Send ACK (0) or NACK (1) */
    sda_mode_output();
    if (ack)
        GPIO_BC(GPIOB_BASE)  = (1 << SDA_PIN); /* ACK = 0 */
    else
        GPIO_BOP(GPIOB_BASE) = (1 << SDA_PIN); /* NACK = 1 */
    i2c_delay();
    GPIO_BOP(GPIOB_BASE) = (1 << SCL_PIN);
    i2c_delay();
    GPIO_BC(GPIOB_BASE)  = (1 << SCL_PIN);
    i2c_delay();
    sda_mode_input();

    return byte;
}

static uint8_t crc8(const uint8_t *data, int len)
{
    uint8_t crc = 0xFF;
    for (int j = 0; j < len; j++) {
        crc ^= data[j];
        for (int i = 0; i < 8; i++) {
            if (crc & 0x80)
                crc = (crc << 1) ^ 0x31;
            else
                crc <<= 1;
        }
    }
    return crc;
}

/* SHT30 single-shot read (0x2400) */
static int sht30_read_single(int32_t *temp_c_x10, int32_t *hum_x10)
{
    i2c_start();
    if (!i2c_write_byte(0x88)) {
        i2c_stop();
        return -1;
    }
    if (!i2c_write_byte(0x24)) {
        i2c_stop();
        return -2;
    }
    if (!i2c_write_byte(0x00)) {
        i2c_stop();
        return -3;
    }
    i2c_stop();

    /* Measurement time ~15ms */
    delay_cycles(120000);

    uint8_t buf[6];
    i2c_start();
    if (!i2c_write_byte(0x89)) {
        i2c_stop();
        return -4;
    }
    for (int i = 0; i < 5; i++)
        buf[i] = i2c_read_byte(1);
    buf[5] = i2c_read_byte(0);
    i2c_stop();

    if (crc8(&buf[0], 2) != buf[2]) return -5;
    if (crc8(&buf[3], 2) != buf[5]) return -6;

    uint16_t raw_temp = ((uint16_t)buf[0] << 8) | buf[1];
    uint16_t raw_hum  = ((uint16_t)buf[3] << 8) | buf[4];

    *temp_c_x10 = -450 + (int32_t)(((uint32_t)1750 * raw_temp) / 65535);
    *hum_x10    = (int32_t)(((uint32_t)1000 * raw_hum) / 65535);

    return 0;
}

/* SHT30 periodic fetch (0xE000, exact factory firmware method) */
static int sht30_read_periodic(int32_t *temp_c_x10, int32_t *hum_x10)
{
    i2c_start();
    if (!i2c_write_byte(0x88)) {
        i2c_stop();
        return -1;
    }
    if (!i2c_write_byte(0xE0)) {
        i2c_stop();
        return -2;
    }
    if (!i2c_write_byte(0x00)) {
        i2c_stop();
        return -3;
    }
    i2c_stop();

    delay_cycles(10000);

    uint8_t buf[6];
    i2c_start();
    if (!i2c_write_byte(0x89)) {
        i2c_stop();
        return -4;
    }
    for (int i = 0; i < 5; i++)
        buf[i] = i2c_read_byte(1);
    buf[5] = i2c_read_byte(0);
    i2c_stop();

    if (crc8(&buf[0], 2) != buf[2]) return -5;
    if (crc8(&buf[3], 2) != buf[5]) return -6;

    uint16_t raw_temp = ((uint16_t)buf[0] << 8) | buf[1];
    uint16_t raw_hum  = ((uint16_t)buf[3] << 8) | buf[4];

    *temp_c_x10 = -450 + (int32_t)(((uint32_t)1750 * raw_temp) / 65535);
    *hum_x10    = (int32_t)(((uint32_t)1000 * raw_hum) / 65535);

    return 0;
}


/* ── Button tracking ── */
static int state_pa0 = 0;

static void check_inputs(void)
{
    int pa0 = gpio_read_pin(GPIOA_BASE, 0);
    if (pa0 != state_pa0) {
        delay_cycles(10000); /* debounce */
        if (gpio_read_pin(GPIOA_BASE, 0) == pa0) {
            state_pa0 = pa0;
            if (pa0 == 1) {
                uart_puts("\r\n  >>> [BUTTON SW1] PRESSED! <<<\r\n");
                GPIO_BOP(GPIOC_BASE) = (1 << 14); /* Green LEDs ON */
            } else {
                uart_puts("  >>> [BUTTON SW1] RELEASED! <<<\r\n");
                GPIO_BC(GPIOC_BASE) = (1 << 14);  /* Green LEDs OFF */
            }
        }
    }
}

/* Delay while polling buttons */
static void delay_ms_polling(uint32_t ms)
{
    uint32_t chunks = ms / 10;
    if (chunks == 0) chunks = 1;
    for (uint32_t i = 0; i < chunks; i++) {
        check_inputs();
        delay_cycles(8000);
    }
}

static void print_signed_fixed1(int32_t val_x10)
{
    if (val_x10 < 0) {
        uart_putc('-');
        val_x10 = -val_x10;
    }
    print_dec(val_x10 / 10);
    uart_putc('.');
    uart_putc('0' + (val_x10 % 10));
}

/* ── Modbus CRC-16 (for CRIR M1) ── */
static uint16_t modbus_crc16(const uint8_t *buf, int len)
{
    uint16_t crc = 0xFFFF;
    for (int pos = 0; pos < len; pos++) {
        crc ^= (uint16_t)buf[pos];
        for (int i = 8; i != 0; i--) {
            if ((crc & 0x0001) != 0) {
                crc >>= 1;
                crc ^= 0xA001;
            } else {
                crc >>= 1;
            }
        }
    }
    return crc;
}

/* ── USART0 for Honeywell CRIR M1 CO2 Sensor (PA9 TX, PA10 RX @ 9600 8N1) ── */
static void usart0_init(void)
{
    /* APB2 clock bit 14: USART0 */
    RCU_APB2EN |= (1 << 14);
    delay_cycles(1000);

    /* Configure PA9 (TX) and PA10 (RX) as AF1 */
    uint32_t ctl = GPIO_CTL(GPIOA_BASE);
    ctl &= ~((3U << (9 * 2)) | (3U << (10 * 2)));
    ctl |=  ((2U << (9 * 2)) | (2U << (10 * 2))); /* Mode 10 = AF */
    GPIO_CTL(GPIOA_BASE) = ctl;

    /* Push-Pull for TX, Push-Pull for RX */
    GPIO_OMD(GPIOA_BASE) &= ~((1U << 9) | (1U << 10));

    /* Speed: 50 MHz */
    uint32_t ospd = GPIO_OSPD(GPIOA_BASE);
    ospd |= (3U << (9 * 2)) | (3U << (10 * 2));
    GPIO_OSPD(GPIOA_BASE) = ospd;

    /* Pull-Up on both lines */
    uint32_t pud = GPIO_PUD(GPIOA_BASE);
    pud &= ~((3U << (9 * 2)) | (3U << (10 * 2)));
    pud |=  ((1U << (9 * 2)) | (1U << (10 * 2)));
    GPIO_PUD(GPIOA_BASE) = pud;

    /* Alternate Function 1 (AF1) on PA9 and PA10 */
    uint32_t af = GPIO_AFSEL1(GPIOA_BASE);
    af &= ~((0xFU << ((9 - 8) * 4)) | (0xFU << ((10 - 8) * 4)));
    af |=  ((0x1U << ((9 - 8) * 4)) | (0x1U << ((10 - 8) * 4)));
    GPIO_AFSEL1(GPIOA_BASE) = af;

    /* 9600 baud @ 8MHz IRC8M: 8000000 / 9600 = 833.33 -> 0x0341 */
    USART0_CTL0 = 0;
    USART0_BAUD = 0x0341;
    USART0_CTL0 = UEN | TEN | REN;
}

static void usart0_putc(uint8_t c)
{
    uint32_t to = 100000;
    while (!(USART0_STAT & TBE) && --to)
        ;
    USART0_TDATA = c;
}

static int usart0_getc_timeout(uint32_t timeout_ms)
{
    while (timeout_ms--) {
        for (uint32_t i = 0; i < 200; i++) {
            if (USART0_STAT & RBNE)
                return (uint8_t)USART0_RDATA;
            delay_cycles(10);
        }
    }
    return -1;
}

/* Query CRIR M1 CO2 concentration using Cmd #0 (Read Input Register 7) */
static int crir_read_co2(int32_t *co2_ppm, uint8_t *resp_buf)
{
    /* 1. Flush any leftover RX bytes */
    while (USART0_STAT & RBNE) {
        volatile uint32_t dummy = USART0_RDATA;
        (void)dummy;
    }

    /* 2. Send pre-calculated Modbus query (Cmd #0 from factory firmware at 0x0800F00C):
     *    FE 04 00 07 00 01 94 04
     */
    const uint8_t req[8] = { 0xFE, 0x04, 0x00, 0x07, 0x00, 0x01, 0x94, 0x04 };
    for (int i = 0; i < 8; i++) {
        usart0_putc(req[i]);
    }

    /* 3. Read 7-byte response: FE 04 02 <High> <Low> <CRC_L> <CRC_H> */
    for (int i = 0; i < 7; i++) {
        int b = usart0_getc_timeout(100); /* 100ms timeout per byte */
        if (b < 0) {
            return -(i + 1); /* Timeout on byte i */
        }
        resp_buf[i] = (uint8_t)b;
    }

    /* 4. Validate Modbus response */
    if (resp_buf[0] != 0xFE || resp_buf[1] != 0x04 || resp_buf[2] != 0x02) {
        return -10; /* Invalid response header */
    }

    uint16_t expected_crc = (uint16_t)resp_buf[5] | ((uint16_t)resp_buf[6] << 8);
    if (modbus_crc16(resp_buf, 5) != expected_crc) {
        return -11; /* CRC mismatch */
    }

    *co2_ppm = ((int32_t)resp_buf[3] << 8) | resp_buf[4];
    return 0;
}

void main(void)
{
    /* 1. Clocks: GPIOA(17), GPIOB(18), GPIOC(19), GPIOF(22), USART0(14 in APB2), USART1(17 in APB1) */
    RCU_AHBEN  |= (1 << 17) | (1 << 18) | (1 << 19) | (1 << 22);
    RCU_APB1EN |= (1 << 17);
    RCU_APB2EN |= (1 << 14);
    delay_cycles(1000);

    /* 2. Configure PA2 as USART1 TX (AF1) for debug terminal */
    {
        uint32_t ctl = GPIO_CTL(GPIOA_BASE);
        ctl &= ~(3U << (2 * 2));
        ctl |=  (2U << (2 * 2));
        GPIO_CTL(GPIOA_BASE) = ctl;

        uint32_t spd = GPIO_OSPD(GPIOA_BASE);
        spd &= ~(3U << (2 * 2));
        spd |=  (1U << (2 * 2));
        GPIO_OSPD(GPIOA_BASE) = spd;

        uint32_t af = GPIO_AFSEL0(GPIOA_BASE);
        af &= ~(0xFU << (2 * 4));
        af |=  (0x1U << (2 * 4));
        GPIO_AFSEL0(GPIOA_BASE) = af;
    }

    USART1_CTL0 = 0;
    USART1_BAUD = 0x0045; /* 8MHz / 115200 = 0x45 */
    USART1_CTL0 = UEN | TEN;

    delay_ms(200);

    uart_puts("\r\n\r\n====================================================\r\n");
    uart_puts("   HTRAM GD32F150 CRIR M1 CO2 & SHT30 Hardware Probe\r\n");
    uart_puts("====================================================\r\n");

    /* 3. Enable Power Rails */
    uart_puts("[PWR] Asserting Power Rails: PC15, PB3, PA1, PB2, PB11(5V Boost)...\r\n");
    gpio_config_output(GPIOC_BASE, 15);
    GPIO_BOP(GPIOC_BASE) = (1 << 15);

    gpio_config_output(GPIOB_BASE, 3);
    GPIO_BOP(GPIOB_BASE) = (1 << 3);

    gpio_config_output(GPIOA_BASE, 1);
    GPIO_BOP(GPIOA_BASE) = (1 << 1);

    /* PB2: Sensor rail (Active HIGH with pullup) */
    gpio_config_output(GPIOB_BASE, 2);
    {
        uint32_t pud = GPIO_PUD(GPIOB_BASE);
        pud &= ~(3U << (2 * 2));
        pud |=  (1U << (2 * 2));
        GPIO_PUD(GPIOB_BASE) = pud;
    }
    GPIO_BOP(GPIOB_BASE) = (1 << 2);

    /* PB11: CRIR M1 5V Boost DC-DC Enable (Active HIGH) */
    gpio_config_output(GPIOB_BASE, 11);
    GPIO_BOP(GPIOB_BASE) = (1 << 11);

    /* PB9: CRIR M1 Power Switch / Enable (Active HIGH: 1 = Power ON) */
    gpio_config_output(GPIOB_BASE, 9);
    GPIO_BOP(GPIOB_BASE) = (1 << 9);

    /* Deselect SPI Flash */
    gpio_config_output(GPIOA_BASE, 4);
    GPIO_BOP(GPIOA_BASE) = (1 << 4);

    /* Backlight ON (PB8=1), Buzzer OFF (PB0=0) */
    gpio_config_output(GPIOB_BASE, 8);
    GPIO_BOP(GPIOB_BASE) = (1 << 8);

    gpio_config_output(GPIOB_BASE, 0);
    GPIO_BC(GPIOB_BASE) = (1 << 0);

    /* LEDs OFF initially */
    gpio_config_output(GPIOC_BASE, 14);
    gpio_config_output(GPIOB_BASE, 4);
    gpio_config_output(GPIOB_BASE, 5);
    GPIO_BC(GPIOC_BASE) = (1 << 14);
    GPIO_BC(GPIOB_BASE) = (1 << 4) | (1 << 5);

    /* 4. SHT30 Reset Sequence on PA8 */
    uart_puts("[SHT] Cycling SHT30 Reset line (PA8)...\r\n");
    gpio_config_output(GPIOA_BASE, 8);
    GPIO_BC(GPIOA_BASE) = (1 << 8);  /* Reset LOW */
    delay_ms(10);
    GPIO_BOP(GPIOA_BASE) = (1 << 8); /* Reset HIGH */
    delay_ms(50);

    uart_puts("[PWR] All power rails asserted (PC15=1, PB3=1, PA1=1, PB2=1, PB11=1, PB9=1)!\r\n");

    /* 5. Configure Button PA0 */
    uart_puts("[BTN] Configuring SW1 (PA0) as Input...\r\n");
    {
        uint32_t ctl = GPIO_CTL(GPIOA_BASE);
        ctl &= ~(3U << (0 * 2));
        GPIO_CTL(GPIOA_BASE) = ctl;
        uint32_t pud = GPIO_PUD(GPIOA_BASE);
        pud &= ~(3U << (0 * 2));
        GPIO_PUD(GPIOA_BASE) = pud;
    }
    state_pa0 = gpio_read_pin(GPIOA_BASE, 0);

    /* 6. Initialize SHT30 I2C bus: PB6 (SCL), PB7 (SDA) */
    uart_puts("[I2C] Initializing SHT30 Bitbang I2C on PB6/PB7...\r\n");
    i2c_init();

    /* Send factory periodic ART command (0x2B32) to SHT30 */
    i2c_start();
    i2c_write_byte(0x88);
    i2c_write_byte(0x2B);
    i2c_write_byte(0x32);
    i2c_stop();

    /* 7. Initialize USART0 for CRIR M1 CO2 sensor */
    uart_puts("[CO2] Initializing USART0 on PA9 (TX) / PA10 (RX) @ 9600 8N1...\r\n");
    usart0_init();

    uart_puts("[CO2] Pin diagnostics: PA9(TX)=");
    uart_putc('0' + gpio_read_pin(GPIOA_BASE, 9));
    uart_puts(", PA10(RX)=");
    uart_putc('0' + gpio_read_pin(GPIOA_BASE, 10));
    uart_puts(", PB9=");
    uart_putc('0' + gpio_read_pin(GPIOB_BASE, 9));
    uart_puts(", PB11=");
    uart_putc('0' + gpio_read_pin(GPIOB_BASE, 11));
    uart_puts("\r\n");

    uart_puts("[CO2] Waiting 2s for CRIR M1 NDIR power-up...\r\n");
    delay_ms(2000);

    uart_puts("[CO2] After 2s: PA10(RX)=");
    uart_putc('0' + gpio_read_pin(GPIOA_BASE, 10));
    uart_puts(", USART0_STAT=0x");
    print_hex8((uint8_t)(USART0_STAT >> 8));
    print_hex8((uint8_t)USART0_STAT);
    uart_puts("\r\n");

    uart_puts("\r\n[INFO] Starting periodic measurement loop:\r\n");



    uint32_t count = 0;
    while (1) {
        count++;
        int32_t temp_x10 = 0;
        int32_t hum_x10 = 0;
        int32_t co2_ppm = 0;
        uint8_t co2_raw[8] = {0};

        /* Read SHT30 (T/H) */
        int sht_res = sht30_read_periodic(&temp_x10, &hum_x10);
        if (sht_res != 0) {
            sht_res = sht30_read_single(&temp_x10, &hum_x10);
        }

        /* Read CRIR M1 (CO2) */
        int co2_res = crir_read_co2(&co2_ppm, co2_raw);

        uart_puts("[#");
        print_dec(count);
        uart_puts("] ");

        /* Print CO2 result */
        if (co2_res == 0) {
            uart_puts("CO2: ");
            print_dec((uint32_t)co2_ppm);
            uart_puts(" ppm");
        } else {
            uart_puts("CO2 err: ");
            if (co2_res < 0) {
                uart_putc('-');
                print_dec(-co2_res);
            } else {
                print_dec(co2_res);
            }
            uart_puts(" [raw:");
            for (int b = 0; b < 7; b++) {
                uart_putc(' ');
                print_hex8(co2_raw[b]);
            }
            uart_puts("]");
        }

        uart_puts("  |  ");

        /* Print T/H result */
        if (sht_res == 0) {
            uart_puts("Temp: ");
            print_signed_fixed1(temp_x10);
            uart_puts(" C  |  Humidity: ");
            print_signed_fixed1(hum_x10);
            uart_puts(" %RH");
        } else {
            uart_puts("SHT err: ");
            if (sht_res < 0) {
                uart_putc('-');
                print_dec(-sht_res);
            } else {
                print_dec(sht_res);
            }
        }

        uart_puts("\r\n");

        delay_ms_polling(2000);
    }
}
