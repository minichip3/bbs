import os
import json

def initialize():
    os.makedirs("data", exist_ok=True)

    if not os.path.exists("data/users.json"):
        with open("data/users.json", "w", encoding="utf-8") as f:
            json.dump({}, f, ensure_ascii=False, indent=2)

    if not os.path.exists("data/posts.json"):
        with open("data/posts.json", "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)

    if not os.path.exists("data/login_banner.txt"):
        with open("data/login_banner.txt", "w", encoding="utf-8") as f:
            f.write("RETRO BBS\n\n")

    if not os.path.exists("data/boards.json"):
        with open("data/boards.json", "w", encoding="utf-8") as f:
            json.dump([
                {"id": "notice", "name": "공지사항", "type": "restricted"},
                {"id": "bbs", "name": "자유게시판", "type": "normal"}
            ], f, ensure_ascii=False, indent=2)

    os.makedirs("data/posts", exist_ok=True)
