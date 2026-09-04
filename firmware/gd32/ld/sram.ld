/* Linker script for GD32F150 SRAM execution (8KB: 0x20000000 - 0x20002000) */
MEMORY
{
    SRAM (rwx) : ORIGIN = 0x20000000, LENGTH = 8K
}

ENTRY(Reset_Handler)

SECTIONS
{
    .text : {
        KEEP(*(.isr_vector))
        *(.text*)
        *(.rodata*)
        . = ALIGN(4);
    } > SRAM

    _sidata = LOADADDR(.data);

    .data : {
        . = ALIGN(4);
        _sdata = .;
        *(.data*)
        . = ALIGN(4);
        _edata = .;
    } > SRAM

    .bss : {
        . = ALIGN(4);
        _sbss = .;
        *(.bss*)
        *(COMMON)
        . = ALIGN(4);
        _ebss = .;
    } > SRAM

    _stack_top = ORIGIN(SRAM) + LENGTH(SRAM);

    /DISCARD/ : {
        *(.ARM.*)
        *(.comment)
    }
}
