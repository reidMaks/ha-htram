import esphome.codegen as cg
from esphome.components import display
import esphome.config_validation as cv
from esphome.const import CONF_ID, CONF_LAMBDA
from . import HtramGd32Component, htram_gd32_ns

CONF_HTRAM_GD32_ID = "htram_gd32_id"

HtramGd32Display = htram_gd32_ns.class_("HtramGd32Display", display.Display, cg.PollingComponent)

CONFIG_SCHEMA = display.FULL_DISPLAY_SCHEMA.extend({
    cv.GenerateID(): cv.declare_id(HtramGd32Display),
    cv.GenerateID(CONF_HTRAM_GD32_ID): cv.use_id(HtramGd32Component),
})

async def to_code(config):
    hub = await cg.get_variable(config[CONF_HTRAM_GD32_ID])
    var = cg.new_Pvariable(config[CONF_ID])

    display.add_metadata(
        config[CONF_ID],
        width=240,
        height=240,
        has_hardware_rotation=False,
        byte_order=display.BYTE_ORDER_BIG,
    )

    await display.register_display(var, config)
    cg.add(var.set_parent(hub))

    if (lambda_config := config.get(CONF_LAMBDA)) is not None:
        lambda_ = await cg.process_lambda(
            lambda_config, [(display.DisplayRef, "it")], return_type=cg.void
        )
        cg.add(var.set_writer(lambda_))
