#include <stdint.h>

extern uint32_t _stack_top;
extern uint32_t _sidata;
extern uint32_t _sdata;
extern uint32_t _edata;
extern uint32_t _sbss;
extern uint32_t _ebss;

int main(void);

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
};
