"""하이텔풍 화면 렌더링 유틸리티 (색상/박스 문자/헤더-풋터 바)."""
from bbsio.rawio import rawprint
from wcwidth import wcswidth
import shutil

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

# 이 BBS 전체에서 쓰는 "톤" - 테두리는 청록, 제목/강조는 노랑, 본문은 흰색
C_BORDER = FG_CYAN
C_TITLE = BOLD + FG_YELLOW
C_TEXT = FG_WHITE
C_DIM = DIM + FG_CYAN
C_OK = BOLD + FG_GREEN
C_ERR = BOLD + FG_RED
C_HILITE = BOLD + FG_WHITE


def clear_screen():
    rawprint('\x1b[2J\x1b[H')


def color(text, code):
    return f"{code}{text}{RESET}"


def pad(text, width, align='left'):
    """wcswidth 기준으로 폭을 맞춰 채운다 (한글 2칸 폭 고려)."""
    w = wcswidth(text)
    if w < 0:
        w = len(text)
    if w >= width:
        # 넘치면 잘라낸다
        trimmed = ""
        for c in text:
            if wcswidth(trimmed + c) > width:
                break
            trimmed += c
        return trimmed
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
    """상단 상태 바 - 좌측 서비스명, 우측 시각/사용자 정보를 반전 색상으로."""
    if width is None:
        width, _ = get_screen_size()
    gap = max(1, width - wcswidth(site_title) - wcswidth(right_text) - 2)
    bar = f" {site_title}{' ' * gap}{right_text} "
    bar = pad(bar, width)
    rawprint(REVERSE + BG_CYAN + FG_BLACK + BOLD + bar + RESET + '\n')


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
