import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import switch

from . import HtramGd32Component, htram_gd32_ns

HtramLedSwitch = htram_gd32_ns.class_("HtramLedSwitch", switch.Switch, cg.Component)

CONF_HTRAM_ID = "htram_gd32_id"

# channel index must match firmware/ESP: 0=red 1=yellow 2=green
_LED_CHANNELS = {
    "red": 0,
    "yellow": 1,
    "green": 2,
}

CONFIG_SCHEMA = cv.Schema({
    cv.GenerateID(CONF_HTRAM_ID): cv.use_id(HtramGd32Component),
    **{
        cv.Optional(name): switch.switch_schema(HtramLedSwitch)
        for name in _LED_CHANNELS
    },
})

def to_code(config):
    hub = yield cg.get_variable(config[CONF_HTRAM_ID])

    for name, channel in _LED_CHANNELS.items():
        if name in config:
            sw = yield switch.new_switch(config[name])
            yield cg.register_component(sw, config[name])
            cg.add(sw.set_parent(hub))
            cg.add(sw.set_channel(channel))
            cg.add(hub.set_led_switch(channel, sw))
