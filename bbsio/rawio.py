import sys
import tty
import termios
from wcwidth import wcwidth

def rawprint(text: str, encoding='utf-8'):
    try:
        encoded = text.encode(encoding, errors='replace')
        decoded = encoded.decode(encoding, errors='replace')
        sys.stdout.write(decoded)
        sys.stdout.flush()
    except Exception as e:
        sys.stdout.write(f"[출력 오류] {e}\n")

def getchar():
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    return ch

def rawinput(prompt='', encoding='utf-8') -> str:
    rawprint(prompt, encoding)
    buffer = []
    while True:
        ch = getchar()
        if ch in ('\n', '\r'):
            rawprint('\n', encoding)
            return ''.join(buffer)
        elif ch == '\x7f':  # Backspace
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

def hidden_input(prompt='비밀번호: ', encoding='utf-8') -> str:
    rawprint(prompt, encoding)
    buffer = []
    while True:
        ch = getchar()
        if ch in ('\n', '\r'):
            rawprint('\n', encoding)
            return ''.join(buffer)
        elif ch == '\x7f':  # Backspace
            if buffer:
                last = buffer.pop()
                width = wcwidth(last)
                if width > 0:
                    rawprint('\x1b[{}D'.format(1), encoding)
                    rawprint(' ', encoding)
                    rawprint('\x1b[{}D'.format(1), encoding)
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
