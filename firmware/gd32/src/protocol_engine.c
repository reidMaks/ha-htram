#include "protocol_engine.h"
#include "gd32f150.h"
#include "display.h"
#include "periph.h"
#include "flasher.h"
#include "build_info.h"  /* generated per build: BUILD_EPOCH/BUILD_GIT_HASH/BUILD_DIRTY */

/* ── Low-Level UART1 (PA2=TX, PA3=RX) ── */

static void uart1_putc(uint8_t c)
{
    while (!(USART1_STAT & USART_TBE))
        ;
    USART1_TDATA = c;
}

static void uart1_write(const uint8_t *data, size_t len)
{
    for (size_t i = 0; i < len; i++) {
        uart1_putc(data[i]);
    }
}

/* ── RX ring buffer, filled by USART1_IRQHandler, drained by the main loop ──
 *
 * Polling RDATA from the main loop cannot keep up: at 115200 a byte lands every
 * 87 us into a single-byte register with no FIFO, while the loop can stall for
 * hundreds of ms (CO2 timeout, bitbang UI). Measured on the bench as 0 of 20
 * commands received. The ISR moves each byte out the instant it arrives; the
 * main loop then consumes the ring at its own pace. Size is a power of two so
 * the index wrap is a mask. */
#define RX_RING_SIZE 256
static volatile uint8_t rx_ring[RX_RING_SIZE];
static volatile uint16_t rx_head; /* written by ISR only */
static volatile uint16_t rx_tail; /* written by main only */
static volatile uint16_t rx_overflows;

void USART1_IRQHandler(void)
{
    while (USART1_STAT & USART_RBNE) {
        uint8_t b = (uint8_t)USART1_RDATA; /* read clears RBNE */
        uint16_t next = (uint16_t)((rx_head + 1) & (RX_RING_SIZE - 1));
        if (next != rx_tail) {
            rx_ring[rx_head] = b;
            rx_head = next;
        } else {
            rx_overflows++; /* ring full: drop, keep newest reads flowing */
        }
    }
}

static inline int uart1_rx_ready(void)
{
    return (rx_head != rx_tail) ? 1 : 0;
}

static inline uint8_t uart1_getc(void)
{
    uint8_t b = rx_ring[rx_tail];
    rx_tail = (uint16_t)((rx_tail + 1) & (RX_RING_SIZE - 1));
    return b;
}

void protocol_init(uint32_t baud)
{
    /* Enable GPIOA and USART1 clocks */
    RCU_AHBEN  |= RCU_AHBEN_PAEN;
    RCU_APB1EN |= RCU_APB1EN_USART1EN;

    /* Configure PA2 (TX) and PA3 (RX) as AF1 (USART1) */
    uint32_t ctl_a = GPIO_CTL(GPIOA_BASE);
    ctl_a &= ~((3 << (2 * 2)) | (3 << (3 * 2)));
    ctl_a |= (2 << (2 * 2)) | (2 << (3 * 2)); /* Alternate function */
    GPIO_CTL(GPIOA_BASE) = ctl_a;

    GPIO_AFSEL0(GPIOA_BASE) &= ~((0x0F << (2 * 4)) | (0x0F << (3 * 4)));
    GPIO_AFSEL0(GPIOA_BASE) |= (1 << (2 * 4)) | (1 << (3 * 4)); /* AF1 = USART1 */

    /* Pull-up on RX (PA3) */
    uint32_t pud_a = GPIO_PUD(GPIOA_BASE);
    pud_a &= ~(3 << (3 * 2));
    pud_a |= (1 << (3 * 2));
    GPIO_PUD(GPIOA_BASE) = pud_a;

    USART1_CTL0 = 0;
    /* USART1 Baud Rate Divisor: System Clock / Baud */
    uint32_t div = SYSTEM_CLOCK_HZ / baud;
    /* Enable RX-not-empty interrupt and unmask USART1 (IRQ28) in the NVIC so
     * incoming bytes are captured by USART1_IRQHandler regardless of what the
     * main loop is doing. */
    rx_head = 0;
    rx_tail = 0;
    NVIC_ISER0 = (1U << USART1_IRQn);
    USART1_CTL0 = 0;
    /* USART1 is on APB1, which is SYSTEM_CLOCK_HZ / 2 (36 MHz) */
    USART1_BAUD = (SYSTEM_CLOCK_HZ / 2) / baud;
    USART1_CTL0 = USART_UEN | USART_TEN | USART_REN | USART_RBNEIE;
}

void protocol_send_telemetry(uint16_t co2, int16_t temp, uint16_t hum, uint16_t batt_mv, uint8_t status)
{
    pkt_telemetry_t pkt;
    pkt.magic0 = PROTOCOL_MAGIC0;
    pkt.magic1 = PROTOCOL_MAGIC1;
    pkt.type = PKT_TYPE_TELEMETRY;
    pkt.co2_ppm = co2;
    pkt.temp_001c = temp;
    pkt.hum_001pct = hum;
    pkt.batt_mv = batt_mv;
    pkt.status = status;

    /* CRC over bytes between magic and crc (from type to status: 9 bytes) */
    pkt.crc16 = crc16_ccitt(&pkt.type, sizeof(pkt) - 4);

    uart1_write((const uint8_t *)&pkt, sizeof(pkt));
}

void protocol_send_hello(void)
{
    pkt_hello_t pkt;
    pkt.magic0 = PROTOCOL_MAGIC0;
    pkt.magic1 = PROTOCOL_MAGIC1;
    pkt.type = PKT_TYPE_HELLO;
    pkt.proto_ver = PROTOCOL_VERSION;
    pkt.fw_ver = GD32_FW_VERSION;
    pkt.build_flags = BUILD_DIRTY;
    pkt.build_epoch = BUILD_EPOCH;
    pkt.git_hash = BUILD_GIT_HASH;
    pkt.crc16 = crc16_ccitt(&pkt.type, sizeof(pkt) - 4);

    uart1_write((const uint8_t *)&pkt, sizeof(pkt));
}

void protocol_send_button_event(uint8_t state, uint16_t duration_ms)
{
    pkt_button_event_t pkt;
    pkt.magic0 = PROTOCOL_MAGIC0;
    pkt.magic1 = PROTOCOL_MAGIC1;
    pkt.type = PKT_TYPE_BUTTON;
    pkt.state = state;
    pkt.duration_ms = duration_ms;
    pkt.crc16 = crc16_ccitt(&pkt.type, sizeof(pkt) - 4);

    uart1_write((const uint8_t *)&pkt, sizeof(pkt));
}

/* ── RX Parsing State Machine ── */

typedef enum {
    STATE_MAGIC0,
    STATE_MAGIC1,
    STATE_TYPE,
    STATE_HEADER,
    STATE_PIXELS,
    STATE_MELODY,
    STATE_CRC0,
    STATE_CRC1
} rx_state_t;

static rx_state_t rx_state = STATE_MAGIC0;
static uint8_t current_cmd = 0;
static uint16_t calc_crc = 0;

/* Command specific buffers & counters */
static uint8_t cmd_buf[16];
static uint8_t cmd_buf_idx = 0;
static uint8_t cmd_buf_expected = 0;

static uint8_t rect_x = 0;
static uint8_t rect_y = 0;
static uint8_t rect_w = 0;
static uint8_t rect_h = 0;
static uint16_t pixels_left = 0;
static uint8_t pixel_hi = 0;
static uint8_t pixel_phase = 0;
static uint8_t rx_crc0 = 0;

/* CMD_PLAY_MELODY: streamed count*(freq16_LE, dur16_LE) */
#define MELODY_MAX_NOTES 96
static uint8_t melody_buf[MELODY_MAX_NOTES * 4];
static uint8_t melody_count = 0;   /* stored (clamped) note count */
static uint16_t melody_idx = 0;    /* byte index while streaming */
static uint16_t melody_bytes_left = 0;

void protocol_process_rx(void)
{
    while (uart1_rx_ready()) {
        uint8_t b = uart1_getc();

        switch (rx_state) {
        case STATE_MAGIC0:
            if (b == PROTOCOL_MAGIC0) {
                rx_state = STATE_MAGIC1;
            }
            break;

        case STATE_MAGIC1:
            if (b == PROTOCOL_MAGIC1) {
                rx_state = STATE_TYPE;
            } else if (b == PROTOCOL_MAGIC0) {
                /* stay in MAGIC1 if consecutive 0xAA */
                rx_state = STATE_MAGIC1;
            } else {
                rx_state = STATE_MAGIC0;
            }
            break;

        case STATE_TYPE:
            current_cmd = b;
            calc_crc = crc16_ccitt_update(0x0000, b);
            cmd_buf_idx = 0;

            if (current_cmd == CMD_TYPE_DRAW_RECT) {
                cmd_buf_expected = 6; /* X (1), Y (1), W (1), H (1), Length (2) */
                rx_state = STATE_HEADER;
            } else if (current_cmd == CMD_TYPE_SET_BACKLIGHT) {
                cmd_buf_expected = 1; /* Brightness (1) */
                rx_state = STATE_HEADER;
            } else if (current_cmd == CMD_TYPE_SET_LEDS) {
                cmd_buf_expected = 4; /* R (1), Y (1), G (1), Brightness (1) */
                rx_state = STATE_HEADER;
            } else if (current_cmd == CMD_TYPE_BEEP) {
                cmd_buf_expected = 4; /* Freq (2), Duration (2) */
                rx_state = STATE_HEADER;
            } else if (current_cmd == CMD_TYPE_PLAY_MELODY) {
                cmd_buf_expected = 1; /* Count (1); notes stream after */
                rx_state = STATE_HEADER;
            } else if (current_cmd == CMD_TYPE_ENTER_BOOTLOADER) {
                cmd_buf_expected = 4; /* Key (4) */
                rx_state = STATE_HEADER;
            } else {
                /* Unknown command */
                rx_state = STATE_MAGIC0;
            }
            break;

        case STATE_HEADER:
            calc_crc = crc16_ccitt_update(calc_crc, b);
            cmd_buf[cmd_buf_idx++] = b;

            if (cmd_buf_idx >= cmd_buf_expected) {
                if (current_cmd == CMD_TYPE_DRAW_RECT) {
                    rect_x = cmd_buf[0];
                    rect_y = cmd_buf[1];
                    rect_w = cmd_buf[2];
                    rect_h = cmd_buf[3];
                    pixels_left = (uint16_t)cmd_buf[4] | ((uint16_t)cmd_buf[5] << 8);

                    /* Set ST7789 window for incoming stream */
                    display_set_window(rect_x, rect_y, rect_w, rect_h);
                    pixel_phase = 0;
                    rx_state = STATE_PIXELS;
                } else if (current_cmd == CMD_TYPE_PLAY_MELODY) {
                    melody_count = cmd_buf[0];
                    if (melody_count > MELODY_MAX_NOTES) melody_count = MELODY_MAX_NOTES;
                    melody_idx = 0;
                    melody_bytes_left = (uint16_t)cmd_buf[0] * 4; /* stream full count for CRC */
                    rx_state = (melody_bytes_left == 0) ? STATE_CRC0 : STATE_MELODY;
                } else {
                    rx_state = STATE_CRC0;
                }
            }
            break;

        case STATE_MELODY:
            calc_crc = crc16_ccitt_update(calc_crc, b);
            if (melody_idx < sizeof(melody_buf)) {
                melody_buf[melody_idx] = b; /* store up to the clamp; extra bytes only feed CRC */
            }
            melody_idx++;
            if (--melody_bytes_left == 0) {
                rx_state = STATE_CRC0;
            }
            break;

        case STATE_PIXELS:
            calc_crc = crc16_ccitt_update(calc_crc, b);
            if (pixel_phase == 0) {
                pixel_hi = b;
                pixel_phase = 1;
            } else {
                uint16_t pixel = ((uint16_t)pixel_hi << 8) | b;
                display_send_pixel(pixel);
                pixel_phase = 0;
            }

            if (pixels_left > 0) {
                pixels_left--;
            }
            if (pixels_left == 0) {
                rx_state = STATE_CRC0;
            }
            break;

        case STATE_CRC0:
            rx_crc0 = b;
            rx_state = STATE_CRC1;
            break;

        case STATE_CRC1: {
            uint16_t expected_crc = (uint16_t)rx_crc0 | ((uint16_t)b << 8);
            if (expected_crc == calc_crc) {
#ifdef RX_DEBUG
                /* Bench instrumentation only: acknowledge every accepted frame
                 * so downlink reception can be counted from the host. Never
                 * built into the shipping firmware -- it is not in the §5
                 * protocol and the ESP32 would treat it as a bad packet. */
                {
                    static uint16_t rx_debug_count = 0;
                    rx_debug_count++;
                    uint8_t ack[8];
                    ack[0] = PROTOCOL_MAGIC0;
                    ack[1] = PROTOCOL_MAGIC1;
                    ack[2] = 0x7F;
                    ack[3] = current_cmd;
                    ack[4] = (uint8_t)(rx_debug_count & 0xFF);
                    ack[5] = (uint8_t)(rx_debug_count >> 8);
                    uint16_t ack_crc = crc16_ccitt(&ack[2], 4);
                    ack[6] = (uint8_t)(ack_crc & 0xFF);
                    ack[7] = (uint8_t)(ack_crc >> 8);
                    uart1_write(ack, sizeof(ack));
                }
#endif
                /* Valid command execution */
                if (current_cmd == CMD_TYPE_SET_BACKLIGHT) {
                    display_set_backlight(cmd_buf[0]);
                } else if (current_cmd == CMD_TYPE_SET_LEDS) {
                    periph_set_leds(cmd_buf[0], cmd_buf[1], cmd_buf[2], cmd_buf[3]);
                } else if (current_cmd == CMD_TYPE_BEEP) {
                    uint16_t freq = (uint16_t)cmd_buf[0] | ((uint16_t)cmd_buf[1] << 8);
                    uint16_t dur = (uint16_t)cmd_buf[2] | ((uint16_t)cmd_buf[3] << 8);
                    periph_beep(freq, dur);
                } else if (current_cmd == CMD_TYPE_PLAY_MELODY) {
                    periph_play_melody(melody_buf, melody_count);
                } else if (current_cmd == CMD_TYPE_ENTER_BOOTLOADER) {
                    uint32_t key = (uint32_t)cmd_buf[0] |
                                   ((uint32_t)cmd_buf[1] << 8) |
                                   ((uint32_t)cmd_buf[2] << 16) |
                                   ((uint32_t)cmd_buf[3] << 24);
                    if (key == BOOTLOADER_MAGIC_KEY) {
                        uint8_t ack[6] = {PROTOCOL_MAGIC0, PROTOCOL_MAGIC1, CMD_TYPE_ENTER_BOOTLOADER, 0x79, 0, 0};
                        uint16_t crc = crc16_ccitt(&ack[2], 2);
                        ack[4] = (uint8_t)(crc & 0xFF);
                        ack[5] = (uint8_t)(crc >> 8);
                        uart1_write(ack, sizeof(ack));
                        while (!(USART1_STAT & USART_TC))
                            ;
                        flasher_run();
                    }
                }
            }
            rx_state = STATE_MAGIC0;
            break;
        }

        default:
            rx_state = STATE_MAGIC0;
            break;
        }
    }
}
