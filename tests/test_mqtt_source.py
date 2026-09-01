"""Tests for the MQTT data path.

The coordinator is exercised without a running Home Assistant: the parts under
test here are the source-selection and staleness rules, which are plain state
machines and deserve to be pinned as such.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components"))
sys.path.insert(0, str(ROOT / "custom_components" / "htram"))

import protocol  # noqa: E402
from htram.config_flow import serial_from_name  # noqa: E402
from htram.const import MQTT_KEYS, MQTT_STALE_AFTER, TELEMETRY_TOPIC  # noqa: E402
from htram.coordinator import HTRAMDataUpdateCoordinator as Coordinator  # noqa: E402

SAMPLE = bytes.fromhex("444300020001c818976a5106000c000001042d108a0200173a150c")


@pytest.fixture
def coordinator(monkeypatch):
    """A coordinator with its Home Assistant machinery stubbed out."""
    coord = object.__new__(Coordinator)
    coord.data = {}
    coord.mqtt_enabled = False
    coord._mqtt_last_seen = None
    coord.async_set_updated_data = MagicMock()
    return coord


def test_topic_uses_the_serial():
    assert TELEMETRY_TOPIC.format(serial="RM1221412257") == "C/RM1221412257"


def test_source_is_bluetooth_until_mqtt_arrives(coordinator):
    assert coordinator.active_source == "bluetooth"
    coordinator.mqtt_enabled = True
    assert coordinator.active_source == "bluetooth"


def test_telemetry_switches_the_source_and_notifies(coordinator):
    coordinator.mqtt_enabled = True
    coordinator.async_apply_telemetry(protocol.decode_telemetry(SAMPLE))

    assert coordinator.active_source == "mqtt"
    assert coordinator.data["co2"] == 650
    assert coordinator.data["temperature"] == 23
    assert coordinator.data["humidity"] == 58
    coordinator.async_set_updated_data.assert_called_once()


def test_telemetry_uses_the_same_keys_as_bluetooth(coordinator):
    """Entities must not be able to tell the transports apart."""
    coordinator.mqtt_enabled = True
    coordinator.async_apply_telemetry(protocol.decode_telemetry(SAMPLE))
    assert set(MQTT_KEYS) <= set(coordinator.data)


def test_stale_readings_expire(coordinator, monkeypatch):
    coordinator.mqtt_enabled = True
    coordinator.async_apply_telemetry(protocol.decode_telemetry(SAMPLE))

    import htram.coordinator as mod

    now = mod.time.monotonic()
    monkeypatch.setattr(mod.time, "monotonic", lambda: now + MQTT_STALE_AFTER + 1)

    assert coordinator.active_source == "bluetooth"
    coordinator._expire_stale_mqtt()
    assert all(coordinator.data[key] is None for key in MQTT_KEYS)


def test_fresh_readings_are_left_alone(coordinator):
    coordinator.mqtt_enabled = True
    coordinator.async_apply_telemetry(protocol.decode_telemetry(SAMPLE))
    coordinator._expire_stale_mqtt()
    assert coordinator.data["co2"] == 650


def test_expiry_does_nothing_when_mqtt_is_off(coordinator):
    """Bluetooth-only setups must never have their readings cleared."""
    coordinator.data["co2"] = 700
    coordinator._expire_stale_mqtt()
    assert coordinator.data["co2"] == 700


def test_serial_from_advertised_name():
    assert serial_from_name("HTRAM-RM1221412257") == "RM1221412257"
    assert serial_from_name("HTRAM-") == ""
    assert serial_from_name(None) == ""
    assert serial_from_name("94:E6:86:94:36:B2") == ""
