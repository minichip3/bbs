import os
import time

# BBS_VERBOSE=1이면 바이트 단위 입출력까지 다 찍힘. 기본(0)이면 접속/연결
# 상태 같은 굵직한 이벤트만 남아서 로그가 깔끔함. docker-compose.yml의
# environment에서 토글하면 재빌드 없이 컨테이너 재시작만으로 바뀜.
VERBOSE = os.environ.get('BBS_VERBOSE', '0') == '1'


def log(msg):
    # 기본 로그 - 접속/연결/끊김 같은 이벤트, 항상 출력됨
    print(f"[{time.strftime('%H:%M:%S')}.{int(time.time()*1000)%1000:03d}] {msg}", flush=True)


def log_verbose(msg):
    # 상세 로그 - BBS_VERBOSE=1일 때만 출력됨
    if VERBOSE:
        log(msg)


def log_io(tag, direction, data, gap_ms=None):
    # 모뎀(dialup.py)/텔넷(telnet_server.py) 공용 입출력 로그 포맷.
    # tag: 모뎀이면 장치 경로(/dev/ttyS0), 텔넷이면 클라이언트 IP.
    # direction: '수신' 또는 '송신'.
    if not VERBOSE:
        return
    prefix = f"{gap_ms:.0f}ms 공백 후 " if gap_ms is not None and gap_ms > 300 else ""
    log(f"[{tag}] {prefix}{len(data)}바이트 {direction}: {data[:16]!r}")
