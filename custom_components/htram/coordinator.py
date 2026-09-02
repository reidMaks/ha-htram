"""DataUpdateCoordinator for HTRAM."""
import asyncio
import base64
import time
import logging
from datetime import datetime, timedelta, timezone

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    WRITE_UUID,
    NOTIFY_UUID,
    CMD_GET_REALTIME,
    CMD_GET_SETTINGS,
    CMD_GET_SOUND_STATUS,
    CMD_HEARTBEAT,
    MQTT_KEYS,
    MQTT_STALE_AFTER,
    POLL_INTERVAL
)
from . import protocol

_LOGGER = logging.getLogger(__name__)

# hass.data[DOMAIN][entry_id] is the pattern Home Assistant moved away from:
# runtime_data is typed, scoped to the entry and cleaned up with it.
type HtramConfigEntry = ConfigEntry["HTRAMDataUpdateCoordinator"]

class HTRAMDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching HTRAM data."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        ble_device: BLEDevice,
    ) -> None:
        """Initialize."""
        # config_entry became required in HA 2026.8; omitting it logs a
        # deprecation now and stops working then.
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=POLL_INTERVAL),
        )
        self.ble_device = ble_device
        self.address = ble_device.address
        self.data = {}
        self._client = None

        # Set by __init__.py from the config entry options. When telemetry is
        # arriving over MQTT the Bluetooth poll skips the realtime request,
        # which is most of what it does -- fewer connections, fewer chances to
        # disturb the pairing.
        self.mqtt_enabled = False
        self._mqtt_last_seen: float | None = None

    @property
    def mqtt_is_fresh(self) -> bool:
        """Whether MQTT telemetry arrived recently enough to be trusted."""
        if not self.mqtt_enabled or self._mqtt_last_seen is None:
            return False
        return (time.monotonic() - self._mqtt_last_seen) < MQTT_STALE_AFTER

    @property
    def active_source(self) -> str:
        """Where the CO2, temperature and humidity readings are coming from."""
        return "mqtt" if self.mqtt_is_fresh else "bluetooth"

    def async_apply_telemetry(self, reading) -> None:
        """Merge an MQTT reading and notify entities.

        The keys are the same ones the Bluetooth path writes, so the entities
        never learn which transport fed them -- entity ids and history stay
        continuous across a switch.
        """
        self._mqtt_last_seen = time.monotonic()
        self.data["co2"] = reading.co2
        self.data["temperature"] = reading.temperature
        self.data["humidity"] = reading.humidity
        self.data["last_telemetry"] = reading.timestamp
        self.async_set_updated_data(self.data)

    def _expire_stale_mqtt(self) -> None:
        """Drop readings MQTT stopped refreshing.

        Falling back to Bluetooth polling here would be tempting, but that is
        the very thing that disturbs the pairing, and doing it silently while
        nobody is watching is how a working setup quietly degrades. Showing
        the sensors as unavailable is the honest answer.
        """
        if not self.mqtt_enabled or self.mqtt_is_fresh:
            return
        if self._mqtt_last_seen is None:
            return
        if any(self.data.get(key) is not None for key in MQTT_KEYS):
            _LOGGER.warning(
                "No telemetry on MQTT for over %d s; marking readings unavailable",
                MQTT_STALE_AFTER,
            )
        for key in MQTT_KEYS:
            self.data[key] = None

    async def _async_update_data(self):
        """Fetch data from the device."""
        self._expire_stale_mqtt()
        try:
            # Re-discover device to get fresh objects
            ble_device = bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)
            if ble_device:
                self.ble_device = ble_device

            # Use a larger timeout for the entire update cycle
            # asyncio.timeout is stdlib since 3.11; HA stopped shipping the
            # async_timeout backport, and it was never in our requirements,
            # so importing it now stops the integration from loading at all.
            async with asyncio.timeout(30):
                if not self._client or not self._client.is_connected:
                     # Connect will happen below
                     pass

                from bleak import BleakClient
                from bleak_retry_connector import establish_connection

                _LOGGER.debug(f"Coordinator updating: Check connection to {self.address}")
                
                if self._client and self._client.is_connected:
                     client = self._client
                else:
                     _LOGGER.debug(f"Coordinator updating: Establishing NEW connection to {self.address}")
                     client = await establish_connection(BleakClient, self.ble_device, self.ble_device.address)
                     self._client = client
                
                _LOGGER.debug(f"Coordinator connected: {client.is_connected}")

                # Future objects for async responses
                realtime_future = asyncio.Future()
                settings_future = asyncio.Future()
                sound_future = asyncio.Future()

                # One notification can carry several frames -- a heartbeat ack
                # riding along with an answer is routine -- and a frame can be
                # split across notifications. Matching on bytes [4:6] of the
                # raw buffer misses an answer in either case, so the bytes are
                # accumulated and framed properly.
                buffer = bytearray()
                awaited = {
                    b"\x41\x44": realtime_future,
                    b"\x41\x43": settings_future,
                    b"\x27\x23": sound_future,
                }

                def notification_handler(sender, data: bytearray) -> None:
                    buffer.extend(data)
                    consumed = 0
                    for frame in protocol.iter_frames(bytes(buffer)):
                        consumed = max(consumed, bytes(buffer).find(frame) + len(frame))
                        if not protocol.frame_is_valid(frame):
                            _LOGGER.debug("Discarding frame with a bad checksum: %s", frame.hex())
                            continue
                        opcode = protocol.frame_opcode(frame)
                        future = awaited.get(opcode)
                        if future is not None and not future.done():
                            future.set_result(frame)
                    del buffer[:consumed]

                # Start notifying
                await client.start_notify(NOTIFY_UUID, notification_handler)

                # 0. Send Heartbeat
                await client.write_gatt_char(WRITE_UUID, CMD_HEARTBEAT, response=False)
                await asyncio.sleep(0.5)

                timeout_occurred = False

                # 1. Get Realtime Data -- unless MQTT is already supplying it.
                # The battery and charging flags ride along in the same answer,
                # so this is still requested occasionally rather than never.
                if not self.mqtt_is_fresh:
                    await client.write_gatt_char(WRITE_UUID, CMD_GET_REALTIME, response=False)
                    try:
                        data = await asyncio.wait_for(realtime_future, timeout=5.0)
                        self._parse_realtime(data)
                    except asyncio.TimeoutError:
                        _LOGGER.warning("Timeout waiting for realtime data")
                        timeout_occurred = True

                # 2. Get Sound Status
                await client.write_gatt_char(WRITE_UUID, CMD_GET_SOUND_STATUS, response=False)
                try:
                    data = await asyncio.wait_for(sound_future, timeout=5.0)
                    self._parse_sound(data)
                except asyncio.TimeoutError:
                    _LOGGER.warning("Timeout waiting for sound status")
                    # Non-critical, but note it

                # 3. Get Settings
                await client.write_gatt_char(WRITE_UUID, CMD_GET_SETTINGS, response=False)
                try:
                    data = await asyncio.wait_for(settings_future, timeout=5.0)
                    self._parse_settings(data)
                except asyncio.TimeoutError:
                    _LOGGER.warning("Timeout waiting for settings")
                    # Non-critical

                await client.stop_notify(NOTIFY_UUID)

                # If we had a timeout on realtime data, our connection might be bad.
                # Recycle the client to force a fresh connection next time.
                if timeout_occurred:
                    _LOGGER.debug("Timeouts occurred, forcing client recycle")
                    await self._cleanup_client()

            return self.data

        except asyncio.TimeoutError:
            await self._cleanup_client()
            raise UpdateFailed("Update timed out")
        except BleakError as func_call_error:
            await self._cleanup_client()
            raise UpdateFailed(f"Bluetooth error: {func_call_error}") from func_call_error
        except Exception as e:
            await self._cleanup_client()
            raise UpdateFailed(f"Unexpected error: {repr(e)}") from e

    async def _cleanup_client(self):
        """Clean up the client connection."""
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None

    def _parse_realtime(self, frame: bytes) -> None:
        """Store a realtime answer.

        Parsing lives in protocol, which discards the warm-up sentinels the
        NDIR sensor emits for the first couple of minutes after a boot. This
        method used to parse the frame itself and had no such check, so a
        freshly powered device reported 65534 ppm, -127 C and 254 % over
        Bluetooth while the MQTT path correctly showed nothing.
        """
        reading = protocol.parse_realtime(frame)
        if reading is None:
            _LOGGER.warning("Realtime frame too short: %d bytes", len(frame))
            return

        self.data["co2"] = reading.co2
        self.data["temperature"] = reading.temperature
        self.data["humidity"] = reading.humidity
        self.data["battery"] = reading.battery
        self.data["charging"] = reading.charging

    def _parse_sound(self, frame: bytes) -> None:
        """Store the buzzer state. The switch is a mute switch, so it inverts."""
        enabled = protocol.parse_sound(frame)
        if enabled is None:
            _LOGGER.warning("Sound frame too short: %d bytes", len(frame))
            return
        self.data["mute"] = not enabled

    def _parse_settings(self, frame: bytes) -> None:
        """Store the alarm thresholds and the screen-off timer."""
        settings = protocol.parse_settings(frame)
        if settings is None:
            _LOGGER.warning("Settings frame too short: %d bytes", len(frame))
            return
        self.data["alarm_low"] = settings.alarm_low
        self.data["alarm_high"] = settings.alarm_high
        self.data["screen_off"] = settings.screen_off

    async def async_set_mute(self, mute: bool) -> None:
        """Turn the alarm buzzer off or on."""
        await self._send_command(protocol.set_sound(not mute))
        self.data["mute"] = mute
        self.async_update_listeners()

    async def async_set_temp_unit(self, celsius: bool) -> None:
        """Switch the display between Celsius and Fahrenheit.

        This changes the panel only. Telemetry keeps reporting Celsius either
        way, which is why the console experiments used this as a probe.
        """
        await self._send_command(protocol.set_temperature_unit(celsius))
        self.data["temp_unit"] = "C" if celsius else "F"
        self.async_update_listeners()

    async def async_set_alarm_thresholds(
        self,
        low: int | None = None,
        high: int | None = None,
        screen_off: int | None = None,
    ) -> None:
        """Set the CO2 alarm thresholds and screen-off timer together.

        The device takes all three in one frame, so the unspecified ones are
        filled from the last known state rather than reset.
        """
        new_low = low if low is not None else self.data.get("alarm_low", 800)
        new_high = high if high is not None else self.data.get("alarm_high", 1000)
        new_screen_off = (
            screen_off if screen_off is not None else self.data.get("screen_off", 0)
        )

        if new_low >= new_high:
            raise HomeAssistantError(
                f"The low threshold ({new_low} ppm) must be below the high one "
                f"({new_high} ppm)"
            )

        await self._send_command(
            protocol.set_thresholds(new_low, new_high, new_screen_off)
        )
        self.data["alarm_low"] = new_low
        self.data["alarm_high"] = new_high
        self.data["screen_off"] = new_screen_off
        self.async_update_listeners()

    async def async_set_screen_off(self, minutes: int) -> None:
        """Set the screen-off timer on its own."""
        await self._send_command(protocol.set_screen_off(minutes))
        self.data["screen_off"] = minutes
        self.async_update_listeners()

    async def async_sync_time(self) -> None:
        """Set the device clock, in UTC."""
        await self._send_command(protocol.sync_time(datetime.now(timezone.utc)))
        _LOGGER.debug("Device clock synchronised")

    async def _send_command(self, command: bytes) -> None:
        """Write one frame, reusing the link when there is one.

        The device advertises only in short windows, so a link that already
        exists is worth keeping: tearing it down and reconnecting for each
        command is what used to break the pairing.
        """
        if self._client is not None and self._client.is_connected:
            _LOGGER.debug("Sending %s over the existing link", command[4:6].hex())
            await self._client.write_gatt_char(WRITE_UUID, command, response=False)
            return

        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            raise HomeAssistantError(
                f"{self.address} is not currently reachable over Bluetooth. The "
                "monitor only advertises for a short window after it loses a "
                "connection, so press the button on the device and try again"
            )

        from bleak import BleakClient
        from bleak_retry_connector import establish_connection

        _LOGGER.debug("Sending %s over a new link", command[4:6].hex())
        try:
            client = await establish_connection(BleakClient, ble_device, self.address)
        except (BleakError, TimeoutError) as err:
            raise HomeAssistantError(
                f"Could not connect to {self.address}: {err}. Press the button on "
                "the device to make it discoverable, then try again"
            ) from err

        self._client = client
        await client.write_gatt_char(WRITE_UUID, command, response=False)

    async def async_provision(
        self,
        ssid: str,
        password: str,
        mqtt_server: str | None = None,
        aes_key: str | None = None,
        aes_iv: str | None = None,
    ) -> None:
        """Provision the device, in the order the vendor app uses.

        The radio-mode command first is not optional. Credentials sent while
        the device is still in BLE mode are acknowledged exactly like accepted
        ones and then ignored -- which is why earlier attempts looked like they
        worked and changed nothing.

        The link is dropped at the end for the same reason: the device joins
        the network after the Bluetooth session ends, not during it.
        """
        _LOGGER.debug("Provisioning: switching the radio to WiFi mode")
        await self._send_command(protocol.set_radio_mode(wifi=True))
        await asyncio.sleep(0.5)

        if mqtt_server:
            if not (aes_key and aes_iv):
                raise HomeAssistantError(
                    "An MQTT server needs an AES key and IV: they travel in the "
                    "same frame and the device rejects a partial one"
                )
            _LOGGER.debug("Provisioning MQTT endpoint: %s", mqtt_server)
            await self._send_command(
                protocol.cloud_config(base64.b64decode(aes_key), aes_iv, mqtt_server)
            )
            await asyncio.sleep(1)

        _LOGGER.debug("Provisioning WiFi network: %s", ssid)
        await self._send_command(protocol.wifi_credentials(ssid, password))
        await asyncio.sleep(0.5)
        await self._cleanup_client()

    async def async_provision_wifi(self, ssid: str, password: str):
        """Provision WiFi credentials."""
        packet = protocol.wifi_credentials(ssid, password)
        _LOGGER.debug(f"Provisioning WiFi: {ssid}")
        await self._send_command(packet)

    async def async_provision_mqtt(self, mqtt_server: str, aes_key: str, aes_iv: str):
        """Provision the MQTT endpoint and crypto material.

        The key arrives Base64-encoded, as the vendor cloud issued it; the IV
        goes to the device as the raw bytes of its string. That asymmetry is
        the app's.
        """
        packet = protocol.cloud_config(base64.b64decode(aes_key), aes_iv, mqtt_server)
        _LOGGER.debug(f"Provisioning MQTT: {mqtt_server}")
        await self._send_command(packet)
