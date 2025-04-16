import os
import json
import hashlib
import bbsio.tui as tui
from bbsio.rawio import rawprint, rawinput, hidden_input
from core.board import main_menu

USER_FILE = os.path.join('data', 'users.json')


def load_users():
    if not os.path.exists(USER_FILE):
        return {}
    with open(USER_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_users(users):
    with open(USER_FILE, 'w', encoding='utf-8') as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def login(users):
    rawprint("\x1b[2J\x1b[H")  # Clear screen
    rawprint("로그인을 해 주세요\n\n")
    username = rawinput("아이디: ")
    password = hidden_input("비밀번호: ")

    hashed = hashlib.sha256(password.encode()).hexdigest()
    if username in users and users[username]['password'] == hashed:
        user_info = users[username]
        width = user_info.get('width', 0)
        height = user_info.get('height', 0)
        if width > 0 and height > 0:
            tui.SCREEN_WIDTH = width
            tui.SCREEN_HEIGHT = height
        rawprint(f"\n환영합니다, {username}님!\n")
        rawinput("계속하려면 Enter를 누르세요.")
        return username
    else:
        rawprint("\n로그인 실패. 다시 시도해주세요.\n")
        rawinput("계속하려면 Enter를 누르세요.")
        return None


def register(users):
    rawprint("\x1b[2J\x1b[H")  # Clear screen
    rawprint("★ 회원 가입을 시작합니다 ★\n\n")

    while True:
        username = rawinput("사용하실 아이디를 입력하세요: ").strip()
        if not username:
            rawprint("아이디는 공란일 수 없습니다.\n")
            continue
        try:
            username.encode('ascii')
        except UnicodeEncodeError:
            rawprint("영문/숫자만 가능합니다 (한글 불가).\n")
            continue
        if username in users:
            rawprint("이미 존재하는 아이디입니다. 다른 아이디를 입력해 주세요.\n")
            continue
        break

    rawprint(f"\n아이디: {username}\n")

    while True:
        password1 = hidden_input("비밀번호를 입력하세요: ")
        if not password1:
            rawprint("비밀번호는 공란일 수 없습니다.\n")
            continue
        try:
            password1.encode('ascii')
        except UnicodeEncodeError:
            rawprint("영문/숫자만 가능합니다 (한글 불가).\n")
            continue
        password2 = hidden_input("비밀번호를 다시 입력하세요: ")
        if password1 != password2:
            rawprint("비밀번호가 일치하지 않습니다.\n")
            continue
        break

    try:
        width = int(rawinput("화면 칸 수 (자동: 0): "))
    except ValueError:
        width = 0
    try:
        height = int(rawinput("화면 줄 수 (자동: 0): "))
    except ValueError:
        height = 0

    profile = {
        'name': rawinput("이름: "),
        'sex': rawinput("성별 (M/F): ").upper(),
        'birthday': rawinput("생년월일 (YYYYMMDD): "),
        'post': rawinput("우편번호: "),
        'home_addr': rawinput("집 주소: "),
        'home_tel': rawinput("집 전화번호: "),
        'office_name': rawinput("직장명: "),
        'office_tel': rawinput("직장 전화번호: "),
        'width': width,
        'height': height,
        'is_admin': False
    }

    users[username] = {
        'password': hashlib.sha256(password1.encode()).hexdigest(),
        **profile
    }

    while True:
        show_user_info(username, users, admin_mode=False)
        confirm = rawinput("\n이대로 가입하시겠습니까? (Y: 계속/E: 수정/N: 취소): ").strip().upper()
        if confirm == 'Y':
            break
        elif confirm == 'N':
            rawprint("회원가입이 취소되었습니다.\n")
            users.pop(username, None)
            rawinput("계속하려면 Enter를 누르세요.")
            return None
        elif confirm != 'E':
            rawprint("잘못된 입력입니다. Y, E 또는 N 중에서 선택해 주세요.\n")

        if confirm == 'E':
            try:
                width = int(rawinput("화면 칸 수 (자동: 0): "))
            except ValueError:
                width = 0
            try:
                height = int(rawinput("화면 줄 수 (자동: 0): "))
            except ValueError:
                height = 0

            users[username].update({
                'name': rawinput("이름: "),
                'sex': rawinput("성별 (M/F): ").upper(),
                'birthday': rawinput("생년월일 (YYYYMMDD): "),
                'post': rawinput("우편번호: "),
                'home_addr': rawinput("집 주소: "),
                'home_tel': rawinput("집 전화번호: "),
                'office_name': rawinput("직장명: "),
                'office_tel': rawinput("직장 전화번호: "),
                'width': width,
                'height': height
            })

    save_users(users)
    rawprint("\n회원가입이 완료되었습니다. 환영합니다!\n")
    rawinput("계속하려면 Enter를 누르세요.")
    return username


def draw_login_menu():
    rawprint("\x1b[2J\x1b[H")  # Clear screen
    banner_path = os.path.join('data', 'login_banner.txt')
    if os.path.exists(banner_path):
        with open(banner_path, encoding='utf-8') as f:
            for line in f:
                rawprint(line)
    else:
        rawprint("RETRO BBS\n")
    rawprint("\n")
    rawprint("1. 로그인\n")
    rawprint("2. 회원가입\n")
    rawprint("3. 종료\n")


def login_menu():
    users = load_users()
    while True:
        draw_login_menu()
        choice = rawinput("선택: ")
        if choice == '1':
            user = login(users)
            if user:
                main_menu(user)
        elif choice == '2':
            user = register(users)
            if user:
                main_menu(user)
        elif choice == '3':
            rawprint("다음에 또 만나요!\n")
            break
        else:
            rawprint("잘못된 선택입니다.\n")


def show_user_info(username, users, admin_mode=False):
    user = users.get(username)
    if not user:
        rawprint("사용자 정보를 찾을 수 없습니다.\n")
        return

    rawprint("\x1b[2J\x1b[H")  # Clear screen
    rawprint("신청 내역\n\n")
    rawprint(f" 1 아   이   디 : {username}\n")
    rawprint(f" 2 비 밀  번 호 : {'*' * 8}\n")
    rawprint(f" 3 이        름 : {user.get('name', '')}\n")
    rawprint(f" 4 성        별 : {user.get('sex', '')}\n")
    rawprint(f" 5 생 년  월 일 : {user.get('birthday', '')}\n")
    rawprint(f" 6 우 편  번 호 : {user.get('post', '')}\n")
    rawprint(f" 7 집   주   소 : {user.get('home_addr', '')}\n")
    rawprint(f" 8 집   전   화 : {user.get('home_tel', '')}\n")
    rawprint(f" 9 직   장   명 : {user.get('office_name', '')}\n")
    rawprint(f"10 직 장  전 화 : {user.get('office_tel', '')}\n")
    rawprint(f"11 화면   칸 수 : {user.get('width', 0)}\n")
    rawprint(f"12 화면   줄 수 : {user.get('height', 0)}\n")

    if admin_mode and user.get('is_admin'):
        rawprint("== 이 계정은 관리자 권한을 가지고 있습니다 ==\n")
