#ifndef DISPLAY_H
#define DISPLAY_H

#include <stdint.h>

#define DISPLAY_WIDTH   240
#define DISPLAY_HEIGHT  240

/* Standard RGB565 Colors */
#define COLOR_BLACK     0x0000
#define COLOR_WHITE     0xFFFF
#define COLOR_RED       0xF800
#define COLOR_GREEN     0x07E0
#define COLOR_BLUE      0x001F
#define COLOR_YELLOW    0xFFE0
#define COLOR_CYAN      0x07FF
#define COLOR_MAGENTA   0xF81F
#define COLOR_ORANGE    0xFD20
#define COLOR_GRAY      0x4208
#define COLOR_DARK_GRAY 0x2104

void display_init(void);
void display_set_backlight(uint8_t brightness);
void display_set_window(uint8_t x, uint8_t y, uint8_t w, uint8_t h);
void display_send_pixel(uint16_t color);
void display_start_pixels(uint8_t x, uint8_t y, uint8_t w, uint8_t h);
void display_send_pixel_stream(uint16_t color);
void display_end_pixels(void);
void display_fill_rect(uint8_t x, uint8_t y, uint8_t w, uint8_t h, uint16_t color);
void display_fill_screen(uint16_t color);
void display_draw_string(uint8_t x, uint8_t y, const char *s, uint16_t color, uint16_t bg);

#endif /* DISPLAY_H */
