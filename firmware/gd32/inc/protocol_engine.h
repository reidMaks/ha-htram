#ifndef PROTOCOL_ENGINE_H
#define PROTOCOL_ENGINE_H

#include <stdint.h>
#include "protocol.h"

void protocol_init(uint32_t baud);
void protocol_send_telemetry(uint16_t co2, int16_t temp, uint16_t hum, uint16_t batt_mv, uint8_t status);
void protocol_send_hello(void);
void protocol_send_button_event(uint8_t state, uint16_t duration_ms);
/* Ask the ESP to hold off (resume=0) / resume (resume=1) the pixel stream. */
void protocol_send_flow(uint8_t resume);
void protocol_process_rx(void);
uint8_t protocol_is_external_display_active(void);
/* Hand the panel back to local rendering (standby) or to the ESP. */
void protocol_set_external_display(uint8_t active);

#endif /* PROTOCOL_ENGINE_H */
