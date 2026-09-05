#ifndef PROTOCOL_H
#define PROTOCOL_H

#include <stdint.h>
#include <stddef.h>

#define PROTOCOL_MAGIC0             0xAA
#define PROTOCOL_MAGIC1             0x55

#define PROTOCOL_VERSION            0x01
#define GD32_FW_VERSION             0x0100 /* v1.0.0 */

/* ── Packet Types ── */
#define PKT_TYPE_TELEMETRY          0x01
#define PKT_TYPE_HELLO              0x02

#define CMD_TYPE_DRAW_RECT          0x10
#define CMD_TYPE_SET_BACKLIGHT      0x11
#define CMD_TYPE_SET_LEDS           0x12
#define CMD_TYPE_BEEP               0x13
#define CMD_TYPE_ENTER_BOOTLOADER   0x1F

#define BOOTLOADER_MAGIC_KEY        0xDEADBEEFUL

/* ── Telemetry Status Bitmask ── */
#define STATUS_FLAG_CHARGING        (1 << 0)
#define STATUS_FLAG_USB_PRESENT     (1 << 1)
#define STATUS_FLAG_WARMUP          (1 << 2)
#define STATUS_FLAG_SENSOR_ERR      (1 << 3)
#define STATUS_FLAG_BUTTON_PRESSED  (1 << 4)
#define STATUS_FLAG_LED_GREEN       (1 << 5)
#define STATUS_FLAG_LED_YELLOW      (1 << 6)
#define STATUS_FLAG_LED_RED         (1 << 7)

/* ── Packets (Packed) ── */
#pragma pack(push, 1)

typedef struct {
    uint8_t magic0;         /* 0xAA */
    uint8_t magic1;         /* 0x55 */
    uint8_t type;           /* 0x01 */
    uint16_t co2_ppm;       /* ppm (LE) */
    int16_t temp_001c;      /* 0.01 deg C (LE), e.g. 2500 = 25.00 C */
    uint16_t hum_001pct;    /* 0.01 % (LE), e.g. 4550 = 45.50 % */
    uint16_t batt_mv;       /* mV (LE) */
    uint8_t status;         /* STATUS_FLAG_* */
    uint16_t crc16;         /* CRC-16-CCITT */
} pkt_telemetry_t;

typedef struct {
    uint8_t magic0;         /* 0xAA */
    uint8_t magic1;         /* 0x55 */
    uint8_t type;           /* 0x02 */
    uint8_t proto_ver;      /* 0x01 */
    uint16_t fw_ver;        /* 0x0100 (nibble major.minor.patch) */
    uint8_t build_flags;    /* bit0 = source tree was dirty at build time */
    uint32_t build_epoch;   /* build time, Unix seconds UTC (LE) */
    uint32_t git_hash;      /* first 4 bytes of commit SHA, 8 hex (LE) */
    uint16_t crc16;         /* CRC-16-CCITT */
} pkt_hello_t;

typedef struct {
    uint8_t magic0;         /* 0xAA */
    uint8_t magic1;         /* 0x55 */
    uint8_t type;           /* 0x10 */
    uint8_t x;
    uint8_t y;
    uint8_t w;
    uint8_t h;
    uint16_t length;        /* w * h * 2 bytes */
} cmd_draw_rect_hdr_t;

typedef struct {
    uint8_t magic0;         /* 0xAA */
    uint8_t magic1;         /* 0x55 */
    uint8_t type;           /* 0x11 */
    uint8_t brightness;     /* 0..100 */
    uint16_t crc16;
} cmd_set_backlight_t;

typedef struct {
    uint8_t magic0;         /* 0xAA */
    uint8_t magic1;         /* 0x55 */
    uint8_t type;           /* 0x12 */
    uint8_t red;            /* 0 or 1 */
    uint8_t yellow;         /* 0 or 1 */
    uint8_t green;          /* 0 or 1 */
    uint8_t brightness;     /* 0..100 */
    uint16_t crc16;
} cmd_set_leds_t;

typedef struct {
    uint8_t magic0;         /* 0xAA */
    uint8_t magic1;         /* 0x55 */
    uint8_t type;           /* 0x13 */
    uint16_t freq_hz;       /* Hz */
    uint16_t duration_ms;   /* ms */
    uint16_t crc16;
} cmd_beep_t;

typedef struct {
    uint8_t magic0;         /* 0xAA */
    uint8_t magic1;         /* 0x55 */
    uint8_t type;           /* 0x1F */
    uint32_t magic_key;     /* 0xDEADBEEF */
    uint16_t crc16;
} cmd_enter_bootloader_t;

#pragma pack(pop)

/* ── CRC-16-CCITT (Polynomial 0x1021, Init 0x0000, MSB-first) ── */
static inline uint16_t crc16_ccitt_update(uint16_t crc, uint8_t byte)
{
    crc ^= (uint16_t)byte << 8;
    for (int i = 0; i < 8; i++) {
        if (crc & 0x8000) {
            crc = (crc << 1) ^ 0x1021;
        } else {
            crc = crc << 1;
        }
    }
    return crc;
}

static inline uint16_t crc16_ccitt(const uint8_t *data, size_t len)
{
    uint16_t crc = 0x0000;
    for (size_t i = 0; i < len; i++) {
        crc = crc16_ccitt_update(crc, data[i]);
    }
    return crc;
}

#endif /* PROTOCOL_H */
