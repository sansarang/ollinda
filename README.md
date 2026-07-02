# shopcast (가칭)

소상공인 멀티채널 마케팅 자동화 — **"사진 1장 + 한 줄 메모 → 인스타·유튜브숏·네이버블로그 콘텐츠 생성 → 검수 → 발행 → 알림톡"** 반자동 시스템.

- 아키텍처: [`ARCHITECTURE.md`](ARCHITECTURE.md)
- 외부 코드 출처/라이선스: [`references/MIT/LICENSES.md`](references/MIT/LICENSES.md)

## 핵심 원칙
- 반자동(AI 80% + 사람 검수 20%) — 완전자동은 도달 안 나오고 네이버 저품질 제재.
- 채널/생성기는 어댑터 플러그인. 코어는 라이선스 깨끗하게.
- 해자는 코드가 아니라 **업종특화 + 영업/관리** 서비스 레이어.

## 구조
```
app/
  domain/models.py      도메인 모델/열거형
  adapters/             채널 발행 어댑터 (instagram/youtube/naver_blog/kakao_alimtalk)
  generators/           콘텐츠 생성기 (text_claude ...)
  services/             generate / publish (오케스트레이션)
  registry.py           채널↔어댑터, 종류↔생성기 매핑
  main.py               FastAPI 엔트리
references/MIT/          상업적 사용 가능한 외부 레퍼런스(별도 보관)
```

## 실행 (Phase 0 골격)
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
# 확인: http://127.0.0.1:8000/health , /pipeline/demo
```
`ANTHROPIC_API_KEY` 없으면 생성기는 더미 텍스트로 동작(골격 검증용).

## 로드맵
- Phase 1 (MVP): 1업종 · 웹업로드 → 캡션생성 → 검수 → 인스타 1채널 발행
- Phase 2: 유튜브 숏(영상 자막조립) + 네이버 초안 export
- Phase 3: 알림톡(예약/리마인드) 연동
- Phase 4: 성과 리포트 + 멀티테넌트 + 과금
