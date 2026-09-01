"""Button platform for HTRAM."""
from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HTRAMDataUpdateCoordinator, HtramConfigEntry

async def async_setup_entry(
    hass: HomeAssistant,
    entry: HtramConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the button platform."""
    coordinator = entry.runtime_data
    async_add_entities([HTRAMSyncTimeButton(coordinator)])

class HTRAMSyncTimeButton(CoordinatorEntity, ButtonEntity):
    """Representation of HTRAM Time Sync Button."""

    def __init__(self, coordinator: HTRAMDataUpdateCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_has_entity_name = True
        self._attr_translation_key = "sync_time"
        self._attr_unique_id = f"{coordinator.address}_sync_time"
        self._attr_icon = "mdi:clock-sync"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.address)},
        }

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.coordinator.async_sync_time()
