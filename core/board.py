import os
import json
from datetime import datetime
from bbsio.rawio import rawprint, rawinput, command_input
from bbsio.tui import get_screen_size
from wcwidth import wcswidth

POST_FILE = os.path.join('data', 'posts.json')


def format_board_entry(index, name, board_id, post_count, total_width):
    idx_str = f"{index:>2}."
    name_pad = 20
    id_pad = 10
    post_pad = 6

    # 한글 너비 고려해서 자르기
    trimmed_name = ""
    for c in name:
        if wcswidth(trimmed_name + c) >= name_pad:
            break
        trimmed_name += c
    name_space = " " * (name_pad - wcswidth(trimmed_name))

    id_str = f"[{board_id:<{id_pad}}]"
    post_str = f"({post_count:>3})"

    return f"{idx_str} {trimmed_name}{name_space} {id_str} {post_str}"


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


def view_post(post):
    rawprint("\x1b[2J\x1b[H")  # Clear screen
    rawprint(f"제목: {post['title']}\n")
    rawprint(f"작성자: {post['author']}  작성일: {post['date']}\n")
    rawprint("-" * 40 + "\n")
    rawprint(post['content'] + "\n")
    rawprint("-" * 40 + "\n")
    rawinput("계속하려면 Enter를 누르세요.\n")


def show_board(posts):
    rawprint("\n게시판 목록:\n")
    if not posts:
        rawprint("(게시글이 없습니다)\n")
    for i, post in enumerate(posts):
        rawprint(f"{i+1}. {post['author']} - {post['title']} ({post['date']})\n")
    if posts:
        view_post(posts)
    else:
        rawinput("\n계속하려면 Enter를 누르세요.\n")


def write_post(username, posts, board_id):
    rawprint("제목: ")
    title = input()
    rawprint("내용: ")
    content = input()
    post = {
        'board': board_id,
        'author': username,
        'title': title,
        'content': content,
        'date': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    posts.append(post)
    save_posts(posts)
    rawprint("글이 등록되었습니다!\n")


def main_menu(username):
    from core.command import handle_global_command
    while True:
        rawprint("\x1b[2J\x1b[H")  # Clear screen
        boards = load_boards()
        width, _ = get_screen_size()
        rawprint("-" * width + "\n")

        header = "메인 메뉴"
        now = datetime.now().strftime('%y/%m/%d  %H:%M')

        gap = max(2, width - wcswidth(header) - wcswidth(now))
        rawprint(f"{header}{' ' * gap}{now}\n")
        rawprint("-" * width + "\n")

        # 게시판 목록 출력
        all_posts = load_posts()
        for i, board in enumerate(boards):
            board_name = board["name"]
            board_id = board["id"]
            post_count = sum(1 for p in all_posts if p.get("board") == board_id)
            line = format_board_entry(i+1, board_name, board_id, post_count, width)
            rawprint(line + "\n")
        
        rawprint("-" * width + "\n")

        choice = command_input("번호/명령 (X: 종료)\n > ").strip().lower()

        try:
            selection = int(choice)
            if 1 <= selection <= len(boards):
                board = boards[selection - 1]
                board_id = board['id']
                board_type = board.get('type', 'normal')
                posts = [p for p in load_posts() if p.get('board') == board_id]

                if board_type in ('normal', 'restricted'):
                    board_menu(username, board, posts)
                elif board_type == 'internal':
                    rawprint(f"\n[내부명령] '{board['name']}' 기능 실행 (예: 프로필 수정)\n")
                    rawinput("계속하려면 Enter를 누르세요.\n")
                elif board_type == 'external':
                    rawprint(f"\n[외부명령] '{board['name']}' 기능 실행 (예: 쉘 명령어 실행)\n")
                    rawinput("계속하려면 Enter를 누르세요.\n")
                else:
                    rawprint(f"\n알 수 없는 유형의 메뉴입니다: {board_type}\n")
                    rawinput("계속하려면 Enter를 누르세요.\n")
        except ValueError:
            rawprint("숫자를 입력하세요.\n")


def board_menu(username, board, posts):
    from core.command import handle_global_command
    while True:
        width, _ = get_screen_size()
        rawprint("\x1b[2J\x1b[H")  # Clear screen
        board_name = board['name']
        board_type = board.get('type', 'normal')
        board_id = board['id']
        header = f"{board_name}"
        now = datetime.now().strftime('%y/%m/%d  %H:%M')

        rawprint("-" * width + "\n")
        gap = max(2, width - wcswidth(header) - wcswidth(now))
        rawprint(f"{header}{' ' * gap}{now}\n")
        rawprint("-" * width + "\n")

        if not posts:
            rawprint("(게시글이 없습니다)\n")
        else:
            for i, post in enumerate(posts):
                title = post['title']
                author = post['author']
                date = post['date']
                line = f"{i+1:>2}. {title} / {author} / {date}"
                if wcswidth(line) > width:
                    line = line[:width - 3] + "..."
                rawprint(line + "\n")

        rawprint("-" * width + "\n")
        cmd = command_input("번호/명령 (W: 쓰기, ED: 수정, DD: 삭제, P: 뒤로)\n > ").strip().lower()

        if cmd == 'p':
            break
        elif cmd == 'w':
            if board_type == 'restricted' and username != 'sysop':
                rawprint("이 게시판에서는 글쓰기가 제한되어 있습니다.\n")
                rawinput("계속하려면 Enter를 누르세요.\n")
            else:
                write_post(username, posts, board_id)
        elif cmd == 'ed':
            rawprint("글 수정 기능은 아직 구현되지 않았습니다.\n")
            rawinput("계속하려면 Enter를 누르세요.\n")
        elif cmd == 'dd':
            rawprint("글 삭제 기능은 아직 구현되지 않았습니다.\n")
            rawinput("계속하려면 Enter를 누르세요.\n")
        else:
            try:
                sel = int(cmd)
                if 1 <= sel <= len(posts):
                    view_post(posts[sel - 1])
                else:
                    rawprint("잘못된 번호입니다.\n")
            except ValueError:
                rawprint("잘못된 명령입니다.\n")
