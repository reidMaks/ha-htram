"""Number platform for HTRAM."""
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import HTRAMDataUpdateCoordinator, HtramConfigEntry
from .entity import HtramBluetoothEntity

async def async_setup_entry(
    hass: HomeAssistant,
    entry: HtramConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the number platform."""
    coordinator = entry.runtime_data
    async_add_entities([
        HTRAMAlarmLowNumber(coordinator),
        HTRAMAlarmHighNumber(coordinator),
    ])



class HTRAMAlarmLowNumber(HtramBluetoothEntity, NumberEntity):
    """Representation of HTRAM CO2 Alarm Low Threshold."""

    def __init__(self, coordinator: HTRAMDataUpdateCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_translation_key = "alarm_low"
        self._attr_unique_id = f"{coordinator.address}_alarm_low"
        self._attr_native_step = 50
        self._attr_native_min_value = 400
        self._attr_native_max_value = 1500  # Practical limits
        self._attr_mode = NumberMode.BOX

    async def async_added_to_hass(self) -> None:
        """Restore state if coordinator does not have settings from Bluetooth."""
        await super().async_added_to_hass()
        if not self.coordinator.ble_ok or "alarm_low" not in self.coordinator.data:
            if (last_state := await self.async_get_last_state()) is not None:
                try:
                    self.coordinator.data["alarm_low"] = int(float(last_state.state))
                except (ValueError, TypeError):
                    pass
            if "alarm_low" not in self.coordinator.data:
                self.coordinator.data["alarm_low"] = 800

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get("alarm_low", 800)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_alarm_thresholds(low=int(value))


class HTRAMAlarmHighNumber(HtramBluetoothEntity, NumberEntity):
    """Representation of HTRAM CO2 Alarm High Threshold."""

    def __init__(self, coordinator: HTRAMDataUpdateCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_translation_key = "alarm_high"
        self._attr_unique_id = f"{coordinator.address}_alarm_high"
        self._attr_native_step = 50
        self._attr_native_min_value = 800
        self._attr_native_max_value = 5000
        self._attr_mode = NumberMode.BOX

    async def async_added_to_hass(self) -> None:
        """Restore state if coordinator does not have settings from Bluetooth."""
        await super().async_added_to_hass()
        if not self.coordinator.ble_ok or "alarm_high" not in self.coordinator.data:
            if (last_state := await self.async_get_last_state()) is not None:
                try:
                    self.coordinator.data["alarm_high"] = int(float(last_state.state))
                except (ValueError, TypeError):
                    pass
            if "alarm_high" not in self.coordinator.data:
                self.coordinator.data["alarm_high"] = 1000

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get("alarm_high", 1000)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_alarm_thresholds(high=int(value))

