#!/bin/bash
# 모뎀 데몬 + 텔넷 서버를 같이 띄우고, 둘 다 감시한다.
# 둘 중 하나라도 죽으면 컨테이너 전체를 재시작하도록 exit해서
# Docker의 restart policy(unless-stopped)가 다시 올려주게 한다.
python3 -u server/dialup.py &
DIALUP_PID=$!
python3 -u server/telnet.py &
TELNET_PID=$!

wait -n $DIALUP_PID $TELNET_PID
echo "프로세스 중 하나가 종료됨 - 컨테이너 재시작을 위해 exit"
exit 1
