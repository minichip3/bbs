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


def read_byte(timeout):
    """1바이트를 기다린다. timeout초 안에 안 오면 TransportTimeout."""
    fd = _in_fd()
    ready, _, _ = select.select([fd], [], [], timeout)
    if not ready:
        raise TransportTimeout(f'{timeout}초간 응답 없음')
    data = os.read(fd, 1)
    if not data:
        raise TransportClosed('입력 스트림 종료(EOF)')
    return data[0]


def read_exact(n, timeout):
    """정확히 n바이트를 모을 때까지 읽는다. 바이트 사이 간격에도 timeout이 적용된다
    (블록 전송 도중 상대가 멈추면 끝없이 블로킹하지 않도록)."""
    fd = _in_fd()
    buf = bytearray()
    while len(buf) < n:
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            raise TransportTimeout(f'{timeout}초간 응답 없음 ({len(buf)}/{n}바이트 수신)')
        chunk = os.read(fd, n - len(buf))
        if not chunk:
            raise TransportClosed('입력 스트림 종료(EOF)')
        buf += chunk
    return bytes(buf)


def flush_input(settle=0.1):
    """입력 버퍼에 남아있는 잡음성 바이트를 모두 비운다. 프로토콜 시작 전이나
    취소 직후처럼 "다음에 오는 바이트가 확실히 새 시퀀스의 시작"이어야 하는
    지점에서만 호출해야 한다."""
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
