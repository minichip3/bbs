import serial
import pty
import os
import subprocess
import threading
import time

# 설정
MODEM_PORT = '/dev/ttyS1'           # 실제 모뎀 장치
BBS_COMMAND = ['python3', 'bbs_main.py']  # BBS 실행 명령어
BAUDRATE = 115200                   # 시리얼 통신 속도
CONNECT_TIMEOUT = 40                 # CONNECT 메시지 대기 시간 (초)

def modem_handler():
    ser = None
    try:
        # 1. 모뎀 초기화
        ser = serial.Serial(MODEM_PORT, BAUDRATE, timeout=0.01)
        print(f'모뎀 {MODEM_PORT} 초기화 완료')

        # 2. 모뎀 설정 (AT 명령어)
        ser.write(b'ATE0\r')   # 에코 끄기 (모뎀이 스스로 입력을 에코하지 않음)
        ser.write(b'ATQ0\r')   # 결과 코드 활성화
        ser.write(b'ATS0=0\r') # 자동 응답 끄기 (필요에 따라 설정)
        time.sleep(0.1)
        print('모뎀 설정 완료')

        while True:
            # 3. RING 신호 감지
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if 'RING' in line:
                print('전화벨 감지!')

                # 4. 모뎀 응답 (ATA)
                ser.write(b'ATA\r')   # 전화 받기

                # 5. CONNECT 메시지 확인
                connect_received = False
                start_time = time.time()
                while time.time() - start_time < CONNECT_TIMEOUT:
                    line = ser.readline().decode('utf-8', errors='ignore').strip()
                    if 'CONNECT' in line:
                        print('CONNECT 메시지 수신:', line)
                        connect_received = True
                        break
                    time.sleep(0.1)

                if not connect_received:
                    print('CONNECT 메시지 수신 실패')
                    ser.write(b'ATH\r')  # 연결 끊기
                    continue  # 다음 RING 대기

                # 6. PTY 생성 및 BBS 실행
                master_fd, slave_fd = pty.openpty()
                print('PTY 생성 완료')

                proc = subprocess.Popen(
                    BBS_COMMAND,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    cwd=os.getcwd(),
                    close_fds=True
                )
                os.close(slave_fd)  # 자식 프로세스에서만 사용

                # 7. 데이터 중계(양방향 각각 별도 스레드)
                threading.Thread(
                    target=data_relay,
                    args=(ser, master_fd, proc)
                ).start()

            time.sleep(0.1)

    except serial.SerialException as e:
        print(f'시리얼 포트 오류: {e}')
    except Exception as e:
        print(f'기타 오류: {e}')
    finally:
        if ser:
            ser.close()
            print('모뎀 연결 종료')

def data_relay(ser, master_fd, proc):
    print("데이터 중계 시작")
    disconnect_event = threading.Event()

    # 모뎀 → PTY 방향
    def relay_modem_to_pty():
        buffer = b''
        while proc.poll() is None and not disconnect_event.is_set():
            try:
                data = ser.read(1024)  # timeout이 설정되어 있으므로 무한 대기는 아님
                if data:
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
                            print("NO CARRIER 수신")
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
            except Exception as e:
                print("모뎀→PTY 중계 오류:", e)
                break

    # PTY → 모뎀 방향
    def relay_pty_to_modem():
        while proc.poll() is None:
            try:
                data = os.read(master_fd, 1024)
                if data:
                    ser.write(data)
            except OSError as e:
                # PTY 종료 시 OSError 발생
                break
            except Exception as e:
                print("PTY→모뎀 중계 오류:", e)
                break

    t1 = threading.Thread(target=relay_modem_to_pty)
    t2 = threading.Thread(target=relay_pty_to_modem)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    print("데이터 중계 종료")
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
        print('통화 끊기 에러:', e)

if __name__ == "__main__":
    modem_handler()
