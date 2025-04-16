from bbsio.rawio import rawprint
from wcwidth import wcswidth
import shutil

size = shutil.get_terminal_size(fallback=(80, 24))
SCREEN_WIDTH = size.columns
SCREEN_HEIGHT = size.lines

def get_screen_size():
    return SCREEN_WIDTH, SCREEN_HEIGHT
