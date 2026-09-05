#include "htram_gd32.h"
#include "esphome/core/log.h"
#include "esphome/core/helpers.h"
#include "esphome/core/application.h"
#include <ctime>
#include <cctype>

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

// Single-cell Li-ion voltage (mV) -> state-of-charge %, piecewise-linear.
static float batt_mv_to_pct(uint16_t mv) {
  static const struct {
    uint16_t mv;
    uint8_t pct;
  } curve[] = {{3200, 0},  {3400, 8},  {3550, 20}, {3650, 35}, {3720, 50}, {3780, 62},
               {3850, 72}, {3950, 84}, {4050, 93}, {4150, 98}, {4200, 100}};
  const size_t n = sizeof(curve) / sizeof(curve[0]);
  if (mv <= curve[0].mv) return 0.0f;
  if (mv >= curve[n - 1].mv) return 100.0f;
  for (size_t i = 1; i < n; i++) {
    if (mv < curve[i].mv) {
      float span = curve[i].mv - curve[i - 1].mv;
      float f = (mv - curve[i - 1].mv) / span;
      return curve[i - 1].pct + f * (curve[i].pct - curve[i - 1].pct);
    }
  }
  return 100.0f;
}

void HtramGd32Component::setup() {
  ESP_LOGI(TAG, "Setup HTRAM GD32 component...");
  if (esphome::web_server_base::global_web_server_base != nullptr) {
    esphome::web_server_base::global_web_server_base->add_handler(new Gd32OtaHandler(this));
    ESP_LOGI(TAG, "GD32 OTA HTTP handler registered at /gd32_ota");
  } else {
    ESP_LOGW(TAG, "web_server_base is null, OTA will not be available!");
  }
}

void HtramGd32Component::loop() {
  if (ota_mode_) return;

  while (this->available()) {
    uint8_t c;
    this->read_byte(&c);
    rx_buffer_.push_back(c);

    // Sync on magic
    if (rx_buffer_.size() >= 2) {
      if (rx_buffer_[0] != 0xAA || rx_buffer_[1] != 0x55) {
        rx_buffer_.erase(rx_buffer_.begin());
        continue;
      }
    }

    if (rx_buffer_.size() >= 3) {
      uint8_t type = rx_buffer_[2];
      size_t expected_len = 0;
      if (type == 0x01) {
        expected_len = 14;
      } else if (type == 0x02) {
        // pkt_hello_t: magic(2)+type(1)+proto_ver(1)+fw_ver(2)+flags(1)+epoch(4)+git(4)+crc16(2)
        expected_len = 17;
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
  uint16_t calculated_crc = crc16_ccitt(data + 2, len - 4);

  if (received_crc != calculated_crc) return;

  if (type == 0x01) {
    uint16_t co2 = data[3] | (data[4] << 8);
    int16_t temp = data[5] | (data[6] << 8);
    uint16_t hum = data[7] | (data[8] << 8);
    last_batt_mv_ = data[9] | (data[10] << 8);
    last_status_ = data[11];

    if (this->co2_sensor_ != nullptr && co2 != 0xFFFF) this->co2_sensor_->publish_state(co2);
    if (this->temp_sensor_ != nullptr) this->temp_sensor_->publish_state(temp / 100.0f);
    if (this->hum_sensor_ != nullptr) this->hum_sensor_->publish_state(hum / 100.0f);
    if (this->batt_sensor_ != nullptr) this->batt_sensor_->publish_state(last_batt_mv_);
    if (this->batt_level_sensor_ != nullptr)
      this->batt_level_sensor_->publish_state(batt_mv_to_pct(last_batt_mv_));

    bool usb = (last_status_ & 0x02) != 0;       // STATUS_FLAG_USB_PRESENT
    bool charging = (last_status_ & 0x01) != 0;  // STATUS_FLAG_CHARGING
    if (this->usb_sensor_ != nullptr) this->usb_sensor_->publish_state(usb);
    if (this->charging_sensor_ != nullptr) this->charging_sensor_->publish_state(charging);

    // LED state is device-authoritative: mirror the reported bits onto the switches.
    bool leds[3] = {
        (last_status_ & 0x80) != 0,  // red    -> STATUS_FLAG_LED_RED
        (last_status_ & 0x40) != 0,  // yellow -> STATUS_FLAG_LED_YELLOW
        (last_status_ & 0x20) != 0,  // green  -> STATUS_FLAG_LED_GREEN
    };
    for (uint8_t i = 0; i < 3; i++) {
      led_state_[i] = leds[i];
      if (this->led_switch_[i] != nullptr && this->led_switch_[i]->state != leds[i])
        this->led_switch_[i]->publish_state(leds[i]);
    }
  } else if (type == 0x02) {
    // pkt_hello_t: proto_ver(1) fw_ver(2 LE) build_flags(1) build_epoch(4 LE) git_hash(4 LE).
    // fw_ver is nibble-encoded major.minor.patch, e.g. 0x0100 -> "1.0.0" (see firmware protocol.h).
    uint16_t fw = data[4] | (data[5] << 8);
    uint8_t flags = data[6];
    uint32_t epoch = (uint32_t) data[7] | ((uint32_t) data[8] << 8) | ((uint32_t) data[9] << 16) |
                     ((uint32_t) data[10] << 24);
    uint32_t git = (uint32_t) data[11] | ((uint32_t) data[12] << 8) | ((uint32_t) data[13] << 16) |
                   ((uint32_t) data[14] << 24);

    char ts[20] = "?";
    if (epoch != 0) {
      time_t t = (time_t) epoch;
      struct tm tm_utc;
      gmtime_r(&t, &tm_utc);
      strftime(ts, sizeof(ts), "%Y-%m-%d %H:%M", &tm_utc);
    }
    char ver[64];
    snprintf(ver, sizeof(ver), "%u.%u.%u g%08x%s (%s UTC)", (fw >> 8) & 0xFF, (fw >> 4) & 0x0F,
             fw & 0x0F, (unsigned) git, (flags & 0x01) ? "+" : "", ts);
    if (this->fw_version_sensor_ != nullptr && fw_version_ != ver) {
      fw_version_ = ver;
      this->fw_version_sensor_->publish_state(ver);
    }
  }
}

void HtramGd32Component::dump_config() {
  ESP_LOGCONFIG(TAG, "HTRAM GD32:");
}

void HtramGd32Component::send_beep(uint16_t freq, uint16_t dur) {
  uint8_t pkt[9] = {0xAA, 0x55, 0x13, (uint8_t)(freq & 0xFF), (uint8_t)(freq >> 8), (uint8_t)(dur & 0xFF), (uint8_t)(dur >> 8)};
  uint16_t crc = crc16_ccitt(&pkt[2], 5);
  pkt[7] = crc & 0xFF; pkt[8] = crc >> 8;
  this->write_array(pkt, 9);
}

void HtramGd32Component::send_backlight(uint8_t brightness) {
  uint8_t pkt[6] = {0xAA, 0x55, 0x11, brightness};
  uint16_t crc = crc16_ccitt(&pkt[2], 2);
  pkt[4] = crc & 0xFF; pkt[5] = crc >> 8;
  this->write_array(pkt, 6);
}

void HtramGd32Component::send_leds(uint8_t r, uint8_t y, uint8_t g, uint8_t brightness) {
  uint8_t pkt[9] = {0xAA, 0x55, 0x12, r, y, g, brightness};
  uint16_t crc = crc16_ccitt(&pkt[2], 5);
  pkt[7] = crc & 0xFF; pkt[8] = crc >> 8;
  this->write_array(pkt, 9);
}

void HtramGd32Component::set_led(uint8_t channel, bool state) {
  if (channel >= 3) return;
  led_state_[channel] = state;
  // brightness byte is fixed: hardware LEDs are on/off only.
  send_leds(led_state_[0], led_state_[1], led_state_[2], 1);
}

void HtramGd32Component::send_melody(const uint16_t *freqs, const uint16_t *durs, uint8_t count) {
  if (count == 0) return;
  if (count > 96) count = 96;  // GD32 clamps to MELODY_MAX
  std::vector<uint8_t> pkt;
  pkt.reserve(4 + (size_t) count * 4 + 2);
  pkt.push_back(0xAA);
  pkt.push_back(0x55);
  pkt.push_back(0x14);  // CMD_TYPE_PLAY_MELODY
  pkt.push_back(count);
  for (uint8_t i = 0; i < count; i++) {
    pkt.push_back(freqs[i] & 0xFF);
    pkt.push_back(freqs[i] >> 8);
    pkt.push_back(durs[i] & 0xFF);
    pkt.push_back(durs[i] >> 8);
  }
  uint16_t crc = crc16_ccitt(pkt.data() + 2, pkt.size() - 2);  // type..last note byte
  pkt.push_back(crc & 0xFF);
  pkt.push_back(crc >> 8);
  this->write_array(pkt.data(), pkt.size());
}

void HtramGd32Component::send_stop() {
  // CMD_PLAY_MELODY with count 0 => GD32 silences and cancels playback.
  uint8_t pkt[6] = {0xAA, 0x55, 0x14, 0x00};
  uint16_t crc = crc16_ccitt(&pkt[2], 2);
  pkt[4] = crc & 0xFF;
  pkt[5] = crc >> 8;
  this->write_array(pkt, 6);
}

// note letter (a..g) -> index into the octave-4 semitone table (c,c#,d..b)
static uint16_t rtttl_note_freq(int note_idx, int octave) {
  static const uint16_t base[12] = {262, 277, 294, 311, 330, 349,
                                    370, 392, 415, 440, 466, 494};  // c..b, octave 4
  if (note_idx < 0) return 0;  // pause
  int32_t f = base[note_idx % 12];
  int shift = octave - 4;
  while (shift > 0) { f <<= 1; shift--; }
  while (shift < 0) { f >>= 1; shift++; }
  if (f < 20) f = 20;
  if (f > 20000) f = 20000;
  return (uint16_t) f;
}

void HtramGd32Component::play_rtttl(const std::string &song) {
  // RTTTL: name:d=<dur>,o=<oct>,b=<bpm>:<note>,<note>,...
  size_t c1 = song.find(':');
  if (c1 == std::string::npos) return;
  size_t c2 = song.find(':', c1 + 1);
  if (c2 == std::string::npos) return;

  int def_dur = 4, def_oct = 6, bpm = 63;
  {
    std::string defs = song.substr(c1 + 1, c2 - c1 - 1);
    size_t i = 0;
    while (i < defs.size()) {
      while (i < defs.size() && !isalpha((unsigned char) defs[i])) i++;
      if (i >= defs.size()) break;
      char key = (char) tolower((unsigned char) defs[i]);
      i++;
      while (i < defs.size() && defs[i] != '=') i++;
      if (i < defs.size()) i++;  // skip '='
      int val = 0;
      bool any = false;
      while (i < defs.size() && isdigit((unsigned char) defs[i])) {
        val = val * 10 + (defs[i] - '0');
        i++;
        any = true;
      }
      if (any) {
        if (key == 'd') def_dur = val;
        else if (key == 'o') def_oct = val;
        else if (key == 'b') bpm = val;
      }
    }
  }
  if (bpm <= 0) bpm = 63;
  uint32_t whole_ms = 4u * 60000u / (uint32_t) bpm;  // whole-note duration

  static const int letter_map[7] = {9, 11, 0, 2, 4, 5, 7};  // a,b,c,d,e,f,g
  std::vector<uint16_t> freqs, durs;
  size_t i = c2 + 1;
  size_t n = song.size();
  while (i < n && freqs.size() < 96) {
    while (i < n && (song[i] == ',' || song[i] == ' ')) i++;
    if (i >= n) break;

    int dur = 0;
    bool has_dur = false;
    while (i < n && isdigit((unsigned char) song[i])) { dur = dur * 10 + (song[i] - '0'); i++; has_dur = true; }
    if (!has_dur) dur = def_dur;

    char c = (i < n) ? (char) tolower((unsigned char) song[i]) : 0;
    int note_idx;
    if (c == 'p') {
      note_idx = -1;
      i++;
    } else if (c >= 'a' && c <= 'g') {
      note_idx = letter_map[c - 'a'];
      i++;
      if (i < n && song[i] == '#') { note_idx++; i++; }
    } else {
      i++;
      continue;
    }

    bool dotted = false;
    if (i < n && song[i] == '.') { dotted = true; i++; }
    int oct = def_oct;
    if (i < n && isdigit((unsigned char) song[i])) { oct = song[i] - '0'; i++; }
    if (i < n && song[i] == '.') { dotted = true; i++; }

    uint32_t ms = (dur > 0) ? whole_ms / (uint32_t) dur : whole_ms / 4;
    if (dotted) ms += ms / 2;
    if (ms > 65535) ms = 65535;

    freqs.push_back(rtttl_note_freq(note_idx, oct));
    durs.push_back((uint16_t) ms);
  }

  if (freqs.empty()) {
    ESP_LOGW(TAG, "RTTTL parse produced no notes: %s", song.c_str());
    return;
  }
  ESP_LOGI(TAG, "Playing RTTTL: %d notes (bpm=%d)", (int) freqs.size(), bpm);
  send_melody(freqs.data(), durs.data(), (uint8_t) freqs.size());
}


void HtramGd32Component::send_enter_bootloader() {
  uint8_t pkt[9] = {0xAA, 0x55, 0x1F, 0xEF, 0xBE, 0xAD, 0xDE};
  uint16_t crc = crc16_ccitt(&pkt[2], 5);
  pkt[7] = crc & 0xFF; pkt[8] = crc >> 8;
  this->write_array(pkt, 9);
}

// =========================================================================
// GD32 ROM BOOTLOADER IMPLEMENTATION
// =========================================================================

static bool read_with_timeout(uart::UARTDevice *dev, uint8_t *c, uint32_t timeout_ms) {
  uint32_t start = millis();
  while (millis() - start < timeout_ms) {
    if (dev->available()) {
      dev->read_byte(c);
      return true;
    }
    App.feed_wdt();
    delay(1);
  }
  return false;
}

bool HtramGd32Component::rom_sync(int attempt) {
  int drained = 0;
  while (this->available()) {
    uint8_t dummy;
    this->read_byte(&dummy);
    drained++;
  }
  if (drained > 0) {
    ESP_LOGD(TAG, "[rom_sync #%d] Cleared %d bytes from RX", attempt, drained);
  }

  uint8_t c = 0x7F;
  this->write_array(&c, 1);
  this->flush();

  uint8_t ack = 0;
  uint32_t t0 = millis();
  if (read_with_timeout(this, &ack, 300)) {
    uint32_t dt = millis() - t0;
    if (ack == 0x79) {
      ESP_LOGI(TAG, "[rom_sync #%d] Received ACK (0x79) in %lu ms!", attempt, (unsigned long)dt);
      return true;
    } else if (ack == 0x1F) {
      ESP_LOGI(TAG, "[rom_sync #%d] Received NACK (0x1F) in %lu ms (already synced)", attempt, (unsigned long)dt);
      return true;
    } else {
      ESP_LOGW(TAG, "[rom_sync #%d] Received unknown byte: 0x%02X in %lu ms", attempt, ack, (unsigned long)dt);
    }
  } else {
    uint32_t dt = millis() - t0;
    ESP_LOGW(TAG, "[rom_sync #%d] Timeout waiting for reply (%lu ms)", attempt, (unsigned long)dt);
  }
  return false;
}

bool HtramGd32Component::rom_send_command(uint8_t cmd) {
  uint8_t pkt[2] = {cmd, (uint8_t)~cmd};
  this->write_array(pkt, 2);
  this->flush();
  uint8_t ack = 0;
  uint32_t t0 = millis();
  if (read_with_timeout(this, &ack, 1000)) {
    if (ack == 0x79) {
      ESP_LOGD(TAG, "Command 0x%02X ACKed in %lu ms", cmd, (unsigned long)(millis() - t0));
      return true;
    }
    ESP_LOGW(TAG, "Command 0x%02X rejected, received: 0x%02X (expected 0x79)", cmd, ack);
    return false;
  }
  ESP_LOGW(TAG, "Command 0x%02X timed out waiting for ACK (1000 ms)", cmd);
  return false;
}

bool HtramGd32Component::rom_erase(std::string &err_msg) {
  ESP_LOGI(TAG, "[OTA 4/6] Sending Erase command (0x43 0xBC)...");
  if (!rom_send_command(0x43)) {
    err_msg = "Erase command 0x43 rejected or timed out";
    ESP_LOGE(TAG, "[OTA 4/6] %s", err_msg.c_str());
    return false;
  }
  ESP_LOGI(TAG, "[OTA 4/6] Sending Global Mass Erase (0xFF 0x00)...");
  uint8_t pkt[2] = {0xFF, 0x00};
  this->write_array(pkt, 2);
  this->flush();

  uint8_t ack = 0;
  uint32_t t0 = millis();
  if (read_with_timeout(this, &ack, 10000)) {
    uint32_t dt = millis() - t0;
    if (ack == 0x79) {
      ESP_LOGI(TAG, "[OTA 4/6] Mass erase SUCCESSFUL in %lu ms (ACK 0x79)", (unsigned long)dt);
      return true;
    } else {
      char b[64];
      snprintf(b, sizeof(b), "Mass erase returned 0x%02X (expected 0x79)", ack);
      err_msg = b;
      ESP_LOGE(TAG, "[OTA 4/6] %s", err_msg.c_str());
      return false;
    }
  }
  err_msg = "Mass erase timed out after 10000 ms";
  ESP_LOGE(TAG, "[OTA 4/6] %s", err_msg.c_str());
  return false;
}

bool HtramGd32Component::rom_write_memory(uint32_t address, const uint8_t *data, size_t len, std::string &err_msg) {
  if (len == 0 || len > 256 || (len % 4 != 0)) {
    err_msg = "Invalid write length " + std::to_string(len);
    return false;
  }

  if (!rom_send_command(0x31)) {
    char b[64];
    snprintf(b, sizeof(b), "Write cmd 0x31 rejected at 0x%08X", (unsigned int)address);
    err_msg = b;
    return false;
  }

  // Send address + checksum
  uint8_t addr_buf[5];
  addr_buf[0] = (address >> 24) & 0xFF;
  addr_buf[1] = (address >> 16) & 0xFF;
  addr_buf[2] = (address >> 8) & 0xFF;
  addr_buf[3] = address & 0xFF;
  addr_buf[4] = addr_buf[0] ^ addr_buf[1] ^ addr_buf[2] ^ addr_buf[3];
  this->write_array(addr_buf, 5);
  this->flush();

  uint8_t ack = 0;
  if (!read_with_timeout(this, &ack, 500) || ack != 0x79) {
    char b[64];
    snprintf(b, sizeof(b), "Write addr 0x%08X rejected (ack=0x%02X)", (unsigned int)address, ack);
    err_msg = b;
    return false;
  }

  // Send data + checksum
  std::vector<uint8_t> data_pkt;
  uint8_t len_code = len - 1;
  data_pkt.push_back(len_code);
  uint8_t chk = len_code;
  for (size_t i = 0; i < len; i++) {
    data_pkt.push_back(data[i]);
    chk ^= data[i];
  }
  data_pkt.push_back(chk);
  this->write_array(data_pkt.data(), data_pkt.size());
  this->flush();

  if (!read_with_timeout(this, &ack, 1000) || ack != 0x79) {
    char b[64];
    snprintf(b, sizeof(b), "Write data rejected at 0x%08X (ack=0x%02X)", (unsigned int)address, ack);
    err_msg = b;
    return false;
  }
  return true;
}

bool HtramGd32Component::rom_go(uint32_t address) {
  ESP_LOGI(TAG, "[OTA 6/6] Sending GO command (0x21 0xDE) to start application at 0x%08X...", (unsigned int)address);
  if (!rom_send_command(0x21)) {
    ESP_LOGE(TAG, "[OTA 6/6] GO command 0x21 rejected");
    return false;
  }
  uint8_t addr_buf[5];
  addr_buf[0] = (address >> 24) & 0xFF;
  addr_buf[1] = (address >> 16) & 0xFF;
  addr_buf[2] = (address >> 8) & 0xFF;
  addr_buf[3] = address & 0xFF;
  addr_buf[4] = addr_buf[0] ^ addr_buf[1] ^ addr_buf[2] ^ addr_buf[3];
  this->write_array(addr_buf, 5);
  this->flush();

  uint8_t ack = 0;
  if (!read_with_timeout(this, &ack, 500) || ack != 0x79) {
    ESP_LOGW(TAG, "[OTA 6/6] GO address rejected (ack=0x%02X)", ack);
    return false;
  }
  ESP_LOGI(TAG, "[OTA 6/6] GO command ACKed (0x79)! GD32 application started.");
  return true;
}

std::string HtramGd32Component::execute_ota(const std::vector<uint8_t> &firmware) {
  char buf[256];
  
  if (last_batt_mv_ < 3500 && last_batt_mv_ != 0) {
    snprintf(buf, sizeof(buf), "{\"result\":\"error\",\"stage\":\"safety_gate\",\"reason\":\"battery too low (%d mV)\"}", last_batt_mv_);
    ESP_LOGW(TAG, "[OTA] Safety gate tripped: battery too low (%d mV)", last_batt_mv_);
    return buf;
  }
  
  if (firmware.size() == 0 || firmware.size() > 65536) {
    snprintf(buf, sizeof(buf), "{\"result\":\"error\",\"stage\":\"safety_gate\",\"reason\":\"invalid firmware size %d\"}", (int)firmware.size());
    ESP_LOGW(TAG, "[OTA] Safety gate tripped: invalid size %d", (int)firmware.size());
    return buf;
  }

  uint16_t staged_crc = crc16_ccitt(firmware.data(), firmware.size());
  ESP_LOGI(TAG, "[OTA] --- Starting GD32 OTA Firmware Update ---");
  ESP_LOGI(TAG, "[OTA] Image size: %d bytes, Staged CRC16: 0x%04X, Battery: %d mV, Status: 0x%02X",
           (int)firmware.size(), staged_crc, last_batt_mv_, last_status_);

  ota_mode_ = true;

  // Drain any pending telemetry in RX buffer
  int pre_drained = 0;
  while (this->available()) {
    uint8_t dummy;
    this->read_byte(&dummy);
    pre_drained++;
  }
  if (pre_drained > 0) {
    ESP_LOGD(TAG, "[OTA] Flushed %d old bytes from RX buffer", pre_drained);
  }

  // 1. Trigger bootloader
  ESP_LOGI(TAG, "[OTA 1/6] Sending CMD_ENTER_BOOTLOADER (0x1F, key 0xDEADBEEF) to GD32...");
  send_enter_bootloader();
  this->flush();

  // Wait up to 150ms for GD32 to respond or jump
  uint32_t ack_wait_start = millis();
  std::vector<uint8_t> ack_bytes;
  while (millis() - ack_wait_start < 150) {
    if (this->available()) {
      uint8_t b;
      this->read_byte(&b);
      ack_bytes.push_back(b);
    } else {
      delay(5);
    }
  }

  if (!ack_bytes.empty()) {
    std::string hex_str;
    for (uint8_t b : ack_bytes) {
      char h[4];
      snprintf(h, sizeof(h), "%02X ", b);
      hex_str += h;
    }
    ESP_LOGI(TAG, "[OTA 1/6] GD32 responded (%d bytes): %s", (int)ack_bytes.size(), hex_str.c_str());
    if (ack_bytes.size() >= 4 && ack_bytes[0] == 0xAA && ack_bytes[1] == 0x55 && ack_bytes[2] == 0x1F && ack_bytes[3] == 0x79) {
      ESP_LOGI(TAG, "[OTA 1/6] GD32 acknowledged bootloader jump (0x79)!");
    }
  } else {
    ESP_LOGW(TAG, "[OTA 1/6] No response from GD32 application (may already be in bootloader or running older fw)");
  }

  // 2. Clear any noise
  delay(50);
  while (this->available()) {
    uint8_t dummy;
    this->read_byte(&dummy);
  }

  // 3. Sync with resident flasher
  ESP_LOGI(TAG, "[OTA 3/6] Synchronizing with GD32 resident flasher via 0x7F...");
  bool synced = false;
  for (int retry = 1; retry <= 10; retry++) {
    ESP_LOGI(TAG, "[OTA 3/6] Sync attempt %d/10...", retry);
    if (rom_sync(retry)) {
      synced = true;
      break;
    }
    delay(50);
  }

  if (!synced) {
    ESP_LOGE(TAG, "[OTA 3/6] FAILED: Could not sync with flasher after 10 attempts!");
    rx_buffer_.clear();
    ota_mode_ = false;
    snprintf(buf, sizeof(buf), "{\"result\":\"error\",\"stage\":\"rom_sync\",\"reason\":\"No ACK from flasher (timeout on 0x7F)\"}");
    return buf;
  }

  ESP_LOGI(TAG, "[OTA 3/6] Bootloader synchronization SUCCESSFUL!");

  // 4. Erase Flash
  std::string erase_err;
  if (!rom_erase(erase_err)) {
    rx_buffer_.clear();
    ota_mode_ = false;
    snprintf(buf, sizeof(buf), "{\"result\":\"error\",\"stage\":\"erase\",\"reason\":\"%s\"}", erase_err.c_str());
    return buf;
  }

  // 5. Write Memory
  ESP_LOGI(TAG, "[OTA 5/6] Writing %d bytes to GD32 flash @ 0x08000000...", (int)firmware.size());
  bool success = true;
  int bytes_written = 0;
  std::string write_err;
  uint32_t flash_t0 = millis();

  for (size_t offset = 0; offset < firmware.size(); offset += 256) {
    size_t chunk_len = std::min((size_t)256, firmware.size() - offset);
    uint8_t chunk[256];
    memset(chunk, 0xFF, sizeof(chunk));
    memcpy(chunk, firmware.data() + offset, chunk_len);
    size_t padded_len = (chunk_len + 3) & ~3;

    if (!rom_write_memory(0x08000000 + offset, chunk, padded_len, write_err)) {
      ESP_LOGE(TAG, "[OTA 5/6] Write failed at offset %d: %s", (int)offset, write_err.c_str());
      success = false;
      break;
    }
    bytes_written += chunk_len;
    App.feed_wdt();

    if ((bytes_written % 2048 == 0) || (offset + chunk_len >= firmware.size())) {
      int pct = (bytes_written * 100) / firmware.size();
      ESP_LOGI(TAG, "[OTA 5/6] Flashed %d / %d bytes (%d%%)", bytes_written, (int)firmware.size(), pct);
    }
  }

  if (success) {
    uint32_t dt = millis() - flash_t0;
    ESP_LOGI(TAG, "[OTA 5/6] Flashing completed: %d bytes in %lu ms (%.1f KB/s)",
             bytes_written, (unsigned long)dt, dt > 0 ? (float)bytes_written / dt : 0);
    // 6. Start Application
    rom_go(0x08000000);
  }

  rx_buffer_.clear();
  ota_mode_ = false;

  if (!success) {
    snprintf(buf, sizeof(buf), "{\"result\":\"error\",\"stage\":\"write\",\"reason\":\"%s\",\"bytes_written\":%d}",
             write_err.c_str(), bytes_written);
  } else {
    snprintf(buf, sizeof(buf), "{\"result\":\"ok\",\"bytes_written\":%d,\"staged_crc\":%d}", 
             bytes_written, staged_crc);
  }
  return buf;
}

}  // namespace htram_gd32
}  // namespace esphome
