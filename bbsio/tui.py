"""하이텔풍 화면 렌더링 유틸리티 (색상/박스 문자/헤더-풋터 바)."""
import re
from bbsio.rawio import rawprint
from wcwidth import wcswidth
import shutil

# board.py의 format_board_entry()처럼 색상 코드(ESC 시퀀스)를 이미 섞어
# 만든 문자열을 pad()/box_line()에 그대로 넘기는 경우가 있는데, 그러면
# ESC 시퀀스 바이트까지 "보이는 글자"로 세어져서 패딩 계산이 틀어지고
# 박스 테두리가 줄마다 다르게 어긋나는 문제가 있었다. 폭을 잴 때는 항상
# ESC 시퀀스를 먼저 걷어내고 계산한다.
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')


def _visible_width(text):
    w = wcswidth(_ANSI_RE.sub('', text))
    if w < 0:
        w = len(_ANSI_RE.sub('', text))
    return w

SCREEN_WIDTH = 80
SCREEN_HEIGHT = 24


def get_screen_size():
    return SCREEN_WIDTH, SCREEN_HEIGHT


# --- ANSI 색상 -----------------------------------------------------------
# 하이텔/케텔 시절 실제 화면은 흑백(그린 모노크롬) 단말기부터 컬러 ANSI
# 단말기까지 다양했지만, 후기(2000년대) 하이텔 화면은 청록/노랑/흰색을
# 주로 쓴 컬러 ANSI 메뉴였다. 그 느낌을 재현한다.
RESET = '\x1b[0m'
BOLD = '\x1b[1m'
DIM = '\x1b[2m'
REVERSE = '\x1b[7m'

FG_BLACK = '\x1b[30m'
FG_RED = '\x1b[31m'
FG_GREEN = '\x1b[32m'
FG_YELLOW = '\x1b[33m'
FG_BLUE = '\x1b[34m'
FG_MAGENTA = '\x1b[35m'
FG_CYAN = '\x1b[36m'
FG_WHITE = '\x1b[37m'

BG_BLACK = '\x1b[40m'
BG_BLUE = '\x1b[44m'
BG_CYAN = '\x1b[46m'

# 이 BBS 전체에서 쓰는 "톤" - 거의 모노크롬(흰색), 노랑만 강조색으로 씀
C_BORDER = FG_WHITE
C_TITLE = BOLD + FG_YELLOW
C_TEXT = FG_WHITE
C_DIM = FG_WHITE
C_OK = BOLD + FG_GREEN
C_ERR = BOLD + FG_RED
C_HILITE = BOLD + FG_WHITE


def clear_screen():
    rawprint('\x1b[2J\x1b[H')


def color(text, code):
    return f"{code}{text}{RESET}"


def pad(text, width, align='left'):
    """보이는 폭(wcswidth, 한글 2칸 + ESC 시퀀스 제외) 기준으로 폭을 맞춰 채운다."""
    w = _visible_width(text)
    if w >= width:
        # 넘치면 잘라낸다 - ESC 시퀀스는 그대로 통과시키고, 보이는 문자만
        # 세면서 자른다.
        result = ""
        visible = 0
        i = 0
        while i < len(text):
            m = _ANSI_RE.match(text, i)
            if m:
                result += m.group(0)
                i = m.end()
                continue
            c = text[i]
            cw = wcswidth(c)
            if cw < 0:
                cw = 1
            if visible + cw > width:
                break
            result += c
            visible += cw
            i += 1
        return result
    fill = width - w
    if align == 'right':
        return ' ' * fill + text
    elif align == 'center':
        left = fill // 2
        right = fill - left
        return ' ' * left + text + ' ' * right
    return text + ' ' * fill


def hline(width, ch='-'):
    return C_BORDER + (ch * width) + RESET


def draw_top_bar(site_title, right_text, width=None):
    """상단 상태 바 - 배경색 없이(터미널 기본 검은 배경 유지), 좌측 서비스명은
    굵은 노랑, 우측 시각/사용자 정보는 청록으로 구분하고 밑줄을 긋는다."""
    if width is None:
        width, _ = get_screen_size()
    gap = max(1, width - wcswidth(site_title) - wcswidth(right_text) - 1)
    rawprint(C_TITLE + site_title + RESET + (' ' * gap) + C_DIM + right_text + RESET + '\n')
    rawprint(hline(width) + '\n')


def draw_header(title, right_text='', width=None):
    """박스 상단 - 제목 + (선택)우측 텍스트, 위아래 가로선 포함."""
    if width is None:
        width, _ = get_screen_size()
    rawprint(hline(width) + '\n')
    if right_text:
        gap = max(2, width - wcswidth(title) - wcswidth(right_text))
        rawprint(C_TITLE + title + RESET + (' ' * gap) + C_DIM + right_text + RESET + '\n')
    else:
        rawprint(C_TITLE + title + RESET + '\n')
    rawprint(hline(width) + '\n')


def draw_footer(text, width=None):
    if width is None:
        width, _ = get_screen_size()
    rawprint(hline(width) + '\n')
    rawprint(C_DIM + text + RESET + '\n')


def box_top(width, title=''):
    if title:
        t = f" {title} "
        remain = width - 2 - wcswidth(t)
        left = remain // 2
        right = remain - left
        rawprint(C_BORDER + '+' + ('-' * left) + RESET + C_TITLE + t + RESET +
                  C_BORDER + ('-' * right) + '+' + RESET + '\n')
    else:
        rawprint(C_BORDER + '+' + ('-' * (width - 2)) + '+' + RESET + '\n')


def box_bottom(width):
    rawprint(C_BORDER + '+' + ('-' * (width - 2)) + '+' + RESET + '\n')


def box_line(text, width, align='left'):
    inner = width - 4
    content = pad(text, inner, align)
    rawprint(C_BORDER + '| ' + RESET + C_TEXT + content + RESET + C_BORDER + ' |' + RESET + '\n')


def box_sep(width):
    rawprint(C_BORDER + '+' + ('-' * (width - 2)) + '+' + RESET + '\n')
