/*
 * HTRAM GD32F150 Battery / ADC / SHT30 Diagnostic Probe — runs from SRAM (0x20000000).
 *
 * Does NOT touch Flash. Answers four open questions:
 *
 *   1. What is PB2 really? GD32_HARDWARE_MAP.md §5 calls it the SHT30 power /
 *      pull-up rail (Active-HIGH); firmware/gd32/src/periph.c treats it as a
 *      battery-divider switch and keeps it LOW. Both cannot be true.
 *   2. Does the ADC actually convert? periph.c asserts SWRCST without ever
 *      enabling ETERC/ETSRC, which on GD32F1x0 should mean no trigger at all.
 *   3. What is the true mV scale? Vrefint (ch17) gives VDDA, which turns a raw
 *      count into volts without trusting the factory (raw*3275)>>11 constant.
 *   4. How large is real self-heating? Phase B switches the backlight and the
 *      5 V boost on so the T/H step can be measured instead of modelled.
 *
 * Output is CSV on USART1 (PA2 @ 115200) for the host to plot:
 *   LOG,iter,phase,rawT,rawH,t_c100,rh_c100,adc9,adc17,vdda_mv,batt_fac,batt_ref,pc13,pa15
 */

#include <stdint.h>

/* ── RCU ── */
#define RCU_BASE      0x40021000
#define RCU_CFG0      (*(volatile uint32_t *)(RCU_BASE + 0x04))
#define RCU_AHBEN     (*(volatile uint32_t *)(RCU_BASE + 0x14))
#define RCU_APB2EN    (*(volatile uint32_t *)(RCU_BASE + 0x18))
#define RCU_APB1EN    (*(volatile uint32_t *)(RCU_BASE + 0x1C))
#define RCU_CFG2      (*(volatile uint32_t *)(RCU_BASE + 0x30))

/* ── GPIO ── */
#define GPIOA_BASE    0x48000000
#define GPIOB_BASE    0x48000400
#define GPIOC_BASE    0x48000800

#define GPIO_CTL(b)   (*(volatile uint32_t *)((b) + 0x00))
#define GPIO_OMD(b)   (*(volatile uint32_t *)((b) + 0x04))
#define GPIO_OSPD(b)  (*(volatile uint32_t *)((b) + 0x08))
#define GPIO_PUD(b)   (*(volatile uint32_t *)((b) + 0x0C))
#define GPIO_ISTAT(b) (*(volatile uint32_t *)((b) + 0x10))
#define GPIO_BOP(b)   (*(volatile uint32_t *)((b) + 0x18))
#define GPIO_AFSEL0(b)(*(volatile uint32_t *)((b) + 0x20))
#define GPIO_BC(b)    (*(volatile uint32_t *)((b) + 0x28))

/* ── USART1 (debug out on PA2) ── */
#define USART1_BASE   0x40004400
#define USART1_CTL0   (*(volatile uint32_t *)(USART1_BASE + 0x00))
#define USART1_BAUD   (*(volatile uint32_t *)(USART1_BASE + 0x0C))
#define USART1_STAT   (*(volatile uint32_t *)(USART1_BASE + 0x1C))
#define USART1_TDATA  (*(volatile uint32_t *)(USART1_BASE + 0x28))
#define UEN   (1 << 0)
#define TEN   (1 << 3)
#define TBE   (1 << 7)

/* ── ADC ── */
#define ADC_BASE      0x40012400
#define ADC_STAT      (*(volatile uint32_t *)(ADC_BASE + 0x00))
#define ADC_CTL0      (*(volatile uint32_t *)(ADC_BASE + 0x04))
#define ADC_CTL1      (*(volatile uint32_t *)(ADC_BASE + 0x08))
#define ADC_SAMPT0    (*(volatile uint32_t *)(ADC_BASE + 0x0C))  /* ch10..17 */
#define ADC_SAMPT1    (*(volatile uint32_t *)(ADC_BASE + 0x10))  /* ch0..9   */
#define ADC_RSQ0      (*(volatile uint32_t *)(ADC_BASE + 0x2C))
#define ADC_RSQ2      (*(volatile uint32_t *)(ADC_BASE + 0x34))
#define ADC_RDATA     (*(volatile uint32_t *)(ADC_BASE + 0x4C))

#define ADC_EOC       (1 << 1)
#define ADC_ADCON     (1 << 0)
#define ADC_CLB       (1 << 2)
#define ADC_RSTCLB    (1 << 3)
#define ADC_ETERC     (1 << 20)
#define ADC_SWRCST    (1 << 22)
#define ADC_TSVREN    (1 << 23)

#define VREFINT_CH    17
#define VREFINT_MV    1200      /* GD32F150 internal reference, typical */
#define BATT_CH       9         /* PB1 */

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
        buf[i++] = (char)('0' + (n % 10));
        n /= 10;
    }
    while (i--)
        uart_putc((uint8_t)buf[i]);
}

static void print_int(int32_t v)
{
    if (v < 0) {
        uart_putc('-');
        v = -v;
    }
    print_dec((uint32_t)v);
}

/* Print a 0.01-scaled fixed point value, e.g. 2437 -> "24.37" */
static void print_fixed2(int32_t v)
{
    if (v < 0) {
        uart_putc('-');
        v = -v;
    }
    print_dec((uint32_t)(v / 100));
    uart_putc('.');
    uint32_t frac = (uint32_t)(v % 100);
    uart_putc((uint8_t)('0' + frac / 10));
    uart_putc((uint8_t)('0' + frac % 10));
}

static void gpio_config_output(uint32_t base, int pin)
{
    uint32_t ctl = GPIO_CTL(base);
    ctl &= ~(3U << (pin * 2));
    ctl |=  (1U << (pin * 2));
    GPIO_CTL(base) = ctl;

    GPIO_OMD(base) &= ~(1U << pin);

    uint32_t ospd = GPIO_OSPD(base);
    ospd &= ~(3U << (pin * 2));
    ospd |=  (3U << (pin * 2));
    GPIO_OSPD(base) = ospd;
}

static inline int gpio_read_pin(uint32_t base, int pin)
{
    return (GPIO_ISTAT(base) & (1U << pin)) ? 1 : 0;
}

/* ── I2C bitbang on PB6 (SCL) / PB7 (SDA) — same routine as uart_test.c ── */
#define SCL_PIN  6
#define SDA_PIN  7

static inline void i2c_delay(void)
{
    delay_cycles(40);
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
    RCU_AHBEN |= (1 << 18);

    gpio_config_output(GPIOB_BASE, SCL_PIN);
    GPIO_OMD(GPIOB_BASE) |= (1U << SCL_PIN);
    gpio_config_output(GPIOB_BASE, SDA_PIN);
    GPIO_OMD(GPIOB_BASE) |= (1U << SDA_PIN);

    uint32_t pud = GPIO_PUD(GPIOB_BASE);
    pud &= ~((3U << (SCL_PIN * 2)) | (3U << (SDA_PIN * 2)));
    pud |=  ((1U << (SCL_PIN * 2)) | (1U << (SDA_PIN * 2)));
    GPIO_PUD(GPIOB_BASE) = pud;

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
    GPIO_BC(GPIOB_BASE)  = (1 << SDA_PIN);
    i2c_delay();
    GPIO_BC(GPIOB_BASE)  = (1 << SCL_PIN);
    i2c_delay();
}

static void i2c_stop(void)
{
    sda_mode_output();
    GPIO_BC(GPIOB_BASE)  = (1 << SDA_PIN);
    i2c_delay();
    GPIO_BOP(GPIOB_BASE) = (1 << SCL_PIN);
    i2c_delay();
    GPIO_BOP(GPIOB_BASE) = (1 << SDA_PIN);
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
            byte |= (uint8_t)(1 << i);
        GPIO_BC(GPIOB_BASE) = (1 << SCL_PIN);
        i2c_delay();
    }

    sda_mode_output();
    if (ack)
        GPIO_BC(GPIOB_BASE)  = (1 << SDA_PIN);
    else
        GPIO_BOP(GPIOB_BASE) = (1 << SDA_PIN);
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
                crc = (uint8_t)((crc << 1) ^ 0x31);
            else
                crc = (uint8_t)(crc << 1);
        }
    }
    return crc;
}

/* Single-shot high-repeatability read (0x2400). Returns 0 on success. */
static int sht30_read_raw(uint16_t *raw_t, uint16_t *raw_h)
{
    uint8_t buf[6];

    i2c_start();
    if (!i2c_write_byte(0x88)) { i2c_stop(); return -1; }
    if (!i2c_write_byte(0x24)) { i2c_stop(); return -2; }
    if (!i2c_write_byte(0x00)) { i2c_stop(); return -3; }
    i2c_stop();

    delay_ms(20);

    i2c_start();
    if (!i2c_write_byte(0x89)) { i2c_stop(); return -4; }
    for (int i = 0; i < 5; i++)
        buf[i] = i2c_read_byte(1);
    buf[5] = i2c_read_byte(0);
    i2c_stop();

    if (crc8(&buf[0], 2) != buf[2] || crc8(&buf[3], 2) != buf[5])
        return -5;

    *raw_t = (uint16_t)(((uint16_t)buf[0] << 8) | buf[1]);
    *raw_h = (uint16_t)(((uint16_t)buf[3] << 8) | buf[4]);
    return 0;
}

/* ── ADC ── */

static void adc_power_up(void)
{
    RCU_APB2EN |= (1 << 9);
    RCU_CFG0 |= 0x8000;     /* ADC prescaler */
    RCU_CFG2 |= 0x0100;     /* ADC clock source */
    delay_cycles(2000);

    /* PB1 (ch9) analog, no pull */
    uint32_t ctl = GPIO_CTL(GPIOB_BASE);
    ctl |= (3U << (1 * 2));
    GPIO_CTL(GPIOB_BASE) = ctl;
    GPIO_PUD(GPIOB_BASE) &= ~(3U << (1 * 2));

    ADC_RSQ0 = 0;                       /* one conversion */
    ADC_SAMPT1 = (7U << (BATT_CH * 3)); /* 239.5 cycles on ch9 */
    ADC_SAMPT0 = (7U << ((VREFINT_CH - 10) * 3));

    ADC_CTL1 |= ADC_ADCON;
    delay_cycles(4000);

    ADC_CTL1 |= ADC_RSTCLB;
    uint32_t to = 100000;
    while ((ADC_CTL1 & ADC_RSTCLB) && --to)
        ;
    ADC_CTL1 |= ADC_CLB;
    to = 100000;
    while ((ADC_CTL1 & ADC_CLB) && --to)
        ;
}

/*
 * mode 0 = SWRCST only            (exactly what firmware/gd32/src/periph.c does)
 * mode 1 = ETERC + ETSRC=0b111, then SWRCST   (the documented way)
 * mode 2 = re-assert ADCON        (classic F1 fallback)
 *
 * Sets *ok to 0 if EOC never arrived.
 */
static uint16_t adc_convert(uint8_t ch, int mode, int *ok)
{
    ADC_RSQ0 = 0;
    ADC_RSQ2 = ch;
    if (ch < 10)
        ADC_SAMPT1 = (7U << (ch * 3));
    else
        ADC_SAMPT0 = (7U << ((ch - 10) * 3));

    ADC_STAT = 0;

    if (mode == 1) {
        uint32_t c = ADC_CTL1;
        c &= ~(7U << 17);
        c |= (7U << 17) | ADC_ETERC;
        ADC_CTL1 = c;
    }

    if (mode == 2)
        ADC_CTL1 |= ADC_ADCON;
    else
        ADC_CTL1 |= ADC_SWRCST;

    uint32_t to = 200000;
    while (!(ADC_STAT & ADC_EOC) && --to)
        ;

    *ok = to ? 1 : 0;
    return (uint16_t)(ADC_RDATA & 0xFFFF);
}

static void set_pb2(int level)
{
    if (level)
        GPIO_BOP(GPIOB_BASE) = (1 << 2);
    else
        GPIO_BC(GPIOB_BASE) = (1 << 2);
}

static void report_sht30(const char *label)
{
    uint16_t rt = 0, rh = 0;
    uart_puts(label);
    int rc = sht30_read_raw(&rt, &rh);
    if (rc != 0) {
        uart_puts("FAIL rc=");
        print_int(rc);
        uart_puts("\r\n");
        return;
    }
    int32_t t = -4500 + ((int32_t)17500 * rt) / 65535;
    int32_t h = ((int32_t)10000 * rh) / 65535;
    uart_puts("OK  raw_t=");
    print_dec(rt);
    uart_puts(" raw_h=");
    print_dec(rh);
    uart_puts("  T=");
    print_fixed2(t);
    uart_puts("C  RH=");
    print_fixed2(h);
    uart_puts("%\r\n");
}

void main(void)
{
    /* Clocks: GPIOA/B/C, USART1 */
    RCU_AHBEN  |= (1 << 17) | (1 << 18) | (1 << 19);
    RCU_APB1EN |= (1 << 17);
    delay_cycles(1000);

    /* PA2 = USART1 TX (AF1) */
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
    USART1_BAUD = 0x0045;
    USART1_CTL0 = UEN | TEN;

    delay_ms(200);
    uart_puts("\r\n\r\n===============================================\r\n");
    uart_puts("  HTRAM Battery / ADC / SHT30 Diagnostic Probe\r\n");
    uart_puts("===============================================\r\n");

    /* Power latches FIRST — without PC15 the board can cut its own supply. */
    gpio_config_output(GPIOC_BASE, 15);
    GPIO_BOP(GPIOC_BASE) = (1 << 15);
    gpio_config_output(GPIOB_BASE, 3);
    GPIO_BOP(GPIOB_BASE) = (1 << 3);
    gpio_config_output(GPIOA_BASE, 1);
    GPIO_BOP(GPIOA_BASE) = (1 << 1);
    uart_puts("[PWR] PC15=1 PB3=1 PA1=1\r\n");

    /* SHT30 nRESET high */
    gpio_config_output(GPIOA_BASE, 8);
    GPIO_BOP(GPIOA_BASE) = (1 << 8);

    /* PB2 as push-pull output with pull-up, starting HIGH (hardware-map role) */
    gpio_config_output(GPIOB_BASE, 2);
    {
        uint32_t pud = GPIO_PUD(GPIOB_BASE);
        pud &= ~(3U << (2 * 2));
        pud |=  (1U << (2 * 2));
        GPIO_PUD(GPIOB_BASE) = pud;
    }
    set_pb2(1);

    /* Boost / CO2 rails OFF for the quiet baseline, backlight OFF */
    gpio_config_output(GPIOB_BASE, 11);
    GPIO_BC(GPIOB_BASE) = (1 << 11);
    gpio_config_output(GPIOB_BASE, 9);
    GPIO_BC(GPIOB_BASE) = (1 << 9);
    gpio_config_output(GPIOB_BASE, 8);
    GPIO_BC(GPIOB_BASE) = (1 << 8);

    /* USB VBUS (PC13) float, charger status (PA15) pull-up */
    {
        uint32_t ctl = GPIO_CTL(GPIOC_BASE);
        ctl &= ~(3U << (13 * 2));
        GPIO_CTL(GPIOC_BASE) = ctl;

        ctl = GPIO_CTL(GPIOA_BASE);
        ctl &= ~(3U << (15 * 2));
        GPIO_CTL(GPIOA_BASE) = ctl;
        uint32_t pud = GPIO_PUD(GPIOA_BASE);
        pud &= ~(3U << (15 * 2));
        pud |=  (1U << (15 * 2));
        GPIO_PUD(GPIOA_BASE) = pud;
    }

    i2c_init();
    delay_ms(50);

    /* ── TEST 1: what does PB2 actually gate? ── */
    uart_puts("\r\n--- [1] PB2 role: SHT30 with rail HIGH vs LOW ---\r\n");
    set_pb2(1);
    delay_ms(100);
    report_sht30("  PB2=1 #1: ");
    report_sht30("  PB2=1 #2: ");

    set_pb2(0);
    delay_ms(300);
    report_sht30("  PB2=0 #1: ");
    report_sht30("  PB2=0 #2: ");
    report_sht30("  PB2=0 #3: ");

    set_pb2(1);
    delay_ms(300);
    report_sht30("  PB2=1 restored: ");

    /* ── TEST 2: does the ADC trigger at all? ── */
    uart_puts("\r\n--- [2] ADC trigger strategies on ch9 (PB1) ---\r\n");
    adc_power_up();
    uart_puts("  CTL1 after init = 0x");
    {
        uint32_t v = ADC_CTL1;
        for (int i = 7; i >= 0; i--) {
            uint8_t nib = (uint8_t)((v >> (i * 4)) & 0xF);
            uart_putc((uint8_t)(nib < 10 ? '0' + nib : 'A' + nib - 10));
        }
    }
    uart_puts("\r\n");

    for (int mode = 0; mode <= 2; mode++) {
        for (int pb2 = 1; pb2 >= 0; pb2--) {
            set_pb2(pb2);
            delay_ms(20);
            int ok = 0;
            uint16_t raw = adc_convert(BATT_CH, mode, &ok);
            uart_puts("  mode=");
            print_int(mode);
            uart_puts(mode == 0 ? " (SWRCST only, as in periph.c)" :
                      mode == 1 ? " (ETERC+ETSRC then SWRCST)  " :
                                  " (re-assert ADCON)          ");
            uart_puts("  PB2=");
            print_int(pb2);
            uart_puts("  EOC=");
            uart_puts(ok ? "yes" : "NO ");
            uart_puts("  raw=");
            print_dec(raw);
            uart_puts("\r\n");
        }
    }
    set_pb2(1);

    /* ── TEST 3: Vrefint -> real VDDA -> real battery scale ── */
    uart_puts("\r\n--- [3] Vrefint (ch17) and derived scale ---\r\n");
    ADC_CTL1 |= ADC_TSVREN;
    delay_ms(10);

    int ok_ref = 0, ok_bat = 0;
    uint16_t raw_ref = adc_convert(VREFINT_CH, 1, &ok_ref);
    uint16_t raw_bat = adc_convert(BATT_CH, 1, &ok_bat);

    uart_puts("  raw_vrefint=");
    print_dec(raw_ref);
    uart_puts(ok_ref ? " (EOC ok)" : " (EOC FAILED)");
    uart_puts("\r\n  raw_batt=");
    print_dec(raw_bat);
    uart_puts(ok_bat ? " (EOC ok)" : " (EOC FAILED)");
    uart_puts("\r\n");

    uint32_t vdda_mv = 0;
    if (raw_ref)
        vdda_mv = ((uint32_t)VREFINT_MV * 4095U) / raw_ref;
    uart_puts("  VDDA = ");
    print_dec(vdda_mv);
    uart_puts(" mV\r\n");

    uart_puts("  batt via factory formula (raw*3275)>>11 = ");
    print_dec(((uint32_t)raw_bat * 3275U) >> 11);
    uart_puts(" mV\r\n");
    uart_puts("  batt via Vrefint, assuming 2:1 divider   = ");
    if (vdda_mv)
        print_dec(((uint32_t)raw_bat * vdda_mv * 2U) / 4095U);
    else
        uart_puts("n/a");
    uart_puts(" mV\r\n");

    /* ── TEST 4: which channel actually carries a battery-like voltage? ── */
    uart_puts("\r\n--- [4] Channel scan (PB2=1 / PB2=0) ---\r\n");
    for (uint8_t ch = 0; ch <= 17; ch++) {
        int ok_a = 0, ok_b = 0;
        set_pb2(1);
        delay_ms(5);
        uint16_t a = adc_convert(ch, 1, &ok_a);
        set_pb2(0);
        delay_ms(5);
        uint16_t b = adc_convert(ch, 1, &ok_b);
        set_pb2(1);

        uart_puts("  ch");
        if (ch < 10) uart_putc(' ');
        print_dec(ch);
        uart_puts(": PB2=1 -> ");
        print_dec(a);
        uart_puts("   PB2=0 -> ");
        print_dec(b);
        if (!ok_a || !ok_b)
            uart_puts("   [EOC fail]");
        uart_puts("\r\n");
    }

    /* ── PHASE LOG ── */
    uart_puts("\r\n--- [5] Continuous log ---\r\n");
    uart_puts("# phase A = quiet (backlight off, boost off)\r\n");
    uart_puts("# phase B = backlight ON + 5V boost ON + CO2 powered\r\n");
    uart_puts("# phase C = quiet again\r\n");
    uart_puts("LOG,iter,phase,rawT,rawH,t_c100,rh_c100,adc9,adc17,vdda_mv,batt_fac,batt_ref,pc13,pa15\r\n");

    set_pb2(1);
    for (uint32_t iter = 0; ; iter++) {
        char phase;
        if (iter < 30) {
            phase = 'A';
        } else if (iter < 70) {
            phase = 'B';
            GPIO_BOP(GPIOB_BASE) = (1 << 8) | (1 << 11) | (1 << 9);
        } else {
            phase = 'C';
            GPIO_BC(GPIOB_BASE) = (1 << 8) | (1 << 11) | (1 << 9);
        }

        uint16_t rt = 0, rh = 0;
        int rc = sht30_read_raw(&rt, &rh);

        int ok_r = 0, ok_b = 0;
        uint16_t r17 = adc_convert(VREFINT_CH, 1, &ok_r);
        uint16_t r9  = adc_convert(BATT_CH, 1, &ok_b);
        uint32_t vdda = r17 ? (((uint32_t)VREFINT_MV * 4095U) / r17) : 0;

        uart_puts("LOG,");
        print_dec(iter);
        uart_putc(',');
        uart_putc((uint8_t)phase);
        uart_putc(',');
        if (rc == 0) {
            int32_t t = -4500 + ((int32_t)17500 * rt) / 65535;
            int32_t h = ((int32_t)10000 * rh) / 65535;
            print_dec(rt);
            uart_putc(',');
            print_dec(rh);
            uart_putc(',');
            print_int(t);
            uart_putc(',');
            print_int(h);
        } else {
            uart_puts("0,0,,");
        }
        uart_putc(',');
        print_dec(r9);
        uart_putc(',');
        print_dec(r17);
        uart_putc(',');
        print_dec(vdda);
        uart_putc(',');
        print_dec(((uint32_t)r9 * 3275U) >> 11);
        uart_putc(',');
        print_dec(vdda ? (((uint32_t)r9 * vdda * 2U) / 4095U) : 0);
        uart_putc(',');
        print_int(gpio_read_pin(GPIOC_BASE, 13));
        uart_putc(',');
        print_int(gpio_read_pin(GPIOA_BASE, 15));
        uart_puts("\r\n");

        delay_ms(950);
    }
}
