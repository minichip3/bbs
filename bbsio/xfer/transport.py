"""XModem 전송용 raw 바이트 입출력.

bbsio.rawio는 사용자 키 입력을 문자(멀티바이트 UTF-8/EUC-KR 등)로 디코딩해서
돌려주는 걸 전제로 만들어져 있는데, XModem은 순수 바이너리 프로토콜이라
그 디코딩 경로를 타면 안 된다(0x80 이상 바이트를 멀티바이트 문자의 시작으로
오인해서 다음 바이트까지 같이 삼켜버리거나, 잘못된 인코딩으로 판단해 대체
문자로 치환해버림 - 둘 다 파일 데이터를 조용히 손상시킨다). 여기서는
os.read()/os.write()로 세션의 stdin/stdout fd(telnet.py/dialup.py가 만든
PTY 한쪽 끝)를 직접 건드려서 문자 디코딩을 완전히 우회한다.

telnet.py/dialup.py 둘 다 bbs.py 자식 프로세스를 stdin=stdout=슬레이브 PTY fd
하나로 띄우므로(전이중 시리얼 회선을 흉내), sys.stdin.fileno()로 읽고
sys.stdout.fileno()로 쓰면 두 채널 모두에서 동일하게 동작한다.
"""

import os
import select
import sys


class TransportTimeout(Exception):
    """지정한 시간 안에 상대로부터 응답(바이트)이 오지 않음."""
    pass


class TransportClosed(Exception):
    """상대가 연결을 끊어 입력 스트림이 EOF됨."""
    pass


def _in_fd():
    return sys.stdin.fileno()


def _out_fd():
    return sys.stdout.fileno()


# ZModem 블록이 커진 뒤로는(수 KB) 바이트 하나마다 select()+os.read()를 왕복하는
# 방식 자체가 실제 회선 지연보다 더 큰 오버헤드였다 - 헤더 스캔이나 서브패킷
# 파싱은 전부 read_byte()를 바이트 단위로 반복 호출한다(zmodem.py 참고). 커널이
# 이미 들고 있는 만큼을 한 번에 끌어와 로컬 버퍼에 쌓아두고, 그 다음부터는
# 버퍼가 다 소진될 때까지 이 캐시에서 서빙한다 - 도착한 바이트 수만큼 select+
# read를 반복하는 대신 버퍼가 빌 때만 다시 커널을 호출한다.
_READ_CHUNK = 65536

_rx_buf = b''
_rx_pos = 0


def _fill(timeout):
    """로컬 버퍼가 비었을 때 커널로부터 최대 _READ_CHUNK바이트를 채운다."""
    global _rx_buf, _rx_pos
    fd = _in_fd()
    ready, _, _ = select.select([fd], [], [], timeout)
    if not ready:
        raise TransportTimeout(f'{timeout}초간 응답 없음')
    chunk = os.read(fd, _READ_CHUNK)
    if not chunk:
        raise TransportClosed('입력 스트림 종료(EOF)')
    _rx_buf = chunk
    _rx_pos = 0


def read_byte(timeout):
    """1바이트를 기다린다. timeout초 안에 안 오면 TransportTimeout."""
    global _rx_pos
    if _rx_pos >= len(_rx_buf):
        _fill(timeout)
    b = _rx_buf[_rx_pos]
    _rx_pos += 1
    return b


def read_exact(n, timeout):
    """정확히 n바이트를 모을 때까지 읽는다. 바이트 사이 간격에도 timeout이 적용된다
    (블록 전송 도중 상대가 멈추면 끝없이 블로킹하지 않도록)."""
    global _rx_pos
    buf = bytearray()
    while len(buf) < n:
        if _rx_pos >= len(_rx_buf):
            _fill(timeout)
        take = min(n - len(buf), len(_rx_buf) - _rx_pos)
        buf += _rx_buf[_rx_pos:_rx_pos + take]
        _rx_pos += take
    return bytes(buf)


def flush_input(settle=0.1):
    """입력 버퍼(로컬 캐시 + 커널 소켓 버퍼)에 남아있는 잡음성 바이트를 모두
    비운다. 프로토콜 시작 전이나 취소 직후처럼 "다음에 오는 바이트가 확실히
    새 시퀀스의 시작"이어야 하는 지점에서만 호출해야 한다."""
    global _rx_buf, _rx_pos
    _rx_buf = b''
    _rx_pos = 0
    fd = _in_fd()
    while True:
        ready, _, _ = select.select([fd], [], [], settle)
        if not ready:
            return
        chunk = os.read(fd, 4096)
        if not chunk:
            return


def write_bytes(data: bytes):
    fd = _out_fd()
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        view = view[n:]
