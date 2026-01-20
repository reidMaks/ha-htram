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

            async with async_timeout.timeout(20):
                if not self._client or not self._client.is_connected:
                     # Create new client
                     # Note: In a real component using bluetooth library, we would use context managers 
                     # but here we need persistence or re-connection logic. 
                     # For simple polling, connecting each time or keeping connection is a choice. 
                     # Given the instability of some BLE devices, a connect-poll-disconnect cycle might be safer 
                     # OR properly managed persistent connection.
                     # Let's try to establish a connection context just for this update for robustness first.
                     pass

                # We will use the `bluetooth` helper utilities to connect
                # But to keep it simple and standard compliant for HA, we should probably stick to `bleak_retry_connector` if available
                # or just use BleakClient directly with the device object.
                from bleak import BleakClient
                from bleak_retry_connector import establish_connection

                _LOGGER.debug(f"Coordinator updating: Establishing connection to {self.address}")
                client = await establish_connection(BleakClient, self.ble_device, self.ble_device.address)
                try:
                    _LOGGER.debug(f"Coordinator connected: {client.is_connected}")
                    self._client = client
                    
                    # Ensure paired
                    try:
                        await client.pair()
                    except Exception as e:
                        _LOGGER.warning(f"Pairing warning: {e}")

                    
                    # Subscribe to notifications
                    # We need a future to capture the data because notification is async
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
                        elif cmd_id == "4143": # Settings (Alarm limits etc)
                            if not settings_future.done():
                                settings_future.set_result(data)
                        elif cmd_id == "2723": # Sound status
                            if not sound_future.done():
                                sound_future.set_result(data)

                    await client.start_notify(NOTIFY_UUID, notification_handler)

                    # 1. Get Realtime Data
                    await client.write_gatt_char(WRITE_UUID, CMD_GET_REALTIME, response=False)
                    try:
                        data = await asyncio.wait_for(realtime_future, timeout=5.0)
                        self._parse_realtime(data)
                    except asyncio.TimeoutError:
                        _LOGGER.warning("Timeout waiting for realtime data")

                    # 2. Get Sound Status
                    await client.write_gatt_char(WRITE_UUID, CMD_GET_SOUND_STATUS, response=False)
                    try:
                        data = await asyncio.wait_for(sound_future, timeout=5.0)
                        self._parse_sound(data)
                    except asyncio.TimeoutError:
                        _LOGGER.warning("Timeout waiting for sound status")
                        
                    # 3. Get Settings (Optional, maybe less frequent? But for now every poll to ensure state consistency)
                    await client.write_gatt_char(WRITE_UUID, CMD_GET_SETTINGS, response=False)
                    try:
                        data = await asyncio.wait_for(settings_future, timeout=5.0)
                        self._parse_settings(data)
                    except asyncio.TimeoutError:
                         _LOGGER.warning("Timeout waiting for settings")

                    await client.stop_notify(NOTIFY_UUID)

                finally:
                    await client.disconnect()
                    
            return self.data

        except BleakError as func_call_error:
            raise UpdateFailed(f"Bluetooth error: {func_call_error}") from func_call_error
        except Exception as e:
            raise UpdateFailed(f"Unexpected error: {e}") from e

    def _parse_realtime(self, data: bytearray):
        # Format: ... 41 44 04 00 [CO2_HI] [CO2_LO] [TEMP] [HUM] [BATT] [CHG] ...
        # Indices based on previous analysis:
        # CO2: 7, 8
        # Temp: 9
        # Hum: 10
        # Batt: 11
        # Chg: 12
        co2 = int.from_bytes(data[7:9], byteorder='big')
        temp = data[9]
        if temp > 128:
            temp = temp - 256 # Assuming standard signed byte conversion if needed, but checked java code: if > 128, val += SOURCE_ANY? 
            # Java: if (num.intValue() > 128) num = Integer.valueOf(num.intValue() + InputDeviceCompat.SOURCE_ANY); 
            # InputDeviceCompat.SOURCE_ANY is 0xFFFFFF00 (-256). 
            # So if value is 129, it becomes 129 - 256 = -127. Correct, it's a signed byte interpretation.
            pass
        
        hum = data[10]
        batt_level = data[11] # 0-4 bars
        
        # Map bars to percentage (approximate)
        # 0 -> 0%
        # 1 -> 25%
        # 2 -> 50%
        # 3 -> 75%
        # 4 -> 100%
        batt = batt_level * 25
        if batt > 100:
            batt = 100

        charging = data[12]

        self.data["co2"] = co2
        self.data["temperature"] = temp
        self.data["humidity"] = hum
        self.data["battery"] = batt
        self.data["charging"] = charging == 1 # 1 = charging? Java: `showChargeStatue(getValue(bArr6))`

    def _parse_sound(self, data: bytearray):
        # 27 23 ... [STATE at 9]
        is_off = data[9] == 0 # 0 = OFF, 1 = ON based on `showMuteState("0".equals(value))`
        self.data["mute"] = is_off 

    def _parse_settings(self, data: bytearray):
        # 41 43 ... 
        # Low: 7,8
        # High: 9,10
        # Delay (or something): 11,12 - Wait, java says `delay`
        
        # Java:
        # bArr2 (Low) -> 7, 2 bytes
        # bArr3 (High) -> 9, 2 bytes
        # bArr4 (Delay?) -> 11, 2 bytes
        
        low = int.from_bytes(data[7:9], byteorder='big')
        high = int.from_bytes(data[9:11], byteorder='big')
        screen_off = int.from_bytes(data[11:13], byteorder='big')

        self.data["alarm_low"] = low
        self.data["alarm_high"] = high
        self.data["screen_off"] = screen_off 

    async def async_set_mute(self, mute: bool):
        """Set mute state."""
        cmd = CMD_SET_SOUND_OFF if mute else CMD_SET_SOUND_ON
        await self._send_command(cmd)
        self.data["mute"] = mute
        self.async_update_listeners()

    async def _send_command(self, command: bytes):
        """Send a command to the device."""
        # This is tricky because we might not be connected if we are outside the update loop.
        # We need a quick connection.
        from bleak import BleakClient
        from bleak_retry_connector import establish_connection
        ble_device = bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)
        _LOGGER.debug(f"Sending command {command.hex()} to {self.address}")
        client = await establish_connection(BleakClient, ble_device, self.address)
        try:
            await client.write_gatt_char(WRITE_UUID, command, response=False)
            _LOGGER.debug("Command sent successfully")
        finally:
            await client.disconnect()

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

    async def async_set_alarm_thresholds(self, low: int | None = None, high: int | None = None):
        """Set alarm thresholds."""
        # We need current values to partial update
        current_low = self.data.get("alarm_low", 800)
        current_high = self.data.get("alarm_high", 1000)
        current_screen_off = self.data.get("screen_off", 0)

        new_low = low if low is not None else current_low
        new_high = high if high is not None else current_high

        # Validate logic: Low < High
        if new_low >= new_high:
            _LOGGER.warning(f"Low threshold ({new_low}) must be less than High ({new_high})")
            return

        # Build packet
        # 0x4243
        # 7B 41 00 0B 42 43 04 00 20 00 [Low] [High] [ScreenOff] [CRC] 7D
        
        # Bytes:
        # 10: Low HighByte
        # 11: Low LowByte
        # 12: High HighByte
        # 13: High LowByte
        # 14: ScreenOff HighByte
        # 15: ScreenOff LowByte
        
        # Wait, from previous analysis of `MonitorPresenter` and `CMBLERequest`:
        # public static byte[] submitAlertValue(int i, int i2, int i3)
        # It sends ALL THREE: Low, High, ScreenOff. 
        # So we must send screen_off too.
        
        packet = bytearray([0x7B, 0x41, 0x00, 0x0B, 0x42, 0x43, 0x04, 0x00, 0x20, 0x00])
        
        # Low
        packet.append((new_low >> 8) & 0xFF)
        packet.append(new_low & 0xFF)
        
        # High
        packet.append((new_high >> 8) & 0xFF)
        packet.append(new_high & 0xFF)
        
        # Screen Off (Keep existing)
        packet.append((current_screen_off >> 8) & 0xFF)
        packet.append(current_screen_off & 0xFF)

        # CRC
        crc = self._crc16(packet)
        packet.append(crc & 0xFF)
        packet.append((crc >> 8) & 0xFF)
        packet.append(0x7D)

        await self._send_command(packet)
        self.data["alarm_low"] = new_low
        self.data["alarm_high"] = new_high
        self.async_update_listeners()

    async def async_set_screen_off(self, minutes: int):
         # Must preserve alarm thresholds
         current_low = self.data.get("alarm_low", 800)
         current_high = self.data.get("alarm_high", 1000)
         
         # Reuse set_alarm_thresholds logic or duplicate?
         # Duplicating logic here slightly to keep it clear or refactor?
         # Let's just build the packet.
         
         packet = bytearray([0x7B, 0x41, 0x00, 0x0B, 0x42, 0x43, 0x04, 0x00, 0x20, 0x00])
         
         # Low
         packet.append((current_low >> 8) & 0xFF)
         packet.append(current_low & 0xFF)
         
         # High
         packet.append((current_high >> 8) & 0xFF)
         packet.append(current_high & 0xFF)
         
         # Screen Off
         packet.append((minutes >> 8) & 0xFF)
         packet.append(minutes & 0xFF)

         # CRC
         crc = self._crc16(packet)
         packet.append(crc & 0xFF)
         packet.append((crc >> 8) & 0xFF)
         packet.append(0x7D)
         
         await self._send_command(packet)
         self.data["screen_off"] = minutes # Optimistic update
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
        packet.append(crc & 0xFF)
        packet.append((crc >> 8) & 0xFF)
        packet.append(0x7D)
        
        await self._send_command(packet)
        _LOGGER.info("Synced time to device (UTC)")

    def _crc16(self, data: bytearray) -> int:
        """Calculate CRC-16 (XMODEM) 0x1021."""
        crc = 0x0000
        for b in data:
            crc ^= (b << 8)
            for _ in range(8):
                if crc & 0x8000:
                    crc = (crc << 1) ^ 0x1021
                else:
                    crc = crc << 1
                crc &= 0xFFFF
        return crc 
