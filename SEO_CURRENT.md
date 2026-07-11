# SEO_CURRENT — 현재 상위노출·업종주입 로직 실측 (수정 전 분석)

콘텐츠생성 개선 PHASE 0. 코드에서 실측한 "블로그 글이 상위노출되게 어떻게 작성되는가".

## 1. 상위노출 로직 (seo.py → 프롬프트 주입)

**주입 경로**: `BlogDraftGenerator.generate()`(`app/generators/text_claude.py`)가 프롬프트를 조립할 때
seo.py의 디렉티브 상수를 문자열로 이어붙인다:

| 디렉티브 | 내용 | 주입 위치 |
|---|---|---|
| `BLOG_DIRECTIVES` | C-Rank(전문성40·지속성30·반응20·품질10)+D.I.A. 설명, 1인칭 경험체, 제목 키워드 맨앞 25~35자, 첫 문장 키워드, 연관어 2~3, FAQ 1개, 1200~1800자, ##3~5, 키워드 4~6회, 표/목록, [사진N], 저품질 금칙어 | 본문 프롬프트 중반 |
| `BLOG_SELL_STRUCT` | PAS 3줄 오프닝, FAB 번역, BAB 손님 스토리, 반론 선제(FAQ), CTA 계단, 스마트블록 의도별 ## | 〃 |
| `HOOK_RULE` | 훅 공식 4종(결과/손실회피/호기심갭/숫자) — 영상·캡션 쪽 | 캡션·영상 |
| `COPY_PSYCH`/`FACTS_RULE` | 손실회피·구체성·당신화법 / **날조 금지**(없는 가격·스펙·수치 금지, 과장어 금지) | 전 채널 |
| `blog_angle_directive` | 후기/방법/가격 앵글(스마트블록 다중진입) | asset.angle 있을 때 |
| `blogtpl.sequence_directive` | 매장형/셀러형 글 구조 시퀀스(고정정보 자동삽입 통지 포함) | 〃 |
| 키워드 밀도 가드 | "핵심키워드 정확히 3~5회, 첫 문장 1회, 유의어 확장" + 생성 후 `_kw_density` 검증(ok/low/over) | 프롬프트 + 후처리 |

**quality_audit 채점**(`seo.quality_audit`, 100점 감점식):
- 날조 탐지: 출력의 금액·%·수치를 입력(source)과 대조 — 입력에 없으면 -8/건(최대 -20)
- 과장어(`RISKY_EXPRESSIONS` 19종) -6/개(최대 -25), 느낌표 남발 -5, 키워드 6회+ 도배 -10
- 블로그 전용: 제목에 핵심키워드 없음 -12, 첫 문단 없음 -6, 본문 2회 미만 -6, FAQ 없음 -4,
  1000자 미만 -15, ## 없음 -5, [사진1] 없음 -5, **경험 표현(_EXPERIENCE_WORDS 9종) 없음 -12**,
  숫자 5개 미만 -6(구체성), 사진 마커 4개 미만 -4
- `HARD_BLOCK_EXPRESSIONS`(완치·완전무사고 등)는 감점이 아닌 **자동발행 차단**
- 85점 미만이면 `editor.polish`가 경고 기반 1회 리라이트(저점수만, LLM 1콜)

## 2. 업종별 주입 (industries.py)

- **프리셋 6종**(`ACTIVE_INDUSTRIES`): tinting·usedcar·clothing·hair·restaurant·cafe.
  각 프로필: `persona`(말투), `tone`, `hashtag_seeds`, `content_angles`, `photo_guide`, `cta`,
  `cautions`(법규), `pain_points`(고객 고민→PAS 재료), `trust_signals`(신뢰 신호), `example_copy`(few-shot),
  `viral_hooks`(usedcar만 — 폭로/반전솔직/교육형 9종).
- **주입 방식**: `resolve_industry(자유문자열)` → 별칭 부분매칭 → 프롬프트에
  `[페르소나] {persona}` + `industry_brief(p)`(고객고민·신뢰요소·예시문구·바이럴훅·법규주의 블록).
- **없는 업종**: `ensure_profile()`이 가게 등록 시 Claude 1콜로 프로필 생성 → `industry_profiles` 테이블 캐시.
  생성 실패/무키면 `GENERIC`(범용 톤). 즉 미정의 업종도 페르소나·고객고민이 자동 생긴다 —
  단 **AI 생성 프로필에는 viral_hooks가 없다**(프리셋 usedcar만 보유).

## 3. 키워드 (searchad 실검색량 반영)

- `target_keywords()`: 지역 다중 granularity(시+구 / 구 / 동) × 의도어(`_INTENTS`: 추천·후기·가격·비용·잘하는곳…)
  조합 + 메모의 명사 추출 → `_apply_volume()`이 **searchad 실검색량**으로 재정렬
  (`sweet_spot_keywords`: 월 500~5,000 롱테일 우선, 힌트 2글자 겹침 노이즈 필터, 24h TTL 캐시).
- `keyword_plan()`: 대표 1개(headline=제목 맨앞 강제) + 롱테일 2~3개(## 소제목에 배치 지시 — 스마트블록 다중진입).
  searchad 무키면 규칙 기반 폴백에 `estimated` 플래그.
- 셀러는 `product_keywords`(상품·후기·내돈내산 의도축)로 축 전환(`strategies.keyword_axis`).
- 진단(target_kw) 유입 시 kw0을 그 키워드로 교체(상위노출 PHASE 1).

## 4. 실제 데이터 흐름 (사진→블로그)

```
업로드(사진 + note≤50자 + photo_desc≤120자 + 목적/타겟/추가)          [main.py upload / api_demo]
 └ ingest_upload(): photo_boost 보정+EXIF·GPS → asset.note = 조합 텍스트
    ├ vision.analyze_all(최대 6장, 1콜): "[사진N] 보이는 것·글자·포인트" → note에 append
    ├ strategist.build_brief(1콜): note 전체 → JSON(앵글·훅·핵심키워드·타겟·셀링포인트) 
    │   → brief_to_directive() → note에 append ("모든 채널이 반드시 따를 것")
    ├ db.improving_keywords(): 순위 오른 키워드 역주입 → note에 append
    └ generate_for → BlogDraftGenerator:
        resolve_industry(프로필) + target_keywords/keyword_plan(실검색량)
        → 프롬프트 = 가게정보 + 페르소나 + industry_brief + [입력 정보]=asset.note
          + speaker_frame + 키워드 + closing + 템플릿 시퀀스 + BLOG_DIRECTIVES + … + FACTS_RULE
        → 생성 → 제목 3안 중 자동선택(_pick_title) → 고정정보 블록 삽입 → FAQ 보강
        → _kw_density 검증 → quality_audit 채점 → polish(85점 미만 리라이트)
```

무료(teaser.py)도 동일 파이프라인이되 **note가 더 빈약**: 업종 + 목적 + (선택)사진뿐.

## 5. 약점 진단 — "입력이 적어서 생기는 품질 한계"의 발생 지점

1. **입력 병목이 구조적**: 유료 폼 note는 50자 컷(`main.py upload: user_req[:50]`), photo_desc 120자.
   무료 위젯은 업종+목적뿐(`landing demoForm`). D.I.A.+가 요구하는 1차 경험 재료
   (가격·소요시간·손님 반응·작업 포인트·보증)가 **입력 단계에 존재하지 않는다**.
2. **지시문·채점은 경험을 요구하는데 재료가 없다**: `BLOG_DIRECTIVES`/`quality_audit`은 1인칭 경험(-12)·
   구체 수치(-6)를 요구하지만, 재료가 없으면 LLM은 (a) FACTS_RULE 준수 → 생략·일반론(뻔한 글) 또는
   (b) 경험 '표현'만 흉내(날조 위험, `_money_nums` 탐지에 걸리면 재감점). **감점과 날조 사이의 구조적 긴장.**
3. **vision은 확인 절차가 없다**: `analyze_all` 결과가 틀려도(차종·메뉴 오인) 그대로 브리프→본문 전제가 된다.
   추측이 note에 "사실"로 각인되는 단일 경로(`ingest.py asset.note`).
4. **업종 지식이 질문으로 이어지지 않는다**: `trust_signals`(예: 썬팅=필름 등급·보증기간)는 "녹여라"
   지시로만 쓰이고, **그 가게의 실제 값**(무슨 필름? 보증 몇 년?)을 묻는 단계가 없다 → 신뢰 신호가 추상 표현으로만.
5. **vision이 '왜/어떻게'를 못 본다**: 사진엔 결과만 있고 과정·이유(손님이 온 이유, 작업에서 신경 쓴 점)가
   없다 — 이것이 D.I.A.+ 경험서술의 핵심 재료인데 수집 채널 자체가 없음.

→ **개선 방향(A. 스마트 입력 엔진)**: vision 추측을 사용자에게 확인시키고, industries.py의
trust_signals/pain_points를 그 가게의 실제 값을 묻는 3~4개 질문으로 변환, 경험 1문장을 유도해
strategist·blog 프롬프트에 구조적으로 주입한다. 정보가 없으면 지금처럼 정직하게(날조 금지).
