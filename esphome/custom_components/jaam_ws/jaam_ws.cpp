#include "jaam_ws.h"
#include "esphome/core/log.h"

#ifdef USE_ESP_IDF
#include "esphome/components/json/json_util.h"

namespace esphome {
namespace jaam_ws {

static const char *const TAG = "jaam_ws";

static void ws_event_handler(void *arg, esp_event_base_t base, int32_t id, void *data) {
  auto *self = static_cast<JaamWsComponent *>(arg);
  auto *ev = static_cast<esp_websocket_event_data_t *>(data);
  switch (id) {
    case WEBSOCKET_EVENT_CONNECTED:
      self->set_connected(true);
      break;
    case WEBSOCKET_EVENT_DISCONNECTED:
    case WEBSOCKET_EVENT_CLOSED:
      self->set_connected(false);
      break;
    case WEBSOCKET_EVENT_DATA:
      // op_code 1 is a text frame; ping/pong and binary are not ours.
      if (ev->op_code == 0x01 && ev->data_len > 0)
        self->on_ws_data(ev->data_ptr, ev->data_len);
      break;
    default:
      break;
  }
}

void JaamWsComponent::setup() {
  std::string uri = "ws://" + this->host_ + ":" + std::to_string(this->port_) + "/";

  esp_websocket_client_config_t cfg = {};
  cfg.uri = uri.c_str();
  // The map sends nothing while nothing changes, so silence is normal and must
  // not be read as a dead link. Its own integration keeps a 30 s heartbeat;
  // this does the same and lets the client reconnect on its own.
  cfg.reconnect_timeout_ms = 10000;
  cfg.network_timeout_ms = 10000;
  cfg.ping_interval_sec = 30;
  cfg.disable_auto_reconnect = false;
  cfg.buffer_size = 2048;

  this->client_ = esp_websocket_client_init(&cfg);
  if (this->client_ == nullptr) {
    ESP_LOGE(TAG, "could not create client for %s", uri.c_str());
    this->mark_failed();
    return;
  }
  esp_websocket_register_events(this->client_, WEBSOCKET_EVENT_ANY, ws_event_handler, this);
  esp_websocket_client_start(this->client_);
  ESP_LOGI(TAG, "connecting to %s", uri.c_str());
}

void JaamWsComponent::on_ws_data(const char *data, size_t len) {
  // Frames can arrive split across payloads, so bytes accumulate until the
  // JSON parses. The buffer is then cleared whether or not the message was
  // one we care about -- the map also sends system_info, lamp and temperature
  // updates, and keeping the first of those in the buffer glued every later
  // message onto it, after which nothing parsed again. That is why this
  // worked exactly once, on the very first message after connecting.
  this->rx_.append(data, len);
  if (this->rx_.size() > 8192)
    this->rx_.clear();

  uint32_t flags = 0;
  bool found = false;
  const bool parsed = json::parse_json(this->rx_, [&](JsonObject root) -> bool {
    if (!root["home_alert_flags"].isNull()) {
      flags = root["home_alert_flags"].as<uint32_t>();
      found = true;
    }
    return true;
  });
  if (!parsed)
    return;                                  // ще не ціле повідомлення
  this->rx_.clear();
  if (!found)
    return;                                  // повідомлення не про тривогу

  this->flags_ = flags;
  this->pending_ = true;
}

void JaamWsComponent::loop() {
  if (!this->pending_)
    return;
  this->pending_ = false;
  const uint32_t f = this->flags_;
  if (f == this->reported_)
    return;
  this->reported_ = f;
  ESP_LOGI(TAG, "home_alert_flags = 0x%03X", f);
  this->flags_callback_.call(f);
}

void JaamWsComponent::dump_config() {
  ESP_LOGCONFIG(TAG, "JAAM alert map:");
  ESP_LOGCONFIG(TAG, "  Host: %s:%u", this->host_.c_str(), this->port_);
  ESP_LOGCONFIG(TAG, "  Connected: %s", YESNO(this->connected_));
}

}  // namespace jaam_ws
}  // namespace esphome
#endif  // USE_ESP_IDF
