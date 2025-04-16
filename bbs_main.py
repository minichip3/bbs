# bbs_main.py

from core.login import login_menu
from core.init import initialize


def main():
    initialize()
    print("*** 레트로 BBS에 오신 것을 환영합니다! ***\n")
    login_menu()


if __name__ == "__main__":
    main()
