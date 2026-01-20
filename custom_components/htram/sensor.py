"""Sensor platform for HTRAM."""
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONCENTRATION_PARTS_PER_MILLION,
    PERCENTAGE,
    UnitOfTemperature,
)
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
    """Set up the sensor platform."""
    coordinator: HTRAMDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    
    entities = [
        HTRAMSensor(coordinator, "co2", "CO2", SensorDeviceClass.CO2, CONCENTRATION_PARTS_PER_MILLION),
        HTRAMSensor(coordinator, "temperature", "Temperature", SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS),
        HTRAMSensor(coordinator, "humidity", "Humidity", SensorDeviceClass.HUMIDITY, PERCENTAGE),
        HTRAMSensor(coordinator, "battery", "Battery", SensorDeviceClass.BATTERY, PERCENTAGE),
    ]
    async_add_entities(entities)

class HTRAMSensor(CoordinatorEntity, SensorEntity):
    """Representation of a HTRAM Sensor."""

    def __init__(
        self,
        coordinator: HTRAMDataUpdateCoordinator,
        key: str,
        name: str,
        device_class: SensorDeviceClass,
        unit: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._key = key
        self._attr_name = f"HTRAM {name}"
        self._attr_unique_id = f"{coordinator.address}_{key}"
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.address)},
            "name": "HTRAM Air Monitor",
            "manufacturer": "Honeywell",
            "model": "HTRAM-RM", # Should probably fetch from SKU if possible
        }

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self.coordinator.data.get(self._key)
