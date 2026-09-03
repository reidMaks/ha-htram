"""Switch platform for HTRAM."""
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import HTRAMDataUpdateCoordinator, HtramConfigEntry
from .entity import HtramBluetoothEntity

async def async_setup_entry(
    hass: HomeAssistant,
    entry: HtramConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the switch platform."""
    coordinator = entry.runtime_data
    async_add_entities([HTRAMMuteSwitch(coordinator)])

class HTRAMMuteSwitch(HtramBluetoothEntity, SwitchEntity):
    """Representation of HTRAM Mute Switch."""

    def __init__(self, coordinator: HTRAMDataUpdateCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_translation_key = "mute"
        self._attr_unique_id = f"{coordinator.address}_mute"
        self._attr_icon = "mdi:volume-off"

    async def async_added_to_hass(self) -> None:
        """Restore state if coordinator does not have settings from Bluetooth."""
        await super().async_added_to_hass()
        if not self.coordinator.ble_ok or "mute" not in self.coordinator.data:
            if (last_state := await self.async_get_last_state()) is not None:
                if last_state.state == "on":
                    self.coordinator.data["mute"] = True
                elif last_state.state == "off":
                    self.coordinator.data["mute"] = False
            if "mute" not in self.coordinator.data:
                self.coordinator.data["mute"] = False

    @property
    def is_on(self) -> bool:
        """Return true if switch is on.
        Note: Switch ON means MUTE IS ACTIVE (Silent).
        Switch OFF means MUTE IS INACTIVE (Sound is ON).
        """
        return self.coordinator.data.get("mute", False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on (Mute)."""
        await self.coordinator.async_set_mute(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off (Unmute)."""
        await self.coordinator.async_set_mute(False)
