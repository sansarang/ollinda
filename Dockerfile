FROM python:3.12-slim

# 숏 영상 자막조립용 ffmpeg + 한글 폰트 + 문서 PII OCR(tesseract). 한글 정확도용 tessdata_best kor/eng.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg fonts-nanum tesseract-ocr curl \
    && rm -rf /var/lib/apt/lists/*
# tessdata_best(고정확) kor+eng — Debian 기본(fast)은 한글 번호판 판독이 약해 문서 식별번호 누락.
RUN mkdir -p /usr/share/tesseract-best && \
    curl -sL -o /usr/share/tesseract-best/kor.traineddata https://github.com/tesseract-ocr/tessdata_best/raw/main/kor.traineddata && \
    curl -sL -o /usr/share/tesseract-best/eng.traineddata https://github.com/tesseract-ocr/tessdata_best/raw/main/eng.traineddata
ENV TESSDATA_PREFIX=/usr/share/tesseract-best

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 인쇄물 생성용 Chromium(Playwright) + 시스템 의존성. 실패해도 빌드 계속(런타임 graceful).
RUN python -m playwright install --with-deps chromium || echo "playwright chromium 설치 건너뜀(런타임 graceful)"

COPY app ./app

# 영속 데이터(가능하면 디스크 마운트). 기본은 컨테이너 내부.
ENV SHOPCAST_DB=/data/shopcast.sqlite \
    SHOPCAST_STORAGE=/data/storage \
    PORT=8000
RUN mkdir -p /data/storage

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
