"""Sensor platform for HTRAM."""
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.const import (
    PERCENTAGE,
    UnitOfRatio,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import device_registry as dr
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
        HTRAMSensor(coordinator, "co2", "CO2", SensorDeviceClass.CO2, UnitOfRatio.PARTS_PER_MILLION),
        HTRAMSensor(coordinator, "temperature", "Temperature", SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS),
        HTRAMSensor(coordinator, "humidity", "Humidity", SensorDeviceClass.HUMIDITY, PERCENTAGE),
        HTRAMSensor(coordinator, "battery", "Battery", SensorDeviceClass.BATTERY, PERCENTAGE),
        HTRAMSourceSensor(coordinator),
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
        self._attr_has_entity_name = True
        self._attr_translation_key = key
        self._attr_unique_id = f"{coordinator.address}_{key}"
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_state_class = SensorStateClass.MEASUREMENT
        
        # Set precision
        if device_class == SensorDeviceClass.TEMPERATURE:
             self._attr_suggested_display_precision = 1
        else:
             self._attr_suggested_display_precision = 0

        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.address)},
            "name": "HTRAM Air Monitor",
            "manufacturer": "Honeywell",
            "model": "HTRAM-RM",
            "connections": {(dr.CONNECTION_BLUETOOTH, coordinator.address)},
        }

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self.coordinator.data.get(self._key)


class HTRAMSourceSensor(CoordinatorEntity, SensorEntity):
    """Which transport the readings are currently arriving on.

    Without this the switch between Bluetooth and MQTT is invisible, and when
    it goes wrong there is nothing to look at. Diagnostic, so it stays out of
    the way until wanted.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "source"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["bluetooth", "mqtt"]

    def __init__(self, coordinator: HTRAMDataUpdateCoordinator) -> None:
        """Initialize the diagnostic sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_source"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.address)},
            "connections": {(dr.CONNECTION_BLUETOOTH, coordinator.address)},
        }

    @property
    def native_value(self) -> str:
        """Return the active source."""
        return self.coordinator.active_source
