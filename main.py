import subprocess
import threading
import time

# 서버 실행 함수
def run_server(module_name):
    try:
        subprocess.run(['python3', module_name], check=True)  # 오류 발생시 예외 발생
    except subprocess.CalledProcessError as e:
        print(f"오류 발생: {module_name} - {e}")
    except FileNotFoundError:
        print(f"오류: {module_name} 파일을 찾을 수 없습니다.")
    except Exception as e:
        print(f"예기치 않은 오류: {module_name} - {e}")

# 각 서버를 별도 스레드로 실행
def main():
    # 다이얼업 서버 (백그라운드 스레드)
    dialup_thread = threading.Thread(target=run_server, args=('dialup.py',), daemon=True)
    dialup_thread.start()

    # 텔넷 서버 (백그라운드 스레드)
    telnet_thread = threading.Thread(target=run_server, args=('telnet.py',), daemon=True)
    telnet_thread.start()

    # 웹 서버 (메인 스레드) - Flask는 자체 서버 실행 (run_server는 불필요)
    try:
        # Web.py는 flask-socketio를 사용하므로, main.py에서 직접 실행하지 않음
        # 웹서버는 웹서버 자체 내장 서버로 동작
        run_server('web.py')
    except KeyboardInterrupt:
        print("\n서버 종료 (Ctrl+C)")
    except Exception as e:
        print(f"웹 서버 실행 중 오류: {e}")
    finally:
        print("서버 종료")

if __name__ == "__main__":
    main()