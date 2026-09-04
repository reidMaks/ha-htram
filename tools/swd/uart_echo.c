/*
 * Minimal USART1 loopback probe — runs from SRAM (0x20000000), never touches Flash.
 *
 * Isolates the downlink wire (host -> Pico GP4 -> GD32 PA3) from everything else.
 * The loop does nothing but poll RBNE and echo, so there is no main-loop blocking
 * and no protocol state machine to blame: if a byte sent from the host does not
 * come back, the path itself is broken.
 *
 * Host side:  send any byte, expect "<byte>" echoed inside a marker frame.
 */

#include <stdint.h>

#define RCU_BASE      0x40021000
#define RCU_AHBEN     (*(volatile uint32_t *)(RCU_BASE + 0x14))
#define RCU_APB1EN    (*(volatile uint32_t *)(RCU_BASE + 0x1C))

#define GPIOA_BASE    0x48000000
#define GPIO_CTL(b)   (*(volatile uint32_t *)((b) + 0x00))
#define GPIO_OSPD(b)  (*(volatile uint32_t *)((b) + 0x08))
#define GPIO_PUD(b)   (*(volatile uint32_t *)((b) + 0x0C))
#define GPIO_BOP(b)   (*(volatile uint32_t *)((b) + 0x18))
#define GPIO_AFSEL0(b)(*(volatile uint32_t *)((b) + 0x20))

#define GPIOB_BASE    0x48000400
#define GPIOC_BASE    0x48000800

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

static inline void delay_cycles(uint32_t n)
{
    while (n--) {
        __asm__ volatile("");
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

static void put_hex2(uint8_t v)
{
    const char *h = "0123456789ABCDEF";
    uart_putc((uint8_t)h[(v >> 4) & 0xF]);
    uart_putc((uint8_t)h[v & 0xF]);
}

static void gpio_out(uint32_t base, int pin)
{
    uint32_t ctl = GPIO_CTL(base);
    ctl &= ~(3U << (pin * 2));
    ctl |=  (1U << (pin * 2));
    GPIO_CTL(base) = ctl;
}

void main(void)
{
    RCU_AHBEN  |= (1 << 17) | (1 << 18) | (1 << 19);
    RCU_APB1EN |= (1 << 17);
    delay_cycles(1000);

    /* Hold the power latches, or the board can cut its own supply. */
    gpio_out(GPIOC_BASE, 15);
    GPIO_BOP(GPIOC_BASE) = (1 << 15);
    gpio_out(GPIOB_BASE, 3);
    GPIO_BOP(GPIOB_BASE) = (1 << 3);

    /* PA2 = USART1 TX, PA3 = USART1 RX, both AF1 */
    {
        uint32_t ctl = GPIO_CTL(GPIOA_BASE);
        ctl &= ~((3U << (2 * 2)) | (3U << (3 * 2)));
        ctl |=  ((2U << (2 * 2)) | (2U << (3 * 2)));
        GPIO_CTL(GPIOA_BASE) = ctl;

        GPIO_OSPD(GPIOA_BASE) |= (3U << (2 * 2)) | (3U << (3 * 2));

        /* Pull-up on RX so an unconnected line idles high instead of floating */
        uint32_t pud = GPIO_PUD(GPIOA_BASE);
        pud &= ~(3U << (3 * 2));
        pud |=  (1U << (3 * 2));
        GPIO_PUD(GPIOA_BASE) = pud;

        uint32_t af = GPIO_AFSEL0(GPIOA_BASE);
        af &= ~((0xFU << (2 * 4)) | (0xFU << (3 * 4)));
        af |=  ((0x1U << (2 * 4)) | (0x1U << (3 * 4)));
        GPIO_AFSEL0(GPIOA_BASE) = af;
    }

    USART1_CTL0 = 0;
    USART1_BAUD = 0x0045;              /* 8 MHz / 115200 */
    USART1_CTL0 = UEN | TEN | REN;

    delay_cycles(100000);
    uart_puts("\r\n[ECHO] USART1 loopback probe ready. Send bytes.\r\n");

    uint32_t rx_total = 0;
    uint32_t heartbeat = 0;

    while (1) {
        if (USART1_STAT & RBNE) {
            uint8_t c = (uint8_t)USART1_RDATA;
            rx_total++;
            uart_puts("[RX ");
            put_hex2(c);
            uart_puts("]");
        }

        /* Periodic proof of life, so silence is unambiguous: if these keep
         * printing but no [RX] ever appears, nothing is reaching PA3. */
        if (++heartbeat >= 400000) {
            heartbeat = 0;
            uart_puts("\r\n[ECHO] alive, bytes received so far: ");
            {
                uint32_t n = rx_total;
                char buf[12];
                int i = 0;
                if (n == 0) {
                    buf[i++] = '0';
                } else {
                    while (n) {
                        buf[i++] = (char)('0' + (n % 10));
                        n /= 10;
                    }
                }
                while (i--)
                    uart_putc((uint8_t)buf[i]);
            }
            uart_puts("\r\n");
        }
    }
}
