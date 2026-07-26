from bbsio.rawio import rawprint, rawinput, command_input
from bbsio.tui import (
    C_TITLE, C_TEXT, C_DIM, C_OK, C_ERR, C_HILITE, RESET,
    clear_screen, draw_top_bar, draw_footer,
    box_top, box_bottom, box_line, get_screen_size,
)
from core.profile import load_users, save_users, show_user_info, now_str

SITE_NAME = "M I N I - T E L"

# sysop 계정은 최초 관리자로, 여기서 권한을 뺏기거나 삭제되면 아무도
# 회원 관리를 못 하게 되는 상황이 생길 수 있어 항상 보호한다.
PROTECTED_USER = 'sysop'


def admin_menu(username):
    while True:
        try:
            clear_screen()
            width, _ = get_screen_size()
            draw_top_bar(SITE_NAME + " 관리자 메뉴", f"{username}  {now_str()}", width)
            rawprint('\n')

            users = load_users()
            user_ids = list(users.keys())
            box_top(width, '회원 목록')
            if not users:
                box_line("(등록된 회원이 없습니다)", width)
            else:
                for i, uid in enumerate(user_ids):
                    info = users[uid]
                    admin_mark = (C_TITLE + "[관리자]" + RESET) if info.get('is_admin') else "        "
                    last = info.get('last_login') or '(접속 기록 없음)'
                    line = (f"{C_HILITE}{i + 1:>2}{RESET} {admin_mark} "
                            f"{C_TEXT}{uid:<12}{RESET} {C_DIM}{info.get('name', ''):<10} {last}{RESET}")
                    box_line(line, width)
            box_bottom(width)
            draw_footer("[번호:회원 관리] [P:뒤로]", width)

            cmd = command_input(C_TITLE + " > " + RESET).strip().lower()

            if cmd == 'p':
                break
            else:
                try:
                    sel = int(cmd)
                    if 1 <= sel <= len(user_ids):
                        manage_user(user_ids[sel - 1])
                    else:
                        rawprint(C_ERR + "잘못된 번호입니다.\n" + RESET)
                        rawinput("계속하려면 Enter를 누르세요.\n")
                except ValueError:
                    rawprint(C_ERR + "잘못된 명령입니다.\n" + RESET)
                    rawinput("계속하려면 Enter를 누르세요.\n")
        except KeyboardInterrupt:
            break


def manage_user(target_id):
    width, _ = get_screen_size()
    while True:
        try:
            users = load_users()
            if target_id not in users:
                return

            clear_screen()
            draw_top_bar(SITE_NAME + " 회원 관리", now_str(), width)
            rawprint('\n')
            show_user_info(target_id, users, admin_mode=True)

            hint = "[G:관리자 권한 전환] [D:강제 탈퇴] [P:뒤로]"
            if target_id == PROTECTED_USER:
                hint = f"(sysop 계정은 보호되어 권한 변경/삭제 불가)  [P:뒤로]"
            draw_footer(hint, width)

            cmd = command_input(C_TITLE + " > " + RESET).strip().lower()

            if cmd == 'p':
                return
            elif cmd == 'g':
                if target_id == PROTECTED_USER:
                    rawprint(C_ERR + "sysop 계정은 변경할 수 없습니다.\n" + RESET)
                    rawinput("계속하려면 Enter를 누르세요.\n")
                    continue
                users[target_id]['is_admin'] = not users[target_id].get('is_admin', False)
                save_users(users)
                state = "부여" if users[target_id]['is_admin'] else "해제"
                rawprint(C_OK + f"관리자 권한이 {state}되었습니다.\n" + RESET)
                rawinput("계속하려면 Enter를 누르세요.\n")
            elif cmd == 'd':
                if target_id == PROTECTED_USER:
                    rawprint(C_ERR + "sysop 계정은 삭제할 수 없습니다.\n" + RESET)
                    rawinput("계속하려면 Enter를 누르세요.\n")
                    continue
                confirm = rawinput(C_ERR + f"정말 '{target_id}' 계정을 삭제하시겠습니까? (Y/N): " + RESET).strip().upper()
                if confirm == 'Y':
                    users.pop(target_id, None)
                    save_users(users)
                    rawprint(C_OK + "계정이 삭제되었습니다.\n" + RESET)
                    rawinput("계속하려면 Enter를 누르세요.\n")
                    return
                else:
                    rawprint("삭제가 취소되었습니다.\n")
                    rawinput("계속하려면 Enter를 누르세요.\n")
            else:
                rawprint(C_ERR + "잘못된 명령입니다.\n" + RESET)
                rawinput("계속하려면 Enter를 누르세요.\n")
        except KeyboardInterrupt:
            return
