"""Shared entity base.

The device description lives here rather than in each platform. It was
duplicated before, and not identically: only the sensor platform named the
device. Platforms are set up in whatever order Home Assistant chooses, so
whenever one of the others registered first the device existed without a name
and its entities came out as ``select.temperature_unit`` instead of
``select.htram_air_monitor_temperature_unit``.
"""

from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HTRAMDataUpdateCoordinator


class HtramEntity(CoordinatorEntity[HTRAMDataUpdateCoordinator]):
    """Base for every entity this integration creates."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HTRAMDataUpdateCoordinator) -> None:
        """Attach the entity to the one device."""
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            connections={(dr.CONNECTION_BLUETOOTH, coordinator.address)},
            name="HTRAM Air Monitor",
            manufacturer="Honeywell",
            model="HTRAM-RM",
        )


class HtramBluetoothEntity(HtramEntity):
    """For entities only Bluetooth can supply or drive.

    The controls, the battery and the charging flag have no MQTT equivalent:
    the telemetry payload carries readings and nothing else. When the radio is
    gone these report unavailable on their own, while the CO2, temperature and
    humidity sensors carry on from MQTT.
    """

    @property
    def available(self) -> bool:
        """Whether Bluetooth reached the device on the last attempt."""
        return super().available and self.coordinator.ble_ok
