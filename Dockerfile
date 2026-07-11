FROM python:3.12-slim

# 숏 영상 자막조립용 ffmpeg + 한글 폰트(NanumGothic)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg fonts-nanum \
    && rm -rf /var/lib/apt/lists/*

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
