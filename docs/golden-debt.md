# 골든 박제 부채 표 (2026-07-19 ~ 08-02 수정분 전수 감사)

기준: 수정을 **되돌리면 실패하는** 테스트가 있으면 박제됨(✅). 없으면 부채(❌).
감사 범위는 `git log --since=2026-07-19` 전 커밋. 기능 추가는 회귀 위험이 있는 것만 표에 올렸다.

착수 전 59건(1건 적색) → 현재 **115건 전부 green**.

## 1순위 — 발행 산출물 직결

| # | 수정 내용 | 파일 | 커밋 | 박제 |
|---|---|---|---|---|
| 1 | 영상 자막 겁주기 차단(생성·검사 단일 소스) | generators/video.py, seo.py | 33c5c2d, e070c3a | ✅ subtitle_rules, pipeline_sweep |
| 2 | 훅 위반이 영상 전체를 죽이던 버그(소프트 강등) | generators/video.py | 33c5c2d | ✅ subtitle_rules |
| 3 | 자기참조 자막 금지('이 글', '본문에서 확인') | generators/video.py | 43c1016 | ✅ subtitle_rules |
| 4 | 화면-자막 일치 + 구매 순서 배열 | generators/video.py | 4368dcb | ✅ subtitle_rules |
| 5 | 자막 조각 완결성(라벨·짝없는 인용·조사 종결) | generators/video.py | 03cfdbe, cc3f89d | ✅ subtitle_rules |
| 6 | 겁주기 채점 누락(88점 통과) | seo.py | e070c3a | ✅ pipeline_sweep |
| 7 | 지역 토큰 중복('부산 부산') | seo.py | e070c3a | ✅ pipeline_sweep |
| 8 | 날조 탐지 오탐 2종(쉼표 건너뛰기·수사적 나열) | seo.py | 48efd14 | ✅ body_contracts |
| 9 | 제목 화자 규칙 + 타깃 키워드 예외 + 부분어 제외 | seo.py | 15c5caa | ✅ body_contracts |
| 10 | 화자는 파는 쪽(손님 시점은 과교정이었다) | seo.py | 15c5caa | ✅ body_contracts |
| 11 | 손님 말로 검색어(공급자 접미어 실측 축약) | seo.py | 16ee281 | ✅ body_contracts |
| 12 | 지역 축약형 사용('부산광역시' 검색량 0) | seo.py | 4a8f961 | ✅ body_contracts |
| 13 | **도 단위 축약 결함('경상남도'→'경상남')** | seo.py | 40f48e8 (신규) | ✅ body_contracts |
| 14 | 지역 정합 게이트(김해썬팅 실사고) | seo.py | 2e56bdf | ✅ body_contracts (fail-open) |
| 15 | 검색량 5개 배치(20개 넘기면 앞 5개만) | services/searchad.py | 3bb3da4 | ✅ body_contracts |
| 16 | 타임아웃을 출력 예산 비례로(90초 고정 사망) | llm.py | 0fde111 | ✅ rewrite_contracts |
| 17 | thinking은 2000토큰 이상에만 | llm.py | 0fde111 | ✅ rewrite_contracts |
| 18 | stop_reason 스레드별 귀속(오탐·병렬 오염) | llm.py | 0fde111, c6ac735 | ✅ rewrite_contracts |
| 19 | 빈 응답을 '예산 부족'으로 오해하던 재시도 폭풍 | llm.py | 1356b29 | ✅ pipeline_contracts |
| 20 | 보정이 [사진N] 마커를 날조하던 실사고 | services/qualitycheck.py | 9918e05 | ✅ rewrite_contracts |
| 21 | 헤드 키워드 동사 직결 금지(자연 프레임) | generators/text_claude.py | 9194843 | ✅ rewrite_contracts |
| 22 | 검색어를 지어내지 않고 실검색어로 확장 | services/queryscout.py | 6de7604 | ✅ queryscout_contracts |
| 23 | 후보 판정 부분일치(복합어 전멸 방지) | services/queryscout.py | b6c0bde, eaf21e1 | ✅ queryscout_contracts |
| 24 | 도메인 용어 씨앗·주제어 게이트 합류 | services/queryscout.py | 076ed3b, 145e55c | ✅ queryscout_contracts |
| 25 | 검색량 관문(수요 0 문장 배제) | services/queryscout.py | 3a57ab6 | ✅ queryscout_contracts |
| 26 | 셀러 편향 제거(업태별 축) | services/queryscout.py | 1840ecf | ✅ queryscout_contracts |
| 27 | 템플릿 제목 배제 · 토큰 정제(조사·마크업) | services/queryscout.py | 831127b, 5b2e665, ae014e8 | ✅ queryscout_contracts |
| 28 | 활용형('있었어요') 도메인 용어 혼입 차단 | services/queryscout.py | 85b1202 | ✅ queryscout_contracts (불용어·어미) |
| 29 | **축 필터가 업종명 원형만 요구(캐시 콜드 시 전멸)** | services/queryscout.py | 8194890 (신규) | ✅ queryscout_contracts |
| 30 | **'중고차 중고차판매' 류 겹침 조합** | services/queryscout.py | 8194890 (신규) | ✅ queryscout_contracts |

## 2순위 — 데이터 안전·비용·상태

| # | 수정 내용 | 파일 | 커밋 | 박제 |
|---|---|---|---|---|
| 31 | 삭제된 글 부활 차단(묘비) | db.py | 4cc9f39 | ✅ pipeline_contracts |
| 32 | 묘비 소유 검증(크로스 테넌트 동결 취약점) | db.py | c6ac735 | ✅ pipeline_contracts |
| 33 | 요청하지 않은 플랫폼 렌더 금지 | services/ingest.py, video.py | 1042b0d, 2aed59f | ✅ pipeline_contracts |
| 34 | 워치독 기본 OFF | services/ingest.py | 4f5d6d9 | ✅ pipeline_contracts |
| 35 | 크레딧 소진 시 전면 중지 | llm.py | ed74158 | ✅ pipeline_contracts |
| 36 | 영상 성공을 요청 플랫폼별로 판정 | services/ingest.py | 5d8807e, fb84935 | ✅ ops_contracts |
| 37 | 사진 상한 9장(씬 상한과 일치) | services/ingest.py | b0f255d | ✅ ops_contracts |
| 38 | 품질 게이트 시간 상한 | services/qualitycheck.py | a377242 | ✅ ops_contracts |
| 39 | AI 무빙 QC가 불량/검사불가 구분 | media/ai_clip.py | cb1b98e | ✅ ops_contracts |
| 40 | TTS env 공백 방어 | media/tts.py | 0be5cdc | ✅ ops_contracts |
| 41 | **configured()가 공백 키를 '설정됨'으로 읽음** | media/tts.py | fc3b300 (신규) | ✅ ops_contracts |
| 42 | is_demo를 테이블에서 직접 읽기 | main.py | cf9a2ea | ✅ ops_contracts |
| 43 | 유령 잡 필터(영상 2시간·다시쓰기 10분) | main.py | 53a1576, 9848c52 | ✅ ops_contracts |
| 44 | **유령 생성 잡이 배포를 영구 차단** | main.py | 251593e (신규) | ✅ ops_contracts |
| 45 | 노출 판정 허위 표시 제거(블록명 금지) | services/exposure.py | 5c5f348 | ✅ exposure_truth |

## 3순위 — 아직 박제하지 않은 것 (정직 보고)

| 수정 내용 | 커밋 | 박제 안 한 이유 |
|---|---|---|
| 지면 판정 정확도(리다이렉트 해독·블록 귀속) | 051b21d | 사장님이 '블록 귀속 정확도는 후속 별건'으로 분류. 그 작업에서 함께 박제한다. |
| 지면 정찰·야간 크론·실시간 발행 감시 | aaac5f4, b31292b, 73aa125, 053fb23, b42744d | 로컬 맥에서 Playwright로 도는 도구. 서버 테스트 환경에 브라우저가 없어 단위 박제 불가 — 별도 스모크가 필요하다. |
| 순서 역전 수정(검색량 선별 후 순위 조회) | 500ad10 | 실행 순서 계약이라 외부 API 2종을 동시에 흉내내야 한다. 다음 회차. |
| 전환 퍼널·요금제·제품설명서·영상 사진 선택 UI | b2d0447, 60db752, 70a89b1, 3630bcc, b7d5b60 | 화면 계열. 회귀 시 눈에 바로 보이고 발행물을 훼손하지 않는다. |
| Dockerfile assets 복사·가입자 수치 정직화 | 5a22b1f, 350f27b | 배포 산출물·집계. 다음 회차. |
| 고객 피드백 오분류 | 163e654 | 집계 표시 계열. 다음 회차. |

## 규칙

- 기존 파일을 재작성·교체할 때는 그 파일을 무는 골든 전체 통과를 커밋 조건으로 한다.
- 골든은 **문구가 아니라 규칙의 실체**를 물어야 한다. 표현을 물면 개선이 곧 실패가 된다
  (2026-08-02 실사고: `test_human_touch`가 옛 문구 "문장 길이"를 요구해 07-27부터 적색 방치).
- 새 수정은 같은 커밋에서 박제한다. 이 표에 행이 늘어나되 ❌로 남지 않게 한다.
