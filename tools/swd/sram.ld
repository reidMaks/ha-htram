/* Minimal linker script for GD32F150 SRAM execution */
MEMORY
{
    SRAM (rwx) : ORIGIN = 0x20000000, LENGTH = 8K
}

SECTIONS
{
    .text : {
        *(.text*)
        *(.rodata*)
    } > SRAM

    .data : {
        *(.data*)
    } > SRAM

    .bss : {
        *(.bss*)
        *(COMMON)
    } > SRAM

    /DISCARD/ : {
        *(.ARM.*)
        *(.comment)
    }
}
