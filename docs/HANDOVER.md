# 인수인계 — 30분 안에 전체를 이어받는 문서

> 이 문서 하나로 "지금 무엇이 돌고 있고, 어디를 만지면 되는가"를 파악한다.
> 원칙은 `CLAUDE.md`, 사건 계보는 `@docs/lessons.md`, 부채 표는 `@docs/golden-debt.md`.

**2026-08-03 신축 종료.** 이후 코드 변경은 세 경로로만 연다:
① 버그 수정 ② gowatch·adaptation 실측이 요구하는 개선 ③ 사용자 승인 제안.

---

## 1. 시스템 지도

| 서비스 | 역할 | 계약 | health |
|---|---|---|---|
| **shopcast** | 본체 — 생성·게이트·발행·측정·화면 전부 | FastAPI + SQLite(로컬 캐시), 배포=Railway `git push origin main` | `https://ollinda.kr/health` |
| **gorender** | 영상 렌더 분리 백엔드(선택) | `GORENDER_URL`(내부망). 미설정이면 shopcast가 직접 ffmpeg | `app/services/render_backend.py` |
| **gowatch** | 외부 관측 — 발행 글의 변화 감지 | `GOWATCH_URL`+`GOWATCH_TOKEN`. 미설정이면 소비 자체를 건너뜀 | `app/services/gowatch_client.py` |
| **blurworker** | 사진 마스킹(번호판·개인정보) | `app/services/blur_client.py` — 실패해도 생성은 진행 | — |
| **Postgres** | (예약) 현재 본체는 SQLite | 승격은 미결 목록 참조 | — |

**로컬 맥북도 시스템의 일부다.** 지면 정찰(Playwright)은 서버가 아니라 맥에서 돈다 —
`~/business/insight/{blocks,nightly,trackpub,livewatch}.py`. **노트북이 꺼지면 지면 지도가 낡는다.**

---

## 2. 루프 도해 — 진입점 파일

```
① 정찰      지면 지도            ~/business/insight/nightly.py → /admin/blocks-ingest
                                  → kw_blocks(tenant, keyword, blog_blocks, mine, checked_at)
② 빈자리     자리 있음+우리 없음   app/services/gapscout.py  scan()
③ 영역 대조  확실/인접/미지/제외   app/services/gapscout.py  classify() · owner_domain()
                                  근거: 발행 이력·과거 세트·경험 Q&A(+재고) + 업종 스키마 축
④ 글감      확실만 큐에            gapscout.feed()  → writing_queue(source='vacant', 주 2건)
                                  ※ 재료(경험) 없으면 넣지 않고 질문을 돌려준다
⑤ 생성      키워드 단일 관문       app/seo.py resolve_target_keyword()
                                    → _surface_first(지면) → _gap_first(빈자리·소재 뒷받침)
                                  본문: app/generators/text_claude.py
                                  실경험 자동 주입: app/services/generate.py generate_for()
⑥ 게이트    정직·규격·점수         app/services/qualitycheck.py score_gate()
                                  app/services/mass.py industry_gate()
⑦ 영상      온디맨드               app/generators/video.py  _naver_video()
                                  화면-자막 일치: _lines_for_photos(order_ref=원본 순서)
                                  파는 말로 교체: _selling_lines()
⑧ 발행      사장님이 누른다         app/services/publish.py  (자동 발행 없음)
⑨ 측정      4지면                  app/services/exposure.py summary()
                                  순위: app/services/ranktrack.py · race.py
⑩ 학습      개선 카드              app/services/adapt_consume.py ← gowatch
```

**키워드는 `seo.resolve_target_keyword()`, 지역은 `seo.canonical_region()`, LLM은 `app/llm.py`만 거친다.**
관문 밖에서 결정하면 규칙이 갈라진다(2026-08-02 실사고: 빈자리 승격이 다른 분기로 샘).

---

## 3. 운영 루틴 — 누가 무엇을 하는가

| 사장님이 하는 것 | 시스템이 하는 것 |
|---|---|
| 사진 올리기 | 사진 분석·보정·마스킹·R2 미러 |
| 경험 한 줄 답하기(**한 번만**) | 키워드에 맞는 답변을 매 글마다 자동으로 꺼내 씀 |
| 확인 카드에 답하기("해요/안 해요") | 영역 프로필 학습 — 다시 묻지 않음 |
| **발행 버튼 누르기** | 글·영상·키트 준비, 게이트 통과분만 발행 대상으로 |
| 영상 보고 눈으로 판정 | 화면-자막 일치·자막 완결성 기계 검사 |
| — | 지면 정찰·빈자리 판정·순위 추적·색인 확인·주 1회 보고 |

**자동 발행은 없다.** 시스템은 큐에 넣는 것까지 한다.

---

## 4. 사건 계보 3줄 요약

> 전문은 `@docs/lessons.md`.

1. **레이/캐스퍼(2026-07-27)** — 세트마다 키워드를 다시 정하다 다른 차종이 캡션에 섞였다.
   → **세트 = 한 소재 = 한 키워드.** 키워드는 한 번만 정하고 전 채널이 공유한다.
2. **업종명 오용(2026-08-01)** — '중고차판매'(6,580회)로 검색어를 잡았다. '중고차'는 271,600회.
   → **업종명은 공급자 용어다.** 손님이 치는 말로 바꾼다(`searcher_term`).
3. **측정 허위(2026-08-02)** — 만든 적 없는 지면에 "보이는 중"이라고 표시했다.
   → **정직 게이트는 측정에도 적용된다.** 검증 안 된 정밀함보다 두루뭉술한 사실이 낫다.

**반복된 실수 하나**: 확인할 수 있는데 확인하지 않고 말했다.
기록이 잘못된 매핑으로 만들어지면 기록도 거짓말을 한다 — **영상은 프레임을 직접 뽑아 본다.**

---

## 5. 미결·예약 — 착수 조건

| 항목 | 지금 상태 | 착수 조건 |
|---|---|---|
| **B-1 렌더 자립화**(gorender 분리) | `GORENDER_URL` 미설정 시 본체가 렌더 | 렌더가 본체 응답을 막을 때 |
| **순위 추적 병렬화** | 순차 조회 | 추적 키워드 50개 이상 |
| **블록 귀속 복원**(블록명 표시) | 표시 금지(미검증) | 귀속 정확도 검증 완료 후 |
| **커스텀 도메인** | ollinda.kr | 사장님 요청 시 |
| **Postgres 승격** | SQLite | 동시 쓰기 경합 또는 다중 인스턴스 필요 시 |
| **업종 편향 3곳** | 아래 참조 | 자동차·시공 외 업종 실제 운영 시작 시 |

### 🔬 브리핑 역설계 — 보류(2026-08-06 중단, 완주 아님)

`app/services/reverse/` · `/admin/reverse/*`

**왜 멈췄나**: AI 브리핑은 **정보성 질의에만 뜬다**(실측: '자동차 썬팅 농도 기준'에는
`aipickItem` 10건, '부산 동구 썬팅업체'에는 0건). 매출은 **상업성 질의**에서 나므로
브리핑을 완벽히 파도 파는 질의엔 안 뜬다. 방향을 매출 지면(`coexpose`)으로 틀었다.

**남긴 자산**(삭제 금지 — 정보성 콘텐츠용으로 재개 가능):
- `surfaces.py` — `data-template-id`로 지면을 가르는 축. h2/h3가 아니다.
- `BRIEF_JS` — 브리핑 답변 섹션의 **글 단위 인용 출처** 추출.
  `aipickItem`은 브리핑 출처가 아니라 **채널 소개 카드**다(링크 0, 인용수만).
- `contrast.py` — Welch t + 표본 부족이면 미확정 처리.

**동결 상태**: 인자는 하나도 확정되지 않았다(인용군 4건). 미확정인 채로 둔다.
표본 확장·인자 확정은 하지 않는다. 확정된 척 쓰지 마라.

### 업종 편향이 남은 3곳 (2026-08-03 감사)

1. `app/generators/video.py` `_PARTW` — 영상 사진 순서 판정에 자동차 낱말(엔진·시트·타이어…)
2. `app/services/gapscout.py` `_Q_BY_ANGLE` — 경험 질문에 "작업하고 나서"(시공업 말투)
3. `app/db.py` `inventory_context(model, year, car_class)` — 차량 모양 컬럼(영역 판정 **보조** 출처)

**나머지는 전부 데이터·언어 규칙으로 동작한다**(업종 스키마 축·검색량·문서수·한국어 어미).

---

## 6. 되풀이하지 말 것 — 운영 규율

- **push는 `scripts/safe-push.sh`로만.** 맨손 `git push` 금지 — 배포가 진행 중 작업을 죽인 사고 3회.
- **수정은 테스트와 함께 산다.** 되돌리면 실패하는 골든이 없으면 다음 세션에 사라진다.
- **골든은 문구가 아니라 규칙의 실체를 문다.** 표현을 물면 개선이 곧 실패가 된다.
- **조용한 실패 금지.** 실패는 사유를 남겨 진단으로 읽히게 한다 — 오늘 하루 버그 추적의 90%가 이것 때문이었다.
- **영상 검증은 프레임 대조.** 기록만 보면 기록의 잘못을 못 본다.
