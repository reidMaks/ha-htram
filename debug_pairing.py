import asyncio
import logging
from bleak import BleakClient, BleakScanner, BleakError
from bleak_retry_connector import establish_connection

# Configure logging to see what's happening
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("debug_pairing")

async def run():
    print("Scanning for HTRAM devices...")
    device = None
    devices = await BleakScanner.discover()
    for d in devices:
        if d.name and ("HTRAM" in d.name or "Storm_Shadow" in d.name):
            print(f"Found Device: {d.name} - {d.address}")
            device = d
            break
    
    if not device:
        print("No HTRAM device found. Make sure it is powered on.")
        return

    print(f"Attempting to connect to {device.address}...")
    
    try:
        # Use establish_connection just like in the integration
        client = await establish_connection(BleakClient, device, device.address)
        try:
            print(f"Connected: {client.is_connected}")
            
            # Check if paired
            # Note: Bleak on Linux doesn't always show 'paired' state correctly in client properties immediately
            # depending on backend.
            
            print("Attempting to pair...")
            try:
                await client.pair()
                print("Pairing command sent/completed successfully.")
            except NotImplementedError:
                print("Pairing not implemented on this backend (might be auto-handled).")
            except Exception as e:
                print(f"Pairing failed: {e}")

            # Try to read a service to verify connection is usable
            print("Discovering services...")
            for service in client.services:
                print(f"Service: {service.uuid}")

        finally:
            print("Disconnecting...")
            await client.disconnect()

    except Exception as e:
        print(f"FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run())
