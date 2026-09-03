"""Binary Sensor platform for HTRAM."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import HTRAMDataUpdateCoordinator, HtramConfigEntry
from .entity import HtramEntity

async def async_setup_entry(
    hass: HomeAssistant,
    entry: HtramConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensor platform."""
    coordinator = entry.runtime_data
    async_add_entities([
        HTRAMChargingSensor(coordinator),
        HTRAMAlarmSensor(coordinator),
    ])

class HTRAMChargingSensor(HtramEntity, BinarySensorEntity):
    """Representation of HTRAM Charging Status."""

    def __init__(self, coordinator: HTRAMDataUpdateCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        # A literal name plus has_entity_name gave
        # binary_sensor.htram_air_monitor_htram_charging.
        self._attr_translation_key = "charging"
        self._attr_unique_id = f"{coordinator.address}_charging"
        self._attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING

    @property
    def is_on(self) -> bool:
        """Return true if the binary sensor is on."""
        return bool(self.coordinator.data.get("charging", False))

    @property
    def available(self) -> bool:
        """Whether charging status is available.

        Fed by MQTT telemetry when enabled, or by Bluetooth polling otherwise.
        """
        if self.coordinator.data.get("charging") is None:
            return False
        if not self.coordinator.mqtt_enabled and not self.coordinator.ble_ok:
            return False
        return super().available


class HTRAMAlarmSensor(HtramEntity, BinarySensorEntity):
    """Representation of HTRAM CO2 Alarm Status."""

    def __init__(self, coordinator: HTRAMDataUpdateCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_translation_key = "co2_alarm"
        self._attr_unique_id = f"{coordinator.address}_co2_alarm"
        self._attr_device_class = BinarySensorDeviceClass.SAFETY

    @property
    def is_on(self) -> bool:
        """Return true if alarm is active (medium or high risk)."""
        return bool(self.coordinator.data.get("co2_alarm", False))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return risk and alarm level attributes."""
        level = self.coordinator.data.get("alarm_level", 0)
        risk_labels = {0: "low", 1: "medium", 2: "high"}
        return {
            "alarm_level": level,
            "risk_level": risk_labels.get(level, "unknown"),
        }

    @property
    def available(self) -> bool:
        """Whether alarm status is available."""
        if self.coordinator.data.get("co2_alarm") is None:
            return False
        if not self.coordinator.mqtt_enabled and not self.coordinator.ble_ok:
            return False
        return super().available
