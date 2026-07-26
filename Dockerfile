FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir pyserial wcwidth

COPY dialup.py bbs_main.py telnet_server.py logutil.py start.sh ./
COPY bbsio/ ./bbsio/
COPY core/ ./core/
COPY data/ ./data/

RUN chmod +x start.sh

CMD ["./start.sh"]
