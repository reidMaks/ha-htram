import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import binary_sensor
from esphome.const import (
    DEVICE_CLASS_PLUG,
    DEVICE_CLASS_BATTERY_CHARGING,
)

from . import HtramGd32Component

CONF_HTRAM_ID = "htram_gd32_id"
CONF_USB = "usb"
CONF_CHARGING = "charging"
CONF_BUTTON = "button"

CONFIG_SCHEMA = cv.Schema({
    cv.GenerateID(CONF_HTRAM_ID): cv.use_id(HtramGd32Component),
    cv.Optional(CONF_USB): binary_sensor.binary_sensor_schema(
        device_class=DEVICE_CLASS_PLUG,
    ),
    cv.Optional(CONF_CHARGING): binary_sensor.binary_sensor_schema(
        device_class=DEVICE_CLASS_BATTERY_CHARGING,
    ),
    cv.Optional(CONF_BUTTON): binary_sensor.binary_sensor_schema(),
})

def to_code(config):
    hub = yield cg.get_variable(config[CONF_HTRAM_ID])

    if CONF_USB in config:
        bs = yield binary_sensor.new_binary_sensor(config[CONF_USB])
        cg.add(hub.set_usb_binary_sensor(bs))
    if CONF_CHARGING in config:
        bs = yield binary_sensor.new_binary_sensor(config[CONF_CHARGING])
        cg.add(hub.set_charging_binary_sensor(bs))
    if CONF_BUTTON in config:
        bs = yield binary_sensor.new_binary_sensor(config[CONF_BUTTON])
        cg.add(hub.set_button_binary_sensor(bs))
