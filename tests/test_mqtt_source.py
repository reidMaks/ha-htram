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


def test_provisioning_sends_radio_mode_before_credentials():
    """The order is the whole trick.

    Credentials sent while the device is still in BLE mode are acknowledged
    and ignored, which is indistinguishable from success unless the order is
    checked.
    """
    import asyncio

    sent: list[bytes] = []
    coord = object.__new__(Coordinator)

    async def fake_send(packet):
        sent.append(packet)

    async def fake_cleanup():
        sent.append(b"<disconnect>")

    coord._send_command = fake_send
    coord._cleanup_client = fake_cleanup

    asyncio.run(
        coord.async_provision(
            ssid="net",
            password="pw",
            mqtt_server="tcp://mqtt.example",
            aes_key="MDEyMzQ1Njc4OWFiY2RlZg==",
            aes_iv="0123456789abcdef",
        )
    )

    opcodes = [p[4:6].hex() for p in sent if p != b"<disconnect>"]
    assert opcodes == ["7458", "20b0", "7460"]
    assert sent[-1] == b"<disconnect>", "the link must be dropped for the join to happen"


def test_provisioning_rejects_a_server_without_keys():
    """The endpoint and the key share one frame, so half of it is not valid."""
    import asyncio

    from homeassistant.exceptions import HomeAssistantError

    coord = object.__new__(Coordinator)

    async def fake_send(packet):
        pass

    coord._send_command = fake_send

    with pytest.raises(HomeAssistantError):
        asyncio.run(
            coord.async_provision(
                ssid="net", password="pw", mqtt_server="tcp://mqtt.example"
            )
        )
