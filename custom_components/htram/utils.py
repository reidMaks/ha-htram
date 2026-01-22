import struct
from typing import List, Union

class CRC16:
    """CRC16 implementation ported from Android app."""
    CRC16_TABLE = [
        0, 4129, 8258, 12387, 16516, 20645, 24774, 28903, 33032, 37161, 41290, 45419, 49548, 53677, 57806, 61935,
        4657, 528, 12915, 8786, 21173, 17044, 29431, 25302, 37689, 33560, 45947, 41818, 54205, 50076, 62463, 58334,
        9314, 13379, 1056, 5121, 25830, 29895, 17572, 21637, 42346, 46411, 34088, 38153, 58862, 62927, 50604, 54669,
        13907, 9842, 5649, 1584, 30423, 26358, 22165, 18100, 46939, 42874, 38681, 34616, 63455, 59390, 55197, 51132,
        18628, 22757, 26886, 31015, 2112, 6241, 10370, 14499, 51660, 55789, 59918, 64047, 35144, 39273, 43402, 47531,
        23285, 19156, 31543, 27414, 6769, 2640, 15027, 10898, 56317, 52188, 64575, 60446, 39801, 35672, 48059, 43930,
        27942, 23813, 19684, 15555, 11426, 7297, 3168, -4081, 60974, 56845, 52716, 48587, 44458, 40329, 36200, 32071,
        32535, 28470, 24277, 20212, 16019, 11954, 7761, 3696, 65567, 61502, 57309, 53244, 49051, 44986, 40793, 36728,
        37256, 33190, 45514, 41449, 53772, 49707, 62030, 57965, 4224, 158, 12482, 8417, 20740, 16675, 28998, 24933,
        41913, 37783, 50171, 46041, 58429, 54299, 66687, 62557, 8881, 4751, 17139, 13009, 25397, 21267, 33655, 29525,
        46570, 42440, 38312, 34182, 63086, 58956, 54828, 50698, 13538, 9408, 5280, 1150, 30054, 25924, 21796, 17666,
        51163, 47097, 42905, 38839, 67679, 63613, 59421, 55355, 18131, 14065, 9873, 5807, 34647, 30581, 26389, 22323,
        55884, 51755, 64142, 60013, 39368, 35239, 47626, 43497, 22852, 18723, 31110, 26981, 6336, 2207, 14594, 10465,
        60541, 56412, 52283, 48154, 44025, 39896, 35767, 31638, 27509, 23380, 19251, 15122, 10993, 6864, 2735, -2664,
        65198, 61069, 56940, 52811, 48682, 44553, 40424, 36295, 32166, 28037, 23908, 19779, 15650, 11521, 7392, 3263,
        69791, 65726, 61533, 57468, 53275, 49210, 45017, 40952, 36759, 32694, 28501, 24436, 20243, 16178, 11985, 7920
    ]

    # Correcting negative values from Java's signed short behavior manually if needed
    # but in Python we can use unsigned logic and mask with 0xFFFF

    @staticmethod
    def get_crc_table_value(index: int) -> int:
        val = CRC16.CRC16_TABLE[index]
        if val > 32767: # Handling Java signed short overflow artifact if present in copypaste
             pass # The table looks like it has some negative values?
             # Let's re-verify the table source.
             # In the Java file: 32783, 10, etc. The negative-looking ones in my paste might be due to tool output issues?
             # Wait, the `CRC16.java` tool output showed `ServiceStarter.ERROR_UNKNOWN` etc.
             # I should re-read the Java file CAREFULLY or generate the table.
             # The Java code says `gPloy = 4129` which is 0x1021. This is CRC-16-CCITT/XMODEM standard.
             # Initial value is 0.
        return val

    @staticmethod
    def crc16_short(data: bytes) -> int:
        """Calculates CRC16 using the standard CCITT (0x1021) polynomial."""
        # Implementing the algorithmic approach from the Java `getCrcOfByte` to ensure match
        # Java:
        # private short getCrcOfByte(int i) {
        #    int i2 = i << 8;
        #    for (int i3 = 7; i3 >= 0; i3--) {
        #        i2 = (32768 & i2) != 0 ? (i2 << 1) ^ this.gPloy : i2 << 1;
        #    }
        #    return (short) (i2 & 65535);
        # }
        # gPoly = 4129 (0x1021)
        
        # However, the table in Java is precomputed.
        # Let's use `crc16MakeTableMethod` logic from Java.
        # s (accum) = 0
        # loop data:
        #   index = (b & 0xFF) ^ ((s >>> 8) & 0xFF)
        #   s = (s << 8) ^ TABLE[index]
        
        crc = 0
        for byte in data:
            index = (byte ^ (crc >> 8)) & 0xFF
            # We need the correct table value.
            # Let's generate the table value on the fly to avoid copy-paste errors from the 'view_file' output
            # which had weird constants like `ExifInterface.DATA_PACK_BITS_COMPRESSED`.
            
            table_val = CRC16._get_crc_of_byte(index)
            crc = ((crc << 8) & 0xFFFF) ^ table_val
            
        return crc & 0xFFFF

    @staticmethod
    def _get_crc_of_byte(i: int) -> int:
        g_poly = 0x1021
        i2 = i << 8
        for _ in range(8):
            if (0x8000 & i2) != 0:
                i2 = (i2 << 1) ^ g_poly
            else:
                i2 = i2 << 1
        return i2 & 0xFFFF

    @staticmethod
    def crc16_bytes(data: bytes) -> bytes:
        crc = CRC16.crc16_short(data)
        # Java: short2bytes(s) -> bArr[0] = s%256 (low?), bArr[1] = s>>8 (high?)
        # Let's check `CRCCodeUtil.java`:
        # bArr[1] = (byte) (s % 256);  <-- index 1 is LOW byte
        # bArr[0] = (byte) (s >> 8);   <-- index 0 is HIGH byte (Wait, loop i=1; i>=0; i--)
        # Loop: i=1: bArr[1] = s%256. s=s>>8.
        #       i=0: bArr[0] = s%256. 
        # So it is Big Endian? 
        # Java: return short2bytes(crc16Short(bArr));
        # short2bytes:
        # byte[] bArr = new byte[2];
        # for (int i = 1; i >= 0; i--) {
        #     bArr[i] = (byte) (s % 256);
        #     s = (short) (s >> 8);
        # }
        # Iteration 1 (i=1): bArr[1] = low byte. s shifts right.
        # Iteration 2 (i=0): bArr[0] = high byte.
        # Result: [High, Low]. Yes, standard Network Byte Order (Big Endian).
        
        return struct.pack(">H", crc)

    @staticmethod
    def crc16_bytes_le(data: bytes) -> bytes:
        """Little endian CRC for some specific packets if needed."""
        crc = CRC16.crc16_short(data)
        return struct.pack("<H", crc)


def build_command_packet(cmd_head: bytes, payload_parts: List[bytes]) -> bytes:
    """
    Constructs a command packet following the app's structure:
    Merge(Head, Payload..., CRC(Head+Payload), Tail)
    """
    # Merge all parts
    merged = cmd_head
    for part in payload_parts:
        merged += part
        
    # Calculate CRC of the merged data
    crc = CRC16.crc16_bytes(merged)
    
    # Append CRC and Tail
    # Tail is always {125} -> 0x7D
    return merged + crc + b'\x7D'


def construct_submit_ssid(ssid: str, password: str) -> bytes:
    """
    Constructs the 7460 command (submitSSID).
    Structure from CMBLERequest.java:
    Head: {123, 65, 0, 12, 116, 96, 1} -> 7B 41 00 0C 74 60 01
    Payload:
      - 22 bytes of zeros
      - 1 byte: Password Length
      - Password bytes (padded to 64 bytes with zeros)
      - SSID bytes (padded to 33 bytes with zeros)
      - 33 bytes of zeros
    
    Total packet is wrapped with CRC and 0x7D.
    Note: The java code updates byte[3] (length?) before sending?
    `bArrByteMergerAll[3] = (byte) ((bArrByteMergerAll.length - 1) & 255);`
    Yes, byte 3 is the length of the packet (excluding the last byte? or something).
    It effectively sets the length field in the header.
    """
    
    pwd_bytes = password.encode('utf-8')
    ssid_bytes = ssid.encode('utf-8')
    
    # Base Head
    # 0x7B (123), 0x41 (65), 0x00, 0x0C (Length placeholder), 0x74, 0x60, 0x01
    head = bytearray([0x7B, 0x41, 0x00, 0x0C, 0x74, 0x60, 0x01])
    
    # Zeros 22 bytes
    zeros_22 = b'\x00' * 22
    
    # Password Length
    pwd_len = len(pwd_bytes) & 0xFF
    
    # Password Padded (64 bytes)
    pwd_padded = pwd_bytes + b'\x00' * (64 - len(pwd_bytes))
    
    # SSID Padded (33 bytes)
    ssid_padded = ssid_bytes + b'\x00' * (33 - len(ssid_bytes))
    
    # Zeros 33 bytes
    zeros_33 = b'\x00' * 33
    
    # Merge for CRC calculation (and Length fix)
    # Note: `byteMergerAll` in Java merges everything BEFORE CRC.
    # The logic:
    # 1. Merge Head + Zeros22 + PwdLen + Pwd + SSID + Zeros33
    # 2. Update Head[3] with (TotalLength - 1)
    # 3. Append CRC
    # 4. Append 0x7D
    
    content = head + zeros_22 + bytes([pwd_len]) + pwd_padded + ssid_padded + zeros_33
    
    # Update length
    # In Java: bArrByteMergerAll[3] = (byte) ((bArrByteMergerAll.length - 1) & 255);
    # This implies the length byte covers everything except the CRC and Tail? Or maybe up to that point?
    # Actually, in general, packet length fields usually cover the payload.
    # But here, `bArrByteMergerAll` contains EVERYTHING so far.
    # Let's count.
    content_len = len(content)
    content[3] = (content_len - 1) & 0xFF
    
    crc = CRC16.crc16_bytes(content)
    
    # Checksum verification with Java logic:
    # Java does `crc16Bytes` on the `content`.
    
    return content + crc + b'\x7D'


def construct_submit_aes_key(aes_key: str, aes_iv: str, mqtt_server: str) -> bytes:
    """
    Constructs the 20b0 command (submitAESKey).
    This seemingly handles MOV1 (Single packet?) but `WifiListPrecenter` has logic for MOV2 (Multipart).
    Let's implement the simpler MOV1 logic first which is `submitAESKey`.
    
    Head: {123, 65, 0, 12, 32, -80 (0xB0), 1} -> 7B 41 00 0C 20 B0 01
    Payload:
      - Key Length (1 byte)
      - Key Bytes
      - IV Length (1 byte)
      - IV Bytes
      - Server Length (1 byte)
      - Server Bytes
      
    Java Code notes: `Base64.decode(str, 0)` for Key. Wait.
    `submitAESKey(preferenceStringValue, ...)`
    The `preferenceStringValue` (Key) seems to be stored as Base64 string?
    In `WifiListPrecenter.java`, `enrollResponse` stores `aesKey`.
    Usually `aesKey` from API is hex or base64. 
    `CMBLERequest.submitAESKey`: `byte[] bArrDecode = Base64.decode(str, 0);`
    So yes, the input `aes_key` string is expected to be Base64.
    
    IV: `byte[] bytes = str2.getBytes();` -> IV is passed as raw string bytes? 
    Wait. `aesKey` is Base64 decoded. `aesIv` is `getBytes()`.
    That's inconsistent but that's what the code says:
    `byte[] bytes = str2.getBytes();` (str2 is aesIv)
    `byte[] bytes2 = str3.getBytes();` (str3 is mqttServer)
    
    So Key is binary (decoded from B64), IV is String-as-bytes, URL is String-as-bytes.
    
    Wait, `aesIv` is usually hex or base64 too.
    In `WifiListPrecenter.java`:
    `PreferenceUtils.setPreferenceStringValue(..., PreferenceUtils.KEY_AESIV, enrollDeviceInfo.getAesIv());`
    
    If `enrollDeviceInfo` comes from JSON, it's a string.
    If the device expects `str2.getBytes()`, then it expects the ASCII characters of the IV string?
    Or is `aesIv` actually a simple string like "1234567890123456"?
    
    Let's assume for our custom provisioning we will pass everything as expected.
    If we generate a key, we should encode it as Base64 before passing to this function if we want to mimic the signature,
    OR we just change this function to accept bytes.
    Let's accept strings to be safe, but be aware of the Base64 decoding for Key.
    """
    import base64
    
    # Key is Base64 encoded string in the Input, but we decode it to bytes for the packet
    # If the user provides a raw 16-char string key, we might need to handle that.
    # The Java code strictly does Base64.decode.
    # So if we want to set key="1234...", we should Base64 encode it first if we use this function strictly.
    # BUT, to make `utils.py` friendly, let's allow `aes_key` to be a hex string or raw bytes?
    # No, the device receives BYTES. The wrapper function just prepares them.
    # Let's support `aes_key` as Hex String or Base64 String?
    # The Java app treats it as Base64.
    
    try:
        key_bytes = base64.b64decode(aes_key)
    except:
        # Fallback: maybe it's just raw bytes in a string?
        key_bytes = aes_key.encode('utf-8')
        
    iv_bytes = aes_iv.encode('utf-8')
    server_bytes = mqtt_server.encode('utf-8')
    
    head = bytearray([0x7B, 0x41, 0x00, 0x0C, 0x20, 0xB0, 0x01])
    
    payload = (
        bytes([len(key_bytes)]) + key_bytes +
        bytes([len(iv_bytes)]) + iv_bytes +
        bytes([len(server_bytes)]) + server_bytes
    )
    
    content = head + payload
    
    # Update length
    content_len = len(content)
    content[3] = (content_len - 1) & 0xFF
    
    crc = CRC16.crc16_bytes(content)
    
    return content + crc + b'\x7D'
