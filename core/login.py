import os
import sys
import json
import random
import hashlib
from datetime import datetime

import bbsio.tui as tui
from bbsio.tui import (
    rawprint, C_BORDER, C_TITLE, C_TEXT, C_DIM, C_OK, C_ERR, C_HILITE, RESET,
    clear_screen, hline, pad, draw_top_bar, box_top, box_bottom, box_line, box_sep,
    get_screen_size,
)
from bbsio.rawio import rawinput, hidden_input
from core.board import main_menu
from core import stats as stats_mod
from core import mail

QUOTES_FILE = os.path.join('data', 'quotes.txt')

USER_FILE = os.path.join('data', 'users.json')
MAX_LOGIN_TRY = 3

SITE_NAME = "M I N I - T E L"


def load_users():
    if not os.path.exists(USER_FILE):
        return {}
    with open(USER_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_users(users):
    with open(USER_FILE, 'w', encoding='utf-8') as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def pick_quote():
    if not os.path.exists(QUOTES_FILE):
        return ''
    with open(QUOTES_FILE, encoding='utf-8') as f:
        lines = [line.strip() for line in f if line.strip()]
    return random.choice(lines) if lines else ''


def draw_splash(visit_stats=None, quote=''):
    """접속 로고 - 하이텔 접속 시 뜨던 부팅 배너를 흉내낸다."""
    clear_screen()
    width, _ = get_screen_size()
    draw_top_bar(SITE_NAME, datetime.now().strftime('%y/%m/%d %H:%M'), width)
    rawprint('\n')
    banner_path = os.path.join('data', 'login_banner.txt')
    if os.path.exists(banner_path):
        with open(banner_path, encoding='utf-8') as f:
            for line in f:
                rawprint(C_HILITE + line.rstrip('\n') + RESET + '\n')
    else:
        rawprint(C_HILITE + "M I N I - T E L  B B S" + RESET + '\n')
    rawprint('\n')
    rawprint(C_DIM + pad("사설 전자게시판(BBS) 서비스 - 01410 접속을 환영합니다.", width, 'center') + RESET + '\n')
    rawprint('\n')

    if visit_stats is not None:
        member_count = len(load_users())
        info = (f"총 회원 {member_count}명  |  오늘 접속 {visit_stats.get('today_visits', 0)}명"
                f"  |  누적 접속 {visit_stats.get('total_visits', 0)}회")
        rawprint(C_DIM + pad(info, width, 'center') + RESET + '\n')
    if quote:
        rawprint(C_TITLE + pad(f"※ {quote}", width, 'center') + RESET + '\n')
    rawprint('\n')


def draw_login_box():
    width, _ = get_screen_size()
    box_top(width, '접속 안내')
    box_line("이용자 ID를 입력해 주세요.", width)
    box_line("", width)
    box_line("신규 가입을 원하시면 ID란에  NEW  를 입력하십시오.", width)
    box_line("접속을 끊으시려면 ID란에  QUIT 를 입력하십시오.", width)
    box_bottom(width)
    rawprint('\n')


def login_prompt(users):
    """ID/비밀번호를 받아 로그인 처리. 성공 시 username, 취소 시 None,
    종료 요청 시 'QUIT' 문자열을 반환한다."""
    user_id = rawinput(C_TITLE + " 이용자 ID       : " + RESET).strip()

    if user_id.upper() == 'QUIT':
        return 'QUIT'
    if user_id.upper() in ('NEW', 'GUEST', '손님'):
        return register(users)
    if not user_id:
        return None

    tries = 0
    while tries < MAX_LOGIN_TRY:
        password = hidden_input(C_TITLE + " 비밀번호       : " + RESET)
        hashed = hashlib.sha256(password.encode()).hexdigest()

        if user_id in users and users[user_id]['password'] == hashed:
            user_info = users[user_id]
            width_cfg = user_info.get('width', 0)
            height_cfg = user_info.get('height', 0)
            if width_cfg > 0 and height_cfg > 0:
                tui.SCREEN_WIDTH = width_cfg
                tui.SCREEN_HEIGHT = height_cfg

            last_login = user_info.get('last_login')
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            users[user_id]['last_login'] = now
            save_users(users)

            width, _ = get_screen_size()
            rawprint('\n')
            box_top(width, '로그인 성공')
            box_line(f"{user_info.get('name', user_id)}({user_id})님, 반갑습니다!", width)
            if last_login:
                box_line(f"직전 접속 : {last_login}", width)
            else:
                box_line("첫 방문을 환영합니다!", width)
            unread = mail.unread_count(user_id)
            if unread > 0:
                box_line(f"※ 읽지 않은 쪽지가 {unread}통 있습니다!", width)
            box_bottom(width)
            rawinput(C_DIM + "\n계속하려면 Enter를 누르세요." + RESET)
            return user_id

        tries += 1
        remain = MAX_LOGIN_TRY - tries
        if remain > 0:
            rawprint(C_ERR + f"\n 비밀번호가 일치하지 않습니다. (남은 시도: {remain}회)\n" + RESET)
        else:
            rawprint(C_ERR + "\n 비밀번호를 3회 잘못 입력하였습니다. 접속을 종료합니다.\n" + RESET)
            sys.exit(0)

    return None


def register(users):
    clear_screen()
    width, _ = get_screen_size()
    draw_top_bar(SITE_NAME + " 신규가입", datetime.now().strftime('%y/%m/%d %H:%M'), width)
    rawprint('\n')
    rawprint(C_TITLE + pad("★ 회원 가입을 시작합니다 ★", width, 'center') + RESET + '\n\n')

    while True:
        username = rawinput(C_TEXT + "사용하실 아이디를 입력하세요: " + RESET).strip()
        if not username:
            rawprint(C_ERR + "아이디는 공란일 수 없습니다.\n" + RESET)
            continue
        try:
            username.encode('ascii')
        except UnicodeEncodeError:
            rawprint(C_ERR + "영문/숫자만 가능합니다 (한글 불가).\n" + RESET)
            continue
        if username.upper() in ('NEW', 'GUEST', 'QUIT'):
            rawprint(C_ERR + "사용할 수 없는 아이디입니다.\n" + RESET)
            continue
        if username in users:
            rawprint(C_ERR + "이미 존재하는 아이디입니다. 다른 아이디를 입력해 주세요.\n" + RESET)
            continue
        break

    rawprint(f"\n아이디: {username}\n")

    while True:
        password1 = hidden_input("비밀번호를 입력하세요: ")
        if not password1:
            rawprint(C_ERR + "비밀번호는 공란일 수 없습니다.\n" + RESET)
            continue
        try:
            password1.encode('ascii')
        except UnicodeEncodeError:
            rawprint(C_ERR + "영문/숫자만 가능합니다 (한글 불가).\n" + RESET)
            continue
        password2 = hidden_input("비밀번호를 다시 입력하세요: ")
        if password1 != password2:
            rawprint(C_ERR + "비밀번호가 일치하지 않습니다.\n" + RESET)
            continue
        break

    profile = collect_profile()

    users[username] = {
        'password': hashlib.sha256(password1.encode()).hexdigest(),
        'last_login': None,
        **profile,
        'is_admin': False,
    }

    while True:
        show_user_info(username, users)
        confirm = rawinput("\n이대로 가입하시겠습니까? (Y: 계속/E: 수정/N: 취소): ").strip().upper()
        if confirm == 'Y':
            break
        elif confirm == 'N':
            rawprint("회원가입이 취소되었습니다.\n")
            users.pop(username, None)
            rawinput("계속하려면 Enter를 누르세요.")
            return None
        elif confirm == 'E':
            users[username].update(collect_profile())
        else:
            rawprint(C_ERR + "잘못된 입력입니다. Y, E 또는 N 중에서 선택해 주세요.\n" + RESET)

    save_users(users)
    rawprint(C_OK + "\n회원가입이 완료되었습니다. 환영합니다!\n" + RESET)
    rawinput("계속하려면 Enter를 누르세요.")
    return username


def collect_profile():
    try:
        width = int(rawinput("화면 칸 수 (자동: 0): "))
    except ValueError:
        width = 0
    try:
        height = int(rawinput("화면 줄 수 (자동: 0): "))
    except ValueError:
        height = 0

    return {
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
    }


def login_menu():
    users = load_users()
    # 로그인 재시도마다가 아니라 실제 접속(전화 연결) 당 한 번만 집계한다.
    visit_stats = stats_mod.record_visit()
    quote = pick_quote()
    while True:
        draw_splash(visit_stats, quote)
        draw_login_box()
        result = login_prompt(users)
        if result == 'QUIT':
            rawprint(C_OK + "\n다음에 또 만나요!\n" + RESET)
            break
        elif result:
            main_menu(result)
        # 실패/취소 시 다시 접속 화면부터


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
    box_line(f" 6 우 편  번 호 : {user.get('post', '')}", width)
    box_line(f" 7 집   주   소 : {user.get('home_addr', '')}", width)
    box_line(f" 8 집   전   화 : {user.get('home_tel', '')}", width)
    box_line(f" 9 직   장   명 : {user.get('office_name', '')}", width)
    box_line(f"10 직 장  전 화 : {user.get('office_tel', '')}", width)
    box_line(f"11 화면   칸 수 : {user.get('width', 0)}", width)
    box_line(f"12 화면   줄 수 : {user.get('height', 0)}", width)
    if admin_mode and user.get('is_admin'):
        box_sep(width)
        box_line("== 이 계정은 관리자 권한을 가지고 있습니다 ==", width)
    box_bottom(width)
