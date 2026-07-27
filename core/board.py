import os
import json
from datetime import datetime
from bbsio.rawio import rawprint, rawinput, command_input, multiline_input
from bbsio.tui import (
    C_BORDER, C_TITLE, C_TEXT, C_DIM, C_OK, C_ERR, C_HILITE, RESET,
    clear_screen, hline, pad, draw_top_bar, draw_footer,
    box_top, box_bottom, box_line, box_sep, get_screen_size,
)
from wcwidth import wcswidth
from core import mail
from core import admin
from core import files as file_board
from core.profile import is_admin, edit_profile

POST_FILE = os.path.join('data', 'posts.json')
SITE_NAME = "M I N I - T E L"


def now_str():
    return datetime.now().strftime('%y/%m/%d %H:%M')


def format_board_entry(index, name, board_id, post_count, width):
    idx_str = f"{index:>2}."
    name_pad = 22
    id_pad = 10

    trimmed_name = ""
    for c in name:
        if wcswidth(trimmed_name + c) >= name_pad:
            break
        trimmed_name += c
    name_space = " " * (name_pad - wcswidth(trimmed_name))

    id_str = f"[{board_id:<{id_pad}}]"
    post_str = f"({post_count:>3}건)"

    return f"{C_HILITE}{idx_str}{RESET} {C_TEXT}{trimmed_name}{RESET}{name_space} {C_DIM}{id_str}{RESET} {post_str}"


def load_boards():
    with open('data/boards.json', 'r', encoding='utf-8') as f:
        return json.load(f)


def load_posts():
    if not os.path.exists(POST_FILE):
        return []
    with open(POST_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_posts(posts):
    with open(POST_FILE, 'w', encoding='utf-8') as f:
        json.dump(posts, f, ensure_ascii=False, indent=2)


def _can_edit(username, post):
    return username == post['author'] or is_admin(username)


def view_post(post, username):
    width, height = get_screen_size()
    page = 0
    while True:
        try:
            lines = post['content'].splitlines() or ['']
            lines_per_page = max(1, height - 9)
            total_pages = max(1, (len(lines) + lines_per_page - 1) // lines_per_page)
            page = min(page, total_pages - 1)

            clear_screen()
            draw_top_bar(SITE_NAME, now_str(), width)
            box_top(width, f"글 읽기 ({page + 1}/{total_pages})")
            box_line(f"제목 : {post['title']}", width)
            box_line(f"글쓴이: {post['author']}   작성일: {post['date']}", width)
            box_sep(width)

            start = page * lines_per_page
            end = start + lines_per_page
            for line in lines[start:end]:
                box_line(line, width)
            box_bottom(width)

            draw_footer("[ED:수정] [DD:삭제] [F:다음] [B:이전] [P:뒤로]", width)
            cmd = command_input(C_TITLE + " > " + RESET).strip().lower()

            if cmd == 'ed':
                if not _can_edit(username, post):
                    rawprint(C_ERR + "본인 글만 수정할 수 있습니다.\n" + RESET)
                    rawinput("계속하려면 Enter를 누르세요.\n")
                    continue
                new_title = rawinput(C_TITLE + f"새 제목 (현재: {post['title']}) : " + RESET).strip()
                rawprint(C_DIM + "새 내용을 입력하세요. 한 줄에 '.' 만 입력하면 종료됩니다.\n" + RESET)
                new_content = multiline_input('')
                if new_title:
                    post['title'] = new_title
                if new_content.strip():
                    post['content'] = new_content
                all_posts = load_posts()
                for i, p in enumerate(all_posts):
                    if p.get('id') == post.get('id'):
                        all_posts[i] = post
                        break
                save_posts(all_posts)
                rawprint(C_OK + "수정되었습니다.\n" + RESET)
                rawinput("계속하려면 Enter를 누르세요.\n")
            elif cmd == 'dd':
                if not _can_edit(username, post):
                    rawprint(C_ERR + "본인 글만 삭제할 수 있습니다.\n" + RESET)
                    rawinput("계속하려면 Enter를 누르세요.\n")
                    continue
                confirm = rawinput(C_ERR + "정말 삭제하시겠습니까? (Y/N): " + RESET).strip().upper()
                if confirm == 'Y':
                    all_posts = [p for p in load_posts() if p.get('id') != post.get('id')]
                    save_posts(all_posts)
                    rawprint(C_OK + "삭제되었습니다.\n" + RESET)
                    rawinput("계속하려면 Enter를 누르세요.\n")
                    return
                else:
                    rawprint("삭제가 취소되었습니다.\n")
                    rawinput("계속하려면 Enter를 누르세요.\n")
            elif cmd in ('', 'f') and end < len(lines):
                page += 1
            elif cmd == 'b' and page > 0:
                page -= 1
            elif cmd == 'p':
                break
            else:
                rawprint(C_ERR + "잘못된 명령입니다.\n" + RESET)
        except KeyboardInterrupt:
            break


def write_post(username, board_id):
    width, _ = get_screen_size()
    clear_screen()
    draw_top_bar(SITE_NAME + " 글쓰기", now_str(), width)
    rawprint('\n')
    title = rawinput(C_TITLE + "제목 : " + RESET)
    rawprint(C_DIM + "내용을 입력하세요. 한 줄에 '.' 만 입력하면 종료됩니다.\n" + RESET)
    content = multiline_input('')

    # 게시판별로 걸러낸 목록이 아니라 항상 전체 글 목록을 불러와서 그
    # 위에 이어붙여야 한다 - 필터링된 목록을 그대로 저장하면 다른
    # 게시판의 글이 전부 사라지는 버그가 있었다.
    all_posts = load_posts()
    next_id = max((p.get('id', 0) for p in all_posts), default=0) + 1
    post = {
        'id': next_id,
        'board': board_id,
        'author': username,
        'title': title,
        'content': content,
        'date': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    all_posts.append(post)
    save_posts(all_posts)
    rawprint(C_OK + "\n글이 등록되었습니다!\n" + RESET)
    rawinput("계속하려면 Enter를 누르세요.")


def main_menu(username):
    while True:
        clear_screen()
        boards = load_boards()
        width, _ = get_screen_size()

        draw_top_bar(SITE_NAME, f"{username}  {now_str()}", width)
        rawprint('\n')
        box_top(width, '메 인 메 뉴')

        all_posts = load_posts()
        for i, board in enumerate(boards):
            board_name = board["name"]
            board_id = board["id"]
            post_count = sum(1 for p in all_posts if p.get("board") == board_id)
            box_line(format_board_entry(i + 1, board_name, board_id, post_count, width), width)

        box_bottom(width)
        mail_hint = C_TITLE + "[M:쪽지" + RESET
        unread = mail.unread_count(username)
        if unread > 0:
            mail_hint += C_ERR + f"({unread})" + RESET
        mail_hint += C_TITLE + "]" + RESET
        hint = f"번호 선택 (게시판 ID 직접 입력도 가능)  {mail_hint}  [I:내정보]"
        user_is_admin = is_admin(username)
        if user_is_admin:
            hint += C_TITLE + "  [A:관리자]" + RESET
        hint += "  [X:종료]"
        draw_footer(hint, width)

        choice = command_input(C_TITLE + " > " + RESET).strip().lower()

        if choice in ('m', '쪽지', 'mail'):
            mail.mail_menu(username)
            continue
        if choice in ('i', '내정보', 'info'):
            edit_profile(username)
            continue
        if user_is_admin and choice in ('a', '관리', 'admin'):
            admin.admin_menu(username)
            continue

        board = None
        try:
            selection = int(choice)
            if 1 <= selection <= len(boards):
                board = boards[selection - 1]
        except ValueError:
            for b in boards:
                if b['id'].lower() == choice:
                    board = b
                    break

        if board is None:
            if choice:
                rawprint(C_ERR + "잘못된 선택입니다.\n" + RESET)
                rawinput("계속하려면 Enter를 누르세요.")
            continue

        board_id = board['id']
        board_type = board.get('type', 'normal')
        posts = [p for p in load_posts() if p.get('board') == board_id]

        if board_type in ('normal', 'restricted'):
            board_menu(username, board, posts)
        elif board_type == 'file':
            file_board.file_board_menu(username, board)
        elif board_type == 'internal':
            rawprint(f"\n[내부명령] '{board['name']}' 기능 실행 (예: 프로필 수정)\n")
            rawinput("계속하려면 Enter를 누르세요.\n")
        elif board_type == 'external':
            rawprint(f"\n[외부명령] '{board['name']}' 기능 실행 (예: 쉘 명령어 실행)\n")
            rawinput("계속하려면 Enter를 누르세요.\n")
        else:
            rawprint(f"\n알 수 없는 유형의 메뉴입니다: {board_type}\n")
            rawinput("계속하려면 Enter를 누르세요.\n")


def board_menu(username, board, posts):
    page = 0
    while True:
        try:
            width, height = get_screen_size()
            posts_per_page = max(1, height - 9)
            start = page * posts_per_page
            end = start + posts_per_page
            current_posts = posts[start:end]

            clear_screen()
            board_name = board['name']
            board_type = board.get('type', 'normal')
            total_pages = max(1, (len(posts) + posts_per_page - 1) // posts_per_page)

            draw_top_bar(SITE_NAME, f"{username}  {now_str()}", width)
            rawprint('\n')
            box_top(width, f"{board_name} ({page + 1}/{total_pages})")

            if not posts:
                box_line("(등록된 글이 없습니다)", width)
            else:
                for i, post in enumerate(current_posts):
                    line = f"{C_HILITE}{start + i + 1:>3}{RESET} {C_TEXT}{post['title']}{RESET} {C_DIM}/ {post['author']} / {post['date']}{RESET}"
                    box_line(line, width)

            box_bottom(width)
            hint = "[번호:읽기] [W:쓰기] [F:다음] [B:이전] [P:뒤로]"
            if board_type == 'restricted':
                hint += "  (관리자 전용 게시판)"
            draw_footer(hint, width)

            cmd = command_input(C_TITLE + " > " + RESET).strip().lower()

            if cmd == 'w':
                if board_type == 'restricted' and not is_admin(username):
                    rawprint(C_ERR + "이 게시판에서는 글쓰기가 제한되어 있습니다.\n" + RESET)
                    rawinput("계속하려면 Enter를 누르세요.\n")
                else:
                    write_post(username, board['id'])
                    posts = [p for p in load_posts() if p.get('board') == board['id']]
            elif cmd in ('', 'f') and (page + 1) * posts_per_page < len(posts):
                page += 1
                continue
            elif cmd == 'b' and page > 0:
                page -= 1
                continue
            elif cmd == 'p':
                break
            else:
                try:
                    sel = int(cmd)
                    if 1 <= sel <= len(posts):
                        view_post(posts[sel - 1], username)
                        posts = [p for p in load_posts() if p.get('board') == board['id']]
                    else:
                        rawprint(C_ERR + "잘못된 번호입니다.\n" + RESET)
                        rawinput("계속하려면 Enter를 누르세요.\n")
                except ValueError:
                    rawprint(C_ERR + "잘못된 명령입니다.\n" + RESET)
                    rawinput("계속하려면 Enter를 누르세요.\n")
        except KeyboardInterrupt:
            break
