"""Config flow for HTRAM integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothServiceInfo,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_ADDRESS
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN, SERVICE_UUID

_LOGGER = logging.getLogger(__name__)

class HTRAMConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HTRAM."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfo | None = None
        self._discovered_device: Any = None
        self._discovered_devices: dict[str, Any] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfo
    ) -> FlowResult:
        """Handle the bluetooth discovery step."""
        _LOGGER.debug(f"Discovered HTRAM device: {discovery_info}")
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        
        self._discovery_info = discovery_info
        
        # Human readable name
        name = discovery_info.name or discovery_info.address
        self.context["title_placeholders"] = {"name": name}

        return await self.async_step_bluetooth_confirm()


    async def _async_verify_connection(self, discovery_info: BluetoothServiceInfo) -> dict[str, str] | None:
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
            # use establish_connection for robust connection
            _LOGGER.debug(f"Establishing connection to {device.address} using bleak_retry_connector")
            async with establish_connection(BleakClient, device, device.address) as client:
                _LOGGER.debug(f"Connection established to {device.address}. Connected: {client.is_connected}")
                if not client.is_connected:
                     return {"base": "cannot_connect"}
                
                # Try to pair if not bonded
                try:
                    _LOGGER.debug(f"Attempting to pair with {device.address}")
                    await client.pair()
                    _LOGGER.debug(f"Pairing successful with {device.address}")
                except (BleakError, Exception) as e:
                    _LOGGER.warning(f"Pairing failed: {e}")
                    pass

                return None

        except BleakError as e:
            _LOGGER.error(f"Could not connect to HTRAM: {e}")
            msg = str(e).lower()
            if "no backend with an available connection slot" in msg:
                return {"base": "adapter_limit_reached"}
            return {"base": "cannot_connect"}
        except Exception as e:
            _LOGGER.exception(f"Unexpected error connecting to HTRAM: {e}")
            return {"base": "unknown"}


    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
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
    ) -> FlowResult:
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
