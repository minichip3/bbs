from core.login import login_menu
from core.init import initialize
from bbsio.rawio import set_encoding, rawprint, rawinput

def select_locale():
    print("사용할 문자 인코딩을 선택하세요:")
    rawprint("1. 이 글자가 보이면 UTF-8 입니다.\n", 'utf-8')
    rawprint("2. 이 글자가 보이면 완성형 입니다.\n", 'euc-kr')
    rawprint("3. 이 글자가 보이면 조합형 입니다.\n", 'johab')
    print()
    choice = rawinput("선택 (1~3): ", 'utf-8').strip()
    if choice == '1':
        return 'utf-8'
    elif choice == '2':
        return 'euc-kr'
    elif choice == '3':
        return 'johab'
    else:
        return 'utf-8'

def main():
    initialize()
    encoding = select_locale()
    set_encoding(encoding)
    print("*** 레트로 BBS에 오신 것을 환영합니다! ***\n")
    login_menu()


if __name__ == "__main__":
    main()
