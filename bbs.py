import sys
import traceback
from core.login import login_menu
from core.init import initialize
from bbsio.rawio import (
    set_encoding, set_channel, rawprint, rawinput, SessionIdleTimeout, ConnectionClosed,
)

def parse_channel():
    """dialup.py/telnet.py가 --channel=modem 또는 --channel=telnet 인자로
    어느 경로로 들어온 접속인지 알려준다. 직접 실행하는 등 인자가 없으면
    'unknown'으로 둔다."""
    for arg in sys.argv[1:]:
        if arg.startswith('--channel='):
            return arg.split('=', 1)[1]
    return 'unknown'

def select_locale():
    rawprint("사용할 문자 인코딩을 선택하세요:\n", 'euc-kr')
    rawprint("1. 이 글자가 보이면 완성형 입니다.\n", 'euc-kr')
    rawprint("2. 이 글자가 보이면 조합형 입니다.\n", 'johab')
    rawprint("3. 이 글자가 보이면 UTF-8 입니다.\n", 'utf-8')
    rawprint("\n", 'euc-kr')
    choice = rawinput("선택 (1~3, 기본값 1): ", 'euc-kr').strip()
    if choice == '2':
        return 'johab'
    elif choice == '3':
        return 'utf-8'
    else:
        return 'euc-kr'

def main():
    initialize()
    set_channel(parse_channel())
    encoding = select_locale()
    set_encoding(encoding)
    login_menu()


if __name__ == "__main__":
    # 예전엔 여기서 예외가 나면 stderr가 모뎀 PTY로 바로 나가서(도커 로그엔
    # 아무것도 안 남고) 세션이 그냥 조용히 죽었음 - "이유 없는 프리징"처럼
    # 보인 원인 중 하나였음. stderr를 이제 dialup.py가 별도로 캡처하니
    # 여기서 잡아서 제대로 로그를 남기고 정상 종료한다.
    try:
        main()
    except ConnectionClosed:
        print('[세션 종료] 연결 정상 종료', file=sys.stderr)
        sys.exit(0)
    except SessionIdleTimeout as e:
        print(f'[세션 종료] 입력 유휴 타임아웃: {e}', file=sys.stderr)
        sys.exit(0)
    except Exception:
        print('[세션 종료] 처리되지 않은 예외:', file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
