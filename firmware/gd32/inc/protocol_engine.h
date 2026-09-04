#ifndef PROTOCOL_ENGINE_H
#define PROTOCOL_ENGINE_H

#include <stdint.h>
#include "protocol.h"

void protocol_init(uint32_t baud);
void protocol_send_telemetry(uint16_t co2, int16_t temp, uint16_t hum, uint16_t batt_mv, uint8_t status);
void protocol_send_hello(void);
void protocol_process_rx(void);

#endif /* PROTOCOL_ENGINE_H */
