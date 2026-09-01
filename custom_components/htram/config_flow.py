"""Config flow for HTRAM integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback

from .const import CONF_MQTT_ENABLED, CONF_SERIAL, DOMAIN, SERVICE_UUID

_LOGGER = logging.getLogger(__name__)

def serial_from_name(name: str | None) -> str:
    """Pull the serial number out of an advertised name like HTRAM-RM1221412257."""
    if not name:
        return ""
    _, _, serial = name.partition("-")
    return serial or ""


class HTRAMConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HTRAM."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return HTRAMOptionsFlow()

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_device: Any = None
        self._discovered_devices: dict[str, Any] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle the bluetooth discovery step."""
        _LOGGER.debug(f"Discovered HTRAM device: {discovery_info}")
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        
        self._discovery_info = discovery_info
        
        # Human readable name
        name = discovery_info.name or discovery_info.address
        self.context["title_placeholders"] = {"name": name}

        return await self.async_step_bluetooth_confirm()


    async def _async_verify_connection(self, discovery_info: BluetoothServiceInfoBleak) -> dict[str, str] | None:
        """Verify we can connect and pair with the device."""
        from bleak import BleakClient, BleakError
        from bleak_retry_connector import establish_connection
        import asyncio

        _LOGGER.debug(f"Verifying connection to {discovery_info.address}")
        device = bluetooth.async_ble_device_from_address(
            self.hass, discovery_info.address, connectable=True
        )
        if not device:
             _LOGGER.error(f"Device {discovery_info.address} not found in bluetooth cache")
             return {"base": "cannot_connect"}

        try:
            # Revert to standard BleakClient for initial setup to avoid retry-connector complexity with pairing
            # establish_connection can sometimes mask pairing needs or timeout differently
            _LOGGER.debug(f"Establishing connection to {device.address} using BleakClient")
            async with BleakClient(device, timeout=20.0) as client:
                 _LOGGER.debug(f"Connection established to {device.address}. Connected: {client.is_connected}")
                 if not client.is_connected:
                      return {"base": "cannot_connect"}
                 
                 # Implicit Pairing Strategy
                 # On Linux/BlueZ, accessing a secure characteristic or enabling notifications
                 # often triggers the pairing process more reliably than an explicit pair() call,
                 # which can conflict if the OS is already initiating bonding during service discovery.
                 try:
                     _LOGGER.debug(f"Attempting to start notify on {device.address} to trigger auth")
                     # We use the actual notify UUID. If it requires encryption, this triggers pairing.
                     # We define a dummy handler just for this check.
                     def _dummy_handler(sender, data):
                         pass
                     
                     from .const import NOTIFY_UUID
                     await client.start_notify(NOTIFY_UUID, _dummy_handler)
                     _LOGGER.debug("Notifications enabled successfully")
                     # Give a moment for any auth processes to settle
                     await asyncio.sleep(2) 
                     await client.stop_notify(NOTIFY_UUID)
                     
                 except (BleakError, Exception) as e:
                     _LOGGER.warning(f"Notify setup warning (might need pairing): {e}")
                     # If this failed, it might be because we need pairing but the prompt hasn't been answered yet.
                     # We'll just catch it; correct timeout logic above usually handles the user delay.
                     pass

                 return None


        except BleakError as e:
            _LOGGER.error(f"Could not connect to HTRAM: {e}")
            msg = str(e).lower()
            if "no backend with an available connection slot" in msg:
                return {"base": "adapter_limit_reached"}
            if "failed to discover services" in msg:
                return {"base": "pairing_failed"} # This usually means pairing didn't complete in time
            return {"base": "cannot_connect"}
        except Exception as e:
            _LOGGER.exception(f"Unexpected error connecting to HTRAM: {e}")
            return {"base": "unknown"}


    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm discovery."""
        errors: dict[str, str] = {}
        
        if user_input is not None:
             errors_or_none = await self._async_verify_connection(self._discovery_info)
             if not errors_or_none:
                 return self.async_create_entry(
                    title=self._discovery_info.name or self._discovery_info.address,
                    data={},
                )
             errors = errors_or_none

        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={
                "name": self._discovery_info.name or self._discovery_info.address
            },
            errors=errors,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the user step to pick discovered device."""
        errors: dict[str, str] = {}
        
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            
            # Find the discovery info for this address
            discovery_info = self._discovered_devices.get(address)
            if not discovery_info:
                 return self.async_abort(reason="no_devices_found")
            
            errors_or_none = await self._async_verify_connection(discovery_info)
            if not errors_or_none:
                return self.async_create_entry(
                    title=discovery_info.name or discovery_info.address,
                    data={},
                )
            errors = errors_or_none

        # Scan for devices with our Service UUID
        current_addresses = self._async_current_ids()
        for discovery_info in async_discovered_service_info(self.hass):
            if (
                discovery_info.address in current_addresses
                or discovery_info.address in self._discovered_devices
            ):
                continue

            # Check if it matches our device (Service UUID or Name prefix)
            # Service UUID check
            if SERVICE_UUID.lower() in discovery_info.service_uuids or SERVICE_UUID.upper() in discovery_info.service_uuids:
                 self._discovered_devices[discovery_info.address] = discovery_info
            # Name Check backup
            elif discovery_info.name and (discovery_info.name.startswith("HTRAM") or discovery_info.name.startswith("Storm_Shadow")):
                 self._discovered_devices[discovery_info.address] = discovery_info

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        titles = {
            address: (discovery.name or address)
            for address, discovery in self._discovered_devices.items()
        }
        
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_ADDRESS): vol.In(titles),
            }),
            errors=errors,
        )


class HTRAMOptionsFlow(OptionsFlow):
    """Options: where the readings come from.

    Bluetooth works with no setup at all, so it stays the default. MQTT is
    offered here rather than during initial setup because it needs a broker the
    device can reach on port 443 with an anonymous WebSocket listener -- real
    work, and not something to ask about while someone is adding a device.

    Turning it on creates no entities. The existing CO2, temperature and
    humidity sensors keep their ids and history and simply change what feeds
    them.
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show and store the options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if user_input.get(CONF_MQTT_ENABLED) and not user_input.get(CONF_SERIAL, "").strip():
                errors[CONF_SERIAL] = "serial_required"
            else:
                return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        suggested_serial = options.get(CONF_SERIAL) or serial_from_name(self.config_entry.title)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MQTT_ENABLED,
                        default=options.get(CONF_MQTT_ENABLED, False),
                    ): bool,
                    vol.Optional(CONF_SERIAL, default=suggested_serial): str,
                }
            ),
            errors=errors,
        )
