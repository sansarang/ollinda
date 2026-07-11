# Shopcast (올린다) — 확장성 로드맵 (PHASE 9)

> 큰 변경이라 이번 수정에는 **포함하지 않은** 항목들. 각 항목에 구현 노트와 우선순위를 남긴다.
> 근거: `REPORT.md` §3(확장성 병목), ARCH/BUG 분석.

---

## A. 생성 워커 큐 + 재시작 복구 (최우선)

**문제**: 생성 작업이 `threading.Thread(daemon=True)` fire-and-forget (`ingest.py:142`, `main.py:2859/3444`, `teaser.py:67`). 재배포·크래시 시 진행 중 작업이 흔적 없이 증발, 재시도 없음, 동시 렌더 수 무제한(ffmpeg 폭주).

**구현 노트**:
1. `jobs` 테이블 추가: `id, tenant_id, asset_id, kind, status(pending/running/done/failed), attempts, created_at, updated_at, error`.
2. 업로드 시 job 행 INSERT(pending) → 워커가 폴링/처리 → 상태 갱신. `/me/sets/count` 폴링을 job 상태 기반으로 전환하면 "만드는 중"이 조용히 사라지는 문제 해결.
3. 동시성 상한: `threading.Semaphore(2~3)`로 ffmpeg 동시 실행 제한. 최소 구현은 in-process 워커 스레드 1~2개 + DB 큐.
4. 재시작 시 `status='running'`인 고아 job을 pending으로 되돌려 재개.
- **난이도 중 / 효과 최상**. 별도 프로세스(Celery/RQ) 없이 SQLite 큐 + in-process 워커로 시작 가능.

## B. `/api/demo` 이벤트루프 차단 제거

**문제**: `POST /api/demo`(`main.py:194`)가 **async 핸들러 안에서 `teaser.run_teaser`를 동기 실행**(`main.py:229`) → Claude 3~4콜+비전 도는 동안 uvicorn 이벤트루프 전체 정지 → 데모 1건이 사이트 전원 무응답.

**구현 노트**: 핸들러를 `def`(동기)로 바꿔 스타레트 스레드풀로 넘기거나, `await asyncio.to_thread(teaser.run_teaser, ...)` / `run_in_executor`로 오프로드. 로그인 데모(A항 job 큐)와 통합하면 더 깔끔.
- **난이도 하 / 효과 상**.

## C. SQLite → Postgres

**문제**: 단일 라이터. WAL+busy_timeout(B8에서 적용)로 완화했으나 근본은 아님. 다중 백그라운드 스레드 동시 쓰기 + 수평 확장 불가.

**구현 노트**: `db.py`가 이미 얇은 함수 계층이라 커넥션 팩토리(`_conn`)와 SQL만 교체하면 됨(placeholder `?`→`%s`, `AUTOINCREMENT`→`SERIAL` 등). FK 제약·CASCADE 추가로 `delete_store` 고아 행(publications/links/rank_snapshots) 문제도 함께 해결. 진짜 멀티인스턴스 시점에 착수.
- **난이도 중 / 효과 상(스케일 시점)**.

## D. 로컬 디스크 의존 제거 → 수평 확장

**문제**: ffmpeg·비전·보정이 로컬 파일 필수(`storage.py`), 업로드~생성 사이 로컬-only 시간창 존재 → 인스턴스 2대 즉시 불가. 단일 uvicorn 프로세스(워커 1).

**구현 노트**: B5에서 추가한 `storage.ensure_local()`(R2→로컬 복원)를 생성 파이프라인 진입점에 확장 적용하면, 어느 인스턴스에서든 R2에서 원본을 내려받아 처리 가능. 그 후 Postgres(C) + gunicorn 워커 다중화.
- **난이도 상 / C·D 함께 진행**.

## E. 채널 토큰 암호화 (보안 부채)

**문제**: `channel_accounts.access_token_enc`가 이름만 `_enc`이고 실제 평문(`db.py` 주석 인정).

**구현 노트**: `SHOPCAST_SECRET` 파생 키로 `cryptography.fernet` 암복호화 래퍼를 `save_channel_account`/`get_channel_account`에 삽입. 기존 행은 lazy 재암호화. B1에서 시크릿을 fail-closed로 강제했으므로 키 부재 리스크는 해소됨.
- **난이도 하 / 효과 상(보안)**.

---

## 관측성 (교차 관심사)
- 구조화 로깅(현재 핸들러 안 lazy `logging.exception` 산발) + 백그라운드 스레드 실패 집계.
- Sentry/APM 도입 — 현재 백그라운드 스레드 실패가 조용히 사라짐.
- `/health`에 큐 깊이·최근 실패율 노출.

## 권장 순서
1. **A(워커 큐+복구)** — 재시작 유실·ffmpeg 폭주 동시 해결
2. **B(/api/demo 오프로드)** — 저비용 즉시
3. **E(토큰 암호화)** — 저비용 보안
4. **C+D(Postgres+로컬탈피)** — 진짜 멀티인스턴스 시점
