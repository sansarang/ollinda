# 신규 기능 2종 — 경쟁사 추적기 · 인쇄물 자동 생성

> 2026-07-11 · 돈 되는 신규 기능 2개 + 플랜별 게이팅 + 랜딩 무료체험 노출. 스냅샷 `2763132` 이후.
> 기존 기능(순위진단·콘텐츠생성·결제)은 무변경, 신규 추가만. 각 PHASE 커밋 + `import app.main` + 스모크 통과.

## PHASE별 커밋

| PHASE | 커밋 | 내용 |
|---|---|---|
| 게이팅 상수 | (config, PHASE1) | `config.PLAN_LIMITS` + `plan_limit()` |
| 1 | `eb47a58` | 경쟁사 DB(competitors, competitor_snapshots) + 사용량 카운터(users) |
| 2 | `cea983e` | `gating.py`(공용 게이팅) + `services/competitor.py`(스캔·비교) |
| 3 | `d05374a` | `scheduler.py`(APScheduler 일일 자동) + 수동 스캔 엔드포인트 |
| 4 | `c90efcf` | 경쟁사 API + `/me/competitors` UI + 알림 스텁 |
| 5 | `9ec3f65` | `services/printable.py`(Playwright 렌더 + 규격 프리셋) + Dockerfile |
| 6 | `4aa5a16` | 인쇄물 문구생성(FACTS_RULE) + 5종 템플릿 |
| 7 | `061ce36` | 인쇄물 API + `/me/print` UI + 다운로드 |
| 8 | `1e23c04` | 랜딩 신규기능 소개 + 무료체험 CTA + 요금 갱신 |

## 아키텍처

### 기능①: 경쟁사 추적기 (place.py 재활용)
```
등록(/api/competitor) → competitors 테이블
  ↓
스캔: services/competitor.scan_competitor(tenant, comp)
  · 등록 키워드별 place.rank(내 상호) vs place.rank(경쟁사 상호)  ← 순위진단과 동일 엔진
  · competitor_snapshots 저장 + 직전 대비 변화 판정(역전/따라잡힘/벌어짐)
  ↓
자동: scheduler.py(APScheduler) 매일 09:00 KST active 전체 스캔
수동: POST /api/competitor/scan (plan 한도 차감)
표시: GET /api/competitor/report → /me/competitors(현황 카드 + 역전 경보)
알림: 앱내(report) + competitor.notify_alerts(SMTP 이메일 / 카톡 스텁)
```

### 기능②: 인쇄물 자동 생성 (새 렌더 파이프라인)
```
POST /api/print/generate (type·items·photo)
  ↓ gating(print_items 한도)
printable.generate:
  · generate_copy: 헤드라인·태그라인만 app.llm(FACTS_RULE) — 항목·가격은 입력 그대로(날조 금지)
  · build_html: 5종 타입 HTML/CSS 템플릿 + 사진 base64
  · render: Playwright(Chromium) → PNG/PDF → R2 미러  ← asyncio.to_thread(이벤트루프 비차단)
  ↓
db.save_print_job → GET /api/print/list, /print/file/{id}(소유권 확인 다운로드) → /me/print UI
```

## 플랜 한도 (전부 `app/config.py` — 여기서만 조정)
```python
PLAN_LIMITS = {  # -1 = 무제한
  "free":   {"competitor_scans": 5,   "print_items": 3,  "competitors_max": 1},
  "basic":  {"competitor_scans": 30,  "print_items": 10, "competitors_max": 2},
  "pro":    {"competitor_scans": 300, "print_items": 50, "competitors_max": 5},
  "self":   {...pro 별칭...},
  "agency": {"competitor_scans": -1,  "print_items": -1, "competitors_max": -1},
}
```
- 게이팅 공용: `app/gating.py` — `check_limit(user, feature)`(미로그인=가입CTA, 초과=업그레이드CTA), `consume`, `usage_summary`.
- 사용량 카운터: `users.competitor_scans_used / print_items_used`(월간 리셋, `db.incr_feature_usage` 원자 증분).

## API 목록 (신규)
| 메서드 | 경로 | 게이팅 |
|---|---|---|
| POST | `/api/competitor` (등록) | competitors_max |
| GET | `/api/competitor/list` | — |
| POST | `/api/competitor/{id}/delete` | — |
| POST | `/api/competitor/scan` (수동) | competitor_scans 차감 |
| GET | `/api/competitor/report` | — |
| GET | `/me/competitors` (페이지) | 로그인 |
| POST | `/api/print/generate` | print_items 차감 |
| GET | `/api/print/list` | — |
| GET | `/print/file/{id}` (다운로드) | 소유권 |
| GET | `/me/print` (페이지) | 로그인 |

## 정직성 (준수)
- 경쟁사 순위: place 5위 한계 그대로 — 6위 이하는 "5위권 밖", **가짜 순위 없음**(단위검증).
- 인쇄물: 항목·가격은 사장님 입력 **그대로**(FACTS_RULE), AI는 헤드라인/태그라인만. 없는 가격·혜택 생성 금지.
- 랜딩: "무조건 1위/순위 보장" 표현 없음, 실제 제공 범위만.

## 스텁으로 남긴 것
- **카톡 알림톡 발송**: `competitor.notify_alerts`에 이메일(SMTP)은 구현, 카카오 알림톡은 TODO 스텁(템플릿 승인 후).
- **성과형 과금**(기존): 그대로.

## 새 의존성 · Dockerfile 변경 (⚠️ 배포 주의)
- `requirements.txt` 추가: `apscheduler`(경량), `playwright`, `jinja2`.
- `Dockerfile`: `python -m playwright install --with-deps chromium` 추가 — **이미지 크게 늘어남**(~수백MB). 실패해도 `|| echo`로 빌드 계속(런타임 graceful: chromium 없으면 인쇄물 생성이 "준비 중" 에러 반환, 앱은 정상).
- **Railway 배포 시 빌드 시간·이미지 크기 증가** 확인 필요. 빌드 실패 시 chromium 스텝만 문제고 앱은 뜸.
- 스케줄러: `SHOPCAST_DISABLE_SCHEDULER=1`로 끄기, `SHOPCAST_SCAN_HOUR`로 시각 조정. apscheduler 미설치 시 자동 비활성(수동 스캔은 동작).

## 검증
- 각 PHASE `import app.main` + 스모크 4종 통과.
- 유닛/E2E: 게이팅(free 소진→업그레이드CTA, 미로그인→가입CTA), 경쟁사 스캔(가짜순위 0건), 인쇄물 생성(로컬 Chromium 실렌더 PNG 14.8KB), 다운로드 200, 사용량 차감.
- **로컬엔 Playwright+Chromium이 있어 실렌더까지 검증됨.** Railway는 push 후 자동배포에서 chromium 빌드 확인 필요.
