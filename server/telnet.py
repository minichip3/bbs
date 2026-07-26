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
BBS_COMMAND = ['python3', '-u', 'bbs.py']
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


def strip_telnet_iac(data):
    # 소켓으로 들어오는 바이트 중 telnet IAC 명령 시퀀스(IAC+WILL/WONT/DO/DONT+옵션,
    # 총 3바이트)를 걸러내서 bbs.py한테 진짜 입력인 것처럼 넘어가지 않게 한다.
    # IAC IAC(0xFF 0xFF)는 데이터 안의 리터럴 0xFF 한 바이트를 뜻하는 이스케이프라
    # 별도로 처리한다. 완전한 telnet 상태머신은 아니지만 이 용도엔 충분함.
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        if b == IAC:
            if i + 1 < n and data[i + 1] == IAC:
                out.append(IAC)
                i += 2
                continue
            # WILL/WONT/DO/DONT + 옵션 1바이트 = 총 3바이트 커맨드로 간주하고 스킵.
            # 마지막에 IAC만 딱 걸리고 뒤가 아직 안 왔으면(드문 케이스) 그냥 버림.
            i += 3
            continue
        out.append(b)
        i += 1
    return bytes(out)


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
                filtered = strip_telnet_iac(data)
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
