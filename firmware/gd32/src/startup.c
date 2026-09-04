#include <stdint.h>

extern uint32_t _stack_top;
extern uint32_t _sidata;
extern uint32_t _sdata;
extern uint32_t _edata;
extern uint32_t _sbss;
extern uint32_t _ebss;

int main(void);

/* USART1 receive interrupt (IRQ28 -> vector index 44), defined in
 * protocol_engine.c. Confirmed as IRQ28 on the bench: with RBNEIE set, an
 * incoming byte latched ISPR0 bit 28 (tools/swd/irq_probe.c). */
void USART1_IRQHandler(void);

void Reset_Handler(void)
{
    /* Copy .data from Flash to SRAM */
    uint32_t *src = &_sidata;
    uint32_t *dst = &_sdata;
    if (src != dst) {
        while (dst < &_edata) {
            *dst++ = *src++;
        }
    }

    /* Zero out .bss in SRAM */
    dst = &_sbss;
    while (dst < &_ebss) {
        *dst++ = 0;
    }

    /* Call application main */
    main();

    while (1) {
        /* Infinite loop if main returns */
    }
}

void Default_Handler(void)
{
    while (1) {
    }
}

/* Cortex-M3 Vector Table */
__attribute__((section(".isr_vector"), used))
void (* const g_pfnVectors[])(void) = {
    (void (*)(void))(&_stack_top),
    Reset_Handler,
    Default_Handler, /* NMI */
    Default_Handler, /* HardFault */
    Default_Handler, /* MemManage */
    Default_Handler, /* BusFault */
    Default_Handler, /* UsageFault */
    0, 0, 0, 0,      /* Reserved */
    Default_Handler, /* SVCall */
    Default_Handler, /* DebugMon */
    0,               /* Reserved */
    Default_Handler, /* PendSV */
    Default_Handler, /* SysTick */

    /* External interrupts (IRQ0..IRQ28). Only USART1 (IRQ28) is handled. */
    Default_Handler, /* IRQ0  WWDGT */
    Default_Handler, /* IRQ1  LVD */
    Default_Handler, /* IRQ2  RTC */
    Default_Handler, /* IRQ3  FMC */
    Default_Handler, /* IRQ4  RCU */
    Default_Handler, /* IRQ5  EXTI0_1 */
    Default_Handler, /* IRQ6  EXTI2_3 */
    Default_Handler, /* IRQ7  EXTI4_15 */
    Default_Handler, /* IRQ8  TSI */
    Default_Handler, /* IRQ9  DMA_CH0 */
    Default_Handler, /* IRQ10 DMA_CH1_2 */
    Default_Handler, /* IRQ11 DMA_CH3_4 */
    Default_Handler, /* IRQ12 ADC_CMP */
    Default_Handler, /* IRQ13 TIMER0_BRK_UP_TRG_COM */
    Default_Handler, /* IRQ14 TIMER0_CC */
    Default_Handler, /* IRQ15 TIMER1 */
    Default_Handler, /* IRQ16 TIMER2 */
    Default_Handler, /* IRQ17 TIMER5 */
    Default_Handler, /* IRQ18 reserved */
    Default_Handler, /* IRQ19 TIMER13 */
    Default_Handler, /* IRQ20 TIMER14 */
    Default_Handler, /* IRQ21 TIMER15 */
    Default_Handler, /* IRQ22 TIMER16 */
    Default_Handler, /* IRQ23 I2C0_EV */
    Default_Handler, /* IRQ24 I2C1_EV */
    Default_Handler, /* IRQ25 SPI0 */
    Default_Handler, /* IRQ26 SPI1 */
    Default_Handler, /* IRQ27 USART0 */
    USART1_IRQHandler, /* IRQ28 USART1 */
};
