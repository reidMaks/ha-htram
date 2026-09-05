/*
 * Memory-mailbox SRAM flash programmer for GD32F150.
 *
 * Runs from SRAM (0x20000000). Communicates with pyocd purely via SWD memory
 * read/write at mailbox location 0x20001000.
 *
 * No UART needed!
 */

#include <stdint.h>

#define RCU_BASE      0x40021000
#define RCU_AHBEN     (*(volatile uint32_t *)(RCU_BASE + 0x14))
#define RCU_APB1EN    (*(volatile uint32_t *)(RCU_BASE + 0x1C))

#define GPIOB_BASE    0x48000400
#define GPIOC_BASE    0x48000800
#define GPIOF_BASE    0x48001400
#define GPIO_CTL(b)   (*(volatile uint32_t *)((b) + 0x00))
#define GPIO_BOP(b)   (*(volatile uint32_t *)((b) + 0x18))

#define FWDGT_BASE    0x40003000
#define FWDGT_CTL     (*(volatile uint32_t *)(FWDGT_BASE + 0x00))
#define FWDGT_PSC     (*(volatile uint32_t *)(FWDGT_BASE + 0x04))
#define FWDGT_RLD     (*(volatile uint32_t *)(FWDGT_BASE + 0x08))
#define FWDGT_KEY_RELOAD 0xAAAA
#define FWDGT_KEY_ACCESS 0x5555

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

#define PAGE_SIZE     0x400U          /* 1 KB pages on GD32F150 */

#define MAILBOX_ADDR  0x20001000

typedef struct {
    volatile uint32_t magic;      /* 0x4D41494C ("MAIL") */
    volatile uint32_t cmd;        /* 0: idle, 1: write_chunk, 2: done */
    volatile uint32_t status;     /* 0: busy, 1: ok/ready, 2: error */
    volatile uint32_t addr;       /* target flash address */
    volatile uint32_t len;        /* chunk length in bytes */
    volatile uint16_t crc;        /* accumulated CRC-16 */
    volatile uint16_t err_code;   /* 1=erase err, 2=prog err */
    volatile uint8_t  buf[1024];  /* chunk data */
} mailbox_t;

#define mb (*(volatile mailbox_t *)MAILBOX_ADDR)

static void delay(volatile uint32_t n)
{
    while (n--) {
        FWDGT_CTL = FWDGT_KEY_RELOAD;
        __asm__ volatile("");
    }
}

static void fmc_wait_idle(void)
{
    uint32_t guard = 2000000;
    while ((FMC_STAT & STAT_BUSY) && --guard) {
        FWDGT_CTL = FWDGT_KEY_RELOAD;
    }
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
    if (*(volatile uint16_t *)addr != data)
        return -1;
    return 0;
}

void main(void)
{
    /* 1. Disable all interrupts and SysTick so flash erase does not lockup CPU */
    __asm__ volatile("cpsid i");
    *(volatile uint32_t *)0xE000E010 = 0;          /* SysTick CTL */
    *(volatile uint32_t *)0xE000E180 = 0xFFFFFFFF;  /* NVIC ICER */
    *(volatile uint32_t *)0xE000E280 = 0xFFFFFFFF;  /* NVIC ICPR */

    /* Extend Watchdog to maximum ~26 seconds */
    FWDGT_CTL = FWDGT_KEY_ACCESS;
    FWDGT_PSC = 7;
    FWDGT_RLD = 0x0FFF;
    FWDGT_CTL = FWDGT_KEY_RELOAD;
    *(volatile uint32_t *)0x40015804 |= (1 << 8); /* DBG_CTL: freeze FWDGT during debug */

    /* 2. Enable GPIO clocks: GPIOA (17), GPIOB (18), GPIOC (19), GPIOF (22) */
    RCU_AHBEN  |= (1 << 17) | (1 << 18) | (1 << 19) | (1 << 22);
    RCU_APB1EN |= (1 << 17);
    delay(1000);

    /* 3. Hold power latches: PC15, PB3, and PF7 (ESP32 / peripheral power) */
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

        ctl = GPIO_CTL(GPIOF_BASE);
        ctl &= ~(3U << (7 * 2));
        ctl |=  (1U << (7 * 2));
        GPIO_CTL(GPIOF_BASE) = ctl;
        GPIO_BOP(GPIOF_BASE) = (1 << 7);
    }

    /* 4. Unlock FMC */
    fmc_wait_idle();
    if (FMC_CTL & CTL_LK) {
        FMC_KEY = UNLOCK_KEY0;
        FMC_KEY = UNLOCK_KEY1;
    }

    uint32_t last_page = 0xFFFFFFFFU;
    uint16_t crc = 0x0000;

    /* Initialize mailbox */
    mb.magic = 0x4D41494C;
    mb.cmd = 0;
    mb.status = 1; /* READY */
    mb.crc = 0;
    mb.err_code = 0;

    while (1) {
        FWDGT_CTL = FWDGT_KEY_RELOAD;
        if (mb.cmd == 1) { /* WRITE_CHUNK */
            mb.status = 0; /* BUSY */
            uint32_t addr = mb.addr;
            uint32_t len = mb.len;
            if (len > 1024) len = 1024;
            if (len & 1) len++;

            int ok = 1;
            for (uint32_t i = 0; i < len; i += 2) {
                FWDGT_CTL = FWDGT_KEY_RELOAD;
                uint32_t page = (addr + i) & ~(PAGE_SIZE - 1);
                if (page != last_page) {
                    if (fmc_erase_page(page) != 0) {
                        ok = 0;
                        mb.err_code = 1;
                        break;
                    }
                    last_page = page;
                }
                uint16_t hw = (uint16_t)(mb.buf[i] | ((uint16_t)mb.buf[i + 1] << 8));
                if (fmc_program_halfword(addr + i, hw) != 0) {
                    ok = 0;
                    mb.err_code = 2;
                    break;
                }
                for (int bidx = 0; bidx < 2; bidx++) {
                    crc ^= (uint16_t)mb.buf[i + bidx] << 8;
                    for (int k = 0; k < 8; k++)
                        crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021)
                                             : (uint16_t)(crc << 1);
                }
            }
            mb.crc = crc;
            mb.cmd = 0;
            mb.status = ok ? 1 : 2;
            if (!ok) break;
        } else if (mb.cmd == 2) { /* DONE */
            mb.cmd = 0;
            mb.status = 1;
            break;
        }
    }

    while (1) {
        delay(100000);
    }
}
