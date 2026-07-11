# Shopcast (올린다) — 수정 요약 (CHANGES)

> 2026-07-11 · `REPORT.md`/`ARCHITECTURE.md` 분석 기반 수정. 각 PHASE 커밋 후 `python -c "import app.main"` 임포트 검증.
> 스냅샷: `ffeb5ab` (수정 전). 이후 커밋이 이번 작업분.

---

## PHASE 1 — 보안 Critical  (커밋 `66e54b4`, `a158714`)

| ID | 파일 | 내용 |
|----|------|------|
| B1 | `auth.py:15`, `oauth.py:32` | `SHOPCAST_SECRET` 미설정 시 **기동 실패(fail-closed)**. `dev-secret-change-me` 기본값 제거. 세션 HMAC 서명 절단(16자) → 전체 64자 |
| B2 | `main.py:111` | `SHOPCAST_ADMIN_PASS` 미설정 시 `/admin/*` **503 전면 차단**(cleanup·testaccount 등 파괴 라우트 무인증 노출 방지) |
| B11 | 세션 `set_cookie` 6곳 | HTTPS 배포(`SHOPCAST_BASE=https…`)에서 `secure=True`. `auth.cookie_secure()` 헬퍼 |
| B12 | `oauth.py` | X OAuth PKCE `plain` → **S256**(verifier는 SECRET+state 도출, challenge만 노출) |
| B14 | `auth.py` `read_session` | 세션 **60일 만료 검증** 추가 |
| B13 | — | **보류**. `/asset`·`/video`는 Meta·Runway가 server-to-server로 fetch → 로그인 게이트는 발행 파괴. UUID라 실위험 낮음. 올바른 해법(서명 URL)은 미검증 emitter 다수 → 규칙3(파괴 금지) 준수해 이관 |

⚠️ **배포 필수**: `SHOPCAST_SECRET`가 이제 **필수 환경변수**. 기존 로그인 세션 전부 무효화(서명길이 변경).

## PHASE 2 — 크래시·결제·발행  (커밋 `e1cb8da`)

| ID | 파일 | 내용 |
|----|------|------|
| B3 | `services/revise.py:85` | 없는 메서드 `gen._assemble` → `_assemble_legacy`. 쇼츠 자막 수정 시 500 크래시 제거 |
| B4 | `main.py:2577`, `pay_paddle.py` | Paddle 웹훅이 `custom_data.plan`(조작 가능) 대신 `items[].price.id`를 `PADDLE_PRICE_*`와 역매칭. 미매칭 시 플랜변경 보류+로깅 |
| B5 | `storage.py`, `main.py`, `youtube.py`, `ingest.py` | R2 미러 후 로컬 삭제로 인한 발행 404 수정. `public_url_for`/`ensure_local` 추가, `/asset`·`/video` R2 302 폴백, 유튜브 R2 복원, ingest가 발행용 R2 URL을 payload 각인 |

## PHASE 3 — 동시성·쿼터·업로드  (커밋 `51de690`)

| ID | 파일 | 내용 |
|----|------|------|
| B8 | `db.py:21` | `_conn`에 `PRAGMA journal_mode=WAL` + `busy_timeout=5000` + timeout 5s |
| B6 | `db.py` `incr_month_usage` | 단일 UPDATE(CASE) 원자화, `MAX(0,..)` 클램프 |
| B7 | `main.py` 업로드 | 쿼터 **선예약** 후 생성 실패 시 `_refund_usage` 원복(동시 업로드 우회 방지) |
| B9 | `main.py` `_read_image_uploads` | 확장자/content-type 화이트리스트 + 25MB 상한(업로드·데모 공통) |
| B10 | `main.py:2527`, `db.py` | `db.claim_once` 멱등 테이블로 `/billing/success` 이중청구 방지 |

## PHASE 4 — 콘텐츠 정직성·품질 가드  (커밋 `719c97d`)

| ID | 파일 | 내용 |
|----|------|------|
| C1 | `editor.py:55`, `revise.py:109` | 해시태그 8~12개·8개이상 → **3~5개**(seo 기준 통일, 자기모순 루프 제거) |
| C2 | `revise.py`(4곳), `editor.py`(2곳) | 리라이트 프롬프트에 `seo.FACTS_RULE`(가격·스펙 날조·PII 금지) 주입 — 수정 경로 가드 소실 차단 |
| C3 | `industries.py` `industry_brief` | 업종 `cautions`(표시광고법) 포함 → 블로그·쇼츠·X·마켓 전 채널 전달 |
| C4 | `ingest.py` `_autopilot` | 완전자동(autonomy=2)에도 최소점수 게이트(70) + `RISKY_EXPRESSIONS` 히트 시 발행 보류 |
| B15 | — | **수정 불필요 확인**. claude-api 레퍼런스 검증: `thinking={"type":"adaptive"}` + `claude-opus-4-8`은 **정상 파라미터**(`budget_tokens`가 400 대상, adaptive 아님). `generate_for`는 이미 `logging.exception`로 실패 로깅 중 |

## PHASE 5 — 영상 파이프라인  (커밋 `518b874`)

| ID | 파일 | 내용 |
|----|------|------|
| V1 | `photo_boost.py:61`, `video.py:288` | `ImageOps.exif_transpose()` — **세로 사진 눕는 문제** 해결 |
| V2 | `requirements.txt`, `photo_boost.py`, `video.py` | `pillow-heif` + `register_heif_opener()` — **아이폰 HEIC** 업로드 디코딩(미설치 시 graceful) |
| V3 | `video.py` `_assemble_legacy` | `subtitle`을 drawtext(textfile)로 실제 굽기 — 폴백 영상 자막 소실 수정 |
| faststart | `_mux`·`_aspect_variants`·폴백 | `-movflags +faststart` — 웹/R2 프로그레시브 재생 첫 지연 제거 |

## PHASE 6 — 마케팅 실측 (일부)  (커밋 `844a2e6`)

- ✅ **클릭 어트리뷰션**: `link_clicks(code·ts·referrer·ua·utm_source)` 행 단위 로깅 + `link_click_stats` 채널 분해. `/r/{code}`가 referer·UA·utm_source 기록.
- ✅ **검색량**: `lru_cache`(프로세스 수명) → **24h TTL** 캐시, 힌트에 **지역+업종 결합**(전국 키워드 혼입 방지).
- ⏭ **이관**(동작변경/스케줄러 수반):
  - **순위 자동 스냅샷**: in-process 일 1회 스케줄러(startup 데몬 스레드)로 tenant×키워드 `save_rank_snapshot`. → SCALING A(워커)와 함께.
  - **publishAt 골든타임**: `youtube.py:54`가 `payload["publish_at"]`을 읽으나 setter 0건. 업종별 골든타임 테이블로 기본값 주입 시 **영상이 즉시 공개 안 됨**(behavior change)이라 검수 플로우 정책 확정 후.
  - **CTA에 채널별 UTM 링크 자동주입**: `strategies.buy_block`/`text_claude` 마무리 블록에 `/r/{code}?utm_source=<channel>` 발급. 플러밍(link_clicks)은 완료, 주입만 남음.

## PHASE 7 — 글 생성 품질 (일부)  (커밋 `4ab2896`)

- ✅ **숫자 그라운딩**: `quality_audit(..., source=asset.note)` — 출력의 금액·%·수치가 입력에 없으면 '날조 의심' 경고+감점(LLM 0콜). ingest 3개 호출부 주입.
- ✅ **하드블록 금칙어**: `HARD_BLOCK_EXPRESSIONS`(완치·부작용없음·완전무사고 등) — 감점 아니라 **자동발행 절대 차단**.
- ⏭ **이관**(생성기 구조 변경):
  - **훅/제목 3안 출력화**: 캡션·X·쇼츠도 블로그 `_pick_title`처럼 후보 출력→코드 선택(현 `"속으로 구상"`은 검증 불가).
  - **tool_use 구조화 출력**: `_parse_sections` raw 폴백(`text_claude.py:160`)의 조용한 품질저하를 JSON schema로. 파싱/마커 후처리 상당수 제거되나 전 생성기 프롬프트 재작성 필요.

## PHASE 8 — 아키텍처 리팩토링 (안전분만)  (커밋 `8179bd2`)

- ✅ **`app/llm.py` 중앙화**(리팩토링 #2): `_call_llm`을 `llm.call`로 위임(동작 불변). 9개 역수입 모듈 무수정. 요청 timeout=60s 추가.
- ⏭ **이관**(테스트 부재 + 대규모 → 자동실행 모드에서 파괴 위험, 규칙4 준수):
  - #1 `main.py`(3757줄) APIRouter 분해 — **고위험**(라우트 91개, 인라인 HTML). 사람 검수하 단계적으로.
  - #7 HTML 스켈레톤 3벌 → Jinja2 — 렌더링 전면 교체, 회귀 위험 큼.
  - #3 payload dict → TypedDict/dataclass, #4 `PromptContext` 빌더, #5 `ingest` 파이프라인화, #6 미디어 서빙/ZIP/MIME 중복 제거, #8 `config.py`, #10 블로그 후처리 3중 구현 통합.
  - → 각각 개별 PR + 최소 스모크 테스트 선행 권장. `REPORT.md §3` 참조.

## PHASE 9 — 확장성

- ✅ `SCALING.md` 작성(워커 큐·이벤트루프 차단·Postgres·로컬탈피·토큰 암호화).

---

## 남은 추측/미검증 항목
- **B13 서명 URL**: 모든 `/asset`·`/video` emitter를 서명 URL로 전환해야 완결(미검증 emitter 존재 가능 → 보류).
- **PHASE 6 순위 스냅샷의 display=5 한계**: 네이버 지역검색 API가 상위 5위까지만 → 순위 정확도 본질적 한계(REPORT §4). display 확대 가능 여부는 **추측**, API 문서 확인 필요.
- **PHASE 6 지역 키워드 개선 효과**: `_relevant`가 2글자 겹침만 검사 → 여전히 일부 무관 키워드 통과 가능(추측).

## 검증 방법
- 각 PHASE 커밋 전 `SHOPCAST_SECRET=... python -c "import app.main"` 임포트 검증 통과.
- DB 변경(B6·B8·B10·PHASE6·7)은 임시 sqlite로 기능 스모크 테스트 수행(멱등성·원자성·클램프·클릭로그·날조탐지·하드블록).
- **전체 회귀 테스트 스위트는 여전히 0개** — 프로덕션 배포 전 핵심 플로우(ingest→generate→publish, 결제 웹훅) 스모크 테스트 추가 강력 권장(SCALING 관측성 참조).
