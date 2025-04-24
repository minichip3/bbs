from bbsio.rawio import rawprint
from wcwidth import wcswidth
import shutil

SCREEN_WIDTH = 80
SCREEN_HEIGHT = 24

def get_screen_size():
    return SCREEN_WIDTH, SCREEN_HEIGHT
