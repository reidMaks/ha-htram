"""Select platform for HTRAM."""
from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
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
    """Set up the select platform."""
    coordinator: HTRAMDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([HTRAMTempUnitSelect(coordinator)])

class HTRAMTempUnitSelect(CoordinatorEntity, SelectEntity):
    """Representation of HTRAM Temperature Unit Select."""

    def __init__(self, coordinator: HTRAMDataUpdateCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_name = "HTRAM Temperature Unit"
        self._attr_unique_id = f"{coordinator.address}_temp_unit"
        self._attr_options = ["Celsius", "Fahrenheit"]
        self._attr_icon = "mdi:thermometer-cog"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.address)},
        }

    @property
    def current_option(self) -> str | None:
        """Return the current option."""
        unit = self.coordinator.data.get("temp_unit", "C")
        return "Celsius" if unit == "C" else "Fahrenheit"

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        is_c = option == "Celsius"
        await self.coordinator.async_set_temp_unit(is_c)
