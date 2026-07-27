"""XModem-CRC (128바이트 SOH 블록 / 1024바이트 STX "1K-XModem" 블록) 구현.

체크섬 방식(구식 XModem)은 지원하지 않는다 - 수신측은 항상 'C'로 CRC 모드를
요청하고, 송신측도 CRC 모드 요청('C')에만 응답한다. 요즘 쓰는 telnet/시리얼
터미널 클라이언트(SecureCRT, minicom, 이야기 등)는 전부 XModem-CRC를 지원하니
실사용에는 문제 없다.

프로토콜 참고:
  SOH(0x01) blk ~blk data[128]  crc_hi crc_lo   - 128바이트 블록
  STX(0x02) blk ~blk data[1024] crc_hi crc_lo   - 1024바이트 블록("1K")
  EOT(0x04)                                      - 전송 종료
  ACK(0x06) / NAK(0x15)                          - 블록 승인/재전송 요청
  CAN(0x18) CAN(0x18)                            - 전송 취소
  'C'(0x43)                                       - 수신측이 CRC 모드로 시작하자는 요청
  블록 번호는 1에서 시작해 255 다음 0으로 래핑되고(mod 256), 그 보수(255-blk)를
  함께 보내 헤더 손상을 이중 검증한다. 마지막 블록의 남는 자리는 SUB(0x1A)로
  채운다.
"""

from bbsio.xfer import transport
from bbsio.xfer.crc16 import crc16_ccitt

SOH = 0x01
STX = 0x02
EOT = 0x04
ACK = 0x06
NAK = 0x15
CAN = 0x18
SUB = 0x1A
CRC_MODE = ord('C')

DEFAULT_RETRY = 10
DEFAULT_TIMEOUT = 10  # 초 - 상대 응답 대기 시간 (전화 접속 등 느린 회선 고려)


class XModemError(Exception):
    pass


class XModemCancelled(XModemError):
    """상대(또는 우리)가 CAN을 보내 전송을 취소함."""
    pass


class XModemTimeout(XModemError):
    """재시도를 다 써도 상대 응답이 없어 전송을 포기함."""
    pass


class XModemSender:
    """BBS -> 사용자 방향(다운로드)에서 이 BBS가 파일을 보내는 쪽일 때 사용."""

    def __init__(self, retry=DEFAULT_RETRY, timeout=DEFAULT_TIMEOUT):
        self.retry = retry
        self.timeout = timeout

    def _wait_for_crc_request(self):
        """수신측이 CRC 모드를 요청('C')할 때까지 기다린다.
        CAN을 받으면 즉시 취소로 간주한다."""
        for _ in range(self.retry):
            try:
                b = transport.read_byte(self.timeout)
            except transport.TransportTimeout:
                continue
            if b == CRC_MODE:
                return
            if b == CAN:
                raise XModemCancelled('수신측이 전송을 취소했습니다.')
            # NAK 등 다른 바이트는 체크섬 모드 요청이거나 잡음 - 무시하고 계속 대기.
        raise XModemTimeout('수신측이 응답하지 않습니다 (CRC 모드 요청 대기 시간 초과).')

    def _send_block(self, block_num, chunk, block_size):
        header = STX if block_size == 1024 else SOH
        padded = chunk + bytes([SUB]) * (block_size - len(chunk))
        seq = block_num & 0xFF
        packet = bytearray()
        packet.append(header)
        packet.append(seq)
        packet.append(0xFF - seq)
        packet += padded
        crc = crc16_ccitt(padded)
        packet.append((crc >> 8) & 0xFF)
        packet.append(crc & 0xFF)

        for attempt in range(self.retry):
            transport.write_bytes(bytes(packet))
            try:
                reply = transport.read_byte(self.timeout)
            except transport.TransportTimeout:
                continue
            if reply == ACK:
                return
            if reply == CAN:
                raise XModemCancelled('수신측이 전송을 취소했습니다.')
            # NAK나 알 수 없는 바이트 - 같은 블록 재전송.
        raise XModemTimeout(f'블록 {seq}번 재시도 초과.')

    def _send_eot(self):
        for attempt in range(self.retry):
            transport.write_bytes(bytes([EOT]))
            try:
                reply = transport.read_byte(self.timeout)
            except transport.TransportTimeout:
                continue
            if reply == ACK:
                return
            # 스펙상 EOT 첫 번째는 NAK될 수 있음(수신측 구현에 따라) - 재전송.
        raise XModemTimeout('EOT에 대한 응답이 없습니다.')

    def cancel(self):
        transport.write_bytes(bytes([CAN, CAN]))

    def send(self, data: bytes, block_size=1024, progress_cb=None) -> bool:
        """data를 XModem-CRC로 전송한다. block_size는 128 또는 1024.
        progress_cb(sent_bytes, total_bytes)가 있으면 매 블록마다 호출한다.
        성공하면 True, 취소/시간초과면 예외를 던진다."""
        if block_size not in (128, 1024):
            raise ValueError('block_size는 128 또는 1024여야 합니다.')

        transport.flush_input()
        self._wait_for_crc_request()

        total = len(data)
        sent = 0
        block_num = 1
        try:
            if total == 0:
                # 빈 파일 - 블록 없이 바로 EOT.
                self._send_eot()
                if progress_cb:
                    progress_cb(0, 0)
                return True

            while sent < total:
                chunk = data[sent:sent + block_size]
                self._send_block(block_num, chunk, block_size)
                sent += len(chunk)
                block_num += 1
                if progress_cb:
                    progress_cb(sent, total)

            self._send_eot()
            return True
        except XModemError:
            try:
                self.cancel()
            except Exception:
                pass
            raise


class XModemReceiver:
    """BBS -> 사용자 방향의 반대(업로드)에서 이 BBS가 파일을 받는 쪽일 때 사용."""

    def __init__(self, retry=DEFAULT_RETRY, timeout=DEFAULT_TIMEOUT, max_size=None):
        self.retry = retry
        self.timeout = timeout
        # 업로드 한도 미설정 시 무제한으로 받아버리면 악의적/오동작 클라이언트가
        # 디스크를 무한정 채울 수 있어 상한을 둔다 (files.py가 호출 시 지정).
        self.max_size = max_size

    def _send_nak(self):
        transport.write_bytes(bytes([NAK]))

    def _send_ack(self):
        transport.write_bytes(bytes([ACK]))

    def receive(self, progress_cb=None) -> bytes:
        """송신측으로부터 XModem-CRC로 파일을 받아 bytes로 반환한다.
        progress_cb(received_bytes)가 있으면 블록마다 호출한다."""
        transport.flush_input()

        header = self._start_and_get_first_header()
        if header is None:
            # 송신측이 데이터 없이 바로 EOT를 보낸 경우 (빈 파일).
            return b''

        chunks = []
        received = 0
        block_num = 0  # 마지막으로 정상 수신/ACK한 블록 번호 (0 = 아직 없음)
        can_count = 0

        while True:
            if header == EOT:
                self._send_ack()
                break
            if header == CAN:
                can_count += 1
                if can_count >= 2:
                    raise XModemCancelled('송신측이 전송을 취소했습니다.')
                header = self._read_header_with_retry()
                continue
            can_count = 0

            data_len = 1024 if header == STX else 128
            ok, chunk, seq = self._read_block_body(data_len)

            if not ok:
                self._send_nak()
                header = self._read_header_with_retry()
                continue

            expected = (block_num + 1) & 0xFF
            if seq == expected:
                chunks.append(chunk)
                received += len(chunk)
                block_num = seq
                if self.max_size is not None and received > self.max_size:
                    self.cancel()
                    raise XModemError(f'허용된 최대 크기({self.max_size}바이트)를 초과했습니다.')
                self._send_ack()
                if progress_cb:
                    progress_cb(received)
            elif seq == block_num:
                # 직전 블록의 재전송(우리 ACK이 상대에게 전달되지 않은 경우) -
                # 새로 쌓지 않고 다시 ACK만 보낸다.
                self._send_ack()
            else:
                # 순서가 완전히 어긋남 - 프로토콜 오류로 보고 NAK.
                self._send_nak()

            header = self._read_header_with_retry()

        data = b''.join(chunks)
        return _strip_trailing_padding(data)

    def _start_and_get_first_header(self):
        """'C'를 반복 전송하며 첫 블록 헤더(또는 EOT)를 기다린다."""
        for _ in range(self.retry):
            transport.write_bytes(bytes([CRC_MODE]))
            try:
                b = transport.read_byte(self.timeout)
            except transport.TransportTimeout:
                continue
            if b in (SOH, STX):
                return b
            if b == EOT:
                self._send_ack()
                return None
            if b == CAN:
                raise XModemCancelled('송신측이 전송을 취소했습니다.')
            # 잡음 - 계속 재시도.
        raise XModemTimeout('송신측이 응답하지 않습니다 (파일 전송 시작 대기 시간 초과).')

    def _read_header_with_retry(self):
        for _ in range(self.retry):
            try:
                return transport.read_byte(self.timeout)
            except transport.TransportTimeout:
                self._send_nak()
        raise XModemTimeout('다음 블록 대기 시간 초과.')

    def _read_block_body(self, data_len):
        """헤더 바이트 다음의 blk/~blk/data/crc를 읽고 검증한다.
        (block_ok, data, block_num) 튜플을 반환하며, 검증 실패 시 block_ok=False."""
        try:
            rest = transport.read_exact(2 + data_len + 2, self.timeout)
        except transport.TransportTimeout:
            return False, b'', 0
        seq = rest[0]
        comp = rest[1]
        chunk = rest[2:2 + data_len]
        recv_crc = (rest[2 + data_len] << 8) | rest[2 + data_len + 1]

        if (0xFF - seq) & 0xFF != comp:
            return False, b'', 0
        if crc16_ccitt(chunk) != recv_crc:
            return False, b'', 0
        return True, chunk, seq

    def cancel(self):
        transport.write_bytes(bytes([CAN, CAN]))


def _strip_trailing_padding(data: bytes) -> bytes:
    """마지막 블록을 SUB(0x1A)로 채운 패딩을 끝에서부터 제거한다.
    XModem은 파일 크기를 따로 안 보내기 때문에 정확한 원본 크기를 알 방법이
    없다 - 파일이 우연히 0x1A로 끝나는 경우 한 바이트가 잘릴 수 있는 건
    XModem(1K 포함) 자체의 알려진 한계이며, 실전에서도 흔히 감수하는 절충이다."""
    return data.rstrip(bytes([SUB]))
