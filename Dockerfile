FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir pyserial wcwidth

COPY bbs_main.py start.sh ./
COPY server/ ./server/
COPY bbsio/ ./bbsio/
COPY core/ ./core/
COPY data/ ./data/

RUN chmod +x start.sh

CMD ["./start.sh"]
