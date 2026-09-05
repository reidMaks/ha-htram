CC      = arm-none-eabi-gcc
OBJCOPY = arm-none-eabi-objcopy
SIZE    = arm-none-eabi-size

CFLAGS  = -mcpu=cortex-m3 -mthumb -Os -Wall -Wextra -ffreestanding -nostdlib -nostartfiles -Iinc -I$(BUILD_DIR)
LDFLAGS = -Wl,--gc-sections

# ── Build metadata (regenerated every build, embedded into the HELLO packet) ──
GIT_HASH  := $(shell git rev-parse HEAD 2>/dev/null | cut -c1-8)
GIT_HASH  := $(if $(GIT_HASH),$(GIT_HASH),00000000)
GIT_DIRTY := $(shell test -n "$$(git status --porcelain 2>/dev/null)" && echo 1 || echo 0)
BUILD_EPOCH := $(shell date -u +%s)

SRCS    = src/main.c \
          src/display.c \
          src/sensors.c \
          src/periph.c \
          src/protocol_engine.c \
          src/flasher.c \
          src/startup.c

BUILD_DIR = build

OBJS_FLASH = $(patsubst src/%.c,$(BUILD_DIR)/flash_%.o,$(SRCS))
OBJS_SRAM  = $(patsubst src/%.c,$(BUILD_DIR)/sram_%.o,$(SRCS))

TARGET_FLASH_ELF = $(BUILD_DIR)/gd32_firmware.elf
TARGET_FLASH_BIN = $(BUILD_DIR)/gd32_firmware.bin

TARGET_SRAM_ELF  = $(BUILD_DIR)/gd32_firmware_sram.elf
TARGET_SRAM_BIN  = $(BUILD_DIR)/gd32_firmware_sram.bin

BUILD_INFO_H = $(BUILD_DIR)/build_info.h

PYOCD = ../../.venv/bin/pyocd

.PHONY: all flash sram clean load-sram FORCE

all: flash

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

# ── Generated build stamp: rewritten on every build so BUILD_EPOCH is current ──
$(BUILD_INFO_H): FORCE | $(BUILD_DIR)
	@printf '#ifndef BUILD_INFO_H\n#define BUILD_INFO_H\n#define BUILD_EPOCH %suL\n#define BUILD_GIT_HASH 0x%suL\n#define BUILD_DIRTY %s\n#endif\n' \
	    "$(BUILD_EPOCH)" "$(GIT_HASH)" "$(GIT_DIRTY)" > $@

FORCE:

# Only the HELLO producer needs the stamp; keep the rest incremental.
$(BUILD_DIR)/flash_protocol_engine.o $(BUILD_DIR)/sram_protocol_engine.o: $(BUILD_INFO_H)

# ── Flash Target ──
flash: $(BUILD_DIR) $(TARGET_FLASH_BIN)
	@echo "=== Flash Target Built Successfully ==="
	@$(SIZE) $(TARGET_FLASH_ELF)

$(BUILD_DIR)/flash_%.o: src/%.c | $(BUILD_DIR)
	$(CC) $(CFLAGS) -c $< -o $@

$(TARGET_FLASH_ELF): $(OBJS_FLASH) ld/flash.ld
	$(CC) $(CFLAGS) $(LDFLAGS) -T ld/flash.ld -o $@ $(OBJS_FLASH) -lgcc

$(TARGET_FLASH_BIN): $(TARGET_FLASH_ELF)
	$(OBJCOPY) -O binary $< $@

# ── SRAM Target ──
sram: $(BUILD_DIR) $(TARGET_SRAM_BIN)
	@echo "=== SRAM Target Built Successfully ==="
	@$(SIZE) $(TARGET_SRAM_ELF)

$(BUILD_DIR)/sram_%.o: src/%.c | $(BUILD_DIR)
	$(CC) $(CFLAGS) -DEXECUTE_FROM_SRAM -c $< -o $@

$(TARGET_SRAM_ELF): $(OBJS_SRAM) ld/sram.ld
	$(CC) $(CFLAGS) $(LDFLAGS) -T ld/sram.ld -o $@ $(OBJS_SRAM) -lgcc

$(TARGET_SRAM_BIN): $(TARGET_SRAM_ELF)
	$(OBJCOPY) -O binary $< $@

# ── Load and run in SRAM via PyOCD ──
load-sram: sram
	@ENTRY_ADDR=$$(arm-none-eabi-nm $(TARGET_SRAM_ELF) | grep ' T Reset_Handler' | awk '{print "0x"$$1}') ; \
	ENTRY_THUMB=$$(printf "0x%08X" $$((ENTRY_ADDR | 1))) ; \
	echo "Loading $(TARGET_SRAM_BIN) into GD32 SRAM (0x20000000), entry $${ENTRY_THUMB}..." ; \
	$(PYOCD) cmd -t cortex_m -f 100k -c "halt; loadmem 0x20000000 $(TARGET_SRAM_BIN); wreg sp 0x20002000; wreg pc $${ENTRY_THUMB}; wreg xpsr 0x01000000; c"

clean:
	rm -rf $(BUILD_DIR)
