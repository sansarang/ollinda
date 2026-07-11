# BLOG_TRACKING — 사용자 네이버 블로그 등록 · 발행확인 · 순위매칭 · 리포트

네이버 블로그는 **공식 발행 API가 없다** → 사용자가 올린다에서 만든 글을 직접(수동) 발행한다.
그래서 시스템이 "실제로 발행됐는지 / 실제로 몇 위인지"를 추적하려면 **사용자 블로그 등록**이 출발점이다.

## 전체 흐름

```
① 블로그 등록(/me/blog)            ② 발행 확인                    ③ 순위 매칭               ④ 리포트
사용자 URL 입력                    글 발행(수동) 후                blog_id로 검색결과에서      주간 리포트(월 09:10 KST)
 → blog_id 정규화 추출              RSS 최근글 ↔ 생성글 매칭        내 블로그 '정확' 식별       발행수·순위변화·코칭
 → RSS로 실존 검증                  (or 사용자 URL 붙여넣기)        rank_snapshots 소스별 저장   앱내 + 이메일(카톡 스텁)
```

## PHASE 1 — 블로그 URL 등록 (`app/services/blogsync.py`)

- **입력 유연 처리** `normalize_blog_id()`: `https://blog.naver.com/{id}`, `m.blog.naver.com/{id}/글번호`,
  `PostList.naver?blogId={id}`, 아이디만 입력 — 전부 blog_id로 정규화.
- **실존 검증** `verify_blog()`: 공개 RSS(`https://rss.blog.naver.com/{id}.xml`) 조회.
  네이버는 **없는 아이디에도 200 + 빈 채널**(`<title/>`)을 반환 → 채널 title/link가 비면 미존재 판정.
  네트워크 실패는 "존재 판정 불가"로 정직하게 실패 처리(가짜 성공 금지).
- 저장: `tenants.naver_blog_url`, `tenants.blog_id` (`db.set_tenant_blog`). 빈 값 제출 = 연결 해제.
- UI: `/me?tab=report#blog` 연결 카드 + 온보딩 최소 폼(선택 입력) + 홈 미연결 유도 배너.

## PHASE 2 — 발행 확인 (RSS 우선 + 수동 병행)

- **RSS 사용 이유**: 네이버가 공식 제공하는 공개 피드 → 크롤링 리스크 없음, 로그인/비밀번호 불필요.
- 자동 매칭 `find_published()`: 생성글(제목·target_keywords) ↔ RSS 최근글 제목.
  - 제목 포함관계(정규화 8자+) = 1.0 / 토큰 자카드 + 타겟키워드 겹침 가점.
  - **임계 0.5 미만이면 발행으로 만들지 않는다** → 대신 수동 확인 경로 병행.
- 수동 확인: `/kit/{asset}/naver` 하단 "발행함 ✓" — URL 붙여넣기. 등록 blog_id와 다른 블로그 주소는 거부.
- 기록: `blog_publishes(piece_id PK, published_url, published_at, matched_by=rss|manual, match_score)`.
  확인 시 `publications` 기록 + 상태 PUBLISHED + `growth.on_publish`(발행 시점 순위 baseline + 7일 리포트 예약)로 성과 루프에 연결.
- 진입점: 리포트 탭 "🔄 발행 자동 확인" 버튼 / `POST /api/blog/check-published`.

## PHASE 3 — blog_id 기반 정확한 순위 매칭 (`app/services/blogrank.py`)

- 기존 `place.py` 상호명 매칭은 **플레이스(지역검색)용으로 유지** — 건드리지 않음.
- 블로그 순위는 네이버 **블로그검색 API**(`/v1/search/blog.json`, place와 동일 키) 상위 30위에서
  각 결과의 link/bloggerlink에서 blog_id를 추출해 **URL 단위 정확 대조** → 동명 상호 오탐 없음.
- 저장: `rank_snapshots.kind` 소스 구분 — `blog`(지역검색·기존), `place`(플레이스), **`blog_search`(blog_id 블로그탭)**.
  `get_prev_rank(kind=)`·`rank_history(kind=)`로 소스별 변화 계산.
- 노출: `/me/rank` 응답에 `blog_rank/blog_prev/blog_url` 추가 → 리포트 탭 "📝 내 블로그(정확 매칭)" 줄.
- `growth.on_publish`도 블로그 연결 시 blog_search 스냅샷 병행 기록.

## PHASE 4 — 발행 일관성 + 주간 리포트

- `blogsync.posting_consistency()`: RSS pubDate로 **실제 발행 주기** 측정(올린다 사용량이 아니라 블로그 실측)
  → 이번 주 N회/목표, 최근 4주 막대, 연속 발행 주, 주평균. C-Rank '활동 지속성' 지표.
  목표 주기 = `tenants.publish_schedule`(설정 시) 또는 `config.BLOG_WEEKLY_TARGET`(기본 3).
- 주간 리포트 `app/services/weekly_report.py`:
  - 대상: `db.list_tenants_with_blog()` (블로그 연결 가게만).
  - 내용: 발행 일관성 + 7일 순위 변화(소스별) + 미노출→진입 키워드 + 사실 기반 코칭 한 줄.
  - 발송: 앱내(`weekly_reports` 테이블 → 리포트 탭 카드) + 이메일(SMTP 설정 시, 게스트 가짜 이메일 제외)
    + **카카오 알림톡 스텁**(로그만 — 템플릿 승인 후 연결 지점 `_send_kakao_stub`).
  - 스케줄: APScheduler `weekly_blog_report` (기본 월 09:10 KST, `WEEKLY_REPORT_DOW/HOUR`).
    수동 트리거: `POST /admin/reports/weekly`.

## 정직성 원칙 (이 기능이 지키는 것)

1. **가짜 발행 없음** — RSS에서 확인되거나 사용자가 URL로 확인한 것만 '발행됨'. 매칭 근거(`matched_by`) 저장.
2. **가짜 순위 없음** — 상위 N 밖=0(미노출), 조회 불가=None. 임의 숫자 생성 금지.
3. **집계 실패는 '집계중'** — RSS/API 실패 시 수치를 지어내지 않고 정직하게 표기.
4. **"무조건 상위" 금지** — 코칭 문구는 "꾸준히 발행하면 C-Rank 신뢰도가 쌓인다"는 사실 기반.
5. **공식 채널만** — 공개 RSS + 공식 검색 API. 크롤링·로그인 자동화 없음.

## 키/환경변수

| env | 용도 |
|---|---|
| `NAVER_CLIENT_ID/SECRET` | 블로그검색(blogrank)·지역검색(place) 공용. 무키 시 rank=None graceful |
| `SMTP_HOST/USER/PASS/PORT` | 주간 리포트 이메일(미설정 시 앱내만) |
| `SHOPCAST_BLOG_WEEKLY` | 기본 주간 발행 목표(기본 3) |
| `SHOPCAST_REPORT_DOW/HOUR` | 주간 리포트 발송 요일(0=월)/시각(KST) |
