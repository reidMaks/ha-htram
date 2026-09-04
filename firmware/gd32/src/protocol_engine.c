#include "protocol_engine.h"
#include "gd32f150.h"
#include "display.h"
#include "periph.h"

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

static inline int uart1_rx_ready(void)
{
    return (USART1_STAT & USART_RBNE) ? 1 : 0;
}

static inline uint8_t uart1_getc(void)
{
    return (uint8_t)USART1_RDATA;
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
    USART1_BAUD = div;
    USART1_CTL0 = USART_UEN | USART_TEN | USART_REN;
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
                } else {
                    rx_state = STATE_CRC0;
                }
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
                /* Valid command execution */
                if (current_cmd == CMD_TYPE_SET_BACKLIGHT) {
                    display_set_backlight(cmd_buf[0]);
                } else if (current_cmd == CMD_TYPE_SET_LEDS) {
                    periph_set_leds(cmd_buf[0], cmd_buf[1], cmd_buf[2], cmd_buf[3]);
                } else if (current_cmd == CMD_TYPE_BEEP) {
                    uint16_t freq = (uint16_t)cmd_buf[0] | ((uint16_t)cmd_buf[1] << 8);
                    uint16_t dur = (uint16_t)cmd_buf[2] | ((uint16_t)cmd_buf[3] << 8);
                    periph_beep(freq, dur);
                } else if (current_cmd == CMD_TYPE_ENTER_BOOTLOADER) {
                    uint32_t key = (uint32_t)cmd_buf[0] |
                                   ((uint32_t)cmd_buf[1] << 8) |
                                   ((uint32_t)cmd_buf[2] << 16) |
                                   ((uint32_t)cmd_buf[3] << 24);
                    if (key == BOOTLOADER_MAGIC_KEY) {
                        system_enter_bootloader();
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
