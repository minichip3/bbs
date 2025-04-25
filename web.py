import os, pty, select
import subprocess
from flask import Flask, render_template
from flask_socketio import SocketIO, emit

app = Flask(__name__)
socketio = SocketIO(app)

# 터미널 세션 관리 (여기선 1:1만)
sessions = {}

@socketio.on('connect')
def start_pty():
    print('웹 클라이언트 접속')
    master_fd, slave_fd = pty.openpty()
    # bbs_main.py 대신 /bin/bash 돌려도 됨 (테스트는 bash로…)
    proc = subprocess.Popen(
        ['python3', 'bbs_main.py'],
        preexec_fn=os.setsid,
        stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
        close_fds=True
    )
    os.close(slave_fd)
    sid = str(id(master_fd))
    sessions[sid] = (master_fd, proc)
    emit('session', {'sid': sid})

    def pty_to_web():
        while True:
            rl, _, _ = select.select([master_fd], [], [], 0.1)
            if rl:
                output = os.read(master_fd, 1024)
                socketio.emit('output', {'data': output.decode(errors='replace')})
            if proc.poll() is not None:
                break

    import threading
    threading.Thread(target=pty_to_web, daemon=True).start()

@socketio.on('input')
def send_input(message):
    sid = list(sessions.keys())[0]  # 데모: 1명만 관리
    master_fd, proc = sessions[sid]
    data = message['data']
    os.write(master_fd, data.encode())

@socketio.on('disconnect')
def cleanup():
    for sid, (master_fd, proc) in list(sessions.items()):
        try:
            os.close(master_fd)
            proc.terminate()
        except Exception:
            pass
        sessions.pop(sid, None)
    print('웹 클라이언트 종료')

@app.route("/")
def index():
    return '''<!DOCTYPE html>
<html>
<head>
    <script src="https://unpkg.com/xterm/lib/xterm.js"></script>
    <link rel="stylesheet" href="https://unpkg.com/xterm/css/xterm.css" />
    <style>
        #container {
            display: flex;
            justify-content: center; /* 가로 중앙 정렬 */
            align-items: center; /* 세로 중앙 정렬 */
            height: 100vh; /* 화면 전체 높이 사용 */
            background-color: #000080; /* 파란색 배경 */
        }
    </style>
</head>
<body>
    <div id="container">
        <div id="terminal" style="width:800px;height:400px;"></div>
    </div>
    <script src="//cdn.socket.io/4.0.0/socket.io.min.js"></script>
    <script>
        var term = new Terminal({
            cols: 80,
            rows: 24,
            cursorBlink: true,
            theme: {
                background: '#000080', /* 파란색 */
                foreground: '#ffffff', /* 흰색 */
                cursor: '#ffffff', /* 커서 흰색 */
                cursorAccent: '#000080'
            }
        });
        term.open(document.getElementById('terminal'));
        var socket = io();
        socket.on('output', function(msg){
            term.write(msg.data);
        });
        term.onData(function(input){
            socket.emit('input', {data: input});
        });
    </script>
</body>
</html>
'''

if __name__ == "__main__":
    socketio.run(app, host='0.0.0.0', port=8080)