"""Number platform for HTRAM."""
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HTRAMDataUpdateCoordinator

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the number platform."""
    coordinator: HTRAMDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        HTRAMScreenOffNumber(coordinator),
        HTRAMAlarmLowNumber(coordinator),
        HTRAMAlarmHighNumber(coordinator),
    ])

class HTRAMScreenOffNumber(CoordinatorEntity, NumberEntity):
    """Representation of HTRAM Screen Off Timer."""

    def __init__(self, coordinator: HTRAMDataUpdateCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_name = "HTRAM Screen Off Timer"
        self._attr_unique_id = f"{coordinator.address}_screen_off"
        self._attr_native_unit_of_measurement = UnitOfTime.MINUTES
        self._attr_native_min_value = 0
        self._attr_native_max_value = 120 # Assuming 120 minutes max or just a flag?
        self._attr_native_step = 10 # Step by 10 makes sense?
        self._attr_mode = NumberMode.BOX
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.address)},
        }

    @property
    def native_value(self) -> float | None:
        """Return the value."""
        return self.coordinator.data.get("screen_off", 0)

    async def async_set_native_value(self, value: float) -> None:
        """Set the value."""
        await self.coordinator.async_set_screen_off(int(value))

class HTRAMAlarmLowNumber(CoordinatorEntity, NumberEntity):
    """Representation of HTRAM CO2 Alarm Low Threshold."""

    def __init__(self, coordinator: HTRAMDataUpdateCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_name = "HTRAM CO2 Alarm Low"
        self._attr_unique_id = f"{coordinator.address}_alarm_low"
        self._attr_native_step = 50
        self._attr_native_min_value = 400
        self._attr_native_max_value = 1500 # Practical limits
        self._attr_mode = NumberMode.BOX
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.address)},
        }

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get("alarm_low", 800)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_alarm_thresholds(low=int(value))

class HTRAMAlarmHighNumber(CoordinatorEntity, NumberEntity):
    """Representation of HTRAM CO2 Alarm High Threshold."""

    def __init__(self, coordinator: HTRAMDataUpdateCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_name = "HTRAM CO2 Alarm High"
        self._attr_unique_id = f"{coordinator.address}_alarm_high"
        self._attr_native_step = 50
        self._attr_native_min_value = 800
        self._attr_native_max_value = 5000
        self._attr_mode = NumberMode.BOX
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.address)},
        }

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get("alarm_high", 1000)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_alarm_thresholds(high=int(value))
