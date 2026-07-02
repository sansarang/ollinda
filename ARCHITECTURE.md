# Shopcast — 소상공인 멀티채널 마케팅 자동화 (시스템 아키텍처)

> 코드네임 `shopcast` (가칭, 언제든 변경 가능).
> 한 줄 정의: **사장님이 "사진 1장 + 한 줄 메모"만 보내면 → AI가 인스타·유튜브숏·네이버블로그용 콘텐츠로 변환하고 → (검수 후) 자동 발행하고 → 예약·단골 알림톡까지 돌리는** 반자동 시스템.

---

## 0. 설계 원칙 (왜 이렇게 잡는가)

1. **반자동(Human-in-the-loop)이 기본.** 완전자동 콘텐츠는 도달이 안 나오고 네이버는 저품질 제재. → "AI 80% 생성 + 사람 20% 검수/발행" 구조를 아키텍처에 못박는다.
2. **어댑터 패턴 = 채널/생성기는 전부 교체 가능한 플러그인.** 코어(오케스트레이션)는 우리 것, 외부 오픈소스·API는 어댑터 뒤에 격리.
3. **라이선스 위생.** 코어는 외부 카피레프트(AGPL 등)에 오염되지 않게 분리. 가져다 쓰는 코드는 MIT/Apache만, `references/`에 격리하고 출처·라이선스 명시.
4. **멀티테넌트(가게별 격리).** 가게마다 채널 토큰·소재·발행이력이 분리. 토큰은 암호화 저장.
5. **합법 우선.** 정보성/광고성 메시지 구분(정보통신망법), 채널 공식 API 우선, 매크로 최소화.
6. **작게 시작.** 1단계는 1업종·1~3가게로 수동에 가깝게 운영하며 검증 → 점진 자동화.

---

## 1. 큰 그림 (데이터 흐름)

```
 [사장님]
   │  사진/짧은영상 + 한 줄 메모 (카톡 or 웹 업로드)
   ▼
┌─────────────┐     ┌──────────────────┐     ┌───────────────┐
│ Ingest      │ ──▶ │ Asset Store      │ ──▶ │ Generate (AI) │
│ 원재료 수집  │     │ 원본+메타 저장     │     │ 채널별 자산생성 │
└─────────────┘     └──────────────────┘     └───────┬───────┘
                                                      │ 캡션/해시태그/스크립트/자막영상/블로그초안
                                                      ▼
                                            ┌───────────────────┐
                                            │ Review Queue (승인) │  ← 운영자/사장 검수
                                            └─────────┬─────────┘
                                                      │ 승인된 콘텐츠
                                                      ▼
                                            ┌───────────────────┐
                                            │ Publish Orchestr.  │
                                            └──┬──────┬──────┬───┘
                          ┌────────────────────┘      │      └────────────────────┐
                          ▼                            ▼                           ▼
                 [IG Adapter]                 [YouTube Adapter]            [Naver Adapter]
                 Meta Graph API(자동)         Data API(자동·예약)          반자동(초안 export)
                                                                                   │
                          ┌────────────────────────────────────────┐             (사람이 발행)
                          ▼                                          
                 [Kakao Alimtalk Adapter]  ── 예약확인/리마인드(정보성, 자동)
                                            ── 단골 마케팅(광고성, 동의자에게만)
                                                      │
                                                      ▼
                                            ┌───────────────────┐
                                            │ Analytics Collector│ → 월간 성과 리포트
                                            └───────────────────┘
```

---

## 2. 컴포넌트 (책임 분리)

| 컴포넌트 | 책임 | 자동/수동 | 비고 |
|---|---|---|---|
| **Ingest** | 사장님 입력(이미지/영상/메모) 수집·정규화 | 자동 | 웹 업로드 우선, 추후 카카오채널 연동 |
| **Asset Store** | 원본+생성물 파일 저장 | 자동 | 로컬→S3/R2 |
| **Generate** | 1소재 → 채널별 콘텐츠 생성 | 자동(AI) | Claude(텍스트)+이미지/영상+FFmpeg |
| **Review Queue** | 생성물 승인/반려/수정 | **수동(핵심)** | 운영자 대시보드 |
| **Publish Orchestrator** | 승인분을 채널 어댑터로 분배·재시도 | 자동 | 멱등성·실패격리 |
| **Channel Adapters** | 채널별 발행 구현 | 채널마다 | 인터페이스 뒤 격리 |
| **Scheduler** | 예약 발행·반복 작업 | 자동 | DB 큐로 단순 시작 |
| **Analytics** | 채널 인사이트 수집·리포트 | 자동 | 도달/조회/예약전환 |
| **Tenant/Auth** | 가게·사용자·채널토큰(암호화) 관리 | — | 멀티테넌트 핵심 |

---

## 3. 채널 어댑터 — 가능 여부 못박기 (조사 기반)

| 채널 | 방식 | 자동화 | 근거 |
|---|---|---|---|
| 인스타그램 | Meta Graph API (Business/Creator) | ✅ 자동 발행/예약 | 공식 콘텐츠 발행 API, 하루 100건 한도, 릴스 90초 |
| 유튜브 숏 | YouTube Data API (`publishAt`) | ✅ 자동 업로드/예약 | 쿼터 1만/일, 업로드 1,600 → ~6개/일 |
| 네이버 블로그 | **반자동** (AI 초안 → 사람 발행) | ⚠️ 수동 발행 | 공식 발행 API 없음. 매크로=약관위반·저품질 제재 |
| 카카오 알림톡 | 비즈메시지(공식 딜러) | ✅ 자동(정보성) | 템플릿 사전심사, 정보성만. 광고성은 수신동의 |

> **법적 라인:** 예약확인·리마인드·노쇼방지 = 정보성(자유). 할인·이벤트·재방문 유도 = 광고성(사전 수신동의 필요, 단 거래 후 6개월 내 기존고객 예외, 야간 21–08시 별도 동의).

---

## 4. 데이터 모델 (초안)

```
Tenant(가게)        id, name, industry(업종), region, created_at
User(운영자/사장)    id, tenant_id, role, auth...
ChannelAccount      id, tenant_id, channel(ig|youtube|naver|kakao),
                    access_token(enc), refresh_token(enc), meta(json), status
Asset(원재료)        id, tenant_id, type(image|video), path, note(메모), created_at
ContentPiece(생성물) id, tenant_id, asset_id, channel, kind(caption|short|blog|alimtalk),
                    payload(json: text/해시태그/영상경로/자막...), status(draft|approved|rejected|published),
                    scheduled_at, created_at
Publication(발행이력) id, content_id, channel, external_id, published_at, result(json), error
MessageConsent      id, tenant_id, customer_ref, marketing_opt_in, opted_at  # 광고성 발송 합법성
Metric              id, content_id|publication_id, channel, kind(reach|view|like|booking), value, at
```

---

## 5. 기술 스택 (현실·빠른 구축 기준)

- **언어/프레임워크:** Python + **FastAPI** (기존 경험 + Claude Python SDK 친화).
- **AI:** Anthropic **Claude (`claude-opus-4-8`)** = 텍스트(캡션/스크립트/블로그/알림톡 문구). 이미지/영상 생성은 외부 API + **FFmpeg**(자막·조립).
- **DB:** SQLite(시작) → Postgres/Supabase(확장).
- **큐/스케줄:** DB기반 잡 테이블(시작) → Redis/RQ(확장).
- **스토리지:** 로컬(시작) → S3/Cloudflare R2.
- **프론트(운영자 대시보드):** 서버렌더(시작) → React(확장).
- **배포:** Docker.
- **시크릿:** 환경변수, 채널 토큰은 DB에 암호화 저장(절대 평문/소스 하드코딩 금지).

---

## 6. "만들 것 vs 가져올 것" (해자는 코드가 아니라 서비스 레이어)

| 레이어 | 전략 | 비고 |
|---|---|---|
| **코어 오케스트레이션** | **직접 구현** | 우리 자산. 라이선스 깨끗하게 |
| 발행(스케줄) | 직접 어댑터 (공식 API) 또는 Postiz 자가호스팅 참고 | Postiz=AGPL, 코어와 분리 |
| 숏폼 영상 조립 | FFmpeg + 자체 로직, 무라이선스 repo는 **읽기 참고만** | 복붙 금지 |
| 알림톡 클라이언트 | MIT 라이브러리 참고/이식 | `posquit0/...` MIT |
| **업종특화 템플릿·촬영가이드·영업·관리** | **직접 (= 진짜 해자)** | 누구도 못 베끼는 부분 |

`references/` 에 가져온 코드:
- `references/MIT/node-kakao-alimtalk-bizmsg` (MIT) — 알림톡 발송 패턴 참고
- `references/MIT/Free-AI-Social-Media-Scheduler` (MIT) — AI 스케줄러 구조 참고
- `references/MIT/tiktoka-studio-uploader` (MIT) — 영상 벌크 업로드 참고
- (라이선스 없는 repo는 가져오지 않음 — 읽기만)

---

## 7. 단계별 구축 로드맵

- **Phase 0 (지금):** 아키텍처 확정 + 골격 + 도메인 모델 + 어댑터 인터페이스(스텁).
- **Phase 1 (MVP):** 1업종 선정 → `Ingest(웹 업로드) → Generate(캡션+해시태그) → Review → IG 1채널 발행`. 가장 얇은 수직선 1개.
- **Phase 2:** 유튜브 숏(영상 자막조립) + 네이버 초안 export 추가.
- **Phase 3:** 알림톡(예약/리마인드 정보성) 연동 — #7 본체와 결합.
- **Phase 4:** 성과 리포트 + 다가게 운영(멀티테넌트) + 과금.

---

## 8. 리스크·주의 (환각 없이)

- 채널 API 정책·한도는 수시 변경 → 어댑터로 격리해 충격 최소화.
- 도달/조회수는 **자동화 불가** — 시스템은 "생산+발행"을 자동화할 뿐, 성과는 콘텐츠 질·실제 가게 소재에 달림.
- 네이버 자동발행 금지(저품질). 반자동만.
- 광고성 메시지 = 정보통신망법 준수(동의/표기/야간).
- 토큰·개인정보 암호화·최소수집.
