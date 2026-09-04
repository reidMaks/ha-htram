#ifndef SENSORS_H
#define SENSORS_H

#include <stdint.h>

void sensors_init(void);
int sensors_read_sht30(int16_t *temp_001c, uint16_t *hum_001pct);
void sensors_update_thermal_model(uint8_t bl_on, uint8_t usb_on, uint8_t boost_on);
int sensors_poll_co2(uint16_t *co2_ppm, uint8_t *warmup_flag);

#endif /* SENSORS_H */
