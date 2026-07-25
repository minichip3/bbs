import sys
import select
import tty
import termios
from wcwidth import wcwidth, wcswidth

current_encoding = 'utf-8'

# 모뎀→PTY 중계가 죽거나 회선이 소리소문없이 끊겨도 getchar()가 여기서
# 영원히 블로킹해선 안 된다 (이게 "랜덤 프리징"의 핵심 원인이었음).
# 이 시간 동안 입력이 전혀 없으면 SessionIdleTimeout을 던진다.
IDLE_TIMEOUT_SECONDS = 180

class SessionIdleTimeout(Exception):
    pass

def set_encoding(enc):
    global current_encoding
    current_encoding = enc

def flush_input():
    """이미 도착해 입력 버퍼에 쌓여 있는 바이트를 전부 버린다.
    클라이언트 쪽 화면 렌더링이 느려서(예: minicom 한글 렌더 지연)
    사용자가 다음 프롬프트가 뜨기 전에 미리 타이핑한 키 입력이,
    화면에는 아직 안 보이던 이전 프롬프트/배너의 답으로 오인되는 것을 막는다.
    새 프롬프트 문구를 출력하기 '직전'에만 호출해야 한다 - 출력 후에
    호출하면 사용자가 프롬프트를 보고 바로 친, 정당한 빠른 입력까지
    같이 버려질 수 있다."""
    try:
        termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
    except termios.error:
        pass

def rawprint(text: str, encoding=None):
    if encoding is None:
        encoding = current_encoding
    try:
        encoded = text.encode(encoding, errors='replace')
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
    except Exception as e:
        sys.stdout.write(f"[출력 오류] {e}\n")

def _read_byte_with_timeout(fd):
    ready, _, _ = select.select([fd], [], [], IDLE_TIMEOUT_SECONDS)
    if not ready:
        raise SessionIdleTimeout(f'{IDLE_TIMEOUT_SECONDS}초간 입력 없음')
    byte = sys.stdin.buffer.read(1)
    if not byte:
        # PTY 반대편(모뎀 중계)이 닫힘 - 회선이 끊긴 것으로 간주
        raise SessionIdleTimeout('입력 스트림 종료(EOF)')
    return byte

def getchar():
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        byte = _read_byte_with_timeout(fd)
        while True:
            try:
                ch = byte.decode(current_encoding)
                return ch
            except UnicodeDecodeError:
                # 한글 EUC-KR처럼 멀티바이트 문자일 경우 계속 읽음
                byte += _read_byte_with_timeout(fd)

    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    return ch

def rawinput(prompt='', encoding=None) -> str:
    if encoding is None:
        encoding = current_encoding
    flush_input()
    rawprint(prompt, encoding)
    buffer = []
    while True:
        ch = getchar()
        if ch in ('\n', '\r'):
            rawprint('\n', encoding)
            return ''.join(buffer)
        elif ord(ch) in (8, 127):  # Backspace: ^H or DEL
            if buffer:
                last = buffer.pop()
                width = wcwidth(last)
                if width > 0:
                    rawprint('\x1b[{}D'.format(width), encoding)
                    rawprint(' ' * width, encoding)
                    rawprint('\x1b[{}D'.format(width), encoding)
        elif ch == '\x1b':  # Start of escape sequence
            seq = ch + getchar()
            if seq.endswith('['):
                while True:
                    c = getchar()
                    seq += c
                    if c.isalpha():
                        break
                continue  # Ignore full sequence
            elif seq in ('\x1bOP', '\x1bOQ', '\x1bOR', '\x1bOS'):  # F1–F4
                continue
            else:
                continue
        elif ch == '\t':
            continue  # Ignore tab key
        else:
            width = wcwidth(ch)
            if width > 0:
                buffer.append(ch)
                rawprint(ch, encoding)
            # else: ignore zero-width characters

def hidden_input(prompt='비밀번호: ', encoding=None) -> str:
    if encoding is None:
        encoding = current_encoding
    flush_input()
    rawprint(prompt, encoding)
    buffer = []
    while True:
        ch = getchar()
        if ch in ('\n', '\r'):
            rawprint('\n', encoding)
            return ''.join(buffer)
        elif ord(ch) in (8, 127):  # Backspace: ^H or DEL
            if buffer:
                last = buffer.pop()
                width = wcwidth(last)
                if width > 0:
                    rawprint('\x1b[{}D'.format(width), encoding)
                    rawprint(' ', encoding)
                    rawprint('\x1b[{}D'.format(width), encoding)
        elif ch == '\x1b':  # Escape sequence
            seq = ch + getchar()
            if seq.endswith('['):
                while True:
                    c = getchar()
                    seq += c
                    if c.isalpha():
                        break
                continue
            elif seq in ('\x1bOP', '\x1bOQ', '\x1bOR', '\x1bOS'):
                continue
            else:
                continue
        elif ch == '\t':
            continue
        else:
            width = wcwidth(ch)
            if width > 0:
                buffer.append(ch)
                rawprint('*', encoding)

def command_input(prompt=' > ', encoding=None) -> str:
    """
    명령어 입력 전용 함수.
    - prompt 출력 후 명령어를 한 줄로 입력받음.
    - 글로벌 명령어가 감지되면 handle_global_command() 호출.
    """
    if encoding is None:
        encoding = current_encoding

    from core.command import is_global_command, handle_global_command

    while True:
        flush_input()
        rawprint(prompt, encoding)
        buffer = []
        while True:
            ch = getchar()
            if ch in ('\n', '\r'):
                rawprint('\n', encoding)
                command = ''.join(buffer).strip()
                if is_global_command(command):
                    handled = handle_global_command(command)
                    if handled:
                        continue  # 다시 입력 받기
                return command
            elif ord(ch) in (8, 127):  # Backspace
                if buffer:
                    last = buffer.pop()
                    width = wcwidth(last)
                    if width > 0:
                        rawprint('\x1b[{}D'.format(width), encoding)
                        rawprint(' ' * width, encoding)
                        rawprint('\x1b[{}D'.format(width), encoding)
            elif ch == '\x1b':
                seq = ch + getchar()
                if seq.endswith('['):
                    while True:
                        c = getchar()
                        seq += c
                        if c.isalpha():
                            break
                    continue
                elif seq in ('\x1bOP', '\x1bOQ', '\x1bOR', '\x1bOS'):
                    continue
                else:
                    continue
            elif ch == '\t':
                continue
            else:
                width = wcwidth(ch)
                if width > 0:
                    buffer.append(ch)
                    rawprint(ch, encoding)

def multiline_input(prompt='내용 입력 (한 줄에 . 입력 시 종료)', encoding=None):
    if encoding is None:
        encoding = current_encoding

    flush_input()
    rawprint(prompt + '\n', encoding)
    lines = [""]
    current_line = 0

    while True:
        ch = getchar()
        if ch in ('\n', '\r'):
            if lines[current_line].strip() == ".":
                return "\n".join(lines[:-1])
            lines.append("")
            current_line += 1
            rawprint('\n', encoding)
        elif ord(ch) in (8, 127):  # Backspace
            if lines[current_line]:
                last = lines[current_line][-1]
                width = wcwidth(last)
                lines[current_line] = lines[current_line][:-1]
                if width > 0:
                    rawprint('\x1b[{}D'.format(width), encoding)
                    rawprint(' ' * width, encoding)
                    rawprint('\x1b[{}D'.format(width), encoding)
            else:
                if current_line > 0:
                    lines.pop()
                    current_line -= 1
                    rawprint('\x1b[F', encoding)  # Move cursor up one line
                    rawprint('\r' + ' ' * wcswidth(lines[current_line]) + '\r', encoding)  # Clear line
                    rawprint(lines[current_line], encoding)
        elif ch == '\x1b':  # Skip escape sequences
            seq = ch + getchar()
            if seq.endswith('['):
                while True:
                    c = getchar()
                    seq += c
                    if c.isalpha():
                        break
            continue
        elif ch == '\t':
            continue
        else:
            width = wcwidth(ch)
            if width > 0:
                lines[current_line] += ch
                rawprint(ch, encoding)
