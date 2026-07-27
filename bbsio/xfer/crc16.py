"""CRC-16/CCITT (XModem) - 다항식 0x1021, 초기값 0x0000, 룩업 테이블 방식."""

POLYNOMIAL = 0x1021
INITIAL = 0x0000


def _build_table():
    table = []
    for byte in range(256):
        crc = byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ POLYNOMIAL) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
        table.append(crc)
    return table


_TABLE = _build_table()


def crc16_ccitt(data: bytes, crc: int = INITIAL) -> int:
    for b in data:
        crc = ((crc << 8) & 0xFFFF) ^ _TABLE[((crc >> 8) ^ b) & 0xFF]
    return crc & 0xFFFF
