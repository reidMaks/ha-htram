/*
 * USART1 interrupt-number probe — runs from SRAM (0x20000000), never touches Flash.
 *
 * The GD32F150 datasheet carries no interrupt vector table (it lives in the User
 * Manual, which is not in this repo), and the factory image's table is 48
 * identical stubs, so the USART1 IRQ number cannot be read out of either. Rather
 * than guess it -- a wrong guess vectors into garbage -- this probe measures it:
 * every slot points at one handler, every IRQ line is unmasked, and the handler
 * reports its own exception number out of IPSR.
 *
 *     IRQn = exception_number - 16
 *
 * Send any byte from the host; the number that comes back is USART1's IRQ.
 */

#include <stdint.h>

#define RCU_BASE      0x40021000
#define RCU_AHBEN     (*(volatile uint32_t *)(RCU_BASE + 0x14))
#define RCU_APB1EN    (*(volatile uint32_t *)(RCU_BASE + 0x1C))

#define GPIOA_BASE    0x48000000
#define GPIOB_BASE    0x48000400
#define GPIOC_BASE    0x48000800

#define GPIO_CTL(b)   (*(volatile uint32_t *)((b) + 0x00))
#define GPIO_OSPD(b)  (*(volatile uint32_t *)((b) + 0x08))
#define GPIO_PUD(b)   (*(volatile uint32_t *)((b) + 0x0C))
#define GPIO_BOP(b)   (*(volatile uint32_t *)((b) + 0x18))
#define GPIO_AFSEL0(b)(*(volatile uint32_t *)((b) + 0x20))

#define USART1_BASE   0x40004400
#define USART1_CTL0   (*(volatile uint32_t *)(USART1_BASE + 0x00))
#define USART1_BAUD   (*(volatile uint32_t *)(USART1_BASE + 0x0C))
#define USART1_STAT   (*(volatile uint32_t *)(USART1_BASE + 0x1C))
#define USART1_RDATA  (*(volatile uint32_t *)(USART1_BASE + 0x24))
#define USART1_TDATA  (*(volatile uint32_t *)(USART1_BASE + 0x28))

#define UEN     (1 << 0)
#define REN     (1 << 2)
#define TEN     (1 << 3)
#define RBNEIE  (1 << 5)
#define RBNE    (1 << 5)
#define TBE     (1 << 7)

#define SCB_VTOR      (*(volatile uint32_t *)0xE000ED08)
#define NVIC_ISER0    (*(volatile uint32_t *)0xE000E100)
#define NVIC_ISER1    (*(volatile uint32_t *)0xE000E104)

#define VECTOR_COUNT  64

static volatile uint32_t g_last_exception = 0;
static volatile uint32_t g_fire_count = 0;
static uint32_t g_inherited_basepri = 0xEE;
static uint32_t g_inherited_faultmask = 0xEE;
static uint32_t g_control = 0xEE, g_msp = 0xEE, g_psp = 0xEE;

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

static void print_dec(uint32_t n)
{
    char buf[12];
    int i = 0;
    if (n == 0) {
        uart_putc('0');
        return;
    }
    while (n) {
        buf[i++] = (char)('0' + (n % 10));
        n /= 10;
    }
    while (i--)
        uart_putc((uint8_t)buf[i]);
}

static void print_hex(uint32_t v)
{
    const char *h = "0123456789ABCDEF";
    uart_puts("0x");
    for (int i = 7; i >= 0; i--)
        uart_putc((uint8_t)h[(v >> (i * 4)) & 0xF]);
}

/* Every vector slot lands here. IPSR holds the active exception number. */
void common_handler(void)
{
    uint32_t ipsr;
    __asm__ volatile("mrs %0, ipsr" : "=r"(ipsr));

    g_last_exception = ipsr;
    g_fire_count++;

    /* Silence the source so the probe does not spin in the handler. */
    USART1_CTL0 &= ~RBNEIE;
    (void)USART1_RDATA;
}

/* Vector table in SRAM. VTOR needs the base aligned to at least 128 bytes. */
__attribute__((aligned(512)))
static void (*g_vectors[VECTOR_COUNT])(void);

void main(void)
{
    RCU_AHBEN  |= (1 << 17) | (1 << 18) | (1 << 19);
    RCU_APB1EN |= (1 << 17);
    delay_cycles(1000);

    /* Power latches first. */
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

    /* PA2 = TX, PA3 = RX, AF1 */
    {
        uint32_t ctl = GPIO_CTL(GPIOA_BASE);
        ctl &= ~((3U << (2 * 2)) | (3U << (3 * 2)));
        ctl |=  ((2U << (2 * 2)) | (2U << (3 * 2)));
        GPIO_CTL(GPIOA_BASE) = ctl;

        GPIO_OSPD(GPIOA_BASE) |= (3U << (2 * 2)) | (3U << (3 * 2));

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
    USART1_BAUD = 0x0045;
    USART1_CTL0 = UEN | TEN | REN;

    delay_cycles(100000);
    uart_puts("\r\n[IRQ] USART1 interrupt-number probe\r\n");

    /* Point every slot at the same handler, then relocate the table. */
    for (int i = 0; i < VECTOR_COUNT; i++)
        g_vectors[i] = common_handler;

    SCB_VTOR = (uint32_t)g_vectors;
    __asm__ volatile("dsb" ::: "memory");
    __asm__ volatile("isb" ::: "memory");

    /* Unmask every IRQ line: we do not know which one is ours yet. */
    NVIC_ISER0 = 0xFFFFFFFFU;
    NVIC_ISER1 = 0xFFFFFFFFU;

    uart_puts("[IRQ] all vectors armed, enabling USART1 RBNEIE\r\n");

    /* Drain anything stale, then arm the receive interrupt. */
    (void)USART1_RDATA;
    g_last_exception = 0;
    g_fire_count = 0;
    USART1_CTL0 |= RBNEIE;

    /* wreg pc/sp/xpsr does not touch these, so a debugger-loaded image can
     * inherit masking from whatever was halted before it. Report, then clear. */
    {
        uint32_t basepri, faultmask;
        __asm__ volatile("mrs %0, basepri" : "=r"(basepri));
        __asm__ volatile("mrs %0, faultmask" : "=r"(faultmask));
        g_inherited_basepri = basepri;
        g_inherited_faultmask = faultmask;
        uart_puts("[DIAG] inherited BASEPRI=");
        print_hex(basepri);
        uart_puts(" FAULTMASK=");
        print_hex(faultmask);
        uart_puts("\r\n");
    }
    __asm__ volatile("msr basepri, %0" :: "r"(0));
    __asm__ volatile("cpsie f");
    __asm__ volatile("cpsie i");

    /* Control: pend IRQ0 by hand. If this does not vector either, the problem
     * is the interrupt mechanism in this SRAM-loaded context, not USART1. */
    {
        uint32_t control, msp, psp;
        __asm__ volatile("mrs %0, control" : "=r"(control));
        __asm__ volatile("mrs %0, msp" : "=r"(msp));
        __asm__ volatile("mrs %0, psp" : "=r"(psp));
        g_control = control; g_msp = msp; g_psp = psp;
        uart_puts("[DIAG] CONTROL=");
        print_hex(control);
        uart_puts(" MSP=");
        print_hex(msp);
        uart_puts(" PSP=");
        print_hex(psp);
        uart_puts("\r\n");
    }
    uart_puts("[IRQ] control test: pending IRQ0 manually...\r\n");
    *(volatile uint32_t *)0xE000E200 = 1U;
    __asm__ volatile("dsb" ::: "memory");
    __asm__ volatile("isb" ::: "memory");
    delay_cycles(20000);
    uart_puts("[IRQ] control result: fires=");
    print_dec(g_fire_count);
    uart_puts(g_fire_count ? "  -> interrupts WORK\r\n" : "  -> interrupts DEAD\r\n");
    g_fire_count = 0;
    g_last_exception = 0;
    USART1_CTL0 |= RBNEIE;

    uart_puts("[IRQ] ready -- send one byte now\r\n");

    {
        uint32_t primask;
        __asm__ volatile("mrs %0, primask" : "=r"(primask));
        uart_puts("[DIAG] VTOR=");
        print_hex(SCB_VTOR);
        uart_puts(" table@");
        print_hex((uint32_t)g_vectors);
        uart_puts(" PRIMASK=");
        print_dec(primask);
        uart_puts("\r\n[DIAG] CTL0=");
        print_hex(USART1_CTL0);
        uart_puts(" ISER0=");
        print_hex(NVIC_ISER0);
        uart_puts(" ISER1=");
        print_hex(NVIC_ISER1);
        uart_puts("\r\n");
    }

    uint32_t idle = 0;
    while (1) {
        /* Report the raw status too: if RBNE latches but nothing vectors, the
         * byte is arriving and the interrupt path is what is broken. */
        if (USART1_STAT & RBNE) {
            uart_puts("[DIAG] RBNE set, STAT=");
            print_hex(USART1_STAT);
            uart_puts(" CTL0=");
            print_hex(USART1_CTL0);
            uart_puts(" ISPR0=");
            print_hex(*(volatile uint32_t *)0xE000E200);
            uart_puts(" ISPR1=");
            print_hex(*(volatile uint32_t *)0xE000E204);
            uart_puts(" byte=");
            print_hex(USART1_RDATA & 0xFF);
            uart_puts("\r\n");
        }

        if (g_last_exception) {
            uint32_t exc = g_last_exception;
            g_last_exception = 0;

            uart_puts("[IRQ] FIRED: exception=");
            print_dec(exc);
            uart_puts("  -> USART1 IRQn=");
            print_dec(exc - 16);
            uart_puts("\r\n");

            /* Re-arm for a second confirming shot. */
            (void)USART1_RDATA;
            USART1_CTL0 |= RBNEIE;
        }

        if (++idle >= 500000) {
            idle = 0;
            uint32_t primask;
            __asm__ volatile("mrs %0, primask" : "=r"(primask));
            uart_puts("[IRQ] fires=");
            print_dec(g_fire_count);
            uart_puts(" VTOR=");
            print_hex(SCB_VTOR);
            uart_puts(" tbl=");
            print_hex((uint32_t)g_vectors);
            uart_puts(" PRIMASK=");
            print_dec(primask);
            uart_puts(" ISER0=");
            print_hex(NVIC_ISER0);
            uint32_t ipsr;
            __asm__ volatile("mrs %0, ipsr" : "=r"(ipsr));
            uart_puts(" IPSR=");
            print_dec(ipsr);
            uart_puts(" IABR0=");
            print_hex(*(volatile uint32_t *)0xE000E300);
            uart_puts(" SHCSR=");
            print_hex(*(volatile uint32_t *)0xE000ED24);
            uart_puts(" inhBASEPRI=");
            print_hex(g_inherited_basepri);
            uart_puts(" inhFAULTMASK=");
            print_hex(g_inherited_faultmask);
            uart_puts("\r\n       DHCSR=");
            print_hex(*(volatile uint32_t *)0xE000EDF0);
            uart_puts(" (bit3 C_MASKINTS) vec[44]=");
            print_hex((uint32_t)g_vectors[44]);
            uart_puts(" handler=");
            print_hex((uint32_t)common_handler);
            uart_puts("\r\n       CONTROL=");
            print_hex(g_control);
            uart_puts(" MSP=");
            print_hex(g_msp);
            uart_puts(" PSP=");
            print_hex(g_psp);
            uart_puts("\r\n");
        }
    }
}
