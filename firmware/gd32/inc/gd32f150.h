#ifndef GD32F150_H
#define GD32F150_H

#include <stdint.h>
#include <stddef.h>

/* ── Cortex-M3 Core Peripherals ── */
#define SCB_BASE        0xE000ED00
#define SCB_VTOR        (*(volatile uint32_t *)(SCB_BASE + 0x08))
#define SCB_AIRCR       (*(volatile uint32_t *)(SCB_BASE + 0x0C))

/* ── RCU (Reset and Clock Unit) ── */
#define RCU_BASE        0x40021000
#define RCU_CTL         (*(volatile uint32_t *)(RCU_BASE + 0x00))
#define RCU_CFG0        (*(volatile uint32_t *)(RCU_BASE + 0x04))
#define RCU_INT         (*(volatile uint32_t *)(RCU_BASE + 0x08))
#define RCU_APB2RST     (*(volatile uint32_t *)(RCU_BASE + 0x0C))
#define RCU_APB1RST     (*(volatile uint32_t *)(RCU_BASE + 0x10))
#define RCU_AHBEN       (*(volatile uint32_t *)(RCU_BASE + 0x14))
#define RCU_APB2EN      (*(volatile uint32_t *)(RCU_BASE + 0x18))
#define RCU_APB1EN      (*(volatile uint32_t *)(RCU_BASE + 0x1C))

/* RCU AHBEN bits */
#define RCU_AHBEN_PAEN  (1 << 17)
#define RCU_AHBEN_PBEN  (1 << 18)
#define RCU_AHBEN_PCEN  (1 << 19)
#define RCU_AHBEN_PFEN  (1 << 22)

/* RCU APB1EN / APB2EN bits */
#define RCU_APB1EN_USART1EN (1 << 17)
#define RCU_APB2EN_USART0EN (1 << 14)
#define RCU_APB2EN_ADCEN    (1 << 9)
#define RCU_APB1EN_TIMER2EN (1 << 1)

/* ── ADC Registers ── */
#define ADC_BASE        0x40012400
#define ADC_STAT        (*(volatile uint32_t *)(ADC_BASE + 0x00))
#define ADC_CTL0        (*(volatile uint32_t *)(ADC_BASE + 0x04))
#define ADC_CTL1        (*(volatile uint32_t *)(ADC_BASE + 0x08))
#define ADC_SAMPT0      (*(volatile uint32_t *)(ADC_BASE + 0x0C))
#define ADC_SAMPT1      (*(volatile uint32_t *)(ADC_BASE + 0x10))
#define ADC_RSQ0        (*(volatile uint32_t *)(ADC_BASE + 0x2C))
#define ADC_RSQ1        (*(volatile uint32_t *)(ADC_BASE + 0x30))
#define ADC_RSQ2        (*(volatile uint32_t *)(ADC_BASE + 0x34))
#define ADC_RDATA       (*(volatile uint32_t *)(ADC_BASE + 0x4C))

/* ── GPIO Registers ── */
#define GPIOA_BASE      0x48000000
#define GPIOB_BASE      0x48000400
#define GPIOC_BASE      0x48000800
#define GPIOF_BASE      0x48001400

#define GPIO_CTL(b)     (*(volatile uint32_t *)((b) + 0x00))
#define GPIO_OMD(b)     (*(volatile uint32_t *)((b) + 0x04))
#define GPIO_OSPD(b)    (*(volatile uint32_t *)((b) + 0x08))
#define GPIO_PUD(b)     (*(volatile uint32_t *)((b) + 0x0C))
#define GPIO_ISTAT(b)   (*(volatile uint32_t *)((b) + 0x10))
#define GPIO_OCTL(b)    (*(volatile uint32_t *)((b) + 0x14))
#define GPIO_BOP(b)     (*(volatile uint32_t *)((b) + 0x18))
#define GPIO_AFSEL0(b)  (*(volatile uint32_t *)((b) + 0x20))
#define GPIO_AFSEL1(b)  (*(volatile uint32_t *)((b) + 0x24))
#define GPIO_BC(b)      (*(volatile uint32_t *)((b) + 0x28))

#define GPIOA_BOP       GPIO_BOP(GPIOA_BASE)
#define GPIOB_BOP       GPIO_BOP(GPIOB_BASE)
#define GPIOC_BOP       GPIO_BOP(GPIOC_BASE)

#define GPIOA_BC        GPIO_BC(GPIOA_BASE)
#define GPIOB_BC        GPIO_BC(GPIOB_BASE)
#define GPIOC_BC        GPIO_BC(GPIOC_BASE)

/* ── USART Registers ── */
#define USART0_BASE     0x40013800
#define USART0_CTL0     (*(volatile uint32_t *)(USART0_BASE + 0x00))
#define USART0_CTL1     (*(volatile uint32_t *)(USART0_BASE + 0x04))
#define USART0_CTL2     (*(volatile uint32_t *)(USART0_BASE + 0x08))
#define USART0_BAUD     (*(volatile uint32_t *)(USART0_BASE + 0x0C))
#define USART0_STAT     (*(volatile uint32_t *)(USART0_BASE + 0x1C))
#define USART0_RDATA    (*(volatile uint32_t *)(USART0_BASE + 0x24))
#define USART0_TDATA    (*(volatile uint32_t *)(USART0_BASE + 0x28))

#define USART1_BASE     0x40004400
#define USART1_CTL0     (*(volatile uint32_t *)(USART1_BASE + 0x00))
#define USART1_CTL1     (*(volatile uint32_t *)(USART1_BASE + 0x04))
#define USART1_CTL2     (*(volatile uint32_t *)(USART1_BASE + 0x08))
#define USART1_BAUD     (*(volatile uint32_t *)(USART1_BASE + 0x0C))
#define USART1_STAT     (*(volatile uint32_t *)(USART1_BASE + 0x1C))
#define USART1_RDATA    (*(volatile uint32_t *)(USART1_BASE + 0x24))
#define USART1_TDATA    (*(volatile uint32_t *)(USART1_BASE + 0x28))

#define USART_UEN       (1 << 0)
#define USART_REN       (1 << 2)
#define USART_TEN       (1 << 3)
#define USART_RBNE      (1 << 5)
#define USART_TC        (1 << 6)
#define USART_TBE       (1 << 7)

/* ── Free Watchdog (FWDGT / IWDG) ── */
#define FWDGT_BASE      0x40003000
#define FWDGT_CTL       (*(volatile uint32_t *)(FWDGT_BASE + 0x00))
#define FWDGT_PSC       (*(volatile uint32_t *)(FWDGT_BASE + 0x04))
#define FWDGT_RLD       (*(volatile uint32_t *)(FWDGT_BASE + 0x08))
#define FWDGT_STAT      (*(volatile uint32_t *)(FWDGT_BASE + 0x0C))

#define FWDGT_KEY_ENABLE    0xCCCC
#define FWDGT_KEY_RELOAD    0xAAAA
#define FWDGT_KEY_ACCESS    0x5555

/* ── Clock Frequency ── */
#define SYSTEM_CLOCK_HZ 8000000UL

/* ── Delays ── */
static inline void delay_cycles(uint32_t n)
{
    while (n--) {
        __asm__ volatile("");
    }
}

static inline void delay_us(uint32_t us)
{
    /* ~2 cycles per loop @ 8MHz = 0.25us per loop -> 4 loops per us */
    while (us--) {
        delay_cycles(2);
    }
}

static inline void delay_ms(uint32_t ms)
{
    while (ms--) {
        delay_cycles(2000);
    }
}

/* ── GPIO Helpers ── */
static inline void gpio_cfg_out_pp(uint32_t base, int pin)
{
    uint32_t ctl = GPIO_CTL(base);
    ctl &= ~(3 << (pin * 2));
    ctl |= (1 << (pin * 2)); /* 01 = Output */
    GPIO_CTL(base) = ctl;

    GPIO_OMD(base) &= ~(1 << pin); /* 0 = Push-Pull */

    uint32_t ospd = GPIO_OSPD(base);
    ospd &= ~(3 << (pin * 2));
    ospd |= (3 << (pin * 2)); /* 50MHz */
    GPIO_OSPD(base) = ospd;

    uint32_t pud = GPIO_PUD(base);
    pud &= ~(3 << (pin * 2)); /* No pull */
    GPIO_PUD(base) = pud;
}

static inline void gpio_cfg_out_od(uint32_t base, int pin)
{
    uint32_t ctl = GPIO_CTL(base);
    ctl &= ~(3 << (pin * 2));
    ctl |= (1 << (pin * 2)); /* Output */
    GPIO_CTL(base) = ctl;

    GPIO_OMD(base) |= (1 << pin); /* Open-Drain */

    uint32_t ospd = GPIO_OSPD(base);
    ospd &= ~(3 << (pin * 2));
    ospd |= (3 << (pin * 2)); /* 50MHz */
    GPIO_OSPD(base) = ospd;

    uint32_t pud = GPIO_PUD(base);
    pud &= ~(3 << (pin * 2));
    pud |= (1 << (pin * 2)); /* Pull-Up */
    GPIO_PUD(base) = pud;
}

static inline void gpio_cfg_in(uint32_t base, int pin, int pull_mode)
{
    /* pull_mode: 0=none, 1=pullup, 2=pulldown */
    uint32_t ctl = GPIO_CTL(base);
    ctl &= ~(3 << (pin * 2)); /* 00 = Input */
    GPIO_CTL(base) = ctl;

    uint32_t pud = GPIO_PUD(base);
    pud &= ~(3 << (pin * 2));
    pud |= (pull_mode << (pin * 2));
    GPIO_PUD(base) = pud;
}

static inline int gpio_get(uint32_t base, int pin)
{
    return (GPIO_ISTAT(base) & (1 << pin)) ? 1 : 0;
}

static inline void gpio_set(uint32_t base, int pin, int val)
{
    if (val) {
        GPIO_BOP(base) = (1 << pin);
    } else {
        GPIO_BC(base) = (1 << pin);
    }
}

#endif /* GD32F150_H */
