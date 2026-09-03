/*
 * UART Test for GD32F150C8T6 — runs entirely from SRAM
 *
 * Initializes USART1 TX on PA2 and sends a repeating test message.
 * This program does NOT access Flash memory in any way.
 *
 * Hardware setup:
 *   GD32 PA2 (USART1 TX) → Pico GP5 (UART RX)
 *   GND                   → GND
 *
 * Clock: default HSI = 8 MHz, all prescalers = 1
 * Baud: 115200 (BRR = 0x45, actual = 115942, error +0.64%)
 */

#include <stdint.h>

/* ── RCU (Reset and Clock Unit) ── */
#define RCU_BASE      0x40021000
#define RCU_AHBEN     (*(volatile uint32_t *)(RCU_BASE + 0x14))
#define RCU_APB1EN    (*(volatile uint32_t *)(RCU_BASE + 0x1C))

/* ── GPIOA (new-style GPIO, like STM32F0) ── */
#define GPIOA_BASE    0x48000000
#define GPIOA_CTL     (*(volatile uint32_t *)(GPIOA_BASE + 0x00))  /* MODER  */
#define GPIOA_OSPD    (*(volatile uint32_t *)(GPIOA_BASE + 0x08))  /* Speed  */
#define GPIOA_AFSEL0  (*(volatile uint32_t *)(GPIOA_BASE + 0x20))  /* AFRL   */

/* ── USART1 ── */
#define USART1_BASE   0x40004400
#define USART1_CTL0   (*(volatile uint32_t *)(USART1_BASE + 0x00))
#define USART1_BAUD   (*(volatile uint32_t *)(USART1_BASE + 0x0C))
#define USART1_STAT   (*(volatile uint32_t *)(USART1_BASE + 0x1C))
#define USART1_TDATA  (*(volatile uint32_t *)(USART1_BASE + 0x28))

/* USART_CTL0 bits */
#define UEN   (1 << 0)   /* USART enable     */
#define TEN   (1 << 3)   /* Transmitter enable */

/* USART_STAT bits */
#define TBE   (1 << 7)   /* Transmit buffer empty */

/* ────────────────────────────────────────── */

static void delay(volatile uint32_t n)
{
    while (n--) __asm__ volatile("");
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

void main(void)
{
    /* 1. Enable clocks: GPIOA (AHB) and USART1 (APB1) */
    RCU_AHBEN  |= (1 << 17);   /* PAEN  – GPIOA clock  */
    RCU_APB1EN |= (1 << 17);   /* USART1EN              */
    delay(100);

    /* 2. Configure PA2 as Alternate Function, medium speed */
    {
        uint32_t ctl = GPIOA_CTL;
        ctl &= ~(3U  << (2 * 2));   /* clear mode bits for PA2 */
        ctl |=  (2U  << (2 * 2));   /* AF mode = 0b10          */
        GPIOA_CTL = ctl;

        uint32_t spd = GPIOA_OSPD;
        spd &= ~(3U  << (2 * 2));
        spd |=  (1U  << (2 * 2));   /* medium speed (10 MHz)   */
        GPIOA_OSPD = spd;
    }

    /* 3. PA2 → AF1 (USART1_TX) in AFSEL0 (low register, pins 0-7) */
    {
        uint32_t af = GPIOA_AFSEL0;
        af &= ~(0xFU << (2 * 4));   /* clear 4 AF bits for PA2 */
        af |=  (0x1U << (2 * 4));   /* AF1 = USART1_TX         */
        GPIOA_AFSEL0 = af;
    }

    /* 4. USART1: 115200 baud, 8N1 */
    USART1_CTL0 = 0;               /* disable while configuring */
    USART1_BAUD = 0x45;            /* 8 MHz / (16 × 4.3125) ≈ 115200 */
    USART1_CTL0 = UEN | TEN;       /* enable USART + transmitter */

    /* 5. Transmit test message in a loop */
    uint32_t counter = 0;
    while (1) {
        uart_puts("Hello from GD32 SRAM! #");

        /* simple decimal counter */
        char buf[12];
        int i = 0;
        uint32_t n = counter++;
        if (n == 0) {
            buf[i++] = '0';
        } else {
            char tmp[12];
            int j = 0;
            while (n) { tmp[j++] = '0' + (n % 10); n /= 10; }
            while (j--) buf[i++] = tmp[j];
        }
        buf[i] = '\0';
        uart_puts(buf);
        uart_puts("\r\n");

        delay(800000);   /* ~0.5 sec at 8 MHz */
    }
}
