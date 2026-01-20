"""Binary Sensor platform for HTRAM."""
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
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
    """Set up the binary sensor platform."""
    coordinator: HTRAMDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([HTRAMChargingSensor(coordinator)])

class HTRAMChargingSensor(CoordinatorEntity, BinarySensorEntity):
    """Representation of HTRAM Charging Status."""

    def __init__(self, coordinator: HTRAMDataUpdateCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_name = "HTRAM Charging"
        self._attr_unique_id = f"{coordinator.address}_charging"
        self._attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.address)},
        }

    @property
    def is_on(self) -> bool:
        """Return true if the binary sensor is on."""
        return self.coordinator.data.get("charging", False)
