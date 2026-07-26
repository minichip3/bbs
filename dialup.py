import serial
import pty
import os
import subprocess
import threading
import time
import traceback
import termios
import tty
from logutil import log, log_io, log_verbose

# 설정
MODEM_PORT = '/dev/ttyS0'           # 실제 모뎀 장치 (USR5686G, HT802 경유 VoIP 회선)
# -u: bbs_main.py 쪽 stdout도 완전 비버퍼링으로 강제 (PTY라 보통 라인버퍼이긴 하지만 명시)
BBS_COMMAND = ['python3', '-u', 'bbs_main.py']  # BBS 실행 명령어
BAUDRATE = 115200                   # 시리얼 통신 속도 (AT&B1로 DTE측 고정 속도와 일치)
CONNECT_TIMEOUT = 40                 # CONNECT 메시지 대기 시간 (초)

# 모뎀 초기화 AT 명령어 시퀀스.
# 이 VoIP 경로(아날로그 모뎀 오디오 -> HT802 ATA -> Asterisk)는 고속에서
# 핸드셰이크가 거의 실패하고, V.21/Bell103 300bps에서만 안정적으로 연결됨.
# AT&B1이 핵심: 기본값(&B0)은 DTE측 시리얼 속도가 협상된 CONNECT 속도를
# 따라가며 바뀌는데, pyserial 쪽 포트는 baud가 고정이라 CONNECT 300 이후
# 실제 통신 속도가 어긋나며 데이터가 깨졌음. &B1로 DTE측 속도를 115200에
# 고정하고 모뎀이 내부적으로 속도 변환을 하도록 함.
MODEM_INIT_COMMANDS = [
    b'ATZ\r',       # 리셋
    b'ATE0\r',      # 에코 끄기
    b'ATQ0\r',      # 결과 코드(OK 등) 활성화
    b'AT+MS=V32\r', # V.32bis 14400bps 최종 확정 (V.34/26400은 재훈련 잦고 불안정해서 제외)
    b'AT&K0\r',     # 데이터 압축 끔 (K는 흐름제어 아님, USR 공식 레퍼런스로 확인됨)
    b'AT&H1\r',     # TX 하드웨어 흐름제어(CTS) 진짜 명령으로 재시도
    b'AT&R2\r',     # RX 하드웨어 흐름제어(RTS)도 같이 활성화
    b'AT&B1\r',     # DTE측 속도를 고정 (핵심 버그 수정, 위 설명 참고)
    b'ATM1\r',      # 스피커: 연결 전까지만 켜기
    b'ATL0\r',      # 스피커 볼륨 최소
    b'ATS0=0\r',    # 자동 응답 끄기 (RING 감지 후 수동 ATA로 응답)
    b'AT#CID=1\r',  # 발신자번호(CID) 표시 켜기 - RING 사이에 NMBR=/NAME= 줄이 옴.
                    # ATZ 이후에 매번 다시 켜줘야 함 (NVRAM에 저장 안 되고 ATZ로 리셋됨).
]

CID_WAIT_SECONDS = 3  # 첫 RING 이후 CID 정보(NMBR=/NAME=)가 올 때까지 기다리는 시간


def reopen_modem(ser):
    # 물리 UART에서 select-ready-but-empty-read 오류가 나면 그 fd는 더 이상
    # 신뢰할 수 없다고 보고 포트를 닫았다가 다시 연다. 재모뎀설정(AT 시퀀스)
    # 은 다시 하지 않음 - RING/CONNECT 대기 도중 재시도이므로 DTR/RTS만
    # 다시 assert해서 다음 RING을 받을 준비만 갖춘다.
    try:
        ser.close()
    except Exception:
        pass
    time.sleep(0.5)
    new_ser = serial.Serial(MODEM_PORT, BAUDRATE, timeout=0.01, rtscts=True)
    new_ser.dtr = True
    new_ser.rts = True
    log(f'[{MODEM_PORT}] 포트 재시작 완료')
    return new_ser

def modem_handler():
    ser = None
    try:
        # 1. 모뎀 초기화
        ser = serial.Serial(MODEM_PORT, BAUDRATE, timeout=0.01, rtscts=True)
        # DTR/RTS를 명시적으로 assert. pyserial 기본 상태만으로는 이 환경에서
        # 모뎀이 CONNECT를 띄우고도 데이터를 전송하지 않는 문제가 있었음.
        ser.dtr = True
        ser.rts = True
        log(f'[{MODEM_PORT}] 모뎀 초기화 완료')

        # 2. 모뎀 설정 (AT 명령어)
        # ATZ는 리셋이라 응답이 느리게 오므로 명령별로 더 넉넉히 대기
        for cmd in MODEM_INIT_COMMANDS:
            ser.write(cmd)
            wait = 1.0 if cmd == b'ATZ\r' else 0.3
            time.sleep(wait)
            resp = ser.read(256).decode('utf-8', errors='ignore').strip()
            log_verbose(f'[{MODEM_PORT}] {cmd.decode().strip()} -> {resp or "(응답 없음)"}')
        log(f'[{MODEM_PORT}] 모뎀 설정 완료')

        while True:
            # 3. RING 신호 감지
            # 실제 물리 UART 라인이라 회선 잡음/브레이크 신호 등으로 인해
            # select()는 read-ready라고 하는데 실제로는 0바이트가 오는
            # SerialException이 통화 중(relay_modem_to_pty)뿐 아니라 이
            # RING 대기 루프에서도 이론상 발생할 수 있음. 여기서 안 잡으면
            # 아래 바깥쪽 except가 modem_handler() 자체를 끝내버려서
            # (재시작 루프 없이) 데몬이 통째로 죽는다 - 컨테이너가 재시작될
            # 때까지 다음 RING을 영영 못 받는 심각한 장애로 이어짐.
            # 포트를 닫았다 다시 열어서 복구를 시도하고 계속 진행한다.
            try:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
            except serial.SerialException as e:
                log(f'RING 대기 중 시리얼 오류 (포트 재시작 시도): {e}')
                ser = reopen_modem(ser)
                continue
            if 'RING' in line:
                log(f'[{MODEM_PORT}] RING')

                # 3.5 발신자번호(CID) 대기. NMBR=/NAME= 줄은 보통 첫 번째와
                # 두 번째 RING 사이에 옴 - 곧바로 ATA로 받아버리면 그 정보를
                # 영영 못 봄. 짧게 몇 초만 더 읽어서 CID 줄이 오면 로그로 남기고,
                # 그 사이 RING이 또 오면 무시(이미 응답 준비 중이므로).
                cid_deadline = time.time() + CID_WAIT_SECONDS
                while time.time() < cid_deadline:
                    ser.timeout = max(0.01, cid_deadline - time.time())
                    try:
                        cid_line = ser.readline().decode('utf-8', errors='ignore').strip()
                    except serial.SerialException:
                        break
                    finally:
                        ser.timeout = 0.01
                    if cid_line.startswith('NMBR') or cid_line.startswith('NAME'):
                        log(f'[{MODEM_PORT}] {cid_line}')

                # 4. 모뎀 응답 (ATA)
                ser.write(b'ATA\r')   # 전화 받기

                # 5. CONNECT 메시지 확인
                connect_received = False
                start_time = time.time()
                serial_broke_mid_wait = False
                while time.time() - start_time < CONNECT_TIMEOUT:
                    try:
                        line = ser.readline().decode('utf-8', errors='ignore').strip()
                    except serial.SerialException as e:
                        log(f'CONNECT 대기 중 시리얼 오류 (포트 재시작 시도): {e}')
                        ser = reopen_modem(ser)
                        serial_broke_mid_wait = True
                        break
                    if 'CONNECT' in line:
                        log(f'[{MODEM_PORT}] {line}')
                        connect_received = True
                        break
                    time.sleep(0.1)

                if serial_broke_mid_wait:
                    continue  # 다음 RING 대기

                if not connect_received:
                    log(f'[{MODEM_PORT}] CONNECT 실패')
                    ser.write(b'ATH\r')  # 연결 끊기
                    continue  # 다음 RING 대기

                # 6. PTY 생성 및 BBS 실행
                master_fd, slave_fd = pty.openpty()
                # openpty() 직후 slave는 기본 cooked 모드(ICANON/ECHO/ISIG,
                # OPOST/ONLCR 등)로 남아있음. 실측 결과 이 기본 모드에서도
                # EUC-KR 바이트 자체는 손상 없이 그대로 전달됨(줄바꿈만
                # \n -> \r\n으로 바뀌는 정상적인 동작) - 그래도 원격 회선
                # 특유의 타이밍/개행 처리에 어떤 오작동 여지도 남기지 않도록
                # slave를 완전 raw/8비트-투명 모드로 고정해 둔다. bbs_main.py
                # 쪽 getchar()가 첫 입력 시점에 자기 stdin(=이 slave)을 raw로
                # 바꾸긴 하지만, 그 전에 출력되는 환영 화면/인코딩 선택 화면
                # 구간은 이 설정이 없으면 계속 cooked 모드로 나간다.
                tty.setraw(slave_fd)
                log(f'[{MODEM_PORT}] PTY 생성 완료 (raw 모드 설정)')

                # stderr는 절대로 slave_fd(모뎀으로 나가는 통로)에 물리면 안 됨.
                # 예전에는 stderr=slave_fd였는데, bbs_main.py에서 처리 안 된
                # 예외가 나면 트레이스백이 고스란히 모뎀 쪽으로 나가면서(사용자
                # 화면엔 의미 없는 텍스트/무응답) docker logs에는 아무 흔적도
                # 안 남았음 - "이유 없이 멈췄다"처럼 보인 원인 중 하나.
                # 별도 파이프로 빼서 컨테이너 로그에 찍히게 한다.
                proc = subprocess.Popen(
                    BBS_COMMAND,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=subprocess.PIPE,
                    cwd=os.getcwd(),
                    close_fds=True
                )
                os.close(slave_fd)  # 자식 프로세스에서만 사용

                threading.Thread(
                    target=log_bbs_stderr, args=(proc,), daemon=True
                ).start()

                # 7. 데이터 중계(양방향 각각 별도 스레드)
                relay_thread = threading.Thread(
                    target=data_relay,
                    args=(ser, master_fd, proc)
                )
                relay_thread.start()
                # 회선이 한 개뿐이라 통화는 항상 하나만 진행됨. 통화 중에는
                # 위 RING/CONNECT 대기 코드(및 그 안의 reopen_modem())가
                # 같은 ser 객체를 동시에 건드리면 안 되므로, 릴레이 스레드가
                # 끝날 때까지 여기서 블로킹한다. (이걸 안 하면 메인 루프가
                # 통화 중에도 계속 RING을 폴링하다가 relay_modem_to_pty()가
                # 쓰고 있는 ser를 reopen_modem()이 close()해버리는 레이스가
                # 발생함 - self.fd가 None이 된 채로 다른 스레드가 read()를
                # 계속 호출해서 TypeError로 이어졌던 원인.)
                relay_thread.join()

            time.sleep(0.1)

    except serial.SerialException as e:
        log(f'시리얼 포트 오류: {e}')
    except Exception as e:
        log(f'기타 오류: {e}')
    finally:
        if ser:
            ser.close()
            log('모뎀 연결 종료')

def log_bbs_stderr(proc):
    # bbs_main.py의 stderr(트레이스백 등)를 컨테이너 로그로 흘려보냄.
    try:
        for line in iter(proc.stderr.readline, b''):
            if not line:
                break
            log('[bbs_main stderr] ' + line.decode('utf-8', errors='ignore').rstrip())
    except Exception as e:
        log(f'[bbs_main stderr 읽기 오류] {e}')
    finally:
        try:
            proc.stderr.close()
        except Exception:
            pass

def data_relay(ser, master_fd, proc):
    log(f"[{MODEM_PORT}] 데이터 중계 시작")
    disconnect_event = threading.Event()

    # 모뎀 → PTY 방향
    def relay_modem_to_pty():
        buffer = b''
        last_data_time = time.time()
        while proc.poll() is None and not disconnect_event.is_set():
            try:
                data = ser.read(1024)  # timeout이 설정되어 있으므로 무한 대기는 아님
                if data:
                    now = time.time()
                    gap_ms = (now - last_data_time) * 1000
                    log_io(MODEM_PORT, '수신', data, gap_ms)
                    last_data_time = now
                    os.write(master_fd, data)
                    # 한 줄 버퍼에 누적해서 NO CARRIER 체크
                    buffer += data
                    # 줄 끝까지 읽어서 처리
                    while b'\n' in buffer or b'\r' in buffer:
                        if b'\n' in buffer:
                            idx = buffer.index(b'\n')
                        else:
                            idx = buffer.index(b'\r')
                        line = buffer[:idx+1].decode('utf-8', errors='ignore').strip().upper()
                        # print(f"[MODEM LINE] {line}") # 디버깅용 로그
                        if "NO CARRIER" in line:
                            log(f"[{MODEM_PORT}] NO CARRIER")
                            disconnect_event.set()
                            try:
                                os.close(master_fd)
                            except Exception:
                                pass
                            try:
                                proc.terminate()
                            except Exception:
                                pass
                            return   # 즉시 종료
                        buffer = buffer[idx + 1:]
            except Exception:
                # 예전엔 여기서 그냥 break만 하고 끝났음. 그러면 이 스레드는
                # 조용히 죽는데, master_fd/proc는 안 건드리니 반대쪽
                # relay_pty_to_modem 스레드는 os.read(master_fd)에서 계속
                # 블로킹된 채 남아있고(자식이 getchar()에서 막혀 아무것도 안
                # 쓰면 영원히 안 풀림), data_relay()는 t2.join()에서 못 빠져
                #나와 "데이터 중계 종료"도 못 찍고 좀비 세션으로 남았음.
                # (다음 RING이 와도 같은 ser 객체를 이 죽은 스레드가 여전히
                # write()하려 들 수 있어 CONNECT 인식 실패로 이어졌을 가능성)
                # -> 예외 로그를 남기고, NO CARRIER 케이스와 동일하게 확실히
                # 정리(disconnect_event set + master_fd 닫기 + proc 종료)한다.
                log("모뎀→PTY 중계 오류:")
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

    # PTY → 모뎀 방향
    def relay_pty_to_modem():
        last_data_time = time.time()
        while proc.poll() is None and not disconnect_event.is_set():
            try:
                data = os.read(master_fd, 1024)
                if data:
                    now = time.time()
                    gap_ms = (now - last_data_time) * 1000
                    log_io(MODEM_PORT, '송신', data, gap_ms)
                    last_data_time = now
                    ser.write(data)
            except OSError:
                # PTY 종료 시 OSError 발생 (정상 종료 경로)
                break
            except Exception:
                log("PTY→모뎀 중계 오류:")
                traceback.print_exc()
                disconnect_event.set()
                try:
                    proc.terminate()
                except Exception:
                    pass
                break

    t1 = threading.Thread(target=relay_modem_to_pty)
    t2 = threading.Thread(target=relay_pty_to_modem)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    log(f"[{MODEM_PORT}] 데이터 중계 종료")
    try:
        os.close(master_fd)
    except Exception:
        pass
    try:
        proc.terminate()
    except Exception:
        pass
    time.sleep(0.5)
    hangup_modem(ser)

def hangup_modem(ser):
    # RING/CONNECT 대기 루프 쪽 reopen_modem()이 이미 포트를 닫고 새
    # ser 객체로 교체했을 수 있음 (예: 통화 도중 시리얼 오류) - 그 경우
    # 이 함수에 넘어온 ser는 의도적으로 닫힌 것이므로 에러가 아니라 그냥
    # 조용히 넘어간다.
    if ser is None or not ser.is_open:
        log('통화 끊기 생략: 포트가 이미 닫혀 있음')
        return
    try:
        # 모뎀에 명령 모드 진입 시그널 보내기
        time.sleep(1)
        ser.write(b'+++')
        ser.flush()
        time.sleep(2)
        # 끊기 명령 전송
        ser.write(b'ATH\r')
        ser.flush()
        time.sleep(1)
    except Exception as e:
        log(f'통화 끊기 에러: {e}')

if __name__ == "__main__":
    # 마지막 안전망: modem_handler()가 예상 못한 이유로 리턴/예외 종료해도
    # 프로세스 자체는 죽지 않고 포트를 다시 초기화해서 서비스를 이어간다.
    # (Docker restart 정책에 기대면 컨테이너 전체가 내려갔다 올라오는 동안
    # RING을 놓치므로, 프로세스 레벨에서 먼저 복구를 시도하는 편이 낫다.)
    while True:
        try:
            modem_handler()
        except Exception as e:
            log(f'modem_handler() 최상위 예외, 재시작: {e}')
        log('modem_handler() 종료됨 - 5초 후 재시작')
        time.sleep(5)
