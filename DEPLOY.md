# 배포 가이드

## 지금 떠 있는 것 (임시 공개 URL)
- `./deploy.sh` 로 로컬 서버(127.0.0.1:8020) 실행 + `cloudflared tunnel --url http://127.0.0.1:8020` 로 공개.
- **특징:** 즉시 https 공개 URL. 단 ① 맥이 켜져 있어야 하고 ② 재시작하면 URL이 바뀜(임시 데모/검증용).
- 운영자: `https://<터널>/admin` (Basic 인증, deploy.sh의 ADMIN_USER/PASS)
- 사장님: `https://<터널>/u/<토큰>` (인증 없음, 공개)

## 영구 배포 (실제 도메인) — Render (Docker)
ffmpeg가 필요해서 Docker 런타임을 씁니다.

1. 이 폴더를 **GitHub repo로 push** (git init → commit → push).
2. Render(https://render.com) → New + → **Blueprint** → repo 선택 → `render.yaml` 자동 인식.
3. 배포되면 `https://shopcast-xxxx.onrender.com` URL 발급.
4. 환경변수 설정(Render 대시보드):
   - `SHOPCAST_BASE` = 발급된 URL (예약/이미지/OAuth redirect 기준)
   - `SHOPCAST_ADMIN_PASS` = 운영자 비번 (자동생성 가능)
   - `ANTHROPIC_API_KEY` = 크레딧 있는 키 (실제 캡션/블로그)
   - (선택) `IG_APP_ID/SECRET`, `GOOGLE_CLIENT_ID/SECRET` = 계정연결용
5. **커스텀 도메인**: Render → Settings → Custom Domain → 보유 도메인 연결(CNAME) + 자동 HTTPS.

### 주의 (솔직하게)
- **무료 플랜**: 디스크 미지원 → SQLite/업로드가 **재배포 시 사라짐**. 실서비스는 `disk` 추가(starter+) 또는 **Postgres + S3/R2**로 교체 필요.
- **OAuth(인스타/유튜브)**: 안정적인 고정 도메인 + 각 플랫폼 콘솔에 `https://<도메인>/oauth/callback` 등록 + 앱 심사 후 작동. 터널(임시 URL)로는 OAuth 등록이 매번 바뀌어 부적합.
- 인스타 실발행은 **공개 image_url**이 필요 → 배포된 `/asset/...`(공개 https)를 사용하므로 영구 배포가 사실상 전제.

## 다른 호스팅
- **Railway / Fly.io**: 동일 Dockerfile 사용 가능.
- **Cloudtype(국내)**: Docker 또는 Python, 한국 리전.
