import socket
import threading
import pty
import os
import subprocess
import time
import traceback
import tty
from logutil import log, log_io

# 설정
LISTEN_HOST = '0.0.0.0'
LISTEN_PORT = 2323          # 23은 굳이 안 씀 - 이 컨테이너 안에서 우리 말고 아무도 안 쓰지만 혼동 방지
BBS_COMMAND = ['python3', '-u', 'bbs.py', '--channel=telnet']
MAX_CONNECTIONS = 20        # 동시 접속 상한 (스레드/프로세스 무한 생성 방지)

# 접속 빈도 제한 - 봇/스캐너가 짧은 시간에 계속 재접속하는 걸 막는다.
# 메모리에만 들고 있으면 충분 (영속화 필요 없음, 그냥 스캐너 완화용).
RATE_LIMIT_WINDOW_SEC = 60
RATE_LIMIT_MAX_ATTEMPTS = 5
RATE_LIMIT_COOLDOWN_SEC = 300

IAC = 0xFF
WILL = 0xFB
WONT = 0xFC
DO = 0xFD
DONT = 0xFE
TELOPT_ECHO = 0x01
TELOPT_SGA = 0x03

# 서버가 에코를 담당하고(우리 rawinput()이 직접 echo함), 클라이언트는
# 로컬 에코/라인버퍼링 없이 키 하나하나를 바로 보내도록 하는 협상.
# 완전한 telnet 옵션 상태머신은 필요 없음 - 접속 시 한 번만 보내고,
# 그 이후 들어오는 IAC 시퀀스는 그냥 무시(스킵)한다.
NEGOTIATION = bytes([
    IAC, WILL, TELOPT_ECHO,
    IAC, WILL, TELOPT_SGA,
    IAC, DO, TELOPT_SGA,
])

_connection_count_lock = threading.Lock()
_connection_count = 0

_rate_lock = threading.Lock()
_recent_attempts = {}   # ip -> [timestamp, ...]
_cooldown_until = {}    # ip -> timestamp


def is_rate_limited(ip):
    now = time.time()
    with _rate_lock:
        cooldown = _cooldown_until.get(ip)
        if cooldown and now < cooldown:
            return True
        attempts = _recent_attempts.setdefault(ip, [])
        attempts[:] = [t for t in attempts if now - t < RATE_LIMIT_WINDOW_SEC]
        attempts.append(now)
        if len(attempts) > RATE_LIMIT_MAX_ATTEMPTS:
            _cooldown_until[ip] = now + RATE_LIMIT_COOLDOWN_SEC
            return True
    return False


# 접속 직후 텔넷 협상이 오가는 시간대에만 IAC 커맨드를 해석한다(아래
# strip_telnet_iac 사용처 및 주의사항 참고) - 그 뒤로는 순수 데이터로
# 취급해 그대로 통과시킨다. 로그인/메뉴 탐색을 거쳐 ZMODEM/XMODEM 같은
# 바이너리 전송을 시작하기까지는 최소 수십 초가 걸리므로, 실제 협상이
# 다 끝나고도 한참 남는 여유 있는 값이다.
IAC_NEGOTIATION_WINDOW_SEC = 10

_IAC_COMMANDS = (WILL, WONT, DO, DONT)

# 클라이언트가 보낸 협상 커맨드에 대한 응답 매핑. RFC 854상 유효한 짝만 써야 한다 -
# 예를 들어 클라이언트가 WONT(옵션을 안 하겠다)라고 했는데 서버가 DO(그 옵션을 해달라)로
# 답하면 클라이언트는 방금 거부한 걸 다시 요청받은 꼴이 되어 WONT를 반복 전송하게 되고,
# 엄격한 클라이언트는 이 프로토콜 위반을 감지하고 연결을 끊어버린다. 우리는 실제로
# 옵션별 동작을 구현하지 않으므로(완전한 상태머신 아님) 전부 거부(DONT/WONT)로 답해서
# 매 협상을 그 자리에서 종료시킨다 - 재요청이나 서브협상이 이어질 여지를 없앤다.
_IAC_DECLINE = {WILL: DONT, WONT: DONT, DO: WONT, DONT: WONT}

# ECHO/SGA는 예외다 - 접속 시작 시 우리가 이미 NEGOTIATION으로 먼저
# 제안했으므로(WILL ECHO, WILL SGA, DO SGA), 그 뒤에 들어오는 ECHO/SGA
# 관련 WILL/WONT/DO/DONT는 새 요청이 아니라 그 제안에 대한 클라이언트의
# 응답(ack/nak)이다. 여기에 다시 _IAC_DECLINE으로 거부 응답을 보내면
# 우리가 방금 제안한 걸 스스로 거부하는 꼴이 되어(예: 클라이언트가 우리
# WILL ECHO에 DO ECHO로 동의했는데 우리가 다시 DONT ECHO를 보내면) 클라이언트가
# 서버 에코를 취소하고 로컬 에코를 다시 켜버린다 - 비밀번호 입력 화면에서
# '*' 마스킹 대신 평문이 그대로 로컬 에코되어 보이는 버그의 원인이었다.
# 이미 우리가 협상을 시작한 옵션에 대한 응답에는 답장하지 않는다(RFC 854가
# 금지하는 "응답에 대한 재응답" 루프를 피하기 위함이기도 하다).
_IAC_NO_REPLY_OPTIONS = (TELOPT_ECHO, TELOPT_SGA)


def strip_telnet_iac(data):
    # 소켓으로 들어오는 바이트 중 telnet IAC 명령 시퀀스(IAC+WILL/WONT/DO/DONT+옵션,
    # 총 3바이트)를 걸러내서 bbs.py한테 진짜 입력인 것처럼 넘어가지 않게 한다.
    # IAC IAC(0xFF 0xFF)는 데이터 안의 리터럴 0xFF 한 바이트를 뜻하는 이스케이프라
    # 별도로 처리한다. 완전한 telnet 상태머신은 아니지만 이 용도엔 충분함.
    #
    # 주의: IAC 뒤에 오는 바이트가 실제로 WILL/WONT/DO/DONT일 때만 커맨드로 보고
    # 3바이트를 스킵해야 한다. 그렇지 않으면 XModem 등 바이너리 전송 중에 등장하는
    # 리터럴 0xFF 데이터 바이트(체크섬, CRC, 블록 번호 등)를 커맨드로 오인해서
    # 뒤따르는 진짜 데이터 2바이트까지 함께 먹어버려 전송이 깨진다.
    #
    # 커맨드를 그냥 버리기만 하면 클라이언트는 자기가 보낸 협상 커맨드에 대한
    # 응답을 영영 못 받아서(일부 클라이언트가 협상이 안 끝났다고 보고) 60초마다
    # 재접속을 시도하다가 결국 서버 rate limiter에 걸리는 문제가 있었다.
    # 그래서 커맨드를 스킵하는 대신, RFC 854에 맞는 짝(_IAC_DECLINE)으로 거부
    # 응답을 만들어 별도로 반환한다 - 완전한 협상 상태머신은 아니지만 매 요청을
    # 그 자리에서 종료시켜서 클라이언트가 "응답받았다"고 인식하기엔 충분함.
    #
    # 주의: 바로 위의 "0xFF 뒤에 WILL/WONT/DO/DONT가 와야 커맨드로 본다" 가드로도
    # 완전히 안전하지 않다 - ZMODEM 헤더의 위치/CRC 필드처럼 값 범위 제한이 없는
    # 바이너리 필드는 이스케이프 대상이 아니라서(_ESCAPE_NEEDED에 0xFB~0xFE가
    # 없음) 우연히 0xFF 바로 뒤에 0xFB~0xFE 값이 오는 경우가 실제로 발생하고,
    # 그러면 진짜 파일 데이터 3바이트를 커맨드로 오인해 삼켜버려 헤더 CRC가
    # 깨지고 전송이 영원히 멈춘다(실제 배포에서 관찰된 ZMODEM 업로드 무한 대기
    # 원인 - _read_header가 이 손상된 헤더를 CRC 불일치로 계속 거부하면서
    # ZFILE을 못 받고 송신측 재전송 타임아웃까지 ZRINIT만 반복 전송하게 됨).
    # 이 함수만으로는 "진짜 협상 커맨드"와 "우연히 같은 패턴의 바이너리 데이터"를
    # 스트림만 보고 구별할 수 없으므로, 호출부(data_relay)에서 접속 초반
    # 협상 시간대에만 이 함수를 타게 하고 그 뒤 파일 전송이 벌어질 시점에는
    # 아예 호출하지 않는 방식(IAC_NEGOTIATION_WINDOW_SEC)으로 막는다.
    out = bytearray()
    responses = bytearray()
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        if b == IAC:
            if i + 1 < n and data[i + 1] == IAC:
                out.append(IAC)
                i += 2
                continue
            if i + 2 < n and data[i + 1] in _IAC_COMMANDS:
                # WILL/WONT/DO/DONT + 옵션 1바이트 = 총 3바이트 커맨드로 간주하고
                # bbs.py로 넘어가는 스트림에서는 스킵하되, 클라이언트에게
                # RFC 854에 맞는 짝(_IAC_DECLINE)으로 거부 응답을 돌려준다.
                # 단, ECHO/SGA는 우리가 먼저 제안한 옵션이라 그에 대한 응답에는
                # 답장하지 않는다(_IAC_NO_REPLY_OPTIONS 설명 참고).
                option = data[i + 2]
                if option not in _IAC_NO_REPLY_OPTIONS:
                    responses.extend([IAC, _IAC_DECLINE[data[i + 1]], option])
                i += 3
                continue
            if i + 1 >= n:
                # 스트림 끝에 IAC 하나만 걸린 드문 경우 - 다음 recv()에서 이어질
                # 커맨드 시퀀스일 수도 있으나 여기선 완전한 상태머신이 아니므로
                # 리터럴 데이터로 취급해 그냥 통과시킨다.
                out.append(b)
                i += 1
                continue
            # IAC 다음 바이트가 WILL/WONT/DO/DONT가 아니면 telnet 커맨드가
            # 아니라 리터럴 데이터 바이트로 취급하고 한 바이트만 소비한다.
            out.append(b)
            i += 1
            continue
        out.append(b)
        i += 1
    return bytes(out), bytes(responses)


def log_bbs_stderr(proc, tag):
    try:
        for line in iter(proc.stderr.readline, b''):
            if not line:
                break
            log(f'[bbs stderr {tag}] ' + line.decode('utf-8', errors='ignore').rstrip())
    except Exception as e:
        log(f'[bbs stderr 읽기 오류 {tag}] {e}')
    finally:
        try:
            proc.stderr.close()
        except Exception:
            pass


def data_relay(conn, master_fd, proc, tag, ip):
    log(f"[{tag}] 데이터 중계 시작")
    disconnect_event = threading.Event()
    last_recv_time = [time.time()]
    last_send_time = [time.time()]
    iac_negotiation_deadline = time.time() + IAC_NEGOTIATION_WINDOW_SEC

    # 소켓 -> PTY 방향 (텔넷 클라이언트가 보낸 키 입력)
    def relay_socket_to_pty():
        while proc.poll() is None and not disconnect_event.is_set():
            try:
                data = conn.recv(1024)
                if not data:
                    # 클라이언트가 정상적으로 연결을 닫음
                    disconnect_event.set()
                    try:
                        os.close(master_fd)
                    except Exception:
                        pass
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    return
                if time.time() < iac_negotiation_deadline:
                    filtered, iac_responses = strip_telnet_iac(data)
                else:
                    # 협상 시간대가 지났다 - ZMODEM/XMODEM 같은 바이너리 전송이
                    # 시작됐을 수 있으므로 더 이상 IAC 커맨드로 해석하지 않고
                    # 그대로 통과시킨다(위 strip_telnet_iac의 주의사항 참고).
                    filtered, iac_responses = data, b''
                if iac_responses:
                    # 클라이언트가 보낸 IAC 협상 커맨드에 대한 ACK(IAC DO <옵션>).
                    # 이걸 안 보내면 클라이언트가 협상 미완료로 보고 60초마다
                    # 재접속을 시도하다 rate limiter에 걸리는 문제가 있었다.
                    conn.sendall(iac_responses)
                if filtered:
                    now = time.time()
                    gap_ms = (now - last_recv_time[0]) * 1000
                    log_io(ip, '수신', filtered, gap_ms)
                    last_recv_time[0] = now
                    os.write(master_fd, filtered)
            except OSError:
                break
            except Exception:
                log(f"[{tag}] 소켓->PTY 중계 오류:")
                traceback.print_exc()
                disconnect_event.set()
                try:
                    os.close(master_fd)
                except Exception:
                    pass
                try:
                    proc.terminate()
                except Exception:
                    pass
                return

    # PTY -> 소켓 방향 (BBS 출력)
    def relay_pty_to_socket():
        while proc.poll() is None and not disconnect_event.is_set():
            try:
                data = os.read(master_fd, 1024)
                if data:
                    now = time.time()
                    gap_ms = (now - last_send_time[0]) * 1000
                    log_io(ip, '송신', data, gap_ms)
                    last_send_time[0] = now
                    conn.sendall(data)
            except OSError:
                # PTY 종료 시 OSError 발생 (정상 종료 경로) - dialup.py와 동일한 패턴
                break
            except Exception:
                log(f"[{tag}] PTY->소켓 중계 오류:")
                traceback.print_exc()
                disconnect_event.set()
                try:
                    proc.terminate()
                except Exception:
                    pass
                break

    t1 = threading.Thread(target=relay_socket_to_pty)
    t2 = threading.Thread(target=relay_pty_to_socket)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    log(f"[{tag}] 데이터 중계 종료")
    try:
        os.close(master_fd)
    except Exception:
        pass
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        conn.close()
    except Exception:
        pass


def handle_connection(conn, addr):
    global _connection_count
    tag = f'{addr[0]}:{addr[1]}'
    proc = None
    master_fd = None
    try:
        conn.sendall(NEGOTIATION)

        # PTY 생성. dialup.py와 동일하게 raw 모드는 여기서 딱 한 번만 건다 -
        # bbs.py의 getchar() 안에서 매 글자마다 반복해서 걸면 TCSAFLUSH가
        # 아직 안 읽은 입력을 버려버리는 버그가 있었던 걸 이미 확인함(rawio.py 참고).
        master_fd, slave_fd = pty.openpty()
        tty.setraw(slave_fd)

        proc = subprocess.Popen(
            BBS_COMMAND,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=subprocess.PIPE,
            cwd=os.getcwd(),
            close_fds=True
        )
        os.close(slave_fd)

        threading.Thread(
            target=log_bbs_stderr, args=(proc, tag), daemon=True
        ).start()

        data_relay(conn, master_fd, proc, tag, addr[0])
    except Exception:
        log(f"[{tag}] 연결 처리 중 예외:")
        traceback.print_exc()
        try:
            if master_fd is not None:
                os.close(master_fd)
        except Exception:
            pass
        try:
            if proc is not None:
                proc.terminate()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
    finally:
        with _connection_count_lock:
            _connection_count -= 1


def telnet_server():
    global _connection_count
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((LISTEN_HOST, LISTEN_PORT))
    srv.listen(50)
    log(f'텔넷 서버 시작: {LISTEN_HOST}:{LISTEN_PORT}')

    while True:
        conn, addr = srv.accept()
        ip = addr[0]

        if is_rate_limited(ip):
            log(f'접속 빈도 제한으로 거절: {ip}')
            try:
                conn.close()
            except Exception:
                pass
            continue

        with _connection_count_lock:
            if _connection_count >= MAX_CONNECTIONS:
                log(f'최대 동시 접속({MAX_CONNECTIONS}) 초과로 거절: {ip}')
                try:
                    conn.close()
                except Exception:
                    pass
                continue
            _connection_count += 1

        log(f'텔넷 접속: {ip}:{addr[1]}')
        threading.Thread(
            target=handle_connection, args=(conn, addr), daemon=True
        ).start()


if __name__ == "__main__":
    # dialup.py와 동일한 안전망 패턴 - 최상위 루프에서 예상 못한 예외로
    # 죽어도 서버 자체는 재시작해서 서비스를 이어간다.
    while True:
        try:
            telnet_server()
        except Exception as e:
            log(f'telnet_server() 최상위 예외, 재시작: {e}')
        log('telnet_server() 종료됨 - 5초 후 재시작')
        time.sleep(5)
