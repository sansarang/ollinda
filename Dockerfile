FROM python:3.12-slim

# 숏 영상 자막조립용 ffmpeg + 한글 폰트 + 문서 PII OCR(tesseract kor+eng, configs 포함).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg fonts-nanum tesseract-ocr tesseract-ocr-kor tesseract-ocr-eng curl \
    && rm -rf /var/lib/apt/lists/*
# ★ 기본 tessdata 폴더(configs·tsv 포함)에 tessdata_best kor를 '덮어쓴다' — Debian 기본(fast)은 한글
#   번호판 판독이 약해 식별번호 누락. TESSDATA_PREFIX는 설정하지 않는다(그러면 configs를 못 찾아 tsv 실패).
RUN TDIR="$(dirname "$(find /usr/share -name eng.traineddata 2>/dev/null | head -1)")" && \
    curl -fsL -o "$TDIR/kor.traineddata" https://github.com/tesseract-ocr/tessdata_best/raw/main/kor.traineddata && \
    echo "tessdata_best kor → $TDIR"

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 인쇄물 생성용 Chromium(Playwright) + 시스템 의존성. 실패해도 빌드 계속(런타임 graceful).
RUN python -m playwright install --with-deps chromium || echo "playwright chromium 설치 건너뜀(런타임 graceful)"

COPY app ./app
COPY assets ./assets

# 영속 데이터(가능하면 디스크 마운트). 기본은 컨테이너 내부.
ENV SHOPCAST_DB=/data/shopcast.sqlite \
    SHOPCAST_STORAGE=/data/storage \
    PORT=8000
RUN mkdir -p /data/storage

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
