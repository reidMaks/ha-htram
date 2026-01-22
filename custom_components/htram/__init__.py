"""The HTRAM integration."""
import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN
from .coordinator import HTRAMDataUpdateCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH, Platform.NUMBER, Platform.SELECT, Platform.BUTTON]

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up HTRAM from a config entry."""
    address = entry.unique_id
    assert address is not None

    ble_device = bluetooth.async_ble_device_from_address(hass, address.upper(), connectable=True)
    if not ble_device:
        raise ConfigEntryNotReady(f"Could not find HTRAM device with address {address}")

    coordinator = HTRAMDataUpdateCoordinator(hass, ble_device)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register Service
    async def handle_configure_device(call):
        """Handle the service call."""
        ssid = call.data.get("ssid")
        password = call.data.get("password")
        mqtt_server = call.data.get("mqtt_server")
        aes_key = call.data.get("aes_key")
        aes_iv = call.data.get("aes_iv")

        # Find the coordinator. In a real scenario, user should target a device/entity.
        # But for now, we'll try to find the coordinator associated with the service call context
        # or just pick the first one if global? Service calls usually target an entity.
        # Let's assume the user targets an entity or device, but standard HA service calls need target resolution.
        # Simplification: We iterate over all loaded coordinators and apply to all? 
        # Or better: Require device_id/entity_id?
        # Standard approach: register service at platform level or use helper to get coordinator.
        
        # For this custom component, let's iterate all entries for now (assuming 1 device usually)
        # or rely on the user to pick the right one if we implemented entity services.
        # But we are registering a DOMAIN service.
        
        for entry_id, coord in hass.data[DOMAIN].items():
            if mqtt_server and aes_key and aes_iv:
                await coord.async_provision_mqtt(mqtt_server, aes_key, aes_iv)
                # Small delay between commands
                await asyncio.sleep(1)
            
            if ssid and password:
                await coord.async_provision_wifi(ssid, password)

    import voluptuous as vol
    from homeassistant.helpers import config_validation as cv

    SERVICE_SCHEMA = vol.Schema({
        vol.Optional("ssid"): cv.string,
        vol.Optional("password"): cv.string,
        vol.Optional("mqtt_server"): cv.string,
        vol.Optional("aes_key"): cv.string,
        vol.Optional("aes_iv"): cv.string,
    })

    hass.services.async_register(DOMAIN, "configure_device", handle_configure_device, schema=SERVICE_SCHEMA)

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
