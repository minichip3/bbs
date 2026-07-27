import os
import sys
import json
from datetime import datetime

from bbsio.rawio import rawprint, rawinput, command_input
from bbsio.tui import (
    C_TITLE, C_TEXT, C_DIM, C_OK, C_ERR, C_HILITE, RESET,
    clear_screen, draw_top_bar, draw_footer,
    box_top, box_bottom, box_line, box_sep, get_screen_size,
)
from bbsio.xfer.xmodem import XModemSender, XModemReceiver, XModemError
from bbsio.xfer.zmodem import ZModemSender, ZModemReceiver, ZModemError
from core.profile import is_admin

INDEX_FILE = os.path.join('data', 'file_index.json')
FILES_DIR = os.path.join('data', 'files')
SITE_NAME = "M I N I - T E L"

# 업로드 한 번에 허용하는 최대 크기. 자료실이 무제한으로 디스크를 채우는
# 사고(또는 악의적인 반복 업로드)를 막기 위한 하드 캡 - 10MB면 이 BBS
# 성격(텍스트/작은 프로그램 자료실)에 충분히 넉넉하다.
MAX_UPLOAD_SIZE = 10 * 1024 * 1024


def now_str():
    return datetime.now().strftime('%y/%m/%d %H:%M')


def _ensure_dirs():
    os.makedirs(FILES_DIR, exist_ok=True)
    if not os.path.exists(INDEX_FILE):
        with open(INDEX_FILE, 'w', encoding='utf-8') as f:
            json.dump([], f, ensure_ascii=False, indent=2)


def load_index():
    _ensure_dirs()
    with open(INDEX_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_index(entries):
    _ensure_dirs()
    with open(INDEX_FILE, 'w', encoding='utf-8') as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def _can_manage(username, entry):
    return username == entry['uploader'] or is_admin(username)


def _safe_display_name(raw_name):
    """사용자가 입력한 파일명에서 경로 구분자 등을 제거해 data/files/ 밖으로
    벗어나는 경로(../ 등)를 절대 만들 수 없게 한다."""
    name = os.path.basename(raw_name.strip().replace('\x00', ''))
    if not name or name in ('.', '..'):
        return None
    if len(name) > 100:
        name = name[:100]
    return name


def _format_size(num_bytes):
    if num_bytes < 1024:
        return f"{num_bytes}B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f}K"
    return f"{num_bytes / (1024 * 1024):.1f}M"


def _log(msg):
    # rawprint는 세션 stdout(=전송 채널)로 나가므로 XModem 진행 중에는 절대
    # 쓰면 안 된다(프로토콜 바이트 스트림에 텍스트가 섞여 전송이 깨짐).
    # 서버 로그(stderr)로만 남긴다 - telnet.py/dialup.py가 별도로 캡처한다.
    print(f'[files] {msg}', file=sys.stderr)


def _choose_transfer_protocol(action_label):
    """업로드/다운로드 공통 프로토콜 서브메뉴. 'x'/'z'/None(취소)을 반환."""
    rawprint('\n')
    rawprint(C_TITLE + f"{action_label} 방식을 선택하세요.\n" + RESET)
    rawprint(C_TEXT + "[1] XModem   [2] ZModem\n" + RESET)
    sel = rawinput(C_TITLE + " > " + RESET).strip()
    if sel == '1':
        return 'x'
    elif sel == '2':
        return 'z'
    rawprint(C_ERR + "잘못된 선택입니다.\n" + RESET)
    rawinput("계속하려면 Enter를 누르세요.\n")
    return None


def file_board_menu(username, board):
    page = 0
    while True:
        try:
            width, height = get_screen_size()
            entries = load_index()
            per_page = max(1, height - 9)
            start = page * per_page
            end = start + per_page
            current = entries[start:end]
            total_pages = max(1, (len(entries) + per_page - 1) // per_page)

            clear_screen()
            draw_top_bar(SITE_NAME, f"{username}  {now_str()}", width)
            rawprint('\n')
            box_top(width, f"{board['name']} ({page + 1}/{total_pages})")

            if not entries:
                box_line("(등록된 자료가 없습니다)", width)
            else:
                for i, entry in enumerate(current):
                    line = (f"{C_HILITE}{start + i + 1:>3}{RESET} "
                            f"{C_TEXT}{entry['filename']:<28}{RESET} "
                            f"{C_DIM}{_format_size(entry['size']):>7}  "
                            f"{entry['uploader']:<12} {entry['date']}{RESET}")
                    box_line(line, width)
            box_bottom(width)
            draw_footer("[번호:정보] [U:업로드] [G:다운로드] [F:다음] [B:이전] [P:뒤로]", width)

            cmd = command_input(C_TITLE + " > " + RESET).strip().lower()

            if cmd == 'p':
                break
            elif cmd == 'u':
                protocol = _choose_transfer_protocol('업로드')
                if protocol == 'x':
                    upload_file(username)
                elif protocol == 'z':
                    zmodem_upload(username)
            elif cmd == 'g':
                sel_str = rawinput(C_TITLE + "받을 자료 번호: " + RESET).strip()
                try:
                    sel = int(sel_str)
                    if 1 <= sel <= len(entries):
                        protocol = _choose_transfer_protocol('다운로드')
                        if protocol == 'x':
                            download_file(entries[sel - 1])
                        elif protocol == 'z':
                            zmodem_download(entries[sel - 1])
                    else:
                        rawprint(C_ERR + "잘못된 번호입니다.\n" + RESET)
                        rawinput("계속하려면 Enter를 누르세요.\n")
                except ValueError:
                    rawprint(C_ERR + "잘못된 번호입니다.\n" + RESET)
                    rawinput("계속하려면 Enter를 누르세요.\n")
            elif cmd in ('', 'f') and end < len(entries):
                page += 1
            elif cmd == 'b' and page > 0:
                page -= 1
            else:
                try:
                    sel = int(cmd)
                    if 1 <= sel <= len(entries):
                        file_info_menu(username, entries[sel - 1])
                    else:
                        rawprint(C_ERR + "잘못된 번호입니다.\n" + RESET)
                        rawinput("계속하려면 Enter를 누르세요.\n")
                except ValueError:
                    rawprint(C_ERR + "잘못된 명령입니다.\n" + RESET)
                    rawinput("계속하려면 Enter를 누르세요.\n")
        except KeyboardInterrupt:
            break


def file_info_menu(username, entry):
    width, _ = get_screen_size()
    while True:
        try:
            clear_screen()
            draw_top_bar(SITE_NAME + " 자료 정보", now_str(), width)
            rawprint('\n')
            box_top(width, '자료 정보')
            box_line(f"파일명 : {entry['filename']}", width)
            box_line(f"크기   : {_format_size(entry['size'])} ({entry['size']}바이트)", width)
            box_line(f"올린이 : {entry['uploader']}   등록일: {entry['date']}", width)
            box_sep(width)
            desc = entry.get('description') or '(설명 없음)'
            for line in desc.splitlines() or ['']:
                box_line(line, width)
            box_bottom(width)

            hint = "[G:다운로드]"
            if _can_manage(username, entry):
                hint += " [DD:삭제]"
            hint += " [P:뒤로]"
            draw_footer(hint, width)

            cmd = command_input(C_TITLE + " > " + RESET).strip().lower()

            if cmd == 'p':
                break
            elif cmd == 'g':
                protocol = _choose_transfer_protocol('다운로드')
                if protocol == 'x':
                    download_file(entry)
                elif protocol == 'z':
                    zmodem_download(entry)
            elif cmd == 'dd':
                if not _can_manage(username, entry):
                    rawprint(C_ERR + "본인이 올린 자료만 삭제할 수 있습니다.\n" + RESET)
                    rawinput("계속하려면 Enter를 누르세요.\n")
                    continue
                confirm = rawinput(C_ERR + "정말 삭제하시겠습니까? (Y/N): " + RESET).strip().upper()
                if confirm == 'Y':
                    entries = [e for e in load_index() if e['id'] != entry['id']]
                    save_index(entries)
                    try:
                        os.remove(os.path.join(FILES_DIR, entry['stored_name']))
                    except OSError:
                        pass
                    rawprint(C_OK + "삭제되었습니다.\n" + RESET)
                    rawinput("계속하려면 Enter를 누르세요.\n")
                    return
                else:
                    rawprint("삭제가 취소되었습니다.\n")
                    rawinput("계속하려면 Enter를 누르세요.\n")
            else:
                rawprint(C_ERR + "잘못된 명령입니다.\n" + RESET)
                rawinput("계속하려면 Enter를 누르세요.\n")
        except KeyboardInterrupt:
            break


def upload_file(username):
    rawprint('\n')
    display_name = rawinput(C_TITLE + "파일명 : " + RESET).strip()
    safe_name = _safe_display_name(display_name)
    if not safe_name:
        rawprint(C_ERR + "올바른 파일명을 입력하세요.\n" + RESET)
        rawinput("계속하려면 Enter를 누르세요.\n")
        return

    rawprint(C_DIM + "설명을 입력하세요 (한 줄, 생략 가능): " + RESET)
    description = rawinput('').strip()

    rawprint('\n' + C_OK +
              "지금 터미널 프로그램에서 XMODEM(CRC) '보내기'를 시작하세요.\n" +
              RESET + C_DIM +
              "(업로드가 시작될 때까지 잠시 기다립니다. 준비되면 Enter를 누르세요.)\n" +
              RESET)
    rawinput('')

    receiver = XModemReceiver(max_size=MAX_UPLOAD_SIZE)
    try:
        _log(f'{username} 업로드 시작: {safe_name}')
        data = receiver.receive()
    except XModemError as e:
        _log(f'{username} 업로드 실패: {e}')
        rawprint(C_ERR + f"\n업로드가 실패했습니다: {e}\n" + RESET)
        rawinput("계속하려면 Enter를 누르세요.\n")
        return

    entries = load_index()
    next_id = max((e.get('id', 0) for e in entries), default=0) + 1
    stored_name = f"{next_id}_{safe_name}"

    _ensure_dirs()
    with open(os.path.join(FILES_DIR, stored_name), 'wb') as f:
        f.write(data)

    entries.append({
        'id': next_id,
        'filename': safe_name,
        'stored_name': stored_name,
        'description': description,
        'uploader': username,
        'size': len(data),
        'date': now_str(),
    })
    save_index(entries)

    _log(f'{username} 업로드 완료: {safe_name} ({len(data)}바이트)')
    rawprint(C_OK + f"\n업로드가 완료되었습니다! ({len(data)}바이트)\n" + RESET)
    rawinput("계속하려면 Enter를 누르세요.\n")


def zmodem_upload(username):
    """XModem과 달리 파일명/크기를 사용자가 미리 입력할 필요 없이 ZMODEM(Lite)
    핸드셰이크가 전송 중 자동으로 알려준다."""
    rawprint('\n')
    rawprint(C_DIM + "설명을 입력하세요 (한 줄, 생략 가능): " + RESET)
    description = rawinput('').strip()

    rawprint('\n' + C_OK +
              "지금 터미널 프로그램에서 ZMODEM '보내기'를 시작하세요.\n" +
              RESET + C_DIM +
              "(파일명과 크기는 자동으로 전달됩니다. 준비되면 Enter를 누르세요.)\n" +
              RESET)
    rawinput('')

    receiver = ZModemReceiver(max_size=MAX_UPLOAD_SIZE)
    try:
        _log(f'{username} ZMODEM 업로드 시작')
        raw_filename, data = receiver.receive()
    except ZModemError as e:
        _log(f'{username} ZMODEM 업로드 실패: {e}')
        rawprint(C_ERR + f"\n업로드가 실패했습니다: {e}\n" + RESET)
        rawinput("계속하려면 Enter를 누르세요.\n")
        return

    safe_name = _safe_display_name(raw_filename)
    if not safe_name:
        _log(f'{username} ZMODEM 업로드 실패: 올바르지 않은 파일명({raw_filename!r})')
        rawprint(C_ERR + "\n전송된 파일명이 올바르지 않습니다.\n" + RESET)
        rawinput("계속하려면 Enter를 누르세요.\n")
        return

    entries = load_index()
    next_id = max((e.get('id', 0) for e in entries), default=0) + 1
    stored_name = f"{next_id}_{safe_name}"

    _ensure_dirs()
    with open(os.path.join(FILES_DIR, stored_name), 'wb') as f:
        f.write(data)

    entries.append({
        'id': next_id,
        'filename': safe_name,
        'stored_name': stored_name,
        'description': description,
        'uploader': username,
        'size': len(data),
        'date': now_str(),
    })
    save_index(entries)

    _log(f'{username} ZMODEM 업로드 완료: {safe_name} ({len(data)}바이트)')
    rawprint(C_OK + f"\n업로드가 완료되었습니다! ({safe_name}, {len(data)}바이트)\n" + RESET)
    rawinput("계속하려면 Enter를 누르세요.\n")


def download_file(entry):
    path = os.path.join(FILES_DIR, entry['stored_name'])
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except OSError as e:
        rawprint(C_ERR + f"\n파일을 읽을 수 없습니다: {e}\n" + RESET)
        rawinput("계속하려면 Enter를 누르세요.\n")
        return

    rawprint('\n' + C_OK +
              "지금 터미널 프로그램에서 XMODEM(CRC) '받기'를 시작하세요.\n" +
              RESET + C_DIM +
              "(수신이 시작될 때까지 잠시 기다립니다. 준비되면 Enter를 누르세요.)\n" +
              RESET)
    rawinput('')

    sender = XModemSender()
    block_size = 1024 if len(data) > 128 else 128
    try:
        _log(f'다운로드 시작: {entry["filename"]}')
        sender.send(data, block_size=block_size)
    except XModemError as e:
        _log(f'다운로드 실패: {e}')
        rawprint(C_ERR + f"\n다운로드가 실패했습니다: {e}\n" + RESET)
        rawinput("계속하려면 Enter를 누르세요.\n")
        return

    _log(f'다운로드 완료: {entry["filename"]} ({len(data)}바이트)')
    rawprint(C_OK + f"\n다운로드가 완료되었습니다! ({len(data)}바이트)\n" + RESET)
    rawinput("계속하려면 Enter를 누르세요.\n")


def zmodem_download(entry):
    """XModem과 달리 파일명/크기가 핸드셰이크 중 자동으로 전달되므로
    수신측에서 따로 파일명을 지정할 필요가 없다."""
    path = os.path.join(FILES_DIR, entry['stored_name'])
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except OSError as e:
        rawprint(C_ERR + f"\n파일을 읽을 수 없습니다: {e}\n" + RESET)
        rawinput("계속하려면 Enter를 누르세요.\n")
        return

    rawprint('\n' + C_OK +
              "지금 터미널 프로그램에서 ZMODEM '받기'를 시작하세요.\n" +
              RESET + C_DIM +
              "(파일명과 크기가 자동으로 전달됩니다. 준비되면 Enter를 누르세요.)\n" +
              RESET)
    rawinput('')

    sender = ZModemSender()
    try:
        _log(f'ZMODEM 다운로드 시작: {entry["filename"]}')
        sender.send(entry['filename'], data)
    except ZModemError as e:
        _log(f'ZMODEM 다운로드 실패: {e}')
        rawprint(C_ERR + f"\n다운로드가 실패했습니다: {e}\n" + RESET)
        rawinput("계속하려면 Enter를 누르세요.\n")
        return

    _log(f'ZMODEM 다운로드 완료: {entry["filename"]} ({len(data)}바이트)')
    rawprint(C_OK + f"\n다운로드가 완료되었습니다! ({len(data)}바이트)\n" + RESET)
    rawinput("계속하려면 Enter를 누르세요.\n")
