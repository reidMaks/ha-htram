#include <stdint.h>
#include "gd32f150.h"
#include "flasher.h"

#define FMC_BASE        0x40022000
#define FMC_KEY         (*(volatile uint32_t *)(FMC_BASE + 0x04))
#define FMC_STAT        (*(volatile uint32_t *)(FMC_BASE + 0x0C))
#define FMC_CTL         (*(volatile uint32_t *)(FMC_BASE + 0x10))
#define FMC_ADDR        (*(volatile uint32_t *)(FMC_BASE + 0x14))

#define UNLOCK_KEY0     0x45670123UL
#define UNLOCK_KEY1     0xCDEF89ABUL

#define CTL_PG          (1 << 0)
#define CTL_PER         (1 << 1)
#define CTL_MER         (1 << 2)
#define CTL_START       (1 << 6)
#define CTL_LK          (1 << 7)

#define STAT_BUSY       (1 << 0)
#define STAT_PGERR      (1 << 2)
#define STAT_WPERR      (1 << 4)
#define STAT_ENDF       (1 << 5)

#define CMD_SYNC        0x7F
#define CMD_ERASE       0x43
#define CMD_WRITE       0x31
#define CMD_GO          0x21

#define RESP_ACK        0x79
#define RESP_NACK       0x1F

#define RAM_CODE __attribute__((section(".ramcode"), noinline))

RAM_CODE static void ram_uart_putc(uint8_t c)
{
    while (!(USART1_STAT & USART_TBE))
        ;
    USART1_TDATA = c;
}

RAM_CODE static int ram_uart_getc(uint32_t timeout_loops)
{
    while (!(USART1_STAT & USART_RBNE)) {
        FWDGT_CTL = FWDGT_KEY_RELOAD;
        if (timeout_loops) {
            if (--timeout_loops == 0) return -1;
        }
    }
    return (uint8_t)USART1_RDATA;
}

RAM_CODE static void ram_fmc_wait_idle(void)
{
    uint32_t guard = 2000000;
    while ((FMC_STAT & STAT_BUSY) && --guard)
        ;
}

RAM_CODE static void ram_fmc_unlock(void)
{
    ram_fmc_wait_idle();
    if (FMC_CTL & CTL_LK) {
        FMC_KEY = UNLOCK_KEY0;
        FMC_KEY = UNLOCK_KEY1;
    }
}

RAM_CODE static int ram_fmc_erase_all(void)
{
    ram_fmc_wait_idle();
    FMC_CTL |= CTL_MER;
    FMC_CTL |= CTL_START;
    ram_fmc_wait_idle();
    FMC_CTL &= ~CTL_MER;
    if (FMC_STAT & (STAT_PGERR | STAT_WPERR)) {
        FMC_STAT = STAT_ENDF | STAT_PGERR | STAT_WPERR;
        return -1;
    }
    FMC_STAT = STAT_ENDF | STAT_PGERR | STAT_WPERR;
    return 0;
}

RAM_CODE static int ram_fmc_write_words(uint32_t addr, const uint32_t *data, uint32_t word_count)
{
    for (uint32_t i = 0; i < word_count; i++) {
        ram_fmc_wait_idle();
        FMC_CTL |= CTL_PG;
        *(volatile uint32_t *)(addr + (i * 4)) = data[i];
        ram_fmc_wait_idle();
        FMC_CTL &= ~CTL_PG;
        if (FMC_STAT & (STAT_PGERR | STAT_WPERR)) {
            FMC_STAT = STAT_ENDF | STAT_PGERR | STAT_WPERR;
            return -1;
        }
        FMC_STAT = STAT_ENDF | STAT_PGERR | STAT_WPERR;
        if (*(volatile uint32_t *)(addr + (i * 4)) != data[i]) {
            return -1;
        }
    }
    return 0;
}

RAM_CODE void flasher_run(void)
{
    /* 1. Disable interrupts so no ISR executes from Flash */
    __asm__ volatile("cpsid i");

    /* 2. Disable SysTick */
    *(volatile uint32_t *)0xE000E010 = 0;

    /* 3. Disable NVIC USART1 interrupt and clear pending */
    *(volatile uint32_t *)0xE000E180 = 0xFFFFFFFF;
    *(volatile uint32_t *)0xE000E280 = 0xFFFFFFFF;

    /* 4. Disable RX interrupt on USART1, switch to pure polled mode */
    USART1_CTL0 &= ~USART_RBNEIE;

    /* 5. Ensure power latches are held */
    GPIO_BOP(GPIOC_BASE) = (1 << 15); /* PC15 */
    GPIO_BOP(GPIOB_BASE) = (1 << 3) | (1 << 9) | (1 << 11); /* PB3, PB9, PB11 */
    GPIO_BOP(GPIOF_BASE) = (1 << 7); /* PF7 (ESP32 + display power rail) */

    /* 6. Unlock FMC */
    ram_fmc_unlock();

    /* 7. Drain any existing RX bytes */
    while (USART1_STAT & USART_RBNE) {
        volatile uint32_t dummy = USART1_RDATA;
        (void)dummy;
    }

    /* 8. Command loop */
    while (1) {
        int c = ram_uart_getc(0); /* wait indefinitely, kicking watchdog */
        if (c < 0) continue;

        if (c == CMD_SYNC) {
            ram_uart_putc(RESP_ACK);
            continue;
        }

        if (c == CMD_ERASE || c == CMD_WRITE || c == CMD_GO) {
            int inv = ram_uart_getc(2000000);
            if (inv < 0 || (uint8_t)(c ^ inv) != 0xFF) {
                ram_uart_putc(RESP_NACK);
                continue;
            }

            ram_uart_putc(RESP_ACK); /* ACK valid command */

            if (c == CMD_ERASE) {
                int n = ram_uart_getc(2000000);
                int chk = ram_uart_getc(2000000);
                if (n < 0 || chk < 0 || (uint8_t)(n ^ chk) != 0xFF) {
                    ram_uart_putc(RESP_NACK);
                    continue;
                }
                if (ram_fmc_erase_all() == 0) {
                    ram_uart_putc(RESP_ACK);
                } else {
                    ram_uart_putc(RESP_NACK);
                }
            } else if (c == CMD_WRITE) {
                uint8_t addr_b[5];
                int ok = 1;
                for (int i = 0; i < 5; i++) {
                    int b = ram_uart_getc(2000000);
                    if (b < 0) { ok = 0; break; }
                    addr_b[i] = (uint8_t)b;
                }
                if (!ok || addr_b[4] != (addr_b[0] ^ addr_b[1] ^ addr_b[2] ^ addr_b[3])) {
                    ram_uart_putc(RESP_NACK);
                    continue;
                }
                uint32_t target_addr = ((uint32_t)addr_b[0] << 24) |
                                       ((uint32_t)addr_b[1] << 16) |
                                       ((uint32_t)addr_b[2] << 8)  |
                                       (uint32_t)addr_b[3];
                ram_uart_putc(RESP_ACK); /* ACK address */

                int len_code = ram_uart_getc(2000000);
                if (len_code < 0) {
                    ram_uart_putc(RESP_NACK);
                    continue;
                }
                uint32_t data_len = (uint32_t)len_code + 1;
                uint8_t chk_calc = (uint8_t)len_code;
                uint8_t buf[256];
                for (uint32_t i = 0; i < data_len; i++) {
                    int b = ram_uart_getc(2000000);
                    if (b < 0) { ok = 0; break; }
                    buf[i] = (uint8_t)b;
                    chk_calc ^= (uint8_t)b;
                }
                int rx_chk = ram_uart_getc(2000000);
                if (!ok || rx_chk < 0 || (uint8_t)rx_chk != chk_calc) {
                    ram_uart_putc(RESP_NACK);
                    continue;
                }

                uint32_t word_count = (data_len + 3) / 4;
                if (ram_fmc_write_words(target_addr, (const uint32_t *)buf, word_count) == 0) {
                    ram_uart_putc(RESP_ACK);
                } else {
                    ram_uart_putc(RESP_NACK);
                }
            } else if (c == CMD_GO) {
                uint8_t addr_b[5];
                int ok = 1;
                for (int i = 0; i < 5; i++) {
                    int b = ram_uart_getc(2000000);
                    if (b < 0) { ok = 0; break; }
                    addr_b[i] = (uint8_t)b;
                }
                if (!ok || addr_b[4] != (addr_b[0] ^ addr_b[1] ^ addr_b[2] ^ addr_b[3])) {
                    ram_uart_putc(RESP_NACK);
                    continue;
                }
                ram_uart_putc(RESP_ACK);
                while (!(USART1_STAT & USART_TC))
                    ;
                uint32_t target_addr = ((uint32_t)addr_b[0] << 24) |
                                       ((uint32_t)addr_b[1] << 16) |
                                       ((uint32_t)addr_b[2] << 8)  |
                                       (uint32_t)addr_b[3];
                uint32_t new_sp = *(volatile uint32_t *)target_addr;
                uint32_t new_pc = *(volatile uint32_t *)(target_addr + 4);
                __asm__ volatile(
                    "msr msp, %0\n"
                    "cpsie i\n"
                    "bx %1\n"
                    :
                    : "r"(new_sp), "r"(new_pc)
                );
                while (1)
                    ;
            }
        }
    }
}
