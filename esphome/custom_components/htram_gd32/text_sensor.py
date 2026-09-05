import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import text_sensor

from . import HtramGd32Component

CONF_HTRAM_ID = "htram_gd32_id"
CONF_FIRMWARE_VERSION = "firmware_version"
CONF_BUTTON_ACTION = "button_action"

CONFIG_SCHEMA = cv.Schema({
    cv.GenerateID(CONF_HTRAM_ID): cv.use_id(HtramGd32Component),
    cv.Optional(CONF_FIRMWARE_VERSION): text_sensor.text_sensor_schema(
        icon="mdi:chip",
    ),
    cv.Optional(CONF_BUTTON_ACTION): text_sensor.text_sensor_schema(
        icon="mdi:gesture-tap-button",
    ),
})

def to_code(config):
    hub = yield cg.get_variable(config[CONF_HTRAM_ID])

    if CONF_FIRMWARE_VERSION in config:
        ts = yield text_sensor.new_text_sensor(config[CONF_FIRMWARE_VERSION])
        cg.add(hub.set_fw_version_sensor(ts))
    if CONF_BUTTON_ACTION in config:
        ts = yield text_sensor.new_text_sensor(config[CONF_BUTTON_ACTION])
        cg.add(hub.set_button_action_sensor(ts))
