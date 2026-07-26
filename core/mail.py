import os
import json
from datetime import datetime

from bbsio.rawio import rawprint, rawinput, command_input, multiline_input
from bbsio.tui import (
    C_TITLE, C_TEXT, C_DIM, C_OK, C_ERR, C_HILITE, RESET,
    clear_screen, draw_top_bar, draw_footer,
    box_top, box_bottom, box_line, box_sep, get_screen_size,
)

MAIL_FILE = os.path.join('data', 'messages.json')
USER_FILE = os.path.join('data', 'users.json')
SITE_NAME = "M I N I - T E L"


def now_str():
    return datetime.now().strftime('%y/%m/%d %H:%M')


def _load_users():
    # core.login의 load_users()를 그대로 쓰면 core.board -> core.login ->
    # core.board 순환 임포트가 생겨서, 여기서는 users.json을 직접 읽는다.
    if not os.path.exists(USER_FILE):
        return {}
    with open(USER_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_messages():
    if not os.path.exists(MAIL_FILE):
        return []
    with open(MAIL_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_messages(messages):
    with open(MAIL_FILE, 'w', encoding='utf-8') as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)


def unread_count(username):
    return sum(1 for m in load_messages() if m['to'] == username and not m['read'])


def send_message(sender, recipient, content):
    messages = load_messages()
    next_id = max((m['id'] for m in messages), default=0) + 1
    messages.append({
        'id': next_id,
        'from': sender,
        'to': recipient,
        'content': content,
        'date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'read': False,
    })
    save_messages(messages)


def mail_menu(username):
    width, height = get_screen_size()
    messages_per_page = max(1, height - 9)
    page = 0

    while True:
        try:
            clear_screen()
            draw_top_bar(SITE_NAME + " 쪽지함", f"{username}  {now_str()}", width)
            rawprint('\n')

            inbox = [m for m in load_messages() if m['to'] == username]
            inbox.sort(key=lambda m: m['id'], reverse=True)
            total_pages = max(1, (len(inbox) + messages_per_page - 1) // messages_per_page)
            start = page * messages_per_page
            end = start + messages_per_page
            current = inbox[start:end]

            box_top(width, f"받은 쪽지함 ({page + 1}/{total_pages})")
            if not inbox:
                box_line("(받은 쪽지가 없습니다)", width)
            else:
                for i, m in enumerate(current):
                    mark = (C_TITLE + "[새글]" + RESET) if not m['read'] else "      "
                    preview = (m['content'].splitlines() or [''])[0]
                    line = (f"{C_HILITE}{start + i + 1:>2}{RESET} {mark} "
                            f"{C_TEXT}{m['from']:<12}{RESET} {C_DIM}{m['date']}{RESET}  {preview}")
                    box_line(line, width)
            box_bottom(width)
            draw_footer("[번호:읽기] [W:쓰기] [F:다음] [B:이전] [P:뒤로]", width)

            cmd = command_input(C_TITLE + " > " + RESET).strip().lower()

            if cmd == 'p':
                break
            elif cmd == 'w':
                compose_mail(username)
            elif cmd in ('', 'f') and end < len(inbox):
                page += 1
            elif cmd == 'b' and page > 0:
                page -= 1
            else:
                try:
                    sel = int(cmd)
                    if 1 <= sel <= len(inbox):
                        view_mail(username, inbox[sel - 1])
                    else:
                        rawprint(C_ERR + "잘못된 번호입니다.\n" + RESET)
                        rawinput("계속하려면 Enter를 누르세요.\n")
                except ValueError:
                    rawprint(C_ERR + "잘못된 명령입니다.\n" + RESET)
                    rawinput("계속하려면 Enter를 누르세요.\n")
        except KeyboardInterrupt:
            break


def view_mail(username, msg):
    width, _ = get_screen_size()
    if not msg['read']:
        messages = load_messages()
        for m in messages:
            if m['id'] == msg['id']:
                m['read'] = True
        save_messages(messages)

    clear_screen()
    draw_top_bar(SITE_NAME + " 쪽지 읽기", now_str(), width)
    rawprint('\n')
    box_top(width, '쪽지 내용')
    box_line(f"보낸이 : {msg['from']}", width)
    box_line(f"받은날 : {msg['date']}", width)
    box_sep(width)
    for line in (msg['content'].splitlines() or ['']):
        box_line(line, width)
    box_bottom(width)
    draw_footer("[D:삭제] [P:뒤로]", width)

    cmd = command_input(C_TITLE + " > " + RESET).strip().lower()
    if cmd == 'd':
        messages = [m for m in load_messages() if m['id'] != msg['id']]
        save_messages(messages)
        rawprint(C_OK + "쪽지를 삭제했습니다.\n" + RESET)
        rawinput("계속하려면 Enter를 누르세요.\n")


def compose_mail(sender):
    width, _ = get_screen_size()
    clear_screen()
    draw_top_bar(SITE_NAME + " 쪽지 쓰기", now_str(), width)
    rawprint('\n')

    recipient = rawinput(C_TITLE + "받는 사람 ID : " + RESET).strip()
    if not recipient:
        return
    users = _load_users()
    if recipient not in users:
        rawprint(C_ERR + "존재하지 않는 아이디입니다.\n" + RESET)
        rawinput("계속하려면 Enter를 누르세요.\n")
        return
    if recipient == sender:
        rawprint(C_ERR + "자기 자신에게는 쪽지를 보낼 수 없습니다.\n" + RESET)
        rawinput("계속하려면 Enter를 누르세요.\n")
        return

    rawprint(C_DIM + "내용을 입력하세요. 한 줄에 '.' 만 입력하면 종료됩니다.\n" + RESET)
    content = multiline_input('')
    if not content.strip():
        rawprint(C_ERR + "내용이 비어 있어 취소되었습니다.\n" + RESET)
        rawinput("계속하려면 Enter를 누르세요.\n")
        return

    send_message(sender, recipient, content)
    rawprint(C_OK + "쪽지를 보냈습니다!\n" + RESET)
    rawinput("계속하려면 Enter를 누르세요.\n")
