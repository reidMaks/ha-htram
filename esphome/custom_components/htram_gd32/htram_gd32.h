#pragma once

#include "esphome/core/component.h"
#include "esphome/components/uart/uart.h"
#include "esphome/components/sensor/sensor.h"

namespace esphome {
namespace htram_gd32 {

class HtramGd32Component : public Component, public uart::UARTDevice {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;

  void set_co2_sensor(sensor::Sensor *s) { co2_sensor_ = s; }
  void set_temperature_sensor(sensor::Sensor *s) { temp_sensor_ = s; }
  void set_humidity_sensor(sensor::Sensor *s) { hum_sensor_ = s; }
  void set_battery_sensor(sensor::Sensor *s) { batt_sensor_ = s; }

  void send_beep(uint16_t freq, uint16_t dur);
  void send_backlight(uint8_t brightness);
  void send_leds(uint8_t r, uint8_t y, uint8_t g, uint8_t brightness);
  void send_enter_bootloader();

 protected:
  sensor::Sensor *co2_sensor_{nullptr};
  sensor::Sensor *temp_sensor_{nullptr};
  sensor::Sensor *hum_sensor_{nullptr};
  sensor::Sensor *batt_sensor_{nullptr};

  std::vector<uint8_t> rx_buffer_;

  void process_packet_(const uint8_t *data, size_t len);
};

}  // namespace htram_gd32
}  // namespace esphome
