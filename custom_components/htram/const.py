"""Constants for the Honeywell HTRAM Air Monitor integration."""

DOMAIN = "htram"

# Bluetooth UUIDs
SERVICE_UUID = "FC247940-6E08-11E4-80FC-0002A5D5C51B"
NOTIFY_UUID = "F833D6C0-6E0B-11E4-9136-0002A5D5C51B"
WRITE_UUID = "3D115840-6E0B-11E4-B24F-0002A5D5C51B"

# Commands (Byte Arrays)
# 7B 41 00 07 40 44 02 00 FC 3E 7D
# 7B 41 00 07 40 44 02 00 FC 3E 7D
CMD_GET_REALTIME = b"\x7B\x41\x00\x07\x40\x44\x02\x00\xFC\x3E\x7D"

# Heartbeat (Keep Alive) - 24 01
# 7B 41 00 06 24 01 01 78 22 7D
CMD_HEARTBEAT = b"\x7B\x41\x00\x06\x24\x01\x01\x78\x22\x7D"

# 7B 41 00 07 26 23 01 00 09 C0 7D
CMD_GET_SOUND_STATUS = b"\x7B\x41\x00\x07\x26\x23\x01\x00\x09\xC0\x7D"

# CMD_SET_SOUND_OFF (Verified from Java Source)
# 7B 41 00 09 26 43 01 00 00 00 AB 63 7D
CMD_SET_SOUND_OFF = b"\x7B\x41\x00\x09\x26\x43\x01\x00\x00\x00\xAB\x63\x7D"

# CMD_SET_SOUND_ON (Derived from Java Source: submitAlarmSoundStatusOn)
# Byte 10 is 1 (0x01). CRC is 43 102 -> 2B 66 (0x2B66)
# Java: {123, 65, 0, 9, 38, 67, 1, 0, 0, 1, 43, 102, 125}
# 43 = 0x2B. 102 = 0x66.
CMD_SET_SOUND_ON = b"\x7B\x41\x00\x09\x26\x43\x01\x00\x00\x01\x2B\x66\x7D"
 
# Screen Off
# 7B 41 00 09 40 43 04 00 60 06 EF 17 7D (Read Settings - includes screen off)
CMD_GET_SETTINGS = b"\x7B\x41\x00\x09\x40\x43\x04\x00\x60\x06\xEF\x17\x7D"

# Polling Interval
POLL_INTERVAL = 60 

# Temperature Unit
# Fetch: 7B 41 00 07 20 6E 02 06 7E 30 7D
CMD_GET_TEMP_UNIT = b"\x7B\x41\x00\x07\x20\x6E\x02\x06\x7E\x30\x7D"
# Set C: 7B 41 00 08 22 32 02 06 00 A9 E3 7D
CMD_SET_TEMP_UNIT_C = b"\x7B\x41\x00\x08\x22\x32\x02\x06\x00\xA9\xE3\x7D"
# Set F: 7B 41 00 08 22 32 02 06 01 29 E6 7D
CMD_SET_TEMP_UNIT_F = b"\x7B\x41\x00\x08\x22\x32\x02\x06\x01\x29\xE6\x7D"

# Alert Values
# Get: CMD_GET_SETTINGS (0x4043) is used.
# Set: 0x4243. Payload: [Low_Hi][Low_Lo][High_Hi][High_Lo][Delay_Hi][Delay_Lo]
# Command Header: 7B 41 00 0B 42 43 04 00 20 00 ... 7D
# Note: The '20 00' might vary or be fixed.

