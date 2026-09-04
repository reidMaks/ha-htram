#ifndef SENSORS_H
#define SENSORS_H

#include <stdint.h>

void sensors_init(void);
int sensors_read_sht30(int16_t *temp_001c, uint16_t *hum_001pct);
int sensors_poll_co2(uint16_t *co2_ppm, uint8_t *warmup_flag);

#endif /* SENSORS_H */
