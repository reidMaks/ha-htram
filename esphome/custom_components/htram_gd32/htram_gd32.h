#pragma once

#include "esphome/core/component.h"
#include "esphome/components/uart/uart.h"
#include "esphome/components/sensor/sensor.h"
#include "esphome/components/web_server_base/web_server_base.h"
#include <vector>

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

  // Returns JSON string with result
  std::string execute_ota(const std::vector<uint8_t> &firmware);

 protected:
  sensor::Sensor *co2_sensor_{nullptr};
  sensor::Sensor *temp_sensor_{nullptr};
  sensor::Sensor *hum_sensor_{nullptr};
  sensor::Sensor *batt_sensor_{nullptr};

  std::vector<uint8_t> rx_buffer_;
  uint16_t last_batt_mv_{0};
  uint8_t last_status_{0};
  bool ota_mode_{false};

  void process_packet_(const uint8_t *data, size_t len);

  // ROM Bootloader helpers
  bool rom_sync(int attempt);
  bool rom_send_command(uint8_t cmd);
  bool rom_erase(std::string &err_msg);
  bool rom_write_memory(uint32_t address, const uint8_t *data, size_t len, std::string &err_msg);
  bool rom_go(uint32_t address);
};

class Gd32OtaHandler : public AsyncWebHandler {
 public:
  Gd32OtaHandler(HtramGd32Component *parent) : parent_(parent) {}

  bool canHandle(AsyncWebServerRequest *request) const override {
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wdeprecated-declarations"
    return request->url() == "/gd32_ota" && request->method() == HTTP_POST;
#pragma GCC diagnostic pop
  }

  void handleRequest(AsyncWebServerRequest *request) override {
    if (this->firmware_.empty()) {
      request->send(400, "application/json", "{\"result\":\"error\",\"reason\":\"no file uploaded\"}");
      return;
    }
    std::string res = this->parent_->execute_ota(this->firmware_);
    request->send(200, "application/json", res.c_str());
    this->firmware_.clear();
  }

  void handleUpload(AsyncWebServerRequest *request, const PlatformString &filename, size_t index, uint8_t *data, size_t len, bool final) override {
    if (index == 0) {
      this->firmware_.clear();
      this->firmware_.reserve(65536);
    }
    if (len > 0) {
      this->firmware_.insert(this->firmware_.end(), data, data + len);
    }
  }

 private:
  HtramGd32Component *parent_;
  std::vector<uint8_t> firmware_;
};

}  // namespace htram_gd32
}  // namespace esphome
