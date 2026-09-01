"""Constants for the Honeywell HTRAM integration.

Command frames are not stored here. They are built by :mod:`protocol`, which
computes the checksum, so a frame cannot drift out of sync with its CRC the way
the previous hardcoded table did.
"""

from . import protocol

DOMAIN = "htram"

# Bluetooth UUIDs
SERVICE_UUID = "FC247940-6E08-11E4-80FC-0002A5D5C51B"
NOTIFY_UUID = "F833D6C0-6E0B-11E4-9136-0002A5D5C51B"
WRITE_UUID = "3D115840-6E0B-11E4-B24F-0002A5D5C51B"

# How often to poll over Bluetooth, in seconds.
POLL_INTERVAL = 60

# Telemetry topics. The device publishes on C/ and subscribes on D/, using its
# serial number as both the client id and the topic suffix.
TELEMETRY_TOPIC = "C/{serial}"

# Options keys.
CONF_MQTT_ENABLED = "mqtt_enabled"
CONF_SERIAL = "serial"
CONF_SSID = "ssid"
CONF_MQTT_SERVER = "mqtt_server"

# Generated once and kept so re-provisioning does not change them. The vendor
# cloud minted these per device; nothing observable uses them, but the frame
# carrying the broker address carries them too.
CONF_AES_KEY = "aes_key"
CONF_AES_IV = "aes_iv"

# The device publishes every 30 s. Ten missed messages is a clear enough signal
# that it is gone, without reacting to a single dropped one.
MQTT_STALE_AFTER = 300

# Keys the MQTT payload carries. Everything else stays on Bluetooth.
MQTT_KEYS = ("co2", "temperature", "humidity")

# Built once at import: these have no arguments, so the frames never vary.
# Commands that take a value are built at the call site from protocol.
CMD_GET_REALTIME = protocol.realtime()
CMD_HEARTBEAT = protocol.heartbeat()
CMD_GET_SOUND_STATUS = protocol.sound_status()
CMD_GET_SETTINGS = protocol.settings()
