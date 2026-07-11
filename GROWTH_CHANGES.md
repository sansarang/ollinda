# Shopcast (올린다) — 성장 개선 요약 (GROWTH_CHANGES)

> 2026-07-11 · "매출 전환 + 네이버 상위노출 + 영상 완성도 + 랜딩" 4축 개선. 스냅샷 `0b85f14` 이후.
> 규칙 준수: PHASE별 커밋 + `import app.main` 검증(깨지면 즉시 수정), 가격은 `app/config.py` 상수.

---

## PHASE별 커밋 · 수정 파일

| PHASE | 커밋 | 파일 | 요약 |
|---|---|---|---|
| 가격 상수 | `4e5f97e` | **app/config.py**(신설), pay.py | 가격·플랜 중앙화 + 연결제 자동생성 |
| 1 순위 즉시진단 | `0561459` | services/diagnose.py(신설), main.py | 업종+지역+상호 → 현재 네이버 순위 + CTA. `POST /api/rank-check` |
| 2 성과증명 무료체험 | `69c7afe` | services/growth.py(신설), publish.py, db.py, main.py | 발행 시 순위 자동스냅샷 + 7일 리포트(발송 스텁) + /me before/after 카드 |
| 3 가격 구조 | `1f10d83` | landing.py, config.py, db.py | 베이직 인하·연결제·대행카드·성과형 스텁 |
| 4 대행 상품화 | `984ded1` | db.py | users.agency_note + set_agency_note |
| 5 키워드 엔진 | `d6ecdd6`, `844a2e6` | seo.py | keyword_plan(대표/롱테일/추정) + 지역결합 힌트 + 24h TTL |
| 6 블로그 D.I.A.+ | `28cb87d` | generators/text_claude.py | 대표=제목·롱테일=소제목, 유의어 확장 |
| 7 스마트블록·일관성 | `11b3c11` | seo.py, db.py | BLOG_ANGLES 3종 + topic_axis + posting_cadence_tip |
| 8 플레이스 연동 | `5c64686` | text_claude.py, db.py, growth.py | 플레이스 신호 유도 + rank_snapshots.kind(blog/place) 분리 |
| 9 quality_audit | `d5061c4` | seo.py | C-Rank·D.I.A.+ 신호 재정렬(구체수치·이미지4+) |
| 10 영상 기본결함 | `518b874`(앞선 작업) | photo_boost.py, video.py, requirements.txt | EXIF·HEIC·폴백자막·faststart (이미 반영됨) |
| 11 숏폼 노출 | `0d7f067` | video.py | 검색키워드 자막·루프 + BGM 사이드체인 더킹 |
| 12 규격·화질·안정 | `bb4dbf5` | video.py, ingest.py | 렌더 Semaphore + 규격 파생본 crf20 + mux stderr 로깅 |
| 13 전환 킬러 제거 | `78b691f` | landing.py | 단일 CTA·실수치 통계·예시 라벨·보안 포트폴리오 제거 |
| 14 데모 자산 | `7816e3a` | landing.py, scripts/regen_demo.py(신설) | preload/poster + 정직 라벨 + 재생성 스크립트 |
| 15 랜딩 재배열 | `018d4ec` | landing.py | 단일메시지+순위위젯+섹션재배열+OG일치 |

## 바뀐 가격값 (전부 `app/config.py` 상수, env 오버라이드 가능)

| 항목 | 이전 | 이후 | 상수 |
|---|---|---|---|
| 베이직(월) | 39,000 | **29,000** | `PRICE_BASIC` / `SHOPCAST_PRICE_BASIC` |
| 프로(월) | 79,000 | 79,000 | `PRICE_PRO` / `SHOPCAST_PRICE_PRO` |
| 대행(월) | "문의" | **15만~25만** | `AGENCY_FROM`/`AGENCY_TO` |
| 연 결제 | 없음 | **월가×12×0.7**(약 -30%) | `YEARLY_DISCOUNT`, `yearly_price()` → PLANS의 `basic_yearly`/`pro_yearly` |
| 성과형 임계 | — | 상위 10위(1페이지) | `PERFORMANCE_RANK_THRESHOLD` |

## 네이버 신호 → 코드 매핑

- **C-Rank(출처 신뢰)**: `tenants.topic_axis`(전문 주제 축) `db.py`, `seo.posting_cadence_tip`(주N회 발행 캘린더), 발행 시 순위 자동스냅샷 `growth.on_publish`.
- **D.I.A.+(문서 품질)**: 블로그 프롬프트 PAS·1차경험·구체수치·FAQ·제목-본문일치 `text_claude.py`; `seo.quality_audit`(구체수치·이미지4+·경험표현·낚시·빈약 감점, 숫자 그라운딩 날조탐지).
- **플레이스(지역)**: 매장형 마무리 저장·리뷰·예약·전화 유도 + 업체명/지역 일관 `text_claude.py`; `rank_snapshots.kind='place'` 분리추적 `db.save_place_rank`.
- **스마트블록/스니펫**: `seo.BLOG_ANGLES`(후기/방법/가격 3앵글), 롱테일 소제목 배치 `keyword_plan`, FAQ Q&A.

## 스텁으로 남긴 것 (실데이터/외부 연결 필요)

- **7일 리포트 발송**: `growth.send_due_reports`는 로그만(스텁). SMTP/카톡 알림톡 훅 연결 지점 표시. `POST /admin/reports/send-due` 크론 자리 마련.
- **성과형 과금(1페이지 진입)**: `performance_events` 테이블 기록만. 실제 청구 로직 미구현.
- **순위 자동 크론**: 발행 시점 스냅샷은 자동. 일 1회 정기 스냅샷 스케줄러는 SCALING(워커)과 함께 — 현재는 발행 트리거 기반.
- **CTA에 채널별 UTM 자동주입**: 클릭 로깅(`link_clicks`)·`/r/{code}?utm_source` 플러밍은 완료, 생성 CTA에 자동 삽입은 미연결.

## 영상 — verify-before-ship (이 환경서 런타임 검증 불가)

이 작업 환경엔 **ffmpeg·ANTHROPIC_API_KEY·ElevenLabs가 없어** 아래는 코드 미변경/이관. 실제 적용 전 렌더 결과 확인 필요:
- **ElevenLabs `/with-timestamps` 단어 카라오케**: `_build_ass`가 현재 글자수 비례 근사. 타임스탬프 실측화는 tts.py+_build_ass 대수술 → 렌더 검증 가능 환경에서.
- **씬 전환 xfade**: 현재 fade-to-black. xfade는 "별도 트랙 concat=드리프트 없는 싱크" 설계를 깰 수 있어(분석 경고) 오디오싱크 재설계 후 적용.
- **3초 훅 실사진 배경**: 현재 그라데이션 카드. `_card_png`/훅 씬을 실사진 오버레이로 교체(렌더 확인 필요).
- 적용된 것: 검색키워드 자막·루프 지시(프롬프트), BGM 사이드체인 더킹(_mux), 렌더 Semaphore, 규격 파생본 crf20, mux stderr 로깅.

## 랜딩 — 정직성 감사 (제거/수정한 과장·미검증 표현)

- **'자동 발행' → 채널 정확**: OG/메타/FAQ에서 네이버는 '초안 반자동'으로 표기(IG·YT·X만 자동). 히어로-FAQ 불일치 제거.
- **'무조건 1위/순위 보장' 금지 준수**: '상위노출에 유리한 구조로 작성'까지만 주장(히어로·요금·JSON-LD).
- **'37회 손님' 실데이터 아님** → `(예시)` 라벨.
- **카운트업 '0' 통계** → 실제 확정값(5채널·1장·100점·2모드)으로 초기 렌더.
- **CEO 보안 포트폴리오(모의해킹·DFIR·OWASP)** 제거 → '실제 현장 요구에서 개발' 신뢰 문구(소상공인 타깃 부적합 제거).

## 교체한/교체 예정 데모 자산

- 로딩 최적화 적용: `app/static/demo/local_short.mp4`에 `preload=metadata`·`poster=/demo/og.png` + '실제 올린다 생성물' 라벨.
- **실제 재생성은 미실행**(키·ffmpeg 부재). `scripts/regen_demo.py`로 키 보유 환경에서 개선 파이프라인 실물 생성 → `git diff` 확인 후 커밋. 가짜 데모 방지 위해 키 없으면 스크립트가 중단.

## 새 랜딩 섹션 순서 · CTA 위치

히어로(단일메시지 '네이버 상위노출' + **순위진단 위젯**) → 데모영상(즉시신뢰) → 셀프체험 → 문제(PAS) → 해결·차별점(why_rank) → **성과증명(results)** → **정직성(honesty, 상단)** → 통계 → 비교 → 모드 → 기능 → 요금(연결제/대행) → FAQ → 문의 → 마지막 CTA → 스티키 CTA.
CTA: 히어로 주 CTA '카카오로 무료 시작' 단일 + 순위위젯 CTA + 스티키 CTA + 요금 카드 CTA.

## 실데이터 연결 필요 지점 (실제로 채워지는 시점)

- **순위·유입 실측**: 네이버 API 키(NAVER_CLIENT_ID/SECRET, SearchAd) 등록 시 rank_detail/keyword_plan이 '추정'→실측 전환. 클릭 실측은 콘텐츠 CTA에 `/r/{code}` 자동삽입 연결 후.
- **7일 리포트**: 실제 발행 7일 경과 + `/admin/reports/send-due` 크론 연결 시.
- **before/after 순위 카드**: 발행→재스냅샷 2회 이상 쌓이면 표시(현재 `improving_keywords` 기반).

## 추적 필요 지표

무료→유료 전환율 · 7일 리포트 열람률 · 대행 문의수 · 영상 완주율 · 순위진단 위젯 참여율.

## 검증 방법 주석

- 각 PHASE 커밋 전 `SHOPCAST_SECRET=... python -c "import app.main"` 통과. **2건의 SyntaxError(f-string 조건부 `+` 누락)를 import 체크로 잡아 수정 후 amend**(`28cb87d`,`11b3c11`) — 깨진 커밋 미잔존. **1건의 로직버그(blog/place 순위 dedup 충돌)를 기능 스모크 테스트로 잡아 kind별 dedup으로 수정 후 amend**(`5c64686`).
- DB 변경은 임시 sqlite로 기능 테스트(리포트 예약/발송·성과형·순위 kind 분리·클릭로그).
- **회귀 테스트 스위트는 여전히 0개** — 배포 전 핵심 플로우 스모크 테스트 권장(`SCALING.md` 관측성 참조).
