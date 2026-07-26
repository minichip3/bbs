import sys
import os
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

class ConnectionClosed(Exception):
    # 진짜 무입력 타임아웃이 아니라, 상대(모뎀/텔넷)가 정상적으로 연결을
    # 끊어서 입력 스트림이 EOF된 경우. 둘 다 예전엔 SessionIdleTimeout
    # 하나로 뭉뚱그려져서 정상 종료인데도 로그에 "타임아웃"으로 찍혔었다.
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
        # PTY가 raw 모드라 OPOST/ONLCR이 꺼져 있어 커널이 \n -> \r\n 변환을
        # 해주지 않는다. 여기서 명시적으로 정규화해야 클라이언트 터미널에서
        # 줄바꿈이 계단식으로 깨지지 않는다 (커서만 아래로 가고 컬럼 복귀 안 됨).
        normalized = text.replace('\r\n', '\n').replace('\n', '\r\n')
        encoded = normalized.encode(encoding, errors='replace')
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
    except Exception as e:
        sys.stdout.write(f"[출력 오류] {e}\n")

def _redraw_line(prompt, text, encoding, mask=False):
    """백스페이스 등으로 줄 내용이 바뀔 때마다, 지금까지 서버가 처리한
    한 칸씩 커서를 옮기는 방식이었는데, 화면(minicom 등)이 렌더링을
    놓치거나 타이밍이 어긋나면 서버가 아는 커서 위치와 실제 화면 위치가
    어긋나서 입력 줄 밖(박스 테두리)까지 지워버리는 문제가 있었다.
    매번 줄 전체를 지우고 서버가 갖고 있는 진짜 버퍼 내용으로 처음부터
    다시 그리면, 중간에 화면이 어떻게 어긋나 있었든 이 순간부터는 항상
    올바른 상태로 재동기화된다."""
    shown = ('*' * len(text)) if mask else text
    rawprint('\r\x1b[K' + prompt + shown, encoding)

def _read_byte_with_timeout(fd):
    ready, _, _ = select.select([fd], [], [], IDLE_TIMEOUT_SECONDS)
    if not ready:
        raise SessionIdleTimeout(f'{IDLE_TIMEOUT_SECONDS}초간 입력 없음')
    # sys.stdin.buffer(BufferedReader)로 읽으면 안 된다 - 1바이트만
    # 요청해도 내부적으로 커널에서 더 많은 바이트를 미리 당겨와 유저스페이스
    # 버퍼에 쌓아두는데, 위 select()는 커널 큐만 보기 때문에 이미 버퍼에
    # 들어와 있는(하지만 아직 안 돌려준) 바이트를 "데이터 없음"으로 착각해서
    # 영원히 대기하는 버그가 있었다 (두 번째 문자부터 응답이 없어지는 원인 -
    # 예: '1'을 보내면 '1'은 읽히는데 ''이 이미 BufferedReader
    # 내부 버퍼에 들어간 채로 select()가 다음 바이트를 영원히 기다림).
    # os.read()는 버퍼링 없이 커널에서 직접 읽으므로 select()와 짝이 맞는다.
    byte = os.read(fd, 1)
    if not byte:
        # PTY 반대편(모뎀/텔넷 중계)이 닫힘 - 회선이 정상적으로 끊긴 것
        raise ConnectionClosed('입력 스트림 종료(EOF)')
    return byte

def _expected_char_len(first_byte, encoding):
    """첫 바이트만 보고 이 문자가 총 몇 바이트인지 정확히 계산한다.
    예전엔 디코딩이 실패하면 "일단 최대 4바이트까지 계속 먹어보고 그래도
    안 되면 포기"하는 식이었는데, EUC-KR(원래 2바이트)에서 단 한 바이트만
    깨져도 다음 정상 글자의 바이트까지 같이 집어삼켜버리는 원인이었다
    (회선 노이즈로 바이트 하나가 깨지면 그 뒤에 오는 멀쩡한 한 글자 전체가
    함께 유실 - 타이핑 중 몇 글자씩 씹히고, 그만큼 백스페이스 커서 이동량도
    어긋나서 UI 테두리까지 지워지는 버그로 이어졌음). 인코딩별 실제 문자
    길이만큼만 정확히 읽으면 손상 범위가 그 문자 하나로 국한된다."""
    b = first_byte[0]
    if encoding == 'utf-8':
        if b < 0x80:
            return 1
        elif b & 0xE0 == 0xC0:
            return 2
        elif b & 0xF0 == 0xE0:
            return 3
        elif b & 0xF8 == 0xF0:
            return 4
        else:
            return 1  # 유효하지 않은 선행 바이트 - 한 바이트짜리 쓰레기로 취급
    else:  # euc-kr, johab 등 - 완성형/조합형 모두 최상위 비트가 서 있으면 2바이트
        return 2 if b >= 0x80 else 1

def getchar():
    # 예전엔 매 호출마다 tty.setraw(fd)를 다시 걸었는데, 이게 내부적으로
    # TCSAFLUSH를 써서 "아직 안 읽은 입력 버퍼"를 통째로 버려버리는 부작용이
    # 있었다 - 한 글자 처리하고 다음 글자 읽으려는 순간, 이미 도착해서
    # 커널 버퍼에 대기 중이던 다음 글자의 바이트가 그대로 삭제되는 버그였음
    # (한글 멀티바이트 입력 중간 글자가 씹히는 원인). raw 모드는 dialup.py가
    # PTY 만들 때 이미 한 번 걸어두므로 여기서 매번 다시 걸 필요가 없다.
    fd = sys.stdin.fileno()
    first = _read_byte_with_timeout(fd)
    length = _expected_char_len(first, current_encoding)
    raw = first
    while len(raw) < length:
        raw += _read_byte_with_timeout(fd)
    try:
        return raw.decode(current_encoding)
    except UnicodeDecodeError:
        # 정확히 기대한 바이트 수만큼만 읽었는데도 깨져 있으면 진짜 회선
        # 노이즈 - 이 문자 하나만 대체문자로 포기하고, 다음 바이트부터는
        # 건드리지 않는다 (뒤따라오는 글자를 더 이상 잡아먹지 않음).
        return '�'

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
                buffer.pop()
                _redraw_line(prompt, ''.join(buffer), encoding)
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
                _redraw_line(prompt, ''.join(buffer), encoding)
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
                buffer.pop()
                _redraw_line(prompt, ''.join(buffer), encoding, mask=True)
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
                _redraw_line(prompt, ''.join(buffer), encoding, mask=True)

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
                    buffer.pop()
                    _redraw_line(prompt, ''.join(buffer), encoding)
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
                    _redraw_line(prompt, ''.join(buffer), encoding)

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
                lines[current_line] = lines[current_line][:-1]
                _redraw_line('', lines[current_line], encoding)
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
                _redraw_line('', lines[current_line], encoding)
