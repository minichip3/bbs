"""ZMODEM 파일 전송 - 자체 프로토콜 구현 대신 lrzsz(rz/sz)를 서브프로세스로
띄워서 그 stdin/stdout을 세션의 raw 스트림(dialup.py/telnet.py가 만든 PTY의
양 끝)에 그대로 연결한다.

bbsio/xfer/zmodem.py(자체 ZBIN32 파서 구현)로 실제 클라이언트(SecureCRT의
lrzsz, iTerm2+lrzsz, 이야기 등 최소 두 개의 서로 다른 독립 구현체)와 붙여본
결과 ZFILE까지는 통과하지만 ZDATA 서브패킷 CRC32가 매번 불일치하는 원인
불명의 상호운용성 문제가 있었다 - 페이로드 자체는 실제 PE 실행파일
헤더/gzip 헤더로 구조 검증까지 해서 100% 정확히 디코딩됨을 확인했는데도
CRC만 안 맞아, 코드 리뷰/바이트 단위 분석으로는 원인을 못 찾음.

옛날 실제 BBS 소프트웨어(Synchronet, WWIV 등)들도 대부분 ZMODEM을 직접
구현하지 않고 서버 쪽에서도 sz/rz(또는 도스 시절 DSZ)를 서브프로세스로
띄우는 방식을 썼다 - ZMODEM은 원래 이런 미묘한 상호운용성 문제가 흔한
프로토콜이라, 수십 년간 다듬어진 lrzsz 구현체끼리 붙는 게 자체 재구현보다
훨씬 안전하다. 이 모듈은 그 방식을 따른다.
"""

import os
import resource
import subprocess
import sys
import tempfile
import threading

DEFAULT_TIMEOUT = 600  # 초 - 큰 파일/느린 회선을 감안한 전체 전송 상한

# 구식 터미널 자동인식용 배너 - 실제 rz(1)이 대화형 터미널에서 사람이 보라고
# 찍는 문구를 흉내낸 것. lrzsz의 rz는 stdout이 파이프(isatty() 거짓)로
# 연결되면 이 문구를 자체적으로 찍지 않으므로, 진짜 ZRINIT 핸드셰이크 전에
# 우리가 직접 한 번 내보낸다 - SecureCRT/미니콤 등 일부 터미널이 이 문구를
# 보고 자동으로 ZMODEM 수신 모드에 들어간다. 맨 앞 CR은 실 배포(전화 모뎀
# 회선)에서 배너 첫 바이트가 간헐적으로 유실되던 현상을 흡수하기 위한
# 희생용 바이트(bbsio/xfer/zmodem.py의 이전 구현에서 옮겨온 것).
_AUTOSTART_BANNER = b'\rrz waiting to receive.\x1bZ'


class ZModemProcError(Exception):
    pass


def _raw_fds():
    return sys.stdin.fileno(), sys.stdout.fileno()


def _drain_stderr(proc, into):
    # rz/sz의 stderr(진행 상황 메시지, 에러 원인)를 실패 시 로그/에러 메시지에
    # 담을 수 있도록 별도 스레드에서 계속 읽어둔다. 안 읽고 두면 출력이 많은
    # 경우 파이프 버퍼가 차서 자식 프로세스가 멈출 수 있다.
    try:
        for chunk in iter(lambda: proc.stderr.read(4096), b''):
            into.append(chunk)
    except Exception:
        pass


def _make_fsize_limit(max_size):
    # max_size(바이트) 초과 시 커널이 직접 SIGXFSZ로 rz를 죽이게 하는 하드 캡.
    # 서브프로세스라 우리 쪽에서 청크 단위로 크기를 감시할 수 없으므로,
    # 전송이 끝난 뒤에야 크기를 확인하는 것보다 안전하다(그 사이 디스크를
    # 계속 채우는 걸 막음).
    if max_size is None:
        return None

    def _limit():
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_size, max_size))
    return _limit


def receive(max_size=None, timeout=DEFAULT_TIMEOUT):
    """rz로 파일 하나를 받아 (filename, data)를 반환한다. 실패 시 ZModemProcError."""
    in_fd, out_fd = _raw_fds()
    os.write(out_fd, _AUTOSTART_BANNER)
    with tempfile.TemporaryDirectory(prefix='zrecv_') as tmpdir:
        try:
            # --disable-timeouts: rz의 기본 내부 타임아웃이 우리 텔넷 릴레이
            # (파이썬 스레드가 소켓→PTY로 중계) 환경의 지연 변동에는 너무
            # 빡빡해서, 실제로는 데이터가 계속 오고 있는데도 "Got TIMEOUT"으로
            # 포기하고 취소하는 게 실 배포에서 재현됨(대용량 파일 전송 막바지에
            # 특히 잘 걸림). 여기서 개별 타임아웃을 끄고, 완전히 멈춘 전송에
            # 대한 안전망은 아래 subprocess 전체 timeout(초 단위)이 담당한다.
            proc = subprocess.Popen(
                ['rz', '--binary', '--overwrite', '--disable-timeouts'],
                stdin=in_fd, stdout=out_fd, stderr=subprocess.PIPE,
                cwd=tmpdir, close_fds=True,
                preexec_fn=_make_fsize_limit(max_size),
            )
        except FileNotFoundError:
            raise ZModemProcError('rz(lrzsz)가 설치되어 있지 않습니다.')

        stderr_chunks = []
        drain = threading.Thread(target=_drain_stderr, args=(proc, stderr_chunks), daemon=True)
        drain.start()
        try:
            ret = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise ZModemProcError('전송 시간이 초과되었습니다.')
        drain.join(timeout=2)
        stderr_text = b''.join(stderr_chunks).decode('utf-8', errors='replace').strip()

        received = os.listdir(tmpdir)
        if ret != 0 or not received:
            detail = f': {stderr_text}' if stderr_text else ''
            raise ZModemProcError(f'업로드가 실패했습니다 (rz 종료 코드: {ret}){detail}')

        raw_filename = received[0]
        path = os.path.join(tmpdir, raw_filename)
        size = os.path.getsize(path)
        if max_size is not None and size > max_size:
            raise ZModemProcError(f'허용된 최대 크기({max_size}바이트)를 초과했습니다.')
        with open(path, 'rb') as f:
            data = f.read()
        return raw_filename, data


def send(filepath, display_name=None, timeout=DEFAULT_TIMEOUT):
    """sz로 filepath를 보낸다. 실패 시 ZModemProcError.
    display_name을 주면 그 이름으로 상대에게 전달된다(우리 내부 저장
    파일명 대신 사용자가 보던 원래 파일명을 그대로 보여주기 위함)."""
    in_fd, out_fd = _raw_fds()
    if display_name is None:
        display_name = os.path.basename(filepath)

    with tempfile.TemporaryDirectory(prefix='zsend_') as tmpdir:
        link_path = os.path.join(tmpdir, display_name)
        os.symlink(os.path.abspath(filepath), link_path)
        try:
            proc = subprocess.Popen(
                ['sz', '--binary', '--disable-timeouts', link_path],
                stdin=in_fd, stdout=out_fd, stderr=subprocess.PIPE,
                close_fds=True,
            )
        except FileNotFoundError:
            raise ZModemProcError('sz(lrzsz)가 설치되어 있지 않습니다.')

        stderr_chunks = []
        drain = threading.Thread(target=_drain_stderr, args=(proc, stderr_chunks), daemon=True)
        drain.start()
        try:
            ret = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise ZModemProcError('전송 시간이 초과되었습니다.')
        drain.join(timeout=2)
        stderr_text = b''.join(stderr_chunks).decode('utf-8', errors='replace').strip()

        if ret != 0:
            detail = f': {stderr_text}' if stderr_text else ''
            raise ZModemProcError(f'다운로드가 실패했습니다 (sz 종료 코드: {ret}){detail}')
