#pragma once

#include "esphome/core/component.h"
#include "esphome/core/automation.h"

#ifdef USE_ESP_IDF
#include <esp_websocket_client.h>
#endif

#include <string>

namespace esphome {
namespace jaam_ws {

/* A client for the alert map's own WebSocket server.
 *
 * The map (github.com/J-A-A-M/ukraine_alarm_map) already holds what an alert
 * face needs, and serves it on port 81 to anything on the LAN -- that is how
 * its Home Assistant integration works. Talking to it directly rather than
 * through Home Assistant is deliberate: an air raid alert is exactly what must
 * not disappear because a home automation server is restarting, the same
 * argument that put the alarm clock and the minute of silence on the device.
 *
 * Two message types carry everything: `initial_state` on connect and
 * `home_alert_change` afterwards, both with `home_alert_flags` -- a bitmask
 * where bit 0 is the air raid itself and 5..10 are drones, missiles, KABs,
 * ballistic, explosion and recon drones. */
class JaamWsComponent : public Component {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::AFTER_WIFI; }

  void set_host(const std::string &host) { host_ = host; }
  void set_port(uint16_t port) { port_ = port; }
  void add_on_flags_callback(std::function<void(uint32_t)> &&cb) {
    this->flags_callback_.add(std::move(cb));
  }

  bool connected() const { return connected_; }
  uint32_t flags() const { return flags_; }

  /* Called from the WebSocket task, not from loop(). */
  void on_ws_data(const char *data, size_t len);
  void set_connected(bool c) { connected_ = c; }

 protected:
  std::string host_;
  uint16_t port_{81};
  bool connected_{false};

  /* Written by the WebSocket task, read by loop(). The task must not touch
   * anything else: the callbacks run automations that repaint LVGL, and doing
   * that off the main loop is how this project once crashed inside
   * lv_inv_area. So the task only parks a value here and raises a flag. */
  volatile uint32_t flags_{0};
  volatile bool pending_{false};
  uint32_t reported_{0xFFFFFFFF};

  CallbackManager<void(uint32_t)> flags_callback_;

#ifdef USE_ESP_IDF
  esp_websocket_client_handle_t client_{nullptr};
#endif
  std::string rx_;
};

class JaamFlagsTrigger : public Trigger<uint32_t> {
 public:
  explicit JaamFlagsTrigger(JaamWsComponent *parent) {
    parent->add_on_flags_callback([this](uint32_t flags) { this->trigger(flags); });
  }
};

}  // namespace jaam_ws
}  // namespace esphome
