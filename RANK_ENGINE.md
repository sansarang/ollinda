# RANK_ENGINE — 네이버 상위노출 실행 루프

기존에는 순위진단(보여주기)과 콘텐츠생성(만들기)이 끊겨 있었다.
이 기능은 둘을 **진단 → 타겟 생성 → 발행 일관성 → 추적 → 학습**의 닫힌 루프로 연결한다.

## 전체 흐름도

```
┌──────────────────────────────────────────────────────────────────────┐
│  ① 진단 (무료)                                                        │
│  /api/rank-check → diagnose.py: 지역×업종 롱테일 스캔 + 실검색량       │
│  → missing(미노출) 키워드에 targets[] 부착 (make_href)                 │
└──────────────┬───────────────────────────────────────────────────────┘
               ▼ "이 키워드 잡는 글 만들기" CTA (랜딩 위젯 + /me 리포트탭)
┌──────────────────────────────────────────────────────────────────────┐
│  ② 타겟 생성                                                          │
│  /me?target_kw=…&angle=… → 업로드폼 hidden 필드 → ingest_upload       │
│  → asset.target_kw/angle → BlogDraftGenerator: kw0(대표키워드) 교체    │
│  → 제목·첫문장·소제목이 그 키워드 겨냥 (밀도 3~5회 가드 = 도배 금지)    │
│  + 앵글 3종(후기/방법/가격) 변형: /api/blog/angle-variant              │
│  + 내부링크 제안: blogsync.related_published (주제 응집도)              │
└──────────────┬───────────────────────────────────────────────────────┘
               ▼ 사용자 수동 발행(네이버 API 없음) → RSS/수동 발행확인(BLOG_TRACKING.md)
┌──────────────────────────────────────────────────────────────────────┐
│  ③ 발행 일관성 (C-Rank 핵심)                                          │
│  pubcal.week_plan: 주 N회 목표(플랜별/가게 설정) + 이번주 진행률         │
│  + topic_axis(전문 주제 축) 기반 "이번 주 이 주제로 N개" 제안           │
│  + 발행 리마인더(공백 3일+): 앱내 notices + 이메일 + 카톡(스텁)          │
└──────────────┬───────────────────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  ④ 순위 추적                                                          │
│  ranktrack.track_all (APScheduler 매일 07:30 KST)                     │
│  → rank_snapshots(kind=blog|place|blog_search) 스냅샷                 │
│  → rank_deltas: "5위→2위 ⬆️" / "미노출→4위 진입 🎉" 성장 그래프          │
└──────────────┬───────────────────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  ⑤ 학습                                                               │
│  오른 키워드: db.improving_keywords → ingest가 다음 생성 브리프에 역주입 │
│  정체 키워드: ranktrack.stagnant_keywords → 앵글 로테이션 재도전 제안    │
│  → '오늘의 액션'(_daily_action)과 📈 순위 성장 카드가 다음 행동 코칭     │
└──────────────────────────────────────── (②로 되돌아감) ────────────────┘
```

## 네이버 신호 ↔ PHASE 매핑

| 네이버 신호 | 내용 | 충족 위치 |
|---|---|---|
| **C-Rank (주제 집중)** | 같은 주제 꾸준한 발행 | P2: topic_axis + 발행 캘린더 + 리마인더, 블로그등록 P4: RSS 실측 일관성 |
| **C-Rank (활동 지속성)** | 발행 간격·연속성 | P2: publish_activity/streak, 리마인더 잡 |
| **C-Rank (주제 응집도)** | 같은 주제 글 상호 링크 | P4: related_published 내부링크 제안(발행 export 화면) |
| **D.I.A.+ (경험·정보성)** | 1인칭 실경험, 사진·영상, FAQ·표 | 기존 BlogDraftGenerator 유지(건드리지 않음) + target_kw 주입만 추가 |
| **스마트블록 다중진입** | 의도별(후기/방법/가격) 별도 블록 | P4: 앵글 3종 변형(/api/blog/angle-variant) + P1 angle 파라미터 |
| **지식스니펫** | Q&A 소제목 | 기존 '자주 묻는 질문' 3쌍 필수 섹션(유지) |
| **플레이스 순위** | 리뷰 수·최신성, 정보 완성도, 소식 | P5: place_opt 체크리스트 + 리뷰 요청 키트 + kind='place' 분리 추적 |
| **상호 신뢰(블로그↔플레이스)** | 업체명·지역 표기 일관성 | 기존 blog closing(유지) — "업체명·지역 일관 표기" 지시문 |

## 플랜 한도 (전부 `app/config.py`)

| 상수 | 의미 | 기본값 |
|---|---|---|
| `TARGET_CONTENT_SUGGEST` | 미노출→타겟 제안 수 | 3 |
| `PLAN_WEEKLY_TARGET` | 플랜별 주간 권장 발행 | free 1 / basic 2 / pro 3 / agency 5 |
| `REMIND_GAP_DAYS` | 발행 공백 리마인더 기준 | 3일 |
| `RANK_TRACK_KEYWORDS` | 가게당 자동추적 키워드 | 5 |
| `PLAN_LIMITS[*]["angle_variants"]` | 앵글 변형 월 한도 | free 2 / basic 8 / pro 60 / agency 무제한 |
| `BLOG_WEEKLY_TARGET`, `WEEKLY_REPORT_DOW/HOUR` | 블로그 추적(BLOG_TRACKING.md) | 3 / 월 09시 |

게이팅: 진단은 무료(IP 레이트리밋+캐시 기존 유지), 생성은 기존 무료체험/플랜 한도,
앵글 변형은 `gating.check_limit(u, "angle_variants")`.

## APScheduler 잡 목록 (`app/scheduler.py`)

| 잡 id | 주기 (KST) | 동작 |
|---|---|---|
| `competitor_daily` | 매일 09:00 | 경쟁사 스캔(기존) |
| `rank_track_daily` | 매일 07:30 | tenant×키워드 순위 스냅샷 (P3) |
| `publish_reminder` | 매일 18:00 | 발행 공백 리마인더 (P2) |
| `weekly_blog_report` | 월 09:10 | 주간 성과 리포트 (블로그등록 P4) |

수동 트리거: `POST /admin/reports/weekly`, `POST /admin/reports/send-due`.

## 스텁 (연결 지점만 확보)

- **카카오 알림톡**: `pubcal.remind_stale_tenants`(리마인더), `weekly_report._send_kakao_stub`(주간 리포트),
  `competitor.notify_alerts`(기존) — 전부 로그만. 알림톡 템플릿 승인 후 발송 코드 연결.
- **성과형 과금**: `db.record_perf_event` (기존 스텁 유지).

## 정직성 가드 목록

1. 순위: 실측만 — 상위 N 밖=0(미노출), 조회불가=None. 임의 숫자 금지 (`diagnose`, `place`, `blogrank`).
2. "무조건 1위" 보장 문구 금지 — 랜딩 루프 섹션·캘린더·리포트 전부 "꾸준하면 신뢰도가 쌓인다" 사실 기반.
3. 키워드 도배 금지 — target_kw 주입 시에도 밀도 3~5회 가드(`_kw_density`)·D.I.A.+ 구조 유지.
4. 가짜 리뷰 금지 — `place_opt.review_request_texts`는 실제 방문 손님 대상 정당 요청만(대가성 표현 없음).
5. 발행 확인은 RSS 실측 or 사용자 URL 확인만 — 매칭 임계(0.5) 미달은 발행으로 안 만듦.
6. 의료·중고차 하드블록, 표시광고법 리스크 자동발행 금지 (기존 `seo.hard_block_hits` 유지).

## 배포 주의

- 새 테이블(`blog_publishes`, `weekly_reports`, `notices`)과 tenants/users 컬럼은 `init_db()` 마이그레이션이
  자동 처리(ALTER 실패 무시 패턴) — 별도 마이그레이션 불필요.
- 스케줄러 잡 4개는 재시작 시 재등록(`replace_existing=True`). `SHOPCAST_DISABLE_SCHEDULER=1`로 끌 수 있음.
- 네이버 키(`NAVER_CLIENT_ID/SECRET`) 없으면 순위 기능은 전부 graceful(None/빈 결과) — 서비스는 계속 동작.
- push 후 Railway 자동배포: `/health` 200 확인.
