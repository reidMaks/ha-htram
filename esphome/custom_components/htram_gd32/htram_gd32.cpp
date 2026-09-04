#include "htram_gd32.h"
#include "esphome/core/log.h"

namespace esphome {
namespace htram_gd32 {

static const char *const TAG = "htram_gd32";

static uint16_t crc16_ccitt(const uint8_t *data, size_t len) {
  uint16_t crc = 0x0000;
  for (size_t i = 0; i < len; i++) {
    crc ^= (uint16_t)data[i] << 8;
    for (int j = 0; j < 8; j++) {
      if (crc & 0x8000) {
        crc = (crc << 1) ^ 0x1021;
      } else {
        crc = crc << 1;
      }
    }
  }
  return crc;
}

void HtramGd32Component::setup() {
  ESP_LOGI(TAG, "Setup HTRAM GD32 component...");
}

void HtramGd32Component::loop() {
  while (this->available()) {
    uint8_t c;
    this->read_byte(&c);
    // ESP_LOGD(TAG, "RX: %02X", c); // We can uncomment this if we want to spam logs
    rx_buffer_.push_back(c);

    if (rx_buffer_.size() == 1) {
       // Just to see if we get ANY bytes at all
       ESP_LOGD(TAG, "Got first byte in buffer: %02X", c);
    }

    // Sync on magic
    if (rx_buffer_.size() >= 2) {
      if (rx_buffer_[0] != 0xAA || rx_buffer_[1] != 0x55) {
        rx_buffer_.erase(rx_buffer_.begin());
        continue; // Keep trying to sync
      }
    }

    if (rx_buffer_.size() >= 3) {
      uint8_t type = rx_buffer_[2];
      size_t expected_len = 0;
      if (type == 0x01) {
        expected_len = 14; // Telemetry
      } else if (type == 0x02) {
        expected_len = 7;  // Hello
      } else {
        ESP_LOGW(TAG, "Unknown packet type: 0x%02X", type);
        rx_buffer_.clear();
        continue;
      }

      if (rx_buffer_.size() == expected_len) {
        this->process_packet_(rx_buffer_.data(), expected_len);
        rx_buffer_.clear();
      }
    }
  }
}

void HtramGd32Component::process_packet_(const uint8_t *data, size_t len) {
  uint8_t type = data[2];

  uint16_t received_crc = data[len - 2] | (data[len - 1] << 8);
  // Calculate CRC from 'type' byte (data + 2) up to 'status' byte (len - 4 bytes)
  uint16_t calculated_crc = crc16_ccitt(data + 2, len - 4);

  if (received_crc != calculated_crc) {
    ESP_LOGW(TAG, "CRC mismatch: calc 0x%04X != recv 0x%04X", calculated_crc, received_crc);
    return;
  }

  if (type == 0x01) { // Telemetry
    uint16_t co2 = data[3] | (data[4] << 8);
    int16_t temp = data[5] | (data[6] << 8);
    uint16_t hum = data[7] | (data[8] << 8);
    uint16_t batt = data[9] | (data[10] << 8);
    uint8_t status = data[11];

    if (this->co2_sensor_ != nullptr && co2 != 0xFFFF) {
      this->co2_sensor_->publish_state(co2);
    }
    if (this->temp_sensor_ != nullptr) {
      this->temp_sensor_->publish_state(temp / 100.0f);
    }
    if (this->hum_sensor_ != nullptr) {
      this->hum_sensor_->publish_state(hum / 100.0f);
    }
    if (this->batt_sensor_ != nullptr) {
      this->batt_sensor_->publish_state(batt);
    }

    ESP_LOGD(TAG, "Telemetry: CO2=%d, T=%.2f, H=%.2f, Batt=%dmV, Status=0x%02X", 
             co2, temp / 100.0f, hum / 100.0f, batt, status);
  } else if (type == 0x02) {
    ESP_LOGI(TAG, "Received HELLO from GD32");
  }
}

void HtramGd32Component::dump_config() {
  ESP_LOGCONFIG(TAG, "HTRAM GD32:");
  LOG_SENSOR("  ", "CO2", this->co2_sensor_);
  LOG_SENSOR("  ", "Temperature", this->temp_sensor_);
  LOG_SENSOR("  ", "Humidity", this->hum_sensor_);
  LOG_SENSOR("  ", "Battery", this->batt_sensor_);
}

void HtramGd32Component::send_beep(uint16_t freq, uint16_t dur) {
  uint8_t pkt[9];
  pkt[0] = 0xAA;
  pkt[1] = 0x55;
  pkt[2] = 0x13; // CMD_TYPE_BEEP
  pkt[3] = freq & 0xFF;
  pkt[4] = (freq >> 8) & 0xFF;
  pkt[5] = dur & 0xFF;
  pkt[6] = (dur >> 8) & 0xFF;
  uint16_t crc = crc16_ccitt(&pkt[2], 5);
  pkt[7] = crc & 0xFF;
  pkt[8] = (crc >> 8) & 0xFF;
  this->write_array(pkt, 9);
}

void HtramGd32Component::send_backlight(uint8_t brightness) {
  uint8_t pkt[6];
  pkt[0] = 0xAA;
  pkt[1] = 0x55;
  pkt[2] = 0x11; // CMD_TYPE_SET_BACKLIGHT
  pkt[3] = brightness;
  uint16_t crc = crc16_ccitt(&pkt[2], 2);
  pkt[4] = crc & 0xFF;
  pkt[5] = (crc >> 8) & 0xFF;
  this->write_array(pkt, 6);
}

void HtramGd32Component::send_leds(uint8_t r, uint8_t y, uint8_t g, uint8_t brightness) {
  uint8_t pkt[9];
  pkt[0] = 0xAA;
  pkt[1] = 0x55;
  pkt[2] = 0x12; // CMD_TYPE_SET_LEDS
  pkt[3] = r;
  pkt[4] = y;
  pkt[5] = g;
  pkt[6] = brightness;
  uint16_t crc = crc16_ccitt(&pkt[2], 5);
  pkt[7] = crc & 0xFF;
  pkt[8] = (crc >> 8) & 0xFF;
  this->write_array(pkt, 9);
}

void HtramGd32Component::send_enter_bootloader() {
  uint8_t pkt[9];
  pkt[0] = 0xAA;
  pkt[1] = 0x55;
  pkt[2] = 0x1F; // CMD_TYPE_ENTER_BOOTLOADER
  // BOOTLOADER_MAGIC_KEY = 0xDEADBEEF
  pkt[3] = 0xEF;
  pkt[4] = 0xBE;
  pkt[5] = 0xAD;
  pkt[6] = 0xDE;
  uint16_t crc = crc16_ccitt(&pkt[2], 5);
  pkt[7] = crc & 0xFF;
  pkt[8] = (crc >> 8) & 0xFF;
  this->write_array(pkt, 9);
}
}  // namespace htram_gd32
}  // namespace esphome
