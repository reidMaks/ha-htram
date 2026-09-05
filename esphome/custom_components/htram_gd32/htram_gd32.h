#pragma once

#include "esphome/core/component.h"
#include "esphome/components/uart/uart.h"
#include "esphome/components/sensor/sensor.h"
#include "esphome/components/text_sensor/text_sensor.h"
#include "esphome/components/binary_sensor/binary_sensor.h"
#include "esphome/components/switch/switch.h"
#include "esphome/components/web_server_base/web_server_base.h"
#include "esphome/components/display/display.h"
#include "esphome/components/display/display_color_utils.h"
#include <string>
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
  void set_battery_level_sensor(sensor::Sensor *s) { batt_level_sensor_ = s; }
  void set_fw_version_sensor(text_sensor::TextSensor *s) { fw_version_sensor_ = s; }
  void set_usb_binary_sensor(binary_sensor::BinarySensor *s) { usb_sensor_ = s; }
  void set_charging_binary_sensor(binary_sensor::BinarySensor *s) { charging_sensor_ = s; }
  void set_led_switch(uint8_t channel, switch_::Switch *s) {
    if (channel < 3) led_switch_[channel] = s;
  }

  void send_beep(uint16_t freq, uint16_t dur);
  void send_backlight(uint8_t brightness);
  void send_leds(uint8_t r, uint8_t y, uint8_t g, uint8_t brightness);
  void send_enter_bootloader();
  // Stream a note sequence to the GD32 (non-blocking playback there).
  void send_melody(const uint16_t *freqs, const uint16_t *durs, uint8_t count);
  void send_stop();                          // silence / cancel current melody
  void play_rtttl(const std::string &song);  // parse RTTTL, stream to GD32
  void send_draw_rect(uint8_t x, uint8_t y, uint8_t w, uint8_t h, const uint8_t *pixel_data, size_t len);

  // Called by HtramLedSwitch on user command: channel 0=red 1=yellow 2=green.
  void set_led(uint8_t channel, bool state);

  void set_button_binary_sensor(binary_sensor::BinarySensor *s) { button_sensor_ = s; }
  void set_button_action_sensor(text_sensor::TextSensor *s) { button_action_sensor_ = s; }

  // Returns JSON string with result
  std::string execute_ota(const std::vector<uint8_t> &firmware);

 protected:
  sensor::Sensor *co2_sensor_{nullptr};
  sensor::Sensor *temp_sensor_{nullptr};
  sensor::Sensor *hum_sensor_{nullptr};
  sensor::Sensor *batt_sensor_{nullptr};
  sensor::Sensor *batt_level_sensor_{nullptr};
  text_sensor::TextSensor *fw_version_sensor_{nullptr};
  text_sensor::TextSensor *button_action_sensor_{nullptr};
  binary_sensor::BinarySensor *usb_sensor_{nullptr};
  binary_sensor::BinarySensor *charging_sensor_{nullptr};
  binary_sensor::BinarySensor *button_sensor_{nullptr};
  uint8_t click_count_{0};
  switch_::Switch *led_switch_[3]{nullptr, nullptr, nullptr};  // 0=red 1=yellow 2=green
  bool led_state_[3]{false, false, false};
  std::string fw_version_;  // last published, to avoid redundant updates

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

// One of the three indicator LEDs. State is device-authoritative: user commands
// go to the GD32 via set_led(), and the real state is re-published from telemetry.
class HtramLedSwitch : public switch_::Switch, public Component {
 public:
  void set_parent(HtramGd32Component *parent) { parent_ = parent; }
  void set_channel(uint8_t channel) { channel_ = channel; }

 protected:
  void write_state(bool state) override {
    this->publish_state(state);
    if (this->parent_ != nullptr) this->parent_->set_led(this->channel_, state);
  }
  HtramGd32Component *parent_{nullptr};
  uint8_t channel_{0};
};

class HtramGd32Display : public display::Display {
 public:
  void set_parent(HtramGd32Component *parent) { parent_ = parent; }

  void dump_config() override;
  void update() override;

  void draw_pixel_at(int x, int y, Color color) override;
  void draw_pixels_at(int x_start, int y_start, int w, int h, const uint8_t *ptr, display::ColorOrder order,
                      display::ColorBitness bitness, bool big_endian, int x_offset, int y_offset, int x_pad) override;

  display::DisplayType get_display_type() override { return display::DisplayType::DISPLAY_TYPE_COLOR; }

 protected:
  int get_width_internal() override { return 240; }
  int get_height_internal() override { return 240; }

  HtramGd32Component *parent_{nullptr};
  std::vector<uint8_t> chunk_buffer_;
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
