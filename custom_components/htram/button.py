"""Button platform for HTRAM."""
from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import HTRAMDataUpdateCoordinator, HtramConfigEntry
from .entity import HtramBluetoothEntity, HtramEntity

async def async_setup_entry(
    hass: HomeAssistant,
    entry: HtramConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the button platform."""
    coordinator = entry.runtime_data
    async_add_entities([HTRAMSyncTimeButton(coordinator), HTRAMBleSessionButton(coordinator)])

class HTRAMSyncTimeButton(HtramEntity, ButtonEntity):
    """Representation of HTRAM Time Sync Button."""

    def __init__(self, coordinator: HTRAMDataUpdateCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_translation_key = "sync_time"
        self._attr_unique_id = f"{coordinator.address}_sync_time"
        self._attr_icon = "mdi:clock-sync"

    @property
    def available(self) -> bool:
        """Time sync is only supported over Bluetooth."""
        return self.coordinator.last_update_success and self.coordinator.ble_ok

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.coordinator.async_sync_time()



class HTRAMBleSessionButton(HtramEntity, ButtonEntity):
    """Opens a time-boxed Bluetooth session.

    Not a HtramBluetoothEntity: this is the one control that must stay usable
    precisely when Bluetooth is unavailable, since its whole purpose is to get
    it back.
    """

    def __init__(self, coordinator: HTRAMDataUpdateCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_translation_key = "ble_session"
        self._attr_unique_id = f"{coordinator.address}_ble_session"
        self._attr_icon = "mdi:bluetooth-connect"

    async def async_press(self) -> None:
        """Connect and hold the link long enough to make changes."""
        await self.coordinator.async_start_ble_session()
