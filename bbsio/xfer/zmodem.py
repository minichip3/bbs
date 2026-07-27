"""ZMODEM(Lite) - BBS 최적화 서브셋 구현.

진짜 ZMODEM(rz/sz, lrzsz 등)은 ZDLE 이스케이프, 16/32비트 CRC 전환, 헤더의
hex/binary 이중 인코딩, 여러 서브패킷 타입, 중단된 전송 재개(crash recovery)
등을 포함하는 매우 복잡한 프로토콜이다. 여기서는 그중 이 BBS가 실제로 필요로
하는 핵심 개념만 구현한다:

  1. 핸드셰이크  - 수신측이 'rz\\r'을 반복 전송해 수신 준비를 알리고, 송신측은
     그 시퀀스를 감지하면 'sz\\r'로 화답한 뒤 전송을 시작한다.
  2. 메타데이터 헤더 - 데이터 블록에 앞서 파일명/크기를 담은 프레임을 먼저
     보낸다. XModem과 달리 수신측이 파일명·크기를 미리 알아야 하는 불편이
     없고, 패딩 바이트(SUB) 트레일링을 추정해 잘라내는 문제도 없다(정확한
     크기를 알고 있으므로).
  3. 데이터 전송 - XModem과 비슷하게 블록마다 CRC16 검증 + ACK/NAK로 재전송을
     제어하되, 프레임에 길이가 명시돼 있어 마지막 블록도 패딩 없이 정확한
     길이로 전달된다.

주의: 이 구현은 실제 rz/sz(lrzsz 등)와 와이어 프로토콜이 호환되지 않는다 -
BBS의 ZModemSender/ZModemReceiver 두 구현체끼리만 통신할 수 있는 자체
프로토콜이다(XModem처럼 범용 터미널 클라이언트와 상호운용되지 않음).
"""

from bbsio.xfer import transport
from bbsio.xfer.crc16 import crc16_ccitt

CAN = 0x18
ACK = 0x06
NAK = 0x15

FRAME_META = 0x4D  # 'M' - 파일명/크기 메타데이터
FRAME_DATA = 0x44  # 'D' - 데이터 블록
FRAME_END = 0x45   # 'E' - 전송 종료(XModem의 EOT에 해당)

HELLO_RECV = b'rz\r'  # 수신측이 보내는 시작 시퀀스("receive zmodem")
HELLO_SEND = b'sz\r'  # 핸드셰이크 성사 후 송신측이 화답하는 시퀀스("send zmodem")

DEFAULT_RETRY = 10
DEFAULT_TIMEOUT = 10  # 초 - 상대 응답 대기 시간 (전화 접속 등 느린 회선 고려)
DEFAULT_BLOCK_SIZE = 1024
MAX_FILENAME_BYTES = 255  # 메타데이터 프레임의 파일명 길이 필드가 1바이트라 상한이 있음


class ZModemError(Exception):
    pass


class ZModemCancelled(ZModemError):
    """상대(또는 우리)가 CAN을 보내 전송을 취소함."""
    pass


class ZModemTimeout(ZModemError):
    """재시도를 다 써도 상대 응답이 없어 전송을 포기함."""
    pass


def _pack_frame(frame_type, seq, payload):
    """type(1) seq(1) len(2, big-endian) payload crc16(2) 순서로 프레임을 만든다.
    CRC는 헤더(type+seq+len)까지 포함해 계산해서 헤더 손상도 함께 검증한다."""
    header = bytes([frame_type, seq & 0xFF]) + len(payload).to_bytes(2, 'big')
    crc = crc16_ccitt(header + payload)
    return header + payload + crc.to_bytes(2, 'big')


def _wait_for_token(token, retry, timeout):
    """noise 속에서 token 바이트열을 슬라이딩 매칭으로 찾을 때까지 기다린다.
    CAN을 만나면 즉시 취소로 간주한다."""
    matched = 0
    max_attempts = retry * len(token) * 4
    for _ in range(max_attempts):
        try:
            b = transport.read_byte(timeout)
        except transport.TransportTimeout:
            matched = 0
            continue
        if b == CAN:
            raise ZModemCancelled('상대가 전송을 취소했습니다.')
        if b == token[matched]:
            matched += 1
            if matched == len(token):
                return
        else:
            matched = 1 if b == token[0] else 0
    raise ZModemTimeout('시작 시퀀스 대기 시간 초과.')


class ZModemSender:
    """BBS -> 사용자 방향(다운로드)에서 이 BBS가 파일을 보내는 쪽일 때 사용."""

    def __init__(self, retry=DEFAULT_RETRY, timeout=DEFAULT_TIMEOUT, block_size=DEFAULT_BLOCK_SIZE):
        self.retry = retry
        self.timeout = timeout
        self.block_size = block_size

    def _send_frame_and_wait_ack(self, frame_type, seq, payload):
        packet = _pack_frame(frame_type, seq, payload)
        for _ in range(self.retry):
            transport.write_bytes(packet)
            try:
                reply = transport.read_byte(self.timeout)
            except transport.TransportTimeout:
                continue
            if reply == ACK:
                return
            if reply == CAN:
                raise ZModemCancelled('수신측이 전송을 취소했습니다.')
            # NAK나 알 수 없는 바이트 - 같은 프레임 재전송.
        raise ZModemTimeout(f'프레임(seq={seq}) 전송 재시도 초과.')

    def cancel(self):
        transport.write_bytes(bytes([CAN, CAN]))

    def send(self, filename: str, data: bytes, progress_cb=None) -> bool:
        """filename과 data를 ZMODEM(Lite)으로 전송한다.
        progress_cb(sent_bytes, total_bytes)가 있으면 매 블록마다 호출한다.
        성공하면 True, 취소/시간초과면 예외를 던진다."""
        transport.flush_input()
        _wait_for_token(HELLO_RECV, self.retry, self.timeout)
        transport.write_bytes(HELLO_SEND)

        name_bytes = filename.encode('utf-8')[:MAX_FILENAME_BYTES]
        total = len(data)
        meta_payload = (
            len(name_bytes).to_bytes(1, 'big') + name_bytes + total.to_bytes(8, 'big')
        )

        try:
            self._send_frame_and_wait_ack(FRAME_META, 0, meta_payload)

            seq = 1
            sent = 0
            while sent < total:
                chunk = data[sent:sent + self.block_size]
                self._send_frame_and_wait_ack(FRAME_DATA, seq, chunk)
                sent += len(chunk)
                seq = (seq + 1) & 0xFF
                if progress_cb:
                    progress_cb(sent, total)

            self._send_frame_and_wait_ack(FRAME_END, seq, b'')
            if progress_cb and total == 0:
                progress_cb(0, 0)
            return True
        except ZModemError:
            try:
                self.cancel()
            except Exception:
                pass
            raise


class ZModemReceiver:
    """BBS -> 사용자 방향의 반대(업로드)에서 이 BBS가 파일을 받는 쪽일 때 사용."""

    def __init__(self, retry=DEFAULT_RETRY, timeout=DEFAULT_TIMEOUT, max_size=None):
        self.retry = retry
        self.timeout = timeout
        # 업로드 한도 미설정 시 무제한으로 받으면 악의적/오동작 클라이언트가
        # 디스크를 무한정 채울 수 있어 상한을 둔다 (files.py가 호출 시 지정).
        self.max_size = max_size

    def _send_ack(self):
        transport.write_bytes(bytes([ACK]))

    def _send_nak(self):
        transport.write_bytes(bytes([NAK]))

    def cancel(self):
        transport.write_bytes(bytes([CAN, CAN]))

    def _start_and_get_first_header(self):
        """'rz'를 전송해 수신 준비를 알리고 첫 프레임 헤더(FRAME_META)를 기다린다.
        hello는 타임아웃(=상대가 못 들었을 가능성)에만 재전송한다 - 잡음 바이트
        하나 볼 때마다 재전송하면 'sz' 화답이나 META 프레임 바이트 자체가 다시
        hello 폭주를 유발해 송신측이 ACK 대기 중 그걸 NAK로 오인하게 된다."""
        transport.write_bytes(HELLO_RECV)
        for _ in range(self.retry * 8):
            try:
                b = transport.read_byte(self.timeout)
            except transport.TransportTimeout:
                transport.write_bytes(HELLO_RECV)
                continue
            if b == FRAME_META:
                return b
            if b == CAN:
                raise ZModemCancelled('송신측이 전송을 취소했습니다.')
            # 'sz\r' 핸드셰이크 화답이나 잡음 - hello는 재전송하지 않고 계속 읽기만 한다.
        raise ZModemTimeout('송신측이 응답하지 않습니다 (파일 전송 시작 대기 시간 초과).')

    def _read_header_with_retry(self):
        for _ in range(self.retry):
            try:
                return transport.read_byte(self.timeout)
            except transport.TransportTimeout:
                self._send_nak()
        raise ZModemTimeout('다음 프레임 대기 시간 초과.')

    def _read_frame_rest(self, frame_type):
        """헤더 바이트(type) 다음의 seq/len/payload/crc를 읽고 검증한다.
        (block_ok, seq, payload) 튜플을 반환하며, 검증 실패 시 block_ok=False."""
        try:
            head_rest = transport.read_exact(3, self.timeout)
        except transport.TransportTimeout:
            return False, 0, b''
        seq = head_rest[0]
        length = (head_rest[1] << 8) | head_rest[2]
        try:
            rest = transport.read_exact(length + 2, self.timeout)
        except transport.TransportTimeout:
            return False, 0, b''
        payload = rest[:length]
        recv_crc = (rest[length] << 8) | rest[length + 1]
        header = bytes([frame_type]) + head_rest
        if crc16_ccitt(header + payload) != recv_crc:
            return False, 0, b''
        return True, seq, payload

    def receive(self, progress_cb=None):
        """송신측으로부터 ZMODEM(Lite)으로 파일을 받아 (filename, data)를 반환한다.
        progress_cb(received_bytes, total_bytes)가 있으면 블록마다 호출한다."""
        transport.flush_input()

        header = self._start_and_get_first_header()
        ok, _, meta_payload = self._read_frame_rest(header)
        while not ok:
            self._send_nak()
            header = self._read_header_with_retry()
            if header == CAN:
                raise ZModemCancelled('송신측이 전송을 취소했습니다.')
            ok, _, meta_payload = self._read_frame_rest(header)

        name_len = meta_payload[0]
        filename = meta_payload[1:1 + name_len].decode('utf-8', errors='replace')
        total = int.from_bytes(meta_payload[1 + name_len:1 + name_len + 8], 'big')

        if self.max_size is not None and total > self.max_size:
            self.cancel()
            raise ZModemError(f'허용된 최대 크기({self.max_size}바이트)를 초과했습니다.')

        self._send_ack()

        chunks = []
        received = 0
        expected_seq = 1
        can_count = 0

        header = self._read_header_with_retry()
        while True:
            if header == CAN:
                can_count += 1
                if can_count >= 2:
                    raise ZModemCancelled('송신측이 전송을 취소했습니다.')
                header = self._read_header_with_retry()
                continue
            can_count = 0

            if header not in (FRAME_DATA, FRAME_END):
                self._send_nak()
                header = self._read_header_with_retry()
                continue

            ok, seq, payload = self._read_frame_rest(header)
            if not ok:
                self._send_nak()
                header = self._read_header_with_retry()
                continue

            if header == FRAME_END:
                self._send_ack()
                break

            if seq == expected_seq:
                chunks.append(payload)
                received += len(payload)
                if self.max_size is not None and received > self.max_size:
                    self.cancel()
                    raise ZModemError(f'허용된 최대 크기({self.max_size}바이트)를 초과했습니다.')
                expected_seq = (expected_seq + 1) & 0xFF
                self._send_ack()
                if progress_cb:
                    progress_cb(received, total)
            elif seq == (expected_seq - 1) & 0xFF:
                # 직전 블록의 재전송(우리 ACK이 상대에게 전달되지 않은 경우) -
                # 새로 쌓지 않고 다시 ACK만 보낸다.
                self._send_ack()
            else:
                # 순서가 완전히 어긋남 - 프로토콜 오류로 보고 NAK.
                self._send_nak()

            header = self._read_header_with_retry()

        data = b''.join(chunks)
        return filename, data
