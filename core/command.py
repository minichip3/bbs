import sys
from bbsio.rawio import rawinput, rawprint

def is_global_command(cmd: str) -> bool:
    """
    글로벌 명령어인지 확인합니다.
    """
    return cmd.lower() in ['x', 'p']

def handle_global_command(cmd: str) -> bool:
    """
    글로벌 명령어를 처리한다.
    - 처리된 경우 True를 반환하고, 입력 루프를 종료하지 않도록 한다.
    - 처리되지 않은 경우 False를 반환한다.
    """
    if cmd.lower() == 'x':
        # exit_program()의 반환값(취소 시 False)을 그대로 handled로 쓰면
        # 안 된다 - 'x'는 y/n 결과와 무관하게 항상 "전역 명령어로 처리됨"
        # 이어야 한다. 실제 종료는 exit_program() 내부에서 sys.exit()로
        # 바로 끝나버리니, 여기까지 돌아왔다는 건 취소된 경우뿐이다.
        # 예전엔 취소 시 False가 그대로 리턴되면서 command_input()이
        # "x"라는 문자열을 마치 사용자가 고른 메뉴 선택지인 것처럼 호출자에게
        # 돌려줘서 "잘못된 선택입니다"가 뜨는 버그가 있었다.
        exit_program()
        return True
    elif cmd.lower() == 'p':
        raise KeyboardInterrupt
    return False

def exit_program() -> bool:
    """
    프로그램 종료 여부를 사용자에게 확인한다.
    'y' 입력 시 종료, 'n' 입력 시 취소.
    반환값:
        True  - 종료됨
        False - 취소됨
    """
    rawprint("\n정말 종료하시겠습니까? (y/n): ")
    confirm = rawinput().strip().lower()
    if confirm == 'y':
        rawprint("프로그램을 종료합니다.\n")
        sys.exit(0)
    return False