/*
 * GD32F150 flash writer — runs from SRAM (0x20000000), streams an image over
 * USART1 (PA2 TX / PA3 RX @ 115200) and programs it into main flash via FMC.
 *
 * Borrowed pyocd/openocd targets do not fit this part: openocd can't examine a
 * GD32 over SWD-only (no NRST), and the stm32f103rc flash algorithm overflows
 * this chip's 8 KB SRAM. So we drive the FMC directly, the same way rdp_unlock.c
 * drove the option bytes.
 *
 * Chunked, ACKed protocol (no hardware flow control, so the host waits for each
 * ACK before sending the next chunk):
 *
 *   device -> "READY\r\n"
 *   loop:
 *     host   -> [len_lo][len_hi]                 chunk length, 0 = end
 *               [len bytes]                      raw image bytes (padded even)
 *     device :  page-erase as it crosses 1 KB boundaries, program half-words,
 *               read back, and reply 0x06 (ACK) or 0x15 (NAK)
 *   host   -> len 0
 *   device -> "DONE crc=0x....\r\n"
 *
 * Programming target base is 0x08000000. Flash is already blank from the RDP
 * mass-erase, but pages are erased anyway so the tool also works for re-flashes.
 */

#include <stdint.h>

#define RCU_BASE      0x40021000
#define RCU_AHBEN     (*(volatile uint32_t *)(RCU_BASE + 0x14))
#define RCU_APB1EN    (*(volatile uint32_t *)(RCU_BASE + 0x1C))

#define GPIOA_BASE    0x48000000
#define GPIOA_CTL     (*(volatile uint32_t *)(GPIOA_BASE + 0x00))
#define GPIOA_OSPD    (*(volatile uint32_t *)(GPIOA_BASE + 0x08))
#define GPIOA_PUD     (*(volatile uint32_t *)(GPIOA_BASE + 0x0C))
#define GPIOA_AFSEL0  (*(volatile uint32_t *)(GPIOA_BASE + 0x20))

#define GPIOB_BASE    0x48000400
#define GPIOC_BASE    0x48000800
#define GPIO_CTL(b)   (*(volatile uint32_t *)((b) + 0x00))
#define GPIO_BOP(b)   (*(volatile uint32_t *)((b) + 0x18))

#define USART1_BASE   0x40004400
#define USART1_CTL0   (*(volatile uint32_t *)(USART1_BASE + 0x00))
#define USART1_BAUD   (*(volatile uint32_t *)(USART1_BASE + 0x0C))
#define USART1_STAT   (*(volatile uint32_t *)(USART1_BASE + 0x1C))
#define USART1_RDATA  (*(volatile uint32_t *)(USART1_BASE + 0x24))
#define USART1_TDATA  (*(volatile uint32_t *)(USART1_BASE + 0x28))

#define UEN   (1 << 0)
#define REN   (1 << 2)
#define TEN   (1 << 3)
#define RBNE  (1 << 5)
#define TBE   (1 << 7)

#define FMC_BASE      0x40022000
#define FMC_KEY       (*(volatile uint32_t *)(FMC_BASE + 0x04))
#define FMC_STAT      (*(volatile uint32_t *)(FMC_BASE + 0x0C))
#define FMC_CTL       (*(volatile uint32_t *)(FMC_BASE + 0x10))
#define FMC_ADDR      (*(volatile uint32_t *)(FMC_BASE + 0x14))

#define UNLOCK_KEY0   0x45670123UL
#define UNLOCK_KEY1   0xCDEF89ABUL

#define CTL_PG        (1 << 0)
#define CTL_PER       (1 << 1)
#define CTL_START     (1 << 6)
#define CTL_LK        (1 << 7)

#define STAT_BUSY     (1 << 0)
#define STAT_PGERR    (1 << 2)
#define STAT_WPERR    (1 << 4)
#define STAT_ENDF     (1 << 5)

#define FLASH_BASE    0x08000000U
#define PAGE_SIZE     0x400U          /* 1 KB pages on GD32F150 */

#define ACK           0x06
#define NAK           0x15

static void delay(volatile uint32_t n)
{
    while (n--)
        __asm__ volatile("");
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

static void uart_hex16(uint16_t v)
{
    uart_puts("0x");
    for (int i = 3; i >= 0; i--)
        uart_putc(hex_lut[(v >> (i * 4)) & 0xF]);
}

/* Blocking read with a long timeout; returns -1 if nothing arrives. */
static int uart_getc(uint32_t timeout)
{
    while (timeout--) {
        if (USART1_STAT & RBNE)
            return (int)(USART1_RDATA & 0xFF);
    }
    return -1;
}

static void fmc_wait_idle(void)
{
    uint32_t guard = 2000000;
    while ((FMC_STAT & STAT_BUSY) && --guard)
        ;
}

static int fmc_erase_page(uint32_t addr)
{
    fmc_wait_idle();
    FMC_CTL |= CTL_PER;
    FMC_ADDR = addr;
    FMC_CTL |= CTL_START;
    fmc_wait_idle();
    FMC_CTL &= ~CTL_PER;
    if (FMC_STAT & (STAT_PGERR | STAT_WPERR)) {
        FMC_STAT = STAT_ENDF | STAT_PGERR | STAT_WPERR;
        return -1;
    }
    FMC_STAT = STAT_ENDF | STAT_PGERR | STAT_WPERR;
    return 0;
}

static int fmc_program_halfword(uint32_t addr, uint16_t data)
{
    fmc_wait_idle();
    FMC_CTL |= CTL_PG;
    *(volatile uint16_t *)addr = data;
    fmc_wait_idle();
    FMC_CTL &= ~CTL_PG;
    if (FMC_STAT & (STAT_PGERR | STAT_WPERR)) {
        FMC_STAT = STAT_ENDF | STAT_PGERR | STAT_WPERR;
        return -1;
    }
    FMC_STAT = STAT_ENDF | STAT_PGERR | STAT_WPERR;
    /* Verify the programmed half-word read back. */
    if (*(volatile uint16_t *)addr != data)
        return -1;
    return 0;
}

static uint8_t chunk[512];

void main(void)
{
    RCU_AHBEN  |= (1 << 17) | (1 << 18) | (1 << 19);
    RCU_APB1EN |= (1 << 17);
    delay(1000);

    /* Hold power latches. */
    {
        uint32_t ctl = GPIO_CTL(GPIOC_BASE);
        ctl &= ~(3U << (15 * 2));
        ctl |=  (1U << (15 * 2));
        GPIO_CTL(GPIOC_BASE) = ctl;
        GPIO_BOP(GPIOC_BASE) = (1 << 15);

        ctl = GPIO_CTL(GPIOB_BASE);
        ctl &= ~(3U << (3 * 2));
        ctl |=  (1U << (3 * 2));
        GPIO_CTL(GPIOB_BASE) = ctl;
        GPIO_BOP(GPIOB_BASE) = (1 << 3);
    }

    /* PA2 = TX, PA3 = RX, AF1; pull-up on RX */
    GPIOA_CTL = (GPIOA_CTL & ~((3U << (2 * 2)) | (3U << (3 * 2))))
              | ((2U << (2 * 2)) | (2U << (3 * 2)));
    GPIOA_OSPD |= (3U << (2 * 2)) | (3U << (3 * 2));
    GPIOA_PUD = (GPIOA_PUD & ~(3U << (3 * 2))) | (1U << (3 * 2));
    GPIOA_AFSEL0 = (GPIOA_AFSEL0 & ~((0xFU << (2 * 4)) | (0xFU << (3 * 4))))
                 | ((0x1U << (2 * 4)) | (0x1U << (3 * 4)));

    USART1_CTL0 = 0;
    USART1_BAUD = 0x0045;               /* 8 MHz / 115200 */
    USART1_CTL0 = UEN | TEN | REN;

    /* Unlock FMC. */
    fmc_wait_idle();
    if (FMC_CTL & CTL_LK) {
        FMC_KEY = UNLOCK_KEY0;
        FMC_KEY = UNLOCK_KEY1;
    }

    delay(200000);
    uart_puts("READY\r\n");

    uint32_t addr = FLASH_BASE;
    uint32_t last_page = 0xFFFFFFFFU;
    uint16_t crc = 0x0000;

    while (1) {
        int lo = uart_getc(20000000);
        int hi = uart_getc(2000000);
        if (lo < 0 || hi < 0) {
            uart_puts("\r\n[timeout]\r\n");
            continue;
        }
        uint32_t len = (uint32_t)lo | ((uint32_t)hi << 8);
        if (len == 0)
            break;
        if (len > sizeof(chunk))
            len = sizeof(chunk);

        for (uint32_t i = 0; i < len; i++) {
            int b = uart_getc(2000000);
            chunk[i] = (uint8_t)(b < 0 ? 0xFF : b);
        }
        if (len & 1)
            chunk[len++] = 0xFF;        /* pad to a whole half-word */

        int ok = 1;
        for (uint32_t i = 0; i < len; i += 2) {
            uint32_t page = addr & ~(PAGE_SIZE - 1);
            if (page != last_page) {
                if (fmc_erase_page(page) != 0) {
                    ok = 0;
                    break;
                }
                last_page = page;
            }
            uint16_t hw = (uint16_t)(chunk[i] | ((uint16_t)chunk[i + 1] << 8));
            if (fmc_program_halfword(addr, hw) != 0) {
                ok = 0;
                break;
            }
            /* CRC-16-CCITT over the image, for a final integrity check. */
            for (int bidx = 0; bidx < 2; bidx++) {
                crc ^= (uint16_t)chunk[i + bidx] << 8;
                for (int k = 0; k < 8; k++)
                    crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021)
                                         : (uint16_t)(crc << 1);
            }
            addr += 2;
        }

        uart_putc(ok ? ACK : NAK);
        if (!ok) {
            uart_puts("\r\n[FAIL at ");
            uart_hex16((uint16_t)(addr - FLASH_BASE));
            uart_puts("]\r\n");
            while (1)
                ;
        }
    }

    uart_puts("DONE bytes=");
    uart_hex16((uint16_t)(addr - FLASH_BASE));
    uart_puts(" crc=");
    uart_hex16(crc);
    uart_puts("\r\n");

    while (1)
        ;
}
