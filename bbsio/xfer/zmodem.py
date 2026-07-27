"""ZMODEM(Lite) - 실제 ZMODEM(rz/sz, lrzsz 등) 와이어 프로토콜과 호환되는
BBS 최적화 서브셋 구현.

이전 버전은 자체 프레임 포맷(ZModemSender/Receiver끼리만 통신 가능)을 쓰고,
클라이언트 자동 인식을 위해 `b'rz waiting to receive... \\x1b\\x5a'` 같은
임의의 텍스트 문자열을 내보냈다. 하지만 SecureCRT/lrzsz/미니콤 등 실제
터미널 프로그램은 그런 커스텀 문자열이 아니라, 스트림에 실려 오는 *진짜*
ZMODEM 헤더 바이트열(`**\\x18B...`)을 직접 감지해서 업/다운로드 대화상자를
자동으로 띄운다. 그래서 이 구현은 다음을 실제 규격대로 구현한다:

  1. 헤더  - ZPAD(0x2A) ZPAD ZDLE(0x18) ZHEX('B') + 5바이트(타입+플래그/위치)의
     hex 인코딩 + CRC16 + 트레일러. (참고: Forsberg, "The ZMODEM Inter
     Application File Transfer Protocol")
  2. 핸드셰이크 - 수신측은 ZRINIT을 반복 전송해 수신 준비를 알리고, 송신측은
     ZRQINIT/ZFILE로 화답한다. 이 ZRINIT/ZFILE 헤더 자체가 터미널 프로그램의
     자동 인식 트리거이므로, 별도의 텍스트 신호가 필요 없다.
  3. 데이터 전송 - ZDLE 이스케이프된 바이트열 + 서브패킷 종료 마커
     (ZCRCE/ZCRCG/ZCRCQ/ZCRCW) + CRC16. 이 구현은 단순함과 견고함을 위해
     블록마다 ZCRCW로 끝내고 ZACK을 기다리는 stop-and-wait 방식만 쓴다
     (전화접속/텔넷 회선처럼 느리고 잡음 있는 채널에서는 실제 rz/sz의
     스트리밍 모드보다 이쪽이 안전하다).

주의: 헤더는 항상 HEX 인코딩으로만 보낸다(구현 단순화). 다만 받는 쪽에서는
HEX와 BIN(16비트 CRC) 헤더를 모두 해석할 수 있어 실제 sz/rz가 어느 쪽을
보내도 상호운용된다. 32비트 CRC(ZBIN32)와 파일 압축/이어받기 재개는
지원하지 않는다 - ZRINIT에 CANFC32를 알리지 않으므로 표준을 따르는 상대는
32비트 CRC를 쓰지 않는다.
"""

import time

from bbsio.xfer import transport
from bbsio.xfer.crc16 import crc16_ccitt

# --- ZMODEM 프로토콜 상수 (zmodem.h 관례를 그대로 따름) ---------------------

ZPAD = 0x2A   # '*' - 헤더 시작 패딩
ZDLE = 0x18   # Ctrl-X - 이스케이프 문자(ASCII CAN과 동일한 바이트)
ZBIN = 0x41   # 'A' - 16비트 CRC 바이너리 헤더
ZHEX = 0x42   # 'B' - 16비트 CRC HEX 헤더
XON = 0x11

ZCRCE = 0x68  # 'h' - 서브패킷 끝, 프레임 종료(뒤에 새 헤더가 옴)
ZCRCG = 0x69  # 'i' - 서브패킷 끝, 즉시 다음 서브패킷이 이어짐(ACK 불필요)
ZCRCQ = 0x6A  # 'j' - 서브패킷 끝, ZACK 요청하지만 프레임은 계속됨
ZCRCW = 0x6B  # 'k' - 서브패킷 끝, ZACK 요청 + 프레임 종료
_SUBPACKET_ENDS = (ZCRCE, ZCRCG, ZCRCQ, ZCRCW)

ZRQINIT = 0
ZRINIT = 1
ZACK = 3
ZFILE = 4
ZSKIP = 5
ZNAK = 6
ZFIN = 8
ZRPOS = 9
ZDATA = 10
ZEOF = 11

CANFDX = 0x01  # ZRINIT 능력 플래그(ZF0): 전이중 통신 가능
ZCBIN = 0x01   # ZFILE 변환 옵션(ZF0): 바이너리 전송(개행 변환 없음)

# 이스케이프가 필요한 바이트: ZDLE 자신, DLE/XON/XOFF(및 8bit 버전), CR(및 8bit
# 버전, '@' 뒤에 올 때만 필요하지만 항상 이스케이프해도 상호운용에는 문제없다).
_ESCAPE_NEEDED = frozenset({0x18, 0x0D, 0x8D, 0x10, 0x90, 0x11, 0x91, 0x13, 0x93})

DEFAULT_RETRY = 10
DEFAULT_TIMEOUT = 10  # 초 - 상대 응답 대기 시간 (전화 접속 등 느린 회선 고려)
DEFAULT_BLOCK_SIZE = 1024
META_MAX_BYTES = 8192  # ZFILE 메타데이터 서브패킷의 상한(잡음/오동작 대비 방어적 한도)


class ZModemError(Exception):
    pass


class ZModemCancelled(ZModemError):
    """상대(또는 우리)가 CAN을 보내 전송을 취소함."""
    pass


class ZModemTimeout(ZModemError):
    """재시도를 다 써도 상대 응답이 없어 전송을 포기함."""
    pass


class _GarbledHeader(ZModemError):
    """헤더/서브패킷의 CRC가 맞지 않음 - 내부적으로 재시도를 트리거하는 용도.
    (ZModemError를 상속해서, 어쩌다 바깥으로 새어 나가도 files.py의
    `except ZModemError`가 여전히 잡을 수 있게 한다.)"""
    pass


# --- ZDLE 이스케이프 -------------------------------------------------------

def _zdle_escape(data: bytes) -> bytes:
    if not any(b in _ESCAPE_NEEDED for b in data):
        return data
    out = bytearray()
    for b in data:
        if b in _ESCAPE_NEEDED:
            out.append(ZDLE)
            out.append(b ^ 0x40)
        else:
            out.append(b)
    return bytes(out)


# HEX 헤더 뒤의 CR/LF/XON 트레일러처럼 "있으면 소비하고 없으면 그대로 둬야"
# 하는 상황을 위한 1바이트 되돌리기(pushback) 슬롯. 세션당 프로세스가 한 번에
# 송신 또는 수신 하나만 수행하는 단일 스레드 흐름이므로 모듈 전역으로 둬도
# 안전하다(동시에 두 방향을 같은 프로세스에서 돌리지 않는 한).
_pushback = None


def _read_byte(timeout):
    global _pushback
    if _pushback is not None:
        b = _pushback
        _pushback = None
        return b
    return transport.read_byte(timeout)


def _unread_byte(b):
    global _pushback
    _pushback = b


def _read_byte_or_none(timeout):
    try:
        return _read_byte(timeout)
    except transport.TransportTimeout:
        return None


def _skip_hex_trailer(timeout):
    """HEX 헤더 뒤에는 표준적으로 CR (LF|0x80) (XON)이 따라온다(Forsberg 스펙).
    있으면 소비하고, 규격을 따르지 않는 상대라 없다면 읽은 바이트를 그대로
    되돌려 놓아 다음 파싱(다음 헤더 탐색이든 서브패킷 읽기든)에 영향이
    없게 한다."""
    b = _read_byte_or_none(timeout)
    if b is None:
        return
    if b != 0x0D:
        _unread_byte(b)
        return
    b = _read_byte_or_none(timeout)
    if b is None:
        return
    if b not in (0x0A, 0x8A):
        _unread_byte(b)
        return
    b = _read_byte_or_none(timeout)
    if b is None:
        return
    if b not in (0x11, 0x13):
        _unread_byte(b)


def _read_escaped_unit(timeout):
    """이스케이프된 스트림에서 논리적 단위 하나를 읽는다.
    ('data', byte) | ('end', end_type) | ('cancel', None) 중 하나를 반환한다.
    원시 XON/XOFF(흐름 제어가 끼워넣은 잡음 바이트)는 조용히 버리고 계속 읽는다."""
    while True:
        b = _read_byte(timeout)
        if b in (0x11, 0x13):
            continue
        if b != ZDLE:
            return ('data', b)
        b2 = _read_byte(timeout)
        if b2 in _SUBPACKET_ENDS:
            return ('end', b2)
        if b2 == ZDLE:
            return ('cancel', None)
        return ('data', b2 ^ 0x40)


# --- 헤더 생성 --------------------------------------------------------------

def _build_hex_header(frame_type, b1=0, b2=0, b3=0, b4=0) -> bytes:
    header = bytes([frame_type, b1, b2, b3, b4])
    crc = crc16_ccitt(header)
    hexstr = header.hex() + f'{crc:04x}'
    # 트레일러(CR, LF|0x80, XON)는 실제 lrzsz 구현체가 보내는 형태를 그대로
    # 따른 것 - 8bit 클린이 아닌 회선에서도 헤더 경계를 알아볼 수 있게 한다.
    return bytes([ZPAD, ZPAD, ZDLE, ZHEX]) + hexstr.encode('ascii') + bytes([0x0D, 0x8A, XON])


def _flag_header(frame_type, zf0=0, zf1=0, zf2=0, zf3=0) -> bytes:
    # 전송 순서는 ZF3,ZF2,ZF1,ZF0 (ZF0이 마지막 바이트) - zmodem.h 관례.
    return _build_hex_header(frame_type, zf3, zf2, zf1, zf0)


def _pos_header(frame_type, pos=0) -> bytes:
    # 전송 순서는 little-endian(ZP0이 최하위 바이트, 먼저 전송).
    return _build_hex_header(
        frame_type,
        pos & 0xFF, (pos >> 8) & 0xFF, (pos >> 16) & 0xFF, (pos >> 24) & 0xFF,
    )


def _build_data_subpacket(payload: bytes, end_type: int) -> bytes:
    crc = crc16_ccitt(payload + bytes([end_type]))
    return _zdle_escape(payload) + bytes([ZDLE, end_type]) + _zdle_escape(crc.to_bytes(2, 'big'))


# --- 헤더/서브패킷 파싱 ------------------------------------------------------

def _read_hex_header_body(timeout):
    raw = bytearray()
    for _ in range(14):  # 5바이트(타입+4필드) = hex 10자 + CRC16 hex 4자
        b = _read_byte_or_none(timeout)
        if b is None:
            raise _GarbledHeader('HEX 헤더 수신 중 시간 초과.')
        raw.append(b)
    try:
        text = bytes(raw).decode('ascii')
        payload = bytes.fromhex(text[:10])
        crc = int(text[10:14], 16)
    except ValueError:
        raise _GarbledHeader('HEX 헤더 형식이 올바르지 않습니다.')
    if crc16_ccitt(payload) != crc:
        raise _GarbledHeader('HEX 헤더 CRC 불일치.')
    _skip_hex_trailer(timeout)
    return payload[0], payload[1], payload[2], payload[3], payload[4]


def _read_bin_header_body(timeout):
    fields = bytearray()
    for _ in range(5):
        kind, val = _read_escaped_unit(timeout)
        if kind == 'cancel':
            raise ZModemCancelled('상대가 전송을 취소했습니다(CAN*2).')
        if kind != 'data':
            raise _GarbledHeader('BIN 헤더 필드가 올바르지 않습니다.')
        fields.append(val)
    crc_bytes = bytearray()
    for _ in range(2):
        kind, val = _read_escaped_unit(timeout)
        if kind == 'cancel':
            raise ZModemCancelled('상대가 전송을 취소했습니다(CAN*2).')
        if kind != 'data':
            raise _GarbledHeader('BIN 헤더 CRC가 올바르지 않습니다.')
        crc_bytes.append(val)
    crc = (crc_bytes[0] << 8) | crc_bytes[1]
    if crc16_ccitt(bytes(fields)) != crc:
        raise _GarbledHeader('BIN 헤더 CRC 불일치.')
    return fields[0], fields[1], fields[2], fields[3], fields[4]


def _read_header(retry, timeout):
    """잡음(에코, ANSI 시퀀스, 텔넷 협상 바이트 등) 속에서 ZPAD...ZDLE
    시퀀스를 찾을 때까지 스캔한 뒤 헤더 본문을 읽어 검증한다.
    (frame_type, b1, b2, b3, b4)를 반환한다."""
    max_attempts = retry * 64
    attempts = 0
    while attempts < max_attempts:
        b = _read_byte_or_none(timeout)
        attempts += 1
        if b is None or b != ZPAD:
            continue
        while b == ZPAD:
            b = _read_byte_or_none(timeout)
            attempts += 1
            if b is None:
                break
        if b is None or b != ZDLE:
            continue
        kind = _read_byte_or_none(timeout)
        attempts += 1
        if kind is None:
            continue
        if kind == ZDLE:
            raise ZModemCancelled('상대가 전송을 취소했습니다(CAN*2).')
        if kind == ZHEX:
            return _read_hex_header_body(timeout)
        if kind == ZBIN:
            return _read_bin_header_body(timeout)
        # ZBIN32(32비트 CRC)나 알 수 없는 타입 - 지원하지 않으므로 계속 스캔.
    raise ZModemTimeout('헤더 대기 시간 초과.')


def _read_data_subpacket(timeout, max_len=None):
    """데이터 서브패킷 하나를 읽어 (payload, end_type)을 반환한다."""
    payload = bytearray()
    while True:
        kind, val = _read_escaped_unit(timeout)
        if kind == 'cancel':
            raise ZModemCancelled('상대가 전송을 취소했습니다(CAN*2).')
        if kind == 'end':
            crc_bytes = bytearray()
            for _ in range(2):
                k2, v2 = _read_escaped_unit(timeout)
                if k2 == 'cancel':
                    raise ZModemCancelled('상대가 전송을 취소했습니다(CAN*2).')
                if k2 != 'data':
                    raise _GarbledHeader('서브패킷 CRC가 올바르지 않습니다.')
                crc_bytes.append(v2)
            crc = (crc_bytes[0] << 8) | crc_bytes[1]
            if crc16_ccitt(bytes(payload) + bytes([val])) != crc:
                raise _GarbledHeader('서브패킷 CRC 불일치.')
            return bytes(payload), val
        payload.append(val)
        if max_len is not None and len(payload) > max_len:
            raise ZModemError('허용된 최대 크기를 초과했습니다.')


# --- ZFILE 메타데이터 ---------------------------------------------------

def _build_zfile_payload(filename: str, size: int) -> bytes:
    name_bytes = filename.encode('utf-8')
    mtime_octal = format(int(time.time()), 'o')
    details = f'{size} {mtime_octal} 100644 0'.encode('ascii')
    return name_bytes + b'\x00' + details + b'\x00'


def _parse_zfile_payload(payload: bytes):
    nul = payload.find(b'\x00')
    if nul < 0:
        raise _GarbledHeader('ZFILE 페이로드에 파일명 종료 문자가 없습니다.')
    filename = payload[:nul].decode('utf-8', errors='replace')
    rest = payload[nul + 1:].split(b'\x00', 1)[0]
    parts = rest.split()
    size = int(parts[0]) if parts and parts[0].isdigit() else None
    return filename, size


class ZModemSender:
    """BBS -> 사용자 방향(다운로드)에서 이 BBS가 파일을 보내는 쪽일 때 사용.
    사용자 터미널은 표준 ZMODEM 수신측(rz 등) 역할을 한다."""

    def __init__(self, retry=DEFAULT_RETRY, timeout=DEFAULT_TIMEOUT, block_size=DEFAULT_BLOCK_SIZE):
        self.retry = retry
        self.timeout = timeout
        self.block_size = block_size

    def cancel(self):
        transport.write_bytes(bytes([ZDLE] * 5))  # CAN*5 (ZDLE == CAN 바이트값)

    def send(self, filename: str, data: bytes, progress_cb=None) -> bool:
        """filename과 data를 표준 ZMODEM(Lite)으로 전송한다.
        progress_cb(sent_bytes, total_bytes)가 있으면 매 블록마다 호출한다.
        성공하면 True, 취소/시간초과면 예외를 던진다."""
        transport.flush_input()
        try:
            return self._send_inner(filename, data, progress_cb)
        except ZModemError:
            try:
                self.cancel()
            except Exception:
                pass
            raise

    def _send_inner(self, filename, data, progress_cb):
        total = len(data)

        # 1) ZRQINIT을 반복 전송하며 ZRINIT을 기다린다. 이 헤더들이 바로
        #    SecureCRT 등 터미널의 자동 다운로드 감지 트리거다 - 수신측이
        #    이미 rz를 실행해 ZRINIT을 먼저 보내고 있었다면 즉시 잡힌다.
        transport.write_bytes(_build_hex_header(ZRQINIT))
        for _ in range(self.retry):
            try:
                ftype, *_ = _read_header(self.retry, self.timeout)
            except (transport.TransportTimeout, ZModemTimeout, _GarbledHeader):
                transport.write_bytes(_build_hex_header(ZRQINIT))
                continue
            if ftype == ZRINIT:
                break
            if ftype == ZFIN:
                transport.write_bytes(_build_hex_header(ZFIN))
                raise ZModemError('상대가 이미 세션을 종료했습니다.')
        else:
            raise ZModemTimeout('상대가 응답하지 않습니다 (ZRINIT 대기 시간 초과).')

        # 2) ZFILE 헤더 + 메타데이터(파일명/크기) 서브패킷 전송, ZRPOS 대기.
        zfile_payload = _build_zfile_payload(filename, total)

        def _send_zfile():
            transport.write_bytes(_flag_header(ZFILE, zf0=ZCBIN))
            transport.write_bytes(_build_data_subpacket(zfile_payload, ZCRCW))

        _send_zfile()
        start_pos = 0
        for _ in range(self.retry):
            try:
                ftype, b1, b2, b3, b4 = _read_header(self.retry, self.timeout)
            except (transport.TransportTimeout, ZModemTimeout, _GarbledHeader):
                _send_zfile()
                continue
            if ftype == ZRPOS:
                start_pos = b1 | (b2 << 8) | (b3 << 16) | (b4 << 24)
                break
            if ftype == ZSKIP:
                raise ZModemError('상대가 파일 수신을 건너뛰었습니다.')
        else:
            raise ZModemTimeout('상대 응답 대기 시간 초과 (ZRPOS).')

        if start_pos > total:
            start_pos = 0

        # 3) 데이터 전송 - 매 블록을 ZCRCW(= 프레임 종료 + ZACK 요청)로 끝내고
        #    ZACK을 기다리는 stop-and-wait 방식(진짜 rz/sz의 스트리밍보다
        #    느리지만, 전화접속/텔넷 회선에서는 정확성이 속도보다 중요하다).
        #    ZCRCW는 스펙상 "프레임 종료"이므로, 블록마다 새 ZDATA 헤더로
        #    다시 시작해야 한다(헤더 없이 서브패킷만 이어 보내면 안 됨).
        sent = start_pos
        while sent < total:
            chunk = data[sent:sent + self.block_size]
            packet = _build_data_subpacket(chunk, ZCRCW)
            acked = False
            next_sent = None
            for _ in range(self.retry):
                transport.write_bytes(_pos_header(ZDATA, sent))
                transport.write_bytes(packet)
                try:
                    ftype, b1, b2, b3, b4 = _read_header(self.retry, self.timeout)
                except (transport.TransportTimeout, ZModemTimeout, _GarbledHeader):
                    continue
                if ftype == ZACK:
                    acked = True
                    break
                if ftype == ZRPOS:
                    # 수신측이 오류를 감지해 재전송 위치를 알려줌 - 그 위치부터 다시.
                    next_sent = b1 | (b2 << 8) | (b3 << 16) | (b4 << 24)
                    break
            if next_sent is not None:
                sent = next_sent if next_sent <= total else 0
                continue
            if not acked:
                raise ZModemTimeout(f'데이터 블록(offset={sent}) 전송 재시도 초과.')
            sent += len(chunk)
            if progress_cb:
                progress_cb(sent, total)

        # 4) ZEOF 전송, 다음 파일 요청(ZRINIT) 또는 세션 종료(ZFIN) 대기.
        for _ in range(self.retry):
            transport.write_bytes(_pos_header(ZEOF, total))
            try:
                ftype, *_ = _read_header(self.retry, self.timeout)
            except (transport.TransportTimeout, ZModemTimeout, _GarbledHeader):
                continue
            if ftype in (ZRINIT, ZFIN):
                break
        else:
            raise ZModemTimeout('ZEOF 응답 대기 시간 초과.')

        # 5) 세션 종료 - ZFIN을 교환한 뒤 "OO"로 마무리.
        for _ in range(self.retry):
            transport.write_bytes(_build_hex_header(ZFIN))
            try:
                ftype, *_ = _read_header(self.retry, self.timeout)
            except (transport.TransportTimeout, ZModemTimeout, _GarbledHeader):
                continue
            if ftype == ZFIN:
                break
        transport.write_bytes(b'OO')

        if progress_cb and total == 0:
            progress_cb(0, 0)
        return True


class ZModemReceiver:
    """BBS -> 사용자 방향의 반대(업로드)에서 이 BBS가 파일을 받는 쪽일 때 사용.
    사용자 터미널은 표준 ZMODEM 송신측(sz 등) 역할을 한다."""

    def __init__(self, retry=DEFAULT_RETRY, timeout=DEFAULT_TIMEOUT, max_size=None):
        self.retry = retry
        self.timeout = timeout
        # 업로드 한도 미설정 시 무제한으로 받으면 악의적/오동작 클라이언트가
        # 디스크를 무한정 채울 수 있어 상한을 둔다 (files.py가 호출 시 지정).
        self.max_size = max_size

    def cancel(self):
        transport.write_bytes(bytes([ZDLE] * 5))  # CAN*5

    def receive(self, progress_cb=None):
        """송신측으로부터 표준 ZMODEM(Lite)으로 파일을 받아 (filename, data)를
        반환한다. progress_cb(received_bytes, total_bytes)가 있으면 블록마다
        호출한다."""
        transport.flush_input()
        try:
            return self._receive_inner(progress_cb)
        except ZModemError:
            try:
                self.cancel()
            except Exception:
                pass
            raise

    def _send_rinit(self):
        # 이 ZRINIT 헤더 자체가 SecureCRT 등 터미널의 자동 업로드 감지
        # 트리거다 - 사용자가 rz를 수동으로 띄우지 않아도 이 바이트열을 보고
        # 자동으로 sz(업로드)를 시작할 수 있다.
        transport.write_bytes(_flag_header(ZRINIT, zf0=CANFDX))

    def _receive_inner(self, progress_cb):
        # 1) ZRINIT을 반복 전송하며 ZFILE을 기다린다.
        self._send_rinit()
        for _ in range(self.retry * 3):
            try:
                ftype, *_ = _read_header(self.retry, self.timeout)
            except (transport.TransportTimeout, ZModemTimeout, _GarbledHeader):
                self._send_rinit()
                continue
            if ftype == ZFILE:
                break
            if ftype == ZRQINIT:
                self._send_rinit()
                continue
        else:
            raise ZModemTimeout('송신측이 응답하지 않습니다 (파일 전송 시작 대기 시간 초과).')

        # 2) ZFILE의 데이터 서브패킷(파일명/크기)을 읽는다.
        for _ in range(self.retry):
            try:
                meta_payload, _ = _read_data_subpacket(self.timeout, max_len=META_MAX_BYTES)
                break
            except (transport.TransportTimeout, _GarbledHeader):
                transport.write_bytes(_build_hex_header(ZNAK))
        else:
            raise ZModemTimeout('파일 메타데이터 수신 대기 시간 초과.')

        filename, size_hint = _parse_zfile_payload(meta_payload)
        if self.max_size is not None and size_hint is not None and size_hint > self.max_size:
            transport.write_bytes(_build_hex_header(ZSKIP))
            raise ZModemError(f'허용된 최대 크기({self.max_size}바이트)를 초과했습니다.')

        # 3) 처음부터 받겠다고 응답(ZRPOS 0) - 이어받기(재개)는 지원하지 않는다.
        transport.write_bytes(_pos_header(ZRPOS, 0))

        # 4) ZDATA 헤더 + 데이터 서브패킷들을 받는다.
        chunks = []
        received = 0
        header = self._wait_for_data_or_eof()
        while header[0] == ZDATA:
            while True:
                remaining_budget = None
                if self.max_size is not None:
                    remaining_budget = max(0, self.max_size - received) + 1
                try:
                    payload, end_type = _read_data_subpacket(self.timeout, max_len=remaining_budget)
                except (transport.TransportTimeout, _GarbledHeader):
                    transport.write_bytes(_pos_header(ZRPOS, received))
                    break
                chunks.append(payload)
                received += len(payload)
                if self.max_size is not None and received > self.max_size:
                    raise ZModemError(f'허용된 최대 크기({self.max_size}바이트)를 초과했습니다.')
                if progress_cb:
                    progress_cb(received, size_hint or received)
                if end_type in (ZCRCW, ZCRCQ):
                    transport.write_bytes(_pos_header(ZACK, received))
                if end_type in (ZCRCE, ZCRCW):
                    break  # 이 ZDATA 프레임 끝 - 다음은 새 헤더(ZEOF 등).
                # ZCRCG면 서브패킷이 바로 이어지므로 계속 읽는다.
            header = self._wait_for_data_or_eof()

        if header[0] != ZEOF:
            raise _GarbledHeader('ZEOF 대신 알 수 없는 프레임을 받았습니다.')

        # 5) 다음 파일 없음을 알리기 위해 ZRINIT을 다시 보내고 ZFIN을 기다린다.
        for _ in range(self.retry):
            self._send_rinit()
            try:
                ftype, *_ = _read_header(self.retry, self.timeout)
            except (transport.TransportTimeout, ZModemTimeout, _GarbledHeader):
                continue
            if ftype == ZFIN:
                break
        transport.write_bytes(_build_hex_header(ZFIN))
        # 상대의 "OO" 마무리 신호는 오면 흡수하되, 안 와도 세션 종료에는
        # 영향이 없으므로 조용히 넘어간다.
        try:
            transport.read_exact(2, 1)
        except Exception:
            pass

        data = b''.join(chunks)
        if progress_cb and received == 0:
            progress_cb(0, 0)
        return filename, data

    def _wait_for_data_or_eof(self):
        for _ in range(self.retry):
            try:
                ftype, b1, b2, b3, b4 = _read_header(self.retry, self.timeout)
            except (transport.TransportTimeout, ZModemTimeout, _GarbledHeader):
                transport.write_bytes(_build_hex_header(ZNAK))
                continue
            if ftype in (ZDATA, ZEOF):
                return (ftype, b1, b2, b3, b4)
            # 그 외(중복 ZFILE 등)는 무시하고 계속 대기.
        raise ZModemTimeout('데이터 프레임 대기 시간 초과.')
