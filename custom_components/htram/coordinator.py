"""DataUpdateCoordinator for HTRAM."""
import asyncio
import logging
from datetime import timedelta
import async_timeout

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    SERVICE_UUID,
    WRITE_UUID,
    NOTIFY_UUID,
    CMD_GET_REALTIME,
    CMD_GET_SETTINGS,
    CMD_GET_SOUND_STATUS,
    CMD_SET_SOUND_OFF,
    CMD_SET_SOUND_ON,
    CMD_SET_TEMP_UNIT_C,
    CMD_SET_TEMP_UNIT_F,
    CMD_HEARTBEAT,
    POLL_INTERVAL
)

_LOGGER = logging.getLogger(__name__)

class HTRAMDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching HTRAM data."""

    def __init__(self, hass: HomeAssistant, ble_device: BLEDevice) -> None:
        """Initialize."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=POLL_INTERVAL),
        )
        self.ble_device = ble_device
        self.address = ble_device.address
        self.data = {}
        self._client = None

    async def _async_update_data(self):
        """Fetch data from the device."""
        try:
            # Re-discover device to get fresh objects
            ble_device = bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)
            if ble_device:
                self.ble_device = ble_device

            # Use a larger timeout for the entire update cycle
            async with async_timeout.timeout(30):
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

                def notification_handler(sender, data: bytearray):
                    hex_data = data.hex()
                    _LOGGER.debug(f"Received notification: {hex_data}")
                    if len(data) < 6:
                        return

                    cmd_id = data[4:6].hex()
                    
                    if cmd_id == "4144": # Realtime
                        if not realtime_future.done():
                            realtime_future.set_result(data)
                    elif cmd_id == "4143": # Settings
                        if not settings_future.done():
                            settings_future.set_result(data)
                    elif cmd_id == "2723": # Sound status
                        if not sound_future.done():
                            sound_future.set_result(data)

                # Start notifying
                await client.start_notify(NOTIFY_UUID, notification_handler)

                # 0. Send Heartbeat
                await client.write_gatt_char(WRITE_UUID, CMD_HEARTBEAT, response=False)
                await asyncio.sleep(0.5)

                timeout_occurred = False

                # 1. Get Realtime Data
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

    def _parse_realtime(self, data: bytearray):
        # Validation
        if len(data) < 13:
            _LOGGER.warning(f"Realtime data too short: {len(data)}")
            return

        co2 = int.from_bytes(data[7:9], byteorder='big')
        temp = data[9]
        if temp > 128:
            temp = temp - 256
        
        hum = data[10]
        batt_level = data[11]
        batt = batt_level * 25
        if batt > 100:
            batt = 100

        charging = data[12]

        self.data["co2"] = co2
        self.data["temperature"] = temp
        self.data["humidity"] = hum
        self.data["battery"] = batt
        self.data["charging"] = charging == 1

    def _parse_sound(self, data: bytearray):
        if len(data) < 10:
             _LOGGER.warning(f"Sound data too short: {len(data)}")
             return
        is_off = data[9] == 0 
        self.data["mute"] = is_off 

    def _parse_settings(self, data: bytearray):
        if len(data) < 13:
             _LOGGER.warning(f"Settings data too short: {len(data)}")
             return

        low = int.from_bytes(data[7:9], byteorder='big')
        high = int.from_bytes(data[9:11], byteorder='big')
        screen_off = int.from_bytes(data[11:13], byteorder='big')

        self.data["alarm_low"] = low
        self.data["alarm_high"] = high
        self.data["screen_off"] = screen_off 

    async def async_set_mute(self, mute: bool):
        """Set mute state."""
        # Use verified hardcoded packets from Java source
        cmd = CMD_SET_SOUND_OFF if mute else CMD_SET_SOUND_ON
        await self._send_command(cmd)
        self.data["mute"] = mute
        self.async_update_listeners()

    async def async_set_temp_unit(self, celsius: bool):
        """Set temperature unit."""
        cmd = CMD_SET_TEMP_UNIT_C if celsius else CMD_SET_TEMP_UNIT_F
        await self._send_command(cmd)
        # Update local state optimistically
        self.data["temp_unit"] = "C" if celsius else "F"
        self.async_update_listeners()

    async def _send_command(self, command: bytes):
        """Send a command to the device."""
        # This is tricky because we might not be connected if we are outside the update loop.
        # We need a quick connection.
        from bleak import BleakClient
        from bleak_retry_connector import establish_connection
        ble_device = bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)
        _LOGGER.debug(f"Sending command {command.hex()} to {self.address}")
        
        # Reuse existing client if possible
        if self._client and self._client.is_connected:
            client = self._client
            await client.write_gatt_char(WRITE_UUID, command, response=False)
            _LOGGER.debug("Command sent successfully (REUSED connection)")
        else:
             # If not connected during an action, we must connect.
             # We should probably update self._client to keep it persistent too?
            _LOGGER.debug("Sending command: Establishing NEW connection")
            client = await establish_connection(BleakClient, ble_device, self.address)
            self._client = client
            try:
                await client.write_gatt_char(WRITE_UUID, command, response=False)
                _LOGGER.debug("Command sent successfully")
            except Exception:
                # If command fails, perhaps we should disconnect to be clean?
                # But for now let's hope it stays open for next poll?
                # Actually if we just opened it, we might want to keep it open for consistency with the new policy.
                 raise
            # finally:
            #    await client.disconnect() <-- REMOVED

    async def async_set_screen_off(self, minutes: int):
         # Create command for screen off
         # Header: 7B 41 00 0B 42 43 04 00 20 00 00 00 00 00 7D (Example from java for submitScreenOffTime)
         # But wait, java:
         # Logger.e("send command：4243 submitScreenOffTime", new Object[0]);
         # byte[] bArr = {123, 65, 0, 11, 66, 67, 4, 0, 32, 0, 0, 0, 0, 0, 125};
         # bArr[10] = bArrShortToByteArray[1];
         # bArr[11] = bArrShortToByteArray[0];
         # CRC at 12, 13
         
         # Note: Java array is:
         # 0: 123 (7B)
         # 1: 65 (41)
         # ...
         # 8: 32 (0x20) -> This seems to be a mask or type?
         # 9: 0
         # 10: High Byte of value? NO, ShortToByteArray: bArr[i2] = (byte) (i & 255); -> Little Endian?
         # bArr[10] = (byte) ((i & MotionEventCompat.ACTION_POINTER_INDEX_MASK) >> 8); -> High byte!
         # Wait `shortToByteArray` impl in java:
         # bArr[i2] = (byte) (i & 255);
         # bArr[i2 + 1] = (byte) ((i & ... ) >> 8);
         # So index 0 is Lo, index 1 is Hi.
         # But then usage:
         # bArr[10] = bArrShortToByteArray[1]; (High Byte)
         # bArr[11] = bArrShortToByteArray[0]; (Low Byte)
         # So Big Endian on the wire for these 2 bytes?
         
         val_hi = (minutes >> 8) & 0xFF
         val_lo = minutes & 0xFF
         
         # Base Packet
         #                7B    41    00    0B    42    43    04    00    20    00    [HI]  [LO]  [CRC_L] [CRC_H] 7D
         packet = bytearray([0x7B, 0x41, 0x00, 0x0B, 0x42, 0x43, 0x04, 0x00, 0x20, 0x00, val_hi, val_lo])
         
         # Calculate CRC
         crc = self._crc16(packet)
         packet.append(crc & 0xFF)
         packet.append((crc >> 8) & 0xFF)
         packet.append(0x7D)
         
         await self._send_command(packet)
         self.data["screen_off"] = minutes # Optimistic update
         self.async_update_listeners()

    async def async_set_alarm_thresholds(self, low: int | None = None, high: int | None = None, screen_off: int | None = None):
        """Set alarm thresholds and screen off timer."""
        # Get current values to fill in gaps
        current_low = self.data.get("alarm_low", 800)
        current_high = self.data.get("alarm_high", 1000)
        current_screen_off = self.data.get("screen_off", 0)

        new_low = low if low is not None else current_low
        new_high = high if high is not None else current_high
        new_screen_off = screen_off if screen_off is not None else current_screen_off

        # Validate logic: Low < High
        if new_low >= new_high:
            _LOGGER.warning(f"Low threshold ({new_low}) must be less than High ({new_high})")
            return

        # Build packet using "submitAlertValue" structure (Full Update)
        # Header: 7B 41 00 0F 42 43 04 00 40 06 [Lov V] [Hi V] [Screen V] [CRC] 7D
        # Len: 0x0F (15)
        # Cmd: 42 43
        # Magic: 04 00 40 06 (Matches Java submitAlertValue)
        
        packet = bytearray([0x7B, 0x41, 0x00, 0x0F, 0x42, 0x43, 0x04, 0x00, 0x40, 0x06])
        
        # Low (2 bytes Big Endian)
        packet.append((new_low >> 8) & 0xFF)
        packet.append(new_low & 0xFF)
        
        # High (2 bytes Big Endian)
        packet.append((new_high >> 8) & 0xFF)
        packet.append(new_high & 0xFF)
        
        # Screen Off (2 bytes Big Endian) - Pass current value to preserve it (assuming 3rd arg is Screen Off)
        packet.append((new_screen_off >> 8) & 0xFF)
        packet.append(new_screen_off & 0xFF)

        # CRC (Calculated on the first 16 bytes: Header(4) + Data(12))
        crc = self._crc16(packet)
        packet.append((crc >> 8) & 0xFF)
        packet.append(crc & 0xFF)
        packet.append(0x7D)

        await self._send_command(packet)
        
        # Optimistic update
        self.data["alarm_low"] = new_low
        self.data["alarm_high"] = new_high
        self.data["screen_off"] = new_screen_off
        self.async_update_listeners()

    async def async_set_screen_off(self, minutes: int):
         """Set screen off timer using dedicated command."""
         # Use 'submitScreenOffTime' packet structure
         # Header: 7B 41 00 0B 42 43 04 00 20 00 [VAL_HI] [VAL_LO] [CRC] 7D
         # Magic: 20 00
         
         val_hi = (minutes >> 8) & 0xFF
         val_lo = minutes & 0xFF
         
         packet = bytearray([0x7B, 0x41, 0x00, 0x0B, 0x42, 0x43, 0x04, 0x00, 0x20, 0x00, val_hi, val_lo])
         
         # CRC
         crc = self._crc16(packet)
         packet.append((crc >> 8) & 0xFF)
         packet.append(crc & 0xFF)
         packet.append(0x7D)
         
         await self._send_command(packet)
         self.data["screen_off"] = minutes
         self.async_update_listeners()

    async def async_sync_time(self):
        """Sync device time (UTC)."""
        import datetime
        now = datetime.datetime.utcnow()
        
        # Format: YY MM DD HH mm ss (decimal values as bytes)
        # Packet: 7B 41 00 0C 22 42 01 00 [YY] [MM] [DD] [HH] [mm] [ss] [CRC] 7D
        
        # Header + Cmd (22 42) + Flag (01)
        packet = bytearray([0x7B, 0x41, 0x00, 0x0C, 0x22, 0x42, 0x01])
        
        # Date parts (modulo 100 for year to get 2 digits)
        packet.append(now.year % 100)
        packet.append(now.month)
        packet.append(now.day)
        packet.append(now.hour)
        packet.append(now.minute)
        packet.append(now.second)
        
        # CRC
        crc = self._crc16(packet)
        packet.append((crc >> 8) & 0xFF)
        packet.append(crc & 0xFF)
        packet.append(0x7D)
        
        await self._send_command(packet)
        _LOGGER.info("Synced time to device (UTC)")

    def _crc16(self, data: bytearray) -> int:
        """Calculate CRC-16 (Poly 0x8005, Init 0, No Ref)."""
        crc = 0x0000
        for b in data:
            crc ^= (b << 8)
            for _ in range(8):
                if crc & 0x8000:
                    crc = (crc << 1) ^ 0x8005
                else:
                    crc = crc << 1
                crc &= 0xFFFF
        return crc 
