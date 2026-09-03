"""Select platform for HTRAM."""
from homeassistant.components.select import SelectEntity
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import HTRAMDataUpdateCoordinator, HtramConfigEntry
from .entity import HtramBluetoothEntity

async def async_setup_entry(
    hass: HomeAssistant,
    entry: HtramConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the select platform."""
    coordinator = entry.runtime_data
    async_add_entities([
        HTRAMTempUnitSelect(coordinator),
        HTRAMScreenOffSelect(coordinator),
    ])

class HTRAMTempUnitSelect(HtramBluetoothEntity, SelectEntity):
    """Representation of HTRAM Temperature Unit Select."""

    def __init__(self, coordinator: HTRAMDataUpdateCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_translation_key = "temp_unit"
        self._attr_unique_id = f"{coordinator.address}_temp_unit"
        self._attr_options = ["Celsius", "Fahrenheit"]
        self._attr_icon = "mdi:thermometer-cog"

    async def async_added_to_hass(self) -> None:
        """Restore state if coordinator does not have settings from Bluetooth."""
        await super().async_added_to_hass()
        if not self.coordinator.ble_ok or "temp_unit" not in self.coordinator.data:
            if (last_state := await self.async_get_last_state()) is not None:
                if last_state.state == "Fahrenheit":
                    self.coordinator.data["temp_unit"] = "F"
                elif last_state.state == "Celsius":
                    self.coordinator.data["temp_unit"] = "C"
            if "temp_unit" not in self.coordinator.data:
                self.coordinator.data["temp_unit"] = "C"

    @property
    def current_option(self) -> str | None:
        """Return the current option."""
        unit = self.coordinator.data.get("temp_unit", "C")
        return "Celsius" if unit == "C" else "Fahrenheit"

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        is_c = option == "Celsius"
        await self.coordinator.async_set_temp_unit(is_c)


class HTRAMScreenOffSelect(HtramBluetoothEntity, SelectEntity):
    """Representation of HTRAM Screen Off Select."""

    def __init__(self, coordinator: HTRAMDataUpdateCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_translation_key = "screen_off"
        self._attr_unique_id = f"{coordinator.address}_screen_off"
        self._attr_options = ["Always On", "Auto Off (2 min)"]
        self._attr_icon = "mdi:monitor-off"

    async def async_added_to_hass(self) -> None:
        """Restore state if coordinator does not have settings from Bluetooth."""
        await super().async_added_to_hass()
        if not self.coordinator.ble_ok or "screen_off" not in self.coordinator.data:
            if (last_state := await self.async_get_last_state()) is not None:
                if last_state.state == "Auto Off (2 min)":
                    self.coordinator.data["screen_off"] = 120
                elif last_state.state == "Always On":
                    self.coordinator.data["screen_off"] = 0
            if "screen_off" not in self.coordinator.data:
                self.coordinator.data["screen_off"] = 0

    @property
    def current_option(self) -> str | None:
        """Return the current option."""
        val = self.coordinator.data.get("screen_off", 0)
        return "Always On" if val == 0 else "Auto Off (2 min)"

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        val = 0 if option == "Always On" else 120
        await self.coordinator.async_set_screen_off(val)

