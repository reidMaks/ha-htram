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
    coord._session_release = None
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
    assert coordinator.data["battery"] == 100
    assert coordinator.data["charging"] is True
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


def test_options_flow_offers_both_steps():
    """The menu must name the steps the translations describe."""
    import asyncio

    from htram.config_flow import HTRAMOptionsFlow

    flow = object.__new__(HTRAMOptionsFlow)
    flow.async_show_menu = lambda **kw: kw
    result = asyncio.run(flow.async_step_init())
    assert result["menu_options"] == ["data_source", "provision"]


def test_provision_step_reuses_stored_keys():
    """Re-provisioning must not mint new AES material.

    The values are arbitrary, but changing them on every run would mean the
    device and any record of it drift apart for no reason.
    """
    import asyncio

    from htram.config_flow import HTRAMOptionsFlow
    from htram.const import CONF_AES_IV, CONF_AES_KEY

    stored = {CONF_AES_KEY: "MDEyMzQ1Njc4OWFiY2RlZg==", CONF_AES_IV: "0123456789abcdef"}
    seen = {}

    class FakeCoordinator:
        async def async_provision(self, **kwargs):
            seen.update(kwargs)

    flow = object.__new__(HTRAMOptionsFlow)
    entry = MagicMock()
    entry.options = stored
    entry.runtime_data = FakeCoordinator()
    flow._config_entry = entry
    type(flow).config_entry = property(lambda self: entry)
    flow.async_create_entry = lambda **kw: kw

    asyncio.run(
        flow.async_step_provision(
            {"ssid": "net", "password": "pw", "mqtt_server": "tcp://mqtt.example"}
        )
    )

    assert seen["aes_key"] == stored[CONF_AES_KEY]
    assert seen["aes_iv"] == stored[CONF_AES_IV]
    assert seen["mqtt_server"] == "tcp://mqtt.example"


def test_warmup_sentinels_are_dropped_on_both_paths():
    """The device reports nonsense until the NDIR sensor warms up.

    Five of the captured payloads carry CO2 0xFFFE, temperature 0x81 and
    humidity 0xFE together, for the first couple of minutes after a boot. The
    MQTT path discarded them; the Bluetooth path parsed the frame itself and
    did not, so the same device showed 65534 ppm and -127 C over Bluetooth
    while showing nothing over MQTT.
    """
    coord = object.__new__(Coordinator)
    coord.data = {}

    warmup = protocol.build_frame(
        b"\x41\x44", bytes([0x02, 0xFF, 0xFE, 0x81, 0xFE, 4, 1])
    )
    coord._parse_realtime(warmup)

    assert coord.data["co2"] is None
    assert coord.data["temperature"] is None
    assert coord.data["humidity"] is None
    # Battery and charging are not sensor readings and stay valid.
    assert coord.data["battery"] == 100
    assert coord.data["charging"] is True


def test_real_bluetooth_reading_survives():
    coord = object.__new__(Coordinator)
    coord.data = {}
    frame = protocol.build_frame(b"\x41\x44", bytes([0x02, 0x02, 0x26, 23, 58, 4, 0]))
    coord._parse_realtime(frame)
    assert coord.data["co2"] == 550
    assert coord.data["temperature"] == 23


def test_mute_is_the_inverse_of_the_buzzer():
    """The switch is a mute switch, so it is on when the buzzer is off."""
    coord = object.__new__(Coordinator)
    coord.data = {}
    coord._parse_sound(protocol.build_frame(b"\x27\x23", bytes([0x01, 0x00, 0x00, 0x00])))
    assert coord.data["mute"] is True
    coord._parse_sound(protocol.build_frame(b"\x27\x23", bytes([0x01, 0x00, 0x00, 0x01])))
    assert coord.data["mute"] is False


def test_bluetooth_failure_never_fails_the_coordinator_with_mqtt(coordinator):
    """With MQTT configured, the radio must not decide the entry's fate.

    Tolerating only *fresh* telemetry meant that a restart, or a gap in
    telemetry, still raised UpdateFailed -- which marks every entity
    unavailable, including the ones MQTT feeds.
    """
    coordinator.mqtt_enabled = True
    coordinator.data["co2"] = 700

    # No telemetry has ever arrived, so nothing is fresh.
    assert coordinator.mqtt_is_fresh is False
    assert coordinator._tolerate_ble_failure("not advertising") is coordinator.data


def test_bluetooth_failure_still_fails_without_mqtt(coordinator):
    """Bluetooth-only setups have no other source, so the failure is real."""
    from homeassistant.helpers.update_coordinator import UpdateFailed

    coordinator.mqtt_enabled = False
    with pytest.raises(UpdateFailed):
        coordinator._tolerate_ble_failure("not advertising")


async def test_ble_session_lifecycle(coordinator, monkeypatch):
    """Explicit Bluetooth session holds and releases link."""
    from unittest.mock import AsyncMock
    import htram.coordinator as mod

    coordinator.address = "94:E6:86:94:36:B2"
    coordinator.ble_ok = True
    coordinator.async_refresh = AsyncMock()
    coordinator.async_update_listeners = MagicMock()
    coordinator._cleanup_client = AsyncMock()
    coordinator.hass = MagicMock()

    timer_handle = MagicMock()
    monkeypatch.setattr(
        mod, "async_call_later", lambda hass, delay, cb: timer_handle
    )

    await coordinator.async_start_ble_session()
    assert coordinator._session_release is timer_handle

    await coordinator.async_end_ble_session()
    assert coordinator._session_release is None
    assert coordinator.ble_ok is False
    timer_handle.assert_called_once()
    coordinator._cleanup_client.assert_awaited_once()


def test_downlink_ack_ignored_silently(coordinator):
    """An instant 15-byte ACK must not increment the undecodable count."""
    from htram.mqtt_source import HtramMqttSource

    source = HtramMqttSource(MagicMock(), coordinator, "RM1221412257")
    ack = bytes.fromhex("444300020204785634125106000000")

    msg = MagicMock()
    msg.payload = ack
    source._handle_message(msg)

    assert source._undecodable == 0


def test_mqtt_control_available(coordinator, monkeypatch):
    """Control via MQTT is available only when enabled, source attached, fresh, and connected."""
    coordinator.hass = MagicMock()
    coordinator.mqtt_enabled = True
    coordinator.mqtt_source = MagicMock()
    coordinator._mqtt_last_seen = 1000.0

    import time
    monkeypatch.setattr(time, "monotonic", lambda: 1010.0)

    import homeassistant.components.mqtt as ha_mqtt
    monkeypatch.setattr(ha_mqtt, "is_connected", lambda hass: True)

    assert coordinator.mqtt_control_available is True

    # Stale telemetry disables MQTT control
    monkeypatch.setattr(time, "monotonic", lambda: 1400.0)
    assert coordinator.mqtt_control_available is False

    # Disconnected broker disables MQTT control
    monkeypatch.setattr(time, "monotonic", lambda: 1010.0)
    monkeypatch.setattr(ha_mqtt, "is_connected", lambda hass: False)
    assert coordinator.mqtt_control_available is False


def test_coordinator_downlink_builder(coordinator):
    """Downlink packet builder preserves known state while overriding requested fields."""
    coordinator.data = {
        "alarm_low": 750,
        "alarm_high": 1200,
        "screen_off": 120,
        "temp_unit": "F",
        "mute": True,
        "screen_on": True,
        "brightness": 90,
    }

    payload = coordinator._build_downlink_payload(mute=False, low=700)
    assert len(payload) == 31
    assert payload[:6] == b"DC\x00\x02\x00\x04"
    # Low threshold: 700
    assert int.from_bytes(payload[17:19], "little") == 700
    # High threshold: 1200 (preserved)
    assert int.from_bytes(payload[15:17], "little") == 1200
    # Brightness: 90 (preserved)
    assert int.from_bytes(payload[19:21], "little") == 90
    # Auto-off: 1 (since screen_off=120 != 0)
    assert int.from_bytes(payload[21:23], "little") == 1
    # Temp unit: 1 (F)
    assert int.from_bytes(payload[23:25], "little") == 1
    # Buzzer: 1 (since mute=False -> sound enabled)
    assert int.from_bytes(payload[25:27], "little") == 1
    # Screen power: 0 (screen stays on)
    assert int.from_bytes(payload[27:29], "little") == 0



