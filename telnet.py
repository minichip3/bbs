import telnetlib3
import asyncio
import os
import pty
import subprocess

async def pty_proxy(reader, writer):
    # PTY(새 가상 터미널) 생성
    master_fd, slave_fd = pty.openpty()
    # bbs_main.py를 서브프로세스에서 실행(터미널에서처럼)
    proc = subprocess.Popen(
        ['python3', 'bbs_main.py'],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True
    )
    os.close(slave_fd)  # PTY의 slave 쪽은 자식에서만 사용하니 여기선 닫음

    # master_fd를 asyncio에서 non-blocking read/write로 이용
    loop = asyncio.get_event_loop()

    async def from_bbs_to_telnet():
        while True:
            try:
                data = await loop.run_in_executor(None, os.read, master_fd, 1024)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
            except Exception as e:
                break
        writer.close()

    async def from_telnet_to_bbs():
        while True:
            try:
                data = await reader.read(1024)
                if not data:
                    break
                await loop.run_in_executor(None, os.write, master_fd, data)
            except Exception as e:
                break

    # 위의 두 코루틴을 하나의 세션에서 동시에 동작시킴
    await asyncio.gather(
        from_bbs_to_telnet(),
        from_telnet_to_bbs()
    )

    # 세션 종료 후 clean up
    try:
        proc.terminate()
    except Exception:
        pass
    os.close(master_fd)

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    server = telnetlib3.create_server(
        port=2323,
        shell=pty_proxy,
        encoding=None   # <- 반드시 None으로 해야 바이너리로 주고받음
    )
    loop.run_until_complete(server)
    print('텔넷-BBS PTY 중계서버가 2323 포트에서 실행됩니다...')
    loop.run_forever()