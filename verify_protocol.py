import asyncio
import logging
from bleak import BleakClient, BleakScanner

# Constants from decompiled app
SERVICE_UUID = "FC247940-6E08-11E4-80FC-0002A5D5C51B"
NOTIFY_UUID = "F833D6C0-6E0B-11E4-9136-0002A5D5C51B"
WRITE_UUID = "3D115840-6E0B-11E4-B24F-0002A5D5C51B"

# Commands
CMD_GET_REALTIME = bytearray([0x7B, 0x41, 0x00, 0x07, 0x40, 0x44, 0x02, 0x00, 0xFC, 0x3E, 0x7D])
CMD_GET_SETTINGS = bytearray([0x7B, 0x41, 0x00, 0x09, 0x40, 0x43, 0x04, 0x00, 0x60, 0x06, 0xEF, 0x17, 0x7D])
CMD_GET_SOUND = bytearray([0x7B, 0x41, 0x00, 0x07, 0x26, 0x23, 0x01, 0x00, 0x09, 0xC0, 0x7D])

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def notification_handler(sender, data):
    hex_data = data.hex()
    logger.info(f"Received notification: {hex_data}")
    
    if len(data) < 6:
        return

    # Basic CRC or Header check could go here
    
    # Extract command ID (bytes 4-5) to identify response type
    cmd_id = data[4:6].hex()
    
    if cmd_id == "4144": # Realtime Data Response
        # Format: 7B 41 00 0D 41 44 04 00 [CO2_HI] [CO2_LO] [TEMP_INT] [TEMP_DEC] [HUM] [BATT] [CHG] [CRC_HI] [CRC_LO] 7D
        # (Offsets are approximate based on decompiled code, need verification)
        # Decompiled: arraycopy(bytes, 7, bArr2, 0, 2); -> CO2
        co2_raw = int.from_bytes(data[7:9], byteorder='big')
        
        # Decompiled: arraycopy(bytes, 9, bArr3, 0, 1); -> Temp Integer
        # Decompiled: arraycopy(bytes, 10, bArr4, 0, 1); -> Temp Decimal (Actually seems to be decimal part or just byte ordering?)
        # Let's assume standard parsing from `MonitorPresenter.java`:
        # showTempValue(getTempValue(bArr3)); -> bArr3 is byte at index 9.
        temp_raw = data[9]
        if temp_raw > 128:
            temp_raw = temp_raw - 256 # Handle negative if needed, though 128 offset in Java code suggests otherwise
            
        humidity_raw = data[10] # Changed from 11 in decompiled? Verify with live data.
        # Wait, `MonitorPresenter.java`:
        # System.arraycopy(bArr, 10, bArr4, 0, 1); -> Humidity?
        # No: 
        # bArr2 (CO2) -> 7, 2 bytes
        # bArr3 (Temp) -> 9, 1 byte
        # bArr4 (Hum) -> 10, 1 byte  <-- Wait, code says `showHumidityValue(getValue(bArr4))` -> bArr4 is index 10?
        # bArr5 (Capacity) -> 11, 1 byte
        # bArr6 (Charge) -> 12, 1 byte
        
        humidity_raw = data[10]
        vehicle_battery = data[11]
        charging = data[12]

        print(f"CO2: {co2_raw} ppm")
        print(f"Temp: {temp_raw} C")
        print(f"Humidity: {humidity_raw} %")
        print(f"Battery: {vehicle_battery} Bars ({vehicle_battery * 25}%)")
        print(f"Charging: {charging}")

    elif cmd_id == "2723": # Mute State
        # byte[] bArr2 = new byte[1]; System.arraycopy(bArr, 9, bArr2, 0, 1);
        mute_state = data[9]
        print(f"Mute State: {'OFF' if mute_state == 0 else 'ON'}")

def crc16(data: bytearray) -> int:
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

async def run(address):
    async with BleakClient(address) as client:
        logger.info(f"Connected: {client.is_connected}")

        await client.start_notify(NOTIFY_UUID, notification_handler)
        logger.info("Subscribed to notifications")

        logger.info("Writing 'Get Realtime Data' command...")
        await client.write_gatt_char(WRITE_UUID, CMD_GET_REALTIME, response=False)
        await asyncio.sleep(5)
        
        logger.info("Writing 'Get Sound Status' command...")
        await client.write_gatt_char(WRITE_UUID, CMD_GET_SOUND, response=False)
        await asyncio.sleep(5)

        logger.info("Disconnecting...")

async def scan():
    devices = await BleakScanner.discover()
    for d in devices:
        if d.name and "HTRAM" in d.name:
            print(f"Found Device: {d.name} - {d.address}")
            return d.address
    return None

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    address = loop.run_until_complete(scan())
    if address:
        loop.run_until_complete(run(address))
    else:
        print("No HTRAM device found.")
