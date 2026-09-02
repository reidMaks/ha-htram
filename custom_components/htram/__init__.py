"""The HTRAM integration."""
import logging

import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_ID, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import CONF_MQTT_ENABLED, CONF_SERIAL, DOMAIN
from .coordinator import HTRAMDataUpdateCoordinator, HtramConfigEntry
from .mqtt_source import HtramMqttSource

# binary_sensor was missing here, so the charging sensor the platform defines
# was never created.
PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

_LOGGER = logging.getLogger(__name__)


SERVICE_CONFIGURE = "configure_device"
SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required("ssid"): cv.string,
        vol.Required("password"): cv.string,
        vol.Optional("mqtt_server"): cv.string,
        vol.Optional("aes_key"): cv.string,
        vol.Optional("aes_iv"): cv.string,
    }
)


def _coordinator_for_device(hass: HomeAssistant, device_id: str):
    """Resolve a targeted device to its coordinator."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise HomeAssistantError(f"No such device: {device_id}")

    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry and entry.domain == DOMAIN and hasattr(entry, "runtime_data"):
            return entry.runtime_data

    raise HomeAssistantError(
        f"Device {device.name or device_id} is not a loaded HTRAM device"
    )


async def async_setup(hass: HomeAssistant, config) -> bool:
    """Register the provisioning service once, not once per device.

    It takes a device target rather than applying to every configured monitor,
    which is what the previous version did.
    """

    async def handle_configure(call: ServiceCall) -> None:
        coordinator = _coordinator_for_device(hass, call.data[ATTR_DEVICE_ID])
        await coordinator.async_provision(
            ssid=call.data["ssid"],
            password=call.data["password"],
            mqtt_server=call.data.get("mqtt_server"),
            aes_key=call.data.get("aes_key"),
            aes_iv=call.data.get("aes_iv"),
        )

    hass.services.async_register(
        DOMAIN, SERVICE_CONFIGURE, handle_configure, schema=SERVICE_SCHEMA
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HtramConfigEntry) -> bool:
    """Set up HTRAM from a config entry."""
    address = entry.unique_id
    assert address is not None

    mqtt_enabled = entry.options.get(CONF_MQTT_ENABLED, False)

    # Requiring a visible Bluetooth device here used to take the whole entry
    # down -- every sensor, including the ones MQTT feeds. The monitor
    # advertises only in short windows, so that happened routinely. With MQTT
    # configured the readings arrive regardless; only the controls need the
    # radio, and they report their own unavailability.
    if not mqtt_enabled and not bluetooth.async_ble_device_from_address(
        hass, address.upper(), connectable=True
    ):
        raise ConfigEntryNotReady(
            f"{address} is not advertising. Press the button on the device, or "
            f"configure MQTT so readings do not depend on Bluetooth"
        )

    coordinator = HTRAMDataUpdateCoordinator(hass, entry, address.upper())
    coordinator.mqtt_enabled = mqtt_enabled
    if mqtt_enabled:
        # Must not raise: a failed Bluetooth poll is expected here.
        await coordinator.async_refresh()
    else:
        await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    # Telemetry over MQTT is opt-in: it needs a broker the device can reach,
    # which is a good deal of setup, so nothing here assumes it.
    if coordinator.mqtt_enabled:
        serial = entry.options.get(CONF_SERIAL)
        if serial:
            source = HtramMqttSource(hass, coordinator, serial)
            if await source.async_start():
                entry.async_on_unload(source.async_stop)
        else:
            _LOGGER.error(
                "MQTT telemetry is enabled but no serial number is configured; "
                "reconfigure the integration"
            )

    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options_change))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True

async def _async_reload_on_options_change(hass: HomeAssistant, entry: HtramConfigEntry) -> None:
    """Re-run setup so a changed data source takes effect immediately."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: HtramConfigEntry) -> bool:
    """Unload a config entry.

    runtime_data goes away with the entry, so there is nothing to pop.
    """
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
