import os
import json
import hashlib
from datetime import datetime

from bbsio.rawio import rawinput, hidden_input, command_input
from bbsio.tui import (
    rawprint, C_TITLE, C_TEXT, C_DIM, C_OK, C_ERR, RESET,
    clear_screen, draw_top_bar, draw_footer,
    box_top, box_bottom, box_line, box_sep, get_screen_size,
)

USER_FILE = os.path.join('data', 'users.json')
SITE_NAME = "M I N I - T E L"

# login.py(가입/로그인)와 board.py(내 정보 수정, 관리자 메뉴)가 둘 다
# 회원 정보에 접근해야 하는데, login.py가 core.board.main_menu를 이미
# 임포트하고 있어서 board.py가 login.py를 다시 임포트하면 순환 임포트가
# 생긴다. 그래서 회원 관련 공통 로직을 이 모듈로 분리했다.


def now_str():
    return datetime.now().strftime('%y/%m/%d %H:%M')


def load_users():
    if not os.path.exists(USER_FILE):
        return {}
    with open(USER_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_users(users):
    with open(USER_FILE, 'w', encoding='utf-8') as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def is_admin(username):
    return load_users().get(username, {}).get('is_admin', False)


def collect_profile():
    return {
        'name': rawinput("이름: "),
        'sex': rawinput("성별 (M/F): ").upper(),
        'birthday': rawinput("생년월일 (YYYYMMDD): "),
    }


def show_user_info(username, users, admin_mode=False):
    user = users.get(username)
    if not user:
        rawprint(C_ERR + "사용자 정보를 찾을 수 없습니다.\n" + RESET)
        return

    width, _ = get_screen_size()
    box_top(width, '신청 내역')
    box_line(f" 1 아   이   디 : {username}", width)
    box_line(f" 2 비 밀  번 호 : {'*' * 8}", width)
    box_line(f" 3 이        름 : {user.get('name', '')}", width)
    box_line(f" 4 성        별 : {user.get('sex', '')}", width)
    box_line(f" 5 생 년  월 일 : {user.get('birthday', '')}", width)
    if admin_mode and user.get('is_admin'):
        box_sep(width)
        box_line("== 이 계정은 관리자 권한을 가지고 있습니다 ==", width)
    box_bottom(width)


def edit_profile(username):
    """로그인 후 메인 메뉴에서 자기 정보/비밀번호를 직접 고칠 수 있게 한다."""
    width, _ = get_screen_size()

    while True:
        try:
            users = load_users()
            clear_screen()
            draw_top_bar(SITE_NAME + " 내 정보", now_str(), width)
            rawprint('\n')
            show_user_info(username, users, admin_mode=True)
            draw_footer("[E:정보 수정] [W:비밀번호 변경] [P:뒤로]", width)

            cmd = command_input(C_TITLE + " > " + RESET).strip().lower()

            if cmd == 'p':
                break
            elif cmd == 'e':
                rawprint(C_DIM + "\n새 정보를 입력하세요.\n" + RESET)
                new_profile = collect_profile()
                users[username].update(new_profile)
                save_users(users)
                rawprint(C_OK + "정보가 수정되었습니다.\n" + RESET)
                rawinput("계속하려면 Enter를 누르세요.\n")
            elif cmd == 'w':
                pw1 = hidden_input("새 비밀번호: ")
                if not pw1:
                    rawprint(C_ERR + "비밀번호는 공란일 수 없습니다.\n" + RESET)
                    rawinput("계속하려면 Enter를 누르세요.\n")
                    continue
                pw2 = hidden_input("새 비밀번호 확인: ")
                if pw1 != pw2:
                    rawprint(C_ERR + "비밀번호가 일치하지 않습니다.\n" + RESET)
                    rawinput("계속하려면 Enter를 누르세요.\n")
                    continue
                users[username]['password'] = hashlib.sha256(pw1.encode()).hexdigest()
                save_users(users)
                rawprint(C_OK + "비밀번호가 변경되었습니다.\n" + RESET)
                rawinput("계속하려면 Enter를 누르세요.\n")
            else:
                rawprint(C_ERR + "잘못된 명령입니다.\n" + RESET)
                rawinput("계속하려면 Enter를 누르세요.\n")
        except KeyboardInterrupt:
            break
