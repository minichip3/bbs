FROM python:3.11-slim

WORKDIR /app

# lrzsz: ZMODEM 파일 전송을 자체 구현 대신 rz/sz 서브프로세스에 맡기기 위함
# (bbsio/xfer/zmodem_proc.py 참고 - 자체 구현체는 실제 클라이언트와의
# ZDATA CRC32 상호운용성 문제를 해결 못 해 lrzsz로 교체함).
RUN apt-get update && apt-get install -y --no-install-recommends lrzsz \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir pyserial wcwidth

COPY bbs.py start.sh ./
COPY server/ ./server/
COPY bbsio/ ./bbsio/
COPY core/ ./core/
COPY data/ ./data/

RUN chmod +x start.sh

CMD ["./start.sh"]
