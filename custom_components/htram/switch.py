"""Switch platform for HTRAM."""
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
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
    """Set up the switch platform."""
    coordinator: HTRAMDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([HTRAMMuteSwitch(coordinator)])

class HTRAMMuteSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of HTRAM Mute Switch."""

    def __init__(self, coordinator: HTRAMDataUpdateCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_name = "HTRAM Mute"
        self._attr_unique_id = f"{coordinator.address}_mute"
        self._attr_icon = "mdi:volume-off"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.address)},
        }

    @property
    def is_on(self) -> bool:
        """Return true if switch is on.
        Note: Switch ON means MUTE IS ACTIVE (Silent).
        Switch OFF means MUTE IS INACTIVE (Sound is ON).
        Wait, standard UX: "Mute" switch ON = No Sound.
        """
        # data["mute"] is boolean (True = Muted/Off, False = On)
        # Verify coordinator logic: `is_off = data[9] == 0`. 
        # App shows "Mute State" switch. If switch is ON -> Mute is ON (Sound OFF).
        return self.coordinator.data.get("mute", False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on (Mute)."""
        await self.coordinator.async_set_mute(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off (Unmute)."""
        await self.coordinator.async_set_mute(False)
