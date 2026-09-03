"""The integration running inside Home Assistant.

Everything here exercises the real setup path -- config entry, coordinator,
platforms, entity registry -- with only the Bluetooth radio replaced. That is
the part that cannot be checked by importing modules, and the part where a
mistake shows up as an integration that simply fails to load.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)

from custom_components.htram.const import CONF_MQTT_ENABLED, CONF_SERIAL, DOMAIN

ADDRESS = "94:E6:86:94:36:B2"
SERIAL = "RM1221412257"
TOPIC = f"C/{SERIAL}"

# A payload captured from the device: 650 ppm, 23 C, 58 %.
PAYLOAD = bytes.fromhex("444300020001c818976a5106000c000001042d108a0200173a150c")

# Flipped by the tests that need Bluetooth to be gone.
ble_reachable = True

BLE_READING = {
    "co2": 500,
    "temperature": 21,
    "humidity": 55,
    "battery": 100,
    "charging": False,
    "mute": False,
}


@pytest.fixture
def expected_lingering_timers() -> bool:
    """Tolerate Home Assistant's own MQTT housekeeping timer.

    The mqtt_mock fixture starts the real MQTT client, whose periodic task
    outlives the test. It belongs to Home Assistant, not to this integration.
    """
    return True


@pytest.fixture
def ble_device() -> MagicMock:
    """A stand-in for the monitor, so no radio is needed."""
    device = MagicMock()
    device.address = ADDRESS
    device.name = f"HTRAM-{SERIAL}"
    return device


async def _fake_poll(coordinator):
    """Stand in for a Bluetooth poll that reached the device."""
    coordinator.ble_ok = ble_reachable
    if not ble_reachable:
        return coordinator._tolerate_ble_failure("no radio in tests")
    coordinator.data.update(BLE_READING)
    return coordinator.data


async def setup_entry(
    hass: HomeAssistant, ble_device: MagicMock, options: dict | None = None
) -> MockConfigEntry:
    """Set the integration up the way Home Assistant does."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        title=f"HTRAM-{SERIAL}",
        data={},
        options=options or {},
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "homeassistant.components.bluetooth.async_ble_device_from_address",
            return_value=ble_device,
        ),
        patch(
            "custom_components.htram.coordinator.HTRAMDataUpdateCoordinator._async_update_data",
            autospec=True,
            side_effect=_fake_poll,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    return entry


def state_of(hass: HomeAssistant, key: str) -> str | None:
    """Look an entity up by unique id rather than guessing its entity id."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{ADDRESS}_{key}")
    if entity_id is None:
        return None
    state = hass.states.get(entity_id)
    return state.state if state else None


async def test_entry_sets_up(hass: HomeAssistant, custom_integration, ble_device):
    """The whole point: Home Assistant can actually load this."""
    entry = await setup_entry(hass, ble_device)
    assert entry.state.recoverable is False or entry.state.name == "LOADED"
    assert entry.runtime_data is not None


async def test_sensors_are_created(hass: HomeAssistant, custom_integration, ble_device):
    await setup_entry(hass, ble_device)

    assert state_of(hass, "co2") == "500"
    assert state_of(hass, "temperature") == "21"
    assert state_of(hass, "humidity") == "55"
    assert state_of(hass, "battery") == "100"


async def test_source_starts_on_bluetooth(
    hass: HomeAssistant, custom_integration, ble_device
):
    await setup_entry(hass, ble_device)
    assert state_of(hass, "source") == "bluetooth"


async def test_missing_reading_is_unavailable(
    hass: HomeAssistant, custom_integration, ble_device
):
    """A gap must read as unavailable, not as the last value forever."""
    with patch.dict(BLE_READING, {"co2": None}):
        await setup_entry(hass, ble_device)
        assert state_of(hass, "co2") == "unavailable"


async def test_service_is_registered(
    hass: HomeAssistant, custom_integration, ble_device
):
    await setup_entry(hass, ble_device)
    assert hass.services.has_service(DOMAIN, "configure_device")


async def test_unload(hass: HomeAssistant, custom_integration, ble_device):
    entry = await setup_entry(hass, ble_device)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_mqtt_payload_updates_the_same_sensors(
    hass: HomeAssistant, custom_integration, ble_device, mqtt_mock
):
    """The transports must be interchangeable from the entity's side.

    The sensors are created while Bluetooth is the source, then a telemetry
    payload arrives and the same entities change value. If MQTT created its own
    entities instead, this would fail on the ids.
    """
    await setup_entry(
        hass,
        ble_device,
        options={CONF_MQTT_ENABLED: True, CONF_SERIAL: SERIAL},
    )
    assert state_of(hass, "co2") == "500"

    async_fire_mqtt_message(hass, TOPIC, PAYLOAD)
    await hass.async_block_till_done()

    assert state_of(hass, "co2") == "650"
    assert state_of(hass, "temperature") == "23"
    assert state_of(hass, "humidity") == "58"
    assert state_of(hass, "source") == "mqtt"


async def test_corrupt_mqtt_payload_is_ignored(
    hass: HomeAssistant, custom_integration, ble_device, mqtt_mock
):
    """A bad checksum must not reach the sensors."""
    await setup_entry(
        hass,
        ble_device,
        options={CONF_MQTT_ENABLED: True, CONF_SERIAL: SERIAL},
    )

    corrupt = bytearray(PAYLOAD)
    corrupt[20] ^= 0xFF
    async_fire_mqtt_message(hass, TOPIC, bytes(corrupt))
    await hass.async_block_till_done()

    assert state_of(hass, "co2") == "500"
    assert state_of(hass, "source") == "bluetooth"


async def test_mqtt_not_subscribed_when_disabled(
    hass: HomeAssistant, custom_integration, ble_device, mqtt_mock
):
    """A Bluetooth-only setup must ignore whatever is on the broker."""
    await setup_entry(hass, ble_device)

    async_fire_mqtt_message(hass, TOPIC, PAYLOAD)
    await hass.async_block_till_done()

    assert state_of(hass, "co2") == "500"


async def test_every_entity_belongs_to_the_device(
    hass: HomeAssistant, custom_integration, ble_device
):
    """Entity ids must all carry the device prefix.

    The device description used to be repeated in each platform, and only the
    sensor one named the device. Platforms are set up in an arbitrary order, so
    whichever registered first decided whether the name existed yet -- select
    entities came out as select.temperature_unit.
    """
    entry = await setup_entry(hass, ble_device)
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)

    assert entities, "no entities were created at all"
    orphans = [e.entity_id for e in entities if e.device_id is None]
    assert not orphans, f"entities not attached to the device: {orphans}"

    unprefixed = [
        e.entity_id for e in entities if "htram_air_monitor" not in e.entity_id
    ]
    assert not unprefixed, f"entities without the device prefix: {unprefixed}"


async def test_charging_binary_sensor_exists(
    hass: HomeAssistant, custom_integration, ble_device
):
    """binary_sensor was absent from PLATFORMS, so this never appeared."""
    entry = await setup_entry(hass, ble_device)
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{ADDRESS}_charging"
    )
    assert entity_id is not None
    assert hass.states.get(entity_id).state == "off"


async def test_readings_survive_losing_bluetooth(
    hass: HomeAssistant, custom_integration, ble_device, mqtt_mock
):
    """Losing the radio must not take the MQTT-fed sensors down.

    The monitor advertises in short windows, so Bluetooth is gone most of the
    time. Requiring it took the whole config entry down -- all 12 entities --
    even when telemetry was arriving. Control goes unavailable; readings stay.
    """
    global ble_reachable
    await setup_entry(
        hass, ble_device, options={CONF_MQTT_ENABLED: True, CONF_SERIAL: SERIAL}
    )
    async_fire_mqtt_message(hass, TOPIC, PAYLOAD)
    await hass.async_block_till_done()
    assert state_of(hass, "co2") == "650"

    ble_reachable = False
    try:
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()

        # The entry stays loaded and telemetry keeps feeding the sensors, battery and charging.
        assert entry.state.name == "LOADED"
        assert state_of(hass, "co2") == "650"
        assert state_of(hass, "temperature") == "23"
        assert state_of(hass, "battery") == "100"
        registry = er.async_get(hass)
        charging = registry.async_get_entity_id(
            "binary_sensor", DOMAIN, f"{ADDRESS}_charging"
        )
        assert hass.states.get(charging).state == "on"

        # What genuinely needs the radio (controls) says so.
        switch = registry.async_get_entity_id("switch", DOMAIN, f"{ADDRESS}_mute")
        assert hass.states.get(switch).state == "unavailable"
    finally:
        ble_reachable = True


async def test_polling_is_rare_when_mqtt_supplies_the_readings(
    hass: HomeAssistant, custom_integration, ble_device, mqtt_mock
):
    """Bluetooth should stay out of the way when it is not the source.

    The device suspends telemetry while a Bluetooth session is open, so with
    MQTT configured the radio is only wanted when the user deliberately opens
    a session or calls a control service.
    """
    entry = await setup_entry(
        hass, ble_device, options={CONF_MQTT_ENABLED: True, CONF_SERIAL: SERIAL}
    )
    assert entry.runtime_data.update_interval is None


async def test_polling_stays_frequent_without_mqtt(
    hass: HomeAssistant, custom_integration, ble_device
):
    entry = await setup_entry(hass, ble_device)
    assert entry.runtime_data.update_interval.total_seconds() == 60
