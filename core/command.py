import sys
from bbsio.rawio import rawinput, rawprint

def is_global_command(cmd: str) -> bool:
    """
    글로벌 명령어인지 확인합니다.
    현재는 'x' 또는 'X' (종료 명령어)만 처리합니다.
    """
    return cmd.lower() == 'x'

def handle_global_command(cmd: str) -> bool:
    """
    글로벌 명령어를 처리한다.
    - 처리된 경우 True를 반환하고, 입력 루프를 종료하지 않도록 한다.
    - 처리되지 않은 경우 False를 반환한다.
    """
    if cmd.lower() == 'x':
        return exit_program()
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