"""
SEO/성과 엔진 — 플랫폼별 '잘 팔리고 잘 노출되는' 콘텐츠 설계 규칙.
- target_keywords: 지역+업종+검색의도 기반 타겟 키워드(LLM 없이도 결정적 생성).
- *_DIRECTIVES: 각 플랫폼 성과/SEO 베스트프랙티스(프롬프트에 주입).
이 규칙이 곧 제품의 '성과 차별화'. 키워드는 검색량 있는 롱테일을 노린다.
"""
from __future__ import annotations

import re

# 검색 의도 수식어(구매 직전 키워드 = 전환율 높음). 3어절 롱테일 = 경쟁↓·전환↑(검색량 500~5,000 구간 노림).
_INTENTS = ["추천", "후기", "가격", "비용", "잘하는곳", "예약", "위치", "실력"]


# 온라인 셀러용 구매 직전 검색 의도(상품축)
_PRODUCT_INTENTS = ["추천", "후기", "내돈내산", "사용기", "단점", "비교", "가성비"]


import time as _time

_vol_cache: dict = {}
_VOL_TTL = 24 * 3600   # 검색량은 월간 지표 → 24h TTL(프로세스 수명 lru_cache 대체, PHASE 6)


def _volume_boost_cached(hints_key: str) -> tuple:
    """네이버 검색광고 API로 실검색량 스윗스팟(500~5,000) 키워드(24h TTL 캐시). 무키/실패 시 빈 튜플."""
    now = _time.time()
    ent = _vol_cache.get(hints_key)
    if ent and (now - ent[0]) < _VOL_TTL:
        return ent[1]
    try:
        from app.services import searchad
        res = (tuple(searchad.sweet_spot_keywords([h for h in hints_key.split("|") if h]))
               if searchad.configured() else tuple())
    except Exception:
        res = tuple()
    _vol_cache[hints_key] = (now, res)
    return res


def _apply_volume(kws: list[str], limit: int, hints: list[str] | None = None) -> list[str]:
    """검색광고 API 있으면 실검색량 스윗스팟 키워드 2개를 보강(내 지역 키워드는 앞에 유지)."""
    seeds = [h for h in (hints or kws)[:3] if h]
    vol = _volume_boost_cached("|".join(seeds))
    if not vol:
        return kws[:limit]
    extra = [v for v in vol if v and v not in kws][:2]          # 실검색량 신규 키워드 2개
    keep = kws[:max(1, limit - len(extra))]                     # 내 키워드 우선(첫 키워드=지역 유지)
    return list(dict.fromkeys(keep + extra))[:limit]


def product_keywords(note: str = "", brand: str = "", limit: int = 10) -> list[str]:
    """상품/후기축 키워드 — 온라인 셀러용(지역 대신 상품명+구매의도)."""
    kws: list[str] = []
    nouns = [w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", note or "")
             if w not in ("추천", "이벤트", "할인", "후기") and len(w) <= 12]
    # 단어를 쪼개지 말고 '제품 구'로 — 전체 구 + 뒤 2단어(종류어)
    phrase = " ".join(nouns[:3]) if nouns else brand.strip()          # 예: "무선 블루투스 이어폰"
    short = " ".join(nouns[-2:]) if len(nouns) >= 2 else phrase       # 예: "이어폰 노이즈캔슬링"
    heads = [h for h in dict.fromkeys([phrase, short]) if h] or ([brand.strip()] if brand.strip() else [])
    for n in heads:
        for it in _PRODUCT_INTENTS:
            kws.append(f"{n} {it}")
    if brand.strip() and phrase:
        kws.append(f"{brand.strip()} {phrase}")
    seen, out = set(), []
    for k in kws:
        if k and k not in seen:
            seen.add(k); out.append(k)
    return _apply_volume(out, limit, hints=heads)


# 스마트블록 의도별 앵글 3종 — 같은 키워드로 다른 블록 진입(성장 PHASE 7)
BLOG_ANGLES = {
    "review": "[앵글=후기형] 통합검색 '후기' 스마트블록을 노려라. 제목·본문을 1인칭 실제 후기 중심으로"
              "(직접 겪은 상황→과정→만족/아쉬움→추천). 별점·재방문 의사 등 경험 신호를 담아라.",
    "howto":  "[앵글=방법·과정형] '방법/과정' 스마트블록·지식스니펫을 노려라. 단계별(1·2·3) 과정·소요시간·"
              "주의점을 구체 수치로. Q&A 소제목으로 '어떻게'에 정확히 답하라.",
    "price":  "[앵글=가격·비용형] '가격/비용' 스마트블록을 노려라. 가격대·구성·비교 기준을 표로 정리"
              "(단, 입력에 없는 금액은 지어내지 말고 '문의/상담' 유도). 왜 이 가격이 합리적인지 근거 제시.",
}


def blog_angle_directive(angle: str) -> str:
    """의도별 앵글 지시문(후기/방법/가격) — 없으면 빈 문자열."""
    return BLOG_ANGLES.get(angle or "", "")


def posting_cadence_tip(days_since_last: int | None, weekly_target: int = 3) -> str:
    """C-Rank '활동 지속성' 코칭 — 주 N회 발행 권장. 발행 캘린더 안내(성장 PHASE 7)."""
    if days_since_last is None:
        return f"C-Rank는 '꾸준함'에 가점을 줘요. 같은 주제로 주 {weekly_target}회 발행을 목표로 시작해요."
    if days_since_last >= 3:
        return f"{days_since_last}일째 새 글이 없어요. 발행 간격이 벌어지면 C-Rank 신뢰가 식어요 — 오늘 한 편 올려요."
    return f"좋아요! 이 페이스(주 {weekly_target}회)를 유지하면 같은 주제 전문성이 쌓여 상위노출에 유리해져요."


def keyword_plan(industry_name: str, region: str, note: str = "", axis: str = "local", brand: str = "") -> dict:
    """대표키워드 1개(제목) + 롱테일 2~3개(본문 소제목) + 실검색량 여부('추정') — 성장 PHASE 5.
    지역+업종+의도 3요소 조합, 실검색량 500~5,000 롱테일 우선(searchad 주경로, 무키 시 규칙 폴백=추정)."""
    try:
        from app.services import searchad
        estimated = not searchad.configured()
    except Exception:
        estimated = True
    kws = target_keywords(industry_name, region, note, limit=10, axis=axis, brand=brand)
    headline = kws[0] if kws else (f"{region} {industry_name}").strip()
    longtail = [k for k in kws[1:] if k and k != headline][:3]
    return {"headline": headline, "longtail": longtail, "keywords": kws, "estimated": estimated}


def target_keywords(industry_name: str, region: str, note: str = "", limit: int = 10,
                    axis: str = "local", brand: str = "") -> list[str]:
    """키워드 세트. axis='product'면 상품/후기축(셀러), 'both'면 지역+상품 병합, 기본은 지역축."""
    if axis == "product":
        return product_keywords(note, brand, limit)
    if axis == "both":
        merged = product_keywords(note, brand, limit) + target_keywords(industry_name, region, note, limit)
        return list(dict.fromkeys(merged))[:limit]
    kws: list[str] = []
    reg = (region or "").strip()
    ind = (industry_name or "").strip()
    if reg and ind:
        # 지역 다중 granularity — 검색자마다 '동/구/시+구'로 다르게 검색하므로 변형별 키워드 생성
        toks = reg.split()
        variants = [reg]                                          # 부산 동구 초량동
        if len(toks) >= 2:
            variants.append(" ".join(toks[:2]))                  # 부산 동구
        dong = next((t for t in toks if t.endswith(("동", "읍", "면", "가", "리"))), "")
        if dong:
            variants.append(dong)                                # 초량동
        variants = list(dict.fromkeys(variants))
        for v in variants:
            kws.append(f"{v} {ind}")                              # 각 변형 기본
        for v in variants[:2]:                                    # 대표 변형에 의도 결합
            for it in _INTENTS[:4]:
                kws.append(f"{v} {ind} {it}")
    if ind:
        kws += [f"{ind} 추천", f"{ind} 가격"]
    # 메모에서 핵심 명사 추출(신메뉴/차종/시술명 등)
    for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", note or ""):
        if w not in ("추천", "이벤트", "할인") and len(w) <= 12:
            cand = f"{reg} {w}".strip()
            if cand and cand not in kws:
                kws.append(cand)
    # 중복 제거(순서 유지)
    seen, out = set(), []
    for k in kws:
        if k not in seen:
            seen.add(k); out.append(k)
    # 지역+업종 결합 힌트 우선 → 지역 롱테일 실검색량 반영(전국 키워드 혼입 방지, PHASE 6)
    _hints = [h for h in (f"{reg} {ind}".strip(), ind) if h] or None
    return _apply_volume(out, limit, hints=_hints)


# ── 네이버 플레이스(지도) 노출 보조 ──
def place_keywords(industry_name: str, region: str, limit: int = 12) -> list[str]:
    """플레이스 업체명·소개·메뉴·리뷰에 녹일 키워드(지역+업종+의도). 검색 매칭↑."""
    reg, ind = (region or "").strip(), (industry_name or "").strip()
    out: list[str] = []
    if reg and ind:
        out += [f"{reg} {ind}", f"{reg} {ind} 추천", f"{reg} {ind} 잘하는곳",
                f"{reg} {ind} 예약", f"{reg} {ind} 후기", f"{reg} 가까운 {ind}"]
    if ind:
        out += [f"{ind} 추천", f"{ind} 잘하는곳", f"{ind} 가격"]
    seen, res = set(), []
    for k in out:
        if k and k not in seen:
            seen.add(k); res.append(k)
    return res[:limit]


def review_request(tenant_name: str, region: str = "", industry: str = "") -> str:
    """방문자 영수증 리뷰 요청 문구 — 플레이스 노출의 핵심 연료(리뷰 수·키워드)."""
    kw = (f"{region} {industry}").strip() or "후기"
    name = tenant_name or "저희 가게"
    return ("방문해 주셔서 감사합니다! 🙏\n"
            f"도움이 되셨다면 네이버에 '{name}' 검색 → 영수증 리뷰 한 줄만 남겨주세요.\n"
            f"리뷰에 '{kw}' 키워드를 자연스럽게 적어주시면 다른 분들도 더 쉽게 찾을 수 있어요. 💙")


# ── 플랫폼별 성과/SEO 디렉티브(프롬프트 주입) ──
BLOG_DIRECTIVES = (
    "[네이버 상위노출 알고리즘 분석 → 반영 필수]\n"
    "네이버는 C-Rank(주제전문성40%·활동지속성30%·사용자반응20%·품질10%) + "
    "D.I.A.(실제 '경험·후기'를 높게 평가)로 순위를 매긴다. 그래서:\n"
    "- **1인칭 실제 경험·후기체**로 써라('직접 가봤더니', '먹어보니', '시공해보니') → D.I.A 가점.\n"
    "- 한 주제를 깊게(전문성), 곁가지 잡담 금지 → C-Rank 전문성.\n"
    "- 댓글·질문을 부르는 문장 1개(예: '○○ 더 궁금하면 댓글 주세요') → 사용자반응.\n"
    "- 제목: **핵심 키워드를 맨 앞**에(예: '지역+업종+추천/후기'), 25~35자 롱테일. 숫자·연도·혜택 넣으면 클릭↑.\n"
    "- **첫 문장에 핵심 키워드 1회**(검색 의도 즉시 충족, 2~3문장 인트로).\n"
    "- **연관 검색어**(같은 의도의 변형어 2~3개)를 자연스럽게 본문에 녹여라 → D.I.A+ 가점.\n"
    "- **'## 자주 묻는 질문' 섹션 1개**(Q&A 2~3쌍, '저장각' 정보) → 네이버 Q&A·체류 가점.\n"
    "- 분량 1200~1800자, 소제목(##) 3~5개, 타겟 키워드 4~6회(남발 금지).\n"
    "- 신뢰·체류↑: 가격대·찾아오는길·영업시간·주차·예약을 표/목록으로.\n"
    "- [사진N] 마커를 본문 곳곳(체류↑). 마지막 방문/예약 CTA+연락 안내.\n"
    "[저품질·스팸 회피(어기면 3페이지로 추락)]\n"
    "- 과장·낚시·광고성 표현 금지: 최고/최저가/100%/무조건/보장/완벽/대박/강력추천/유일/1위/공짜.\n"
    "- 같은 키워드 7회+ 남발 금지, 복사·짜깁기 금지, 실시간 이슈 억지 삽입 금지, 느낌표 남발 금지."
)

SHORT_DIRECTIVES = (
    "[릴스/쇼츠 알고리즘 분석 → 반영 필수]\n"
    "배포 1위 신호는 '시청 유지(watch time)'. 3초 홀드율 60%+면 도달이 5~10배. "
    "DM 공유(sends)·저장(saves)은 좋아요보다 3~5배 중요. 그래서:\n"
    "- 0~3초 훅: 첫 프레임부터 질문/충격/공감으로 스크롤을 멈춰라('○○ 이거 모르면 손해').\n"
    "- 길이 30~45초(2026 스윗스팟). 15초 이하는 완주 절대량 미달로 도달 붕괴 → 30초 이상 채워라.\n"
    "- 완주율 목표: 30초↓ 65%+, 30~60초 50%+. 끝→처음 루프(재생=새 조회로 카운트).\n"
    "- 직접 만든 나레이션·BGM=원본 오디오(2026 가점, 소규모 계정은 트렌딩 사운드보다 유리).\n"
    "- '저장각' 정보(꿀팁) 1개 + '친구 태그/공유(DM)' 유도 → sends·saves↑.\n"
    "- 제목/설명에 검색 키워드(유튜브=검색엔진), 해시태그 3개.\n"
    "- 자막은 무음 시청 대비 큰 글씨. 마지막 1.5초 명확한 CTA."
)

CAPTION_DIRECTIVES = (
    "[인스타 알고리즘 분석 → 반영 필수]\n"
    "도달 핵심은 watch + sends(DM 공유)·saves. 그래서:\n"
    "- 첫 줄 훅('더보기' 전 노출)로 시선 잡기.\n"
    "- '저장각' 유용함(팁/정보) 1개 + 'DM/공유하고 싶은' 한 줄 → saves·sends↑.\n"
    "- 해시태그는 '분류 라벨'일 뿐(2026) — 정확한 3~5개만. 많으면 도달↓(무해시태그가 나을 때도).\n"
    "- 마지막에 '댓글 질문 1개'(발행 1시간 내 답글=대화신호↑) + 방문/문의 CTA. 과장·낚시 금지."
)

X_DIRECTIVES = (
    "[X 알고리즘] 초반 인게이지먼트 속도가 노출을 좌우. 첫 문장 훅, 한 가지 핵심 메시지, "
    "리트윗/답글 부르는 한 줄, 해시태그 1~2개, 방문/문의 유도. 280자 이내. 과장 금지.\n"
    "⚠️ 2026 핵심: 외부 링크(URL)는 도달 50~90% 감소 → 본문에 링크 넣지 말고 '검색/프로필' 유도. "
    "답글=좋아요의 150배 → 반드시 '질문 한 줄'로 끝내 답글을 유도하라(대화 유발)."
)

# 셀러(상품 판매) 전용 쇼츠/릴스 — '방문'이 아니라 '구매 전환'축
SHORT_DIRECTIVES_SELLER = (
    "[셀러 커머스 영상 — 구매 전환 최적화]\n"
    "이 영상의 목표는 방문이 아니라 '구매(스토어/상세페이지)'다. 판매자가 직접 상품을 보여주는 시연·언박싱 톤(고객 후기 사칭 금지). "
    "쇼츠/릴스 배포 신호(3초 홀드·완주·저장·공유)를 커머스로 노려라:\n"
    "- 0~3초 훅: 문제제기/가격충격/비교('이 가격에 이 퀄은 못 참죠', '○○ 이거 하나면 끝').\n"
    "- 각 장면 = 셀링포인트 1개 시연(소재·기능·사이즈감·활용법). '말'보다 '보여주듯' 묘사.\n"
    "- 사용 전/후(before-after)로 효과를 눈에 보이게.\n"
    "- '장바구니 각이면 저장 ❤️' 저장 유도 1회 → 커머스 저장 신호로 도달↑.\n"
    "- 마지막 CTA는 명확한 구매 안내(프로필 링크 / 스토어 검색어).\n"
    "- 허위 효능·과장 금지(단점 솔직히 한 줄이면 신뢰↑). 길이 30~45초, 자막 큰 글씨."
)

# 정직 원칙 — 모든 생성물(글·영상·상세페이지) 공통. 허위·날조는 '안 만드느니만 못함' + 표시광고법 위반.
FACTS_RULE = (
    "[⚠️ 정직 원칙 — 반드시 지켜라(위반하면 콘텐츠 폐기)]\n"
    "- 입력(메모·사진분석·상품정보)에 '없는' 가격·할인율·수치·스펙·모델명·성분·효능·용량·수상/인증·후기수를 절대 지어내지 마라.\n"
    "- 가격·할인은 입력에 명시됐을 때만 그 값 그대로 써라. 없으면 금액을 아예 언급하지 마라(임의 숫자 금지).\n"
    "- 상품 등급/성능(예: 노이즈캔슬링·방수)과 가격이 안 맞게 쓰지 마라 — 확실치 않은 사실은 쓰지 말고 비워둬라.\n"
    "- 모르는 정보는 '지어내기'보다 '생략'. 추측을 사실처럼 단정하지 마라.\n"
    "- 과장·낚시 금지: 최고/최저가/100%/무조건/보장/완벽/1위/유일/대박.\n"
    "- [🔒 개인정보 보호] 사진분석에 차량 번호판·전화번호·차대번호(VIN)·이름·주소·라벨 숫자가 보여도 "
    "콘텐츠(글·자막·해시태그)에 절대 그대로 쓰지 마라. 특정 개인·차량을 식별할 수 있는 값은 언급 자체를 생략하라."
)

# 훅 — 도달의 80%를 좌우(영상·캡션·X 공통). 3안 구상 후 최강으로 오픈.
HOOK_RULE = (
    "[훅(0~2초 / 첫 줄) — 도달을 좌우]\n"
    "쓰기 전 훅 3개를 속으로 구상해 '가장 강한 1개'로 열어라. 아래 검증 공식 중 택1:\n"
    "① 결과 먼저('3만원으로 이렇게 바뀝니다') ② 손실 회피('이거 모르고 사면 손해') "
    "③ 호기심 갭('판매자만 아는 고르는 법') ④ 구체 숫자('2번이면 끝나는 ○○').\n"
    "⚠️ 밋밋한 인사('안녕하세요 ○○입니다')로 시작 금지 — 첫 마디에 궁금·공감·충격을 넣어라."
)

# 파는 카피 심리 — 모든 글 공통(정직 원칙 위에서).
COPY_PSYCH = (
    "[파는 카피 심리]\n"
    "- 손실 회피 > 이득: 같은 말도 '놓치면 손해' 프레임으로(단, 없는 혜택 지어내기 금지).\n"
    "- 구체성=신뢰: '좋아요' 대신 숫자·디테일('3주 써보니 배터리 40% 남음').\n"
    "- '당신' 화법: 읽는 사람을 직접 지칭('사장님도 이런 적 있으시죠').\n"
    "- 단점 1줄 솔직히 → 신뢰↑('무겁긴 해요, 대신 튼튼')."
)

# 네이버 블로그 — 파는 글 구조.
BLOG_SELL_STRUCT = (
    "[파는 글 구조 — 반드시 적용]\n"
    "① 첫 3줄=PAS: 문제 제기→공감/증폭→'그래서 오늘 보여드릴게요'(검색 유입자 이탈 방지=체류=상위노출).\n"
    "② 스펙이 아니라 FAB: 기능→'그래서 당신에게 뭐가 좋은지'(혜택)로 번역해서 써라.\n"
    "③ 특정 손님 스토리(BAB): 한 사람 사례(전→과정→후)로 몰입시켜라.\n"
    "④ 반론 선제 해소: FAQ에 손님이 망설이는 것(가격/AS/효과/배송)을 미리 답하라.\n"
    "⑤ CTA 계단: 저장→댓글→검색·예약→방문·구매 순(바로 '사세요'는 저항).\n"
    "⑥ 스마트블록 대응: 그 키워드의 세부 검색의도(가격·후기·방법·비교·추천)를 각각 소제목(##)으로 다뤄라 "
    "— 스마트블록·AI 답변 인용에 잡히게(정확·전문적으로 써야 AI가 인용)."
)

# 영상 스크립트 — 파는 글쓰기 + 리텐션.
VIDEO_SCRIPT_CRAFT = (
    "[영상 글쓰기 — 반드시]\n"
    "- 문어체 금지, 말하듯 한 문장 한 호흡('물에 빠뜨려도 멀쩡해요, 보실래요?').\n"
    "- 나레이션↔자막 역할 분리: 나레이션=대화체, 자막=핵심 키워드만 5~7자 큰 글씨('열차단 99%').\n"
    "- 감정 곡선: 궁금(훅)→공감(문제)→해결(시연)→만족(결과)→행동(CTA).\n"
    "- 한 장면=셀링포인트 1개를 '보여주듯' 묘사. 2~3초마다 새 장면/정보(죽은 구간 0).\n"
    "- 현장·과정 1컷 이상 포함(작업하는 손·before/after·제품 디테일) — 릴스 훅용이자 네이버 블로그 '경험 증명'용으로 둘 다 재사용.\n"
    "- 끝 프레임=첫 프레임과 연결(루프→자동 반복=시청시간↑). 마지막 1.5초 단일 CTA(행동 하나만)."
)

# 저장·공유 유도(영상강화 PHASE 5) — 저장·공유(DM)가 좋아요보다 3~5배 가중치.
SAVE_SHARE_RULE = (
    "[저장·공유 유도 — 도달 최강 신호] 콘텐츠를 '저장할 가치'가 있게 만들어라: "
    "정보/튜토리얼 포맷('OO하는 법 3단계', 'OO 고르는 기준 3가지')이 저장 점수가 가장 높다. "
    "가능하면 내레이션·구성을 단계형(1·2·3)으로. 마지막에 저장 유도 1회('저장해두고 필요할 때 보세요') — "
    "좋아요 구걸('좋아요 눌러주세요')은 금지."
)


def save_share_line(platform: str) -> str:
    """플랫폼별 저장·공유 CTA 한 줄(캡션/설명 자동 삽입용, 영상강화 PHASE 5)."""
    return {
        "instagram": "🔖 저장해두고 필요할 때 꺼내보세요 · 도움될 친구에게 DM으로 공유!",
        "youtube": "📌 저장해두면 필요할 때 바로 찾아요 · 도움됐다면 친구에게 공유해 주세요",
        "x": "🔖 북마크해두고 필요할 때 보세요",
    }.get(platform or "instagram", "저장해두고 필요할 때 보세요!")


# 자막 정보 밀도(영상강화 PHASE 2) — 반복재생 유도. 과하지 않게(한 씬 1정보+수치).
SUBTITLE_DENSITY = (
    "[자막 정보 밀도] 각 문장(씬)에 구체 정보 1개(수치·비교·팁)를 꼭 담아라 — 정보가 빽빽하면 "
    "한 번에 다 못 읽어 반복재생하게 된다. 단, 한 씬에 정보 2개 이상 욱여넣지 마라(피로)."
)

# 플랫폼별 최적화(같은 소재도 채널마다 다르게).
PLATFORM_YOUTUBE = "[유튜브 쇼츠=검색엔진] 제목·설명 첫 줄에 검색 키워드를 넣어라. 해시태그 3~5개."
PLATFORM_REEL = "[인스타 릴스] '저장각/공유각' 1회 유도(saves·sends 신호↑). 발행 시 트렌딩 사운드 권장."


def speaker_frame(strat_key: str) -> str:
    """업종/사업형태별 '화자와 목적' 프레이밍 — 정직하면서 효과적인 관점 고정(글·영상 공통)."""
    if strat_key == "seller":
        return ("[화자·목적] 너는 이 상품을 파는 '판매자 본인'이다. 판매자가 직접 상품을 보여주는 "
                "'상품 시연·언박싱·사용법' 관점으로 써라. 목표는 상세페이지로 데려가 '구매'시키는 것. "
                "⚠️ 고객인 척 '내돈내산 후기' 사칭 금지(가짜후기=저품질·불법). '제가 판매하며 직접 보여드릴게요' 톤으로 정직하게.")
    if strat_key == "hybrid":
        return ("[화자·목적] 너는 이 가게를 운영하며 직접 작업·판매하는 '사장 본인'이다. "
                "가까운 손님은 매장 방문, 먼 손님은 온라인 구매로 안내. 사장의 실제 경험·작업 관점으로 정직하게.")
    return ("[화자·목적] 너는 이 일을 직접 하는 '사장(작업자·운영자) 본인'이다. "
            "오늘 직접 한 시공·시술·조리 등을 '작업일지·현장 후기'처럼 써라(어떤 케이스→어떻게 작업→과정·팁·주의점→전/후 결과). "
            "목표는 '이런 고민 있으면 방문·예약하세요'로 방문 유도. ⚠️ 고객인 척 후기 사칭 금지 — 작업자 본인 관점으로 정직하게.")

# ── 저품질/스팸 위험 표현(휴리스틱, 공식 목록 아님) ──
RISKY_EXPRESSIONS = [
    "최고", "최저가", "100%", "무조건", "보장", "완벽", "대박", "강력추천",
    "절대", "유일", "1위", "공짜", "무료나눔", "지금당장", "한정특가", "폭탄세일",
    "초대박", "역대급", "클릭", "꼭 사세요",
]

# ── AI 클리셰(휴먼터치 A1) — 'AI가 쓴 티' 나는 정형 표현. 2026 AI 콘텐츠 피로 → 감점 대상 ──
AI_CLICHES = [
    "알아보겠습니다", "알아보도록 하겠습니다", "소개해드리겠습니다", "소개해 드리겠습니다",
    "추천드립니다", "추천드려요", "추천해 드립니다",
    "도움이 되셨길", "도움이 되었으면", "마무리하겠습니다", "마치겠습니다",
    "어떠셨나요", "포스팅을 마",
]

# 휴먼터치 지시 — blog/insta/X 공통 주입(A1). '사람이 쓴 것 같은' 리듬·구어가 차별화.
HUMAN_TOUCH = (
    "[휴먼터치 — AI 티 빼기(어기면 저품질·독자 이탈)]\n"
    "- 금지 클리셰: '안녕하세요~ 오늘은 ~알아보겠습니다' 류 도입, '~추천드립니다'·'~소개해드리겠습니다' 반복, "
    "'지금까지 ~였습니다'·'도움이 되셨길 바랍니다' 류 마무리, '어떠셨나요?' 상투 질문.\n"
    "- 문장 길이를 일부러 섞어라 — 아주 짧은 문장 뒤에 긴 문장. 문단 길이도 균일하게 맞추지 마라.\n"
    "- 자연스러운 구어 추임새를 가끔만: '근데', '사실', '솔직히' 같은 말(남발 금지).\n"
    "- 사장님(판매자) 1인칭 목소리 유지 — 설명문이 아니라 '내 가게(내 상품) 이야기'로.\n"
    "- 이모지: 네이버 블로그 0~1개, 인스타 1~2개까지만(남발=AI티).\n"
)

_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF❤⭐✨]")

# 감점이 아니라 '발행 차단' 대상 — 의료광고법·자동차관리법 위반 소지가 큰 단정 표현(PHASE 7)
HARD_BLOCK_EXPRESSIONS = [
    "완치", "부작용 없", "부작용없", "무통", "100% 효과", "영구적", "재발 없",
    "완전무사고", "무사고 보장", "침수 아님 보장",
]


def hard_block_hits(text: str) -> list[str]:
    """발행 차단 대상 표현 탐지(감점 아님). 하나라도 걸리면 자동발행 보류(PHASE 7)."""
    t = text or ""
    return [w for w in HARD_BLOCK_EXPRESSIONS if w in t]


def _money_nums(s: str) -> set:
    """텍스트에서 '금액·%·수치+단위'를 정규화 추출(콤마·공백 제거). 날조 탐지용(PHASE 7)."""
    raw = re.findall(r"(\d[\d,]*)\s*(원|만원|%|퍼센트|만|천원)", s or "")
    return {num.replace(",", "") + unit for num, unit in raw}


def keywords_line(kws: list[str]) -> str:
    return "[타겟 키워드(이 키워드로 검색 상위·전환을 노림)] " + ", ".join(kws) if kws else ""


# 경험/후기 신호(D.I.A 가점)
_EXPERIENCE_WORDS = ["후기", "직접", "경험", "먹어보", "써보", "방문", "가봤", "시공해", "느꼈"]


def quality_audit(channel: str, kind: str, payload: dict, source: str = "") -> dict:
    """네이버 랭킹 신호(C-Rank·D.I.A.+·플레이스) 기준 채점(0~100) + 개선 경고.
    가점: 검색의도 정합·1차 경험·구체 수치·이미지 4+·Q&A·제목-본문 일치·롱테일.
    감점/차단: 키워드 도배·낚시(제목-본문 불일치)·빈약 문서·과장/날조(표시광고법)·PII.
    source(입력 메모+사진분석) 제공 시 입력에 없는 금액·수치 날조를 기계적으로 탐지(PHASE 7·9)."""
    text = (payload.get("body") or payload.get("text") or "")
    warnings: list[str] = []
    score = 100

    # 사실 검증: 출력의 금액·%·수치가 입력에 존재하는지 대조(LLM 0콜 날조 탐지)
    if source:
        fabricated = [n for n in _money_nums(text) if n not in _money_nums(source)]
        if fabricated:
            warnings.append(f"입력에 없는 수치/금액 {fabricated[:4]} → 날조 의심(제거 권장)")
            score -= min(20, 8 * len(fabricated))

    # 공통: 저품질/과장 표현
    hits = [w for w in RISKY_EXPRESSIONS if w in text]
    if hits:
        warnings.append(f"과장·광고성 표현 {hits[:5]} → 저품질/스팸 위험")
        score -= min(25, 6 * len(hits))
    if text.count("!") >= 5 or "!!!" in text:
        warnings.append("느낌표 남발 → 스팸 신호")
        score -= 5
    # 휴먼터치(A1): AI 클리셰·균일 문단·이모지 남발 = 'AI가 쓴 티' 감점
    cliches = [w for w in AI_CLICHES if w in text]
    if cliches:
        warnings.append(f"AI 클리셰 {cliches[:3]} → AI티(사람 냄새 없는 글)")
        score -= min(15, 5 * len(cliches))
    paras = [p for p in text.split("\n\n") if len(p.strip()) >= 40 and not p.strip().startswith(("#", "|", "["))]
    if len(paras) >= 4:
        lens = [len(p) for p in paras]
        mean = sum(lens) / len(lens)
        cv = (sum((l - mean) ** 2 for l in lens) / len(lens)) ** 0.5 / mean if mean else 1
        if cv < 0.18:
            warnings.append("문단 길이가 너무 균일 → AI티(길이 변주 권장)")
            score -= 5
    emoji_n = len(_EMOJI_RE.findall(text))
    emoji_cap = {"blog": 1, "caption": 2, "x_post": 2}.get(kind)
    if emoji_cap is not None and emoji_n > emoji_cap:
        warnings.append(f"이모지 {emoji_n}개 > {emoji_cap} → AI티·과장 인상")
        score -= 4
    # 키워드 남발(스터핑)
    for kw in (payload.get("target_keywords") or [])[:3]:
        if kw and text.count(kw) > 6:
            warnings.append(f"'{kw}' {text.count(kw)}회 과다반복(남발)")
            score -= 10

    if kind == "blog":
        title = payload.get("title", "")
        main_kw = (payload.get("target_keywords") or [""])[0]
        if main_kw and main_kw not in title:
            warnings.append(f"제목에 핵심키워드 '{main_kw}' 없음 → 상위노출 크게 불리")
            score -= 12
        if main_kw and main_kw not in text[:120]:
            warnings.append("첫 문단에 핵심키워드 없음 → 검색의도 매칭 약함")
            score -= 6
        if main_kw and text.count(main_kw) < 2:
            warnings.append(f"핵심키워드 '{main_kw}' 본문 노출 부족(2회↓)")
            score -= 6
        if not any(s in text for s in ("자주 묻는", "Q&A", "Q.", "Q1")):
            warnings.append("FAQ(자주 묻는 질문) 없음 → Q&A·체류 가점 놓침")
            score -= 4
        if len(text) < 1000:
            warnings.append(f"본문 {len(text)}자 < 1000 (체류시간↓ → C-Rank 불리)")
            score -= 15
        if "##" not in text:
            warnings.append("소제목(##) 없음 → 구조/가독성 약함")
            score -= 5
        if "[사진1]" not in text:
            warnings.append("사진 마커 없음 → 체류시간↓")
            score -= 5
        if not any(w in text for w in _EXPERIENCE_WORDS):
            warnings.append("실제 경험·후기 표현 약함 → D.I.A 불리")
            score -= 12
        if len(re.findall(r"\d", text)) < 5:      # 구체 수치(소요시간·단계·전후) 부족(PHASE 9)
            warnings.append("구체 수치(시간·단계·전후) 부족 → D.I.A. 구체성↓")
            score -= 6
        if text.count("[사진") < 4:               # 이미지 4장 미만 → 정합·체류 신호 약함(PHASE 9)
            warnings.append(f"이미지 {text.count('[사진')}장 < 4 → 이미지 정합·체류 신호 약함")
            score -= 4
    elif kind in ("short",):
        if not payload.get("hook_strategy"):
            warnings.append("0~3초 훅 없음 → 시청유지↓")
            score -= 15
        d = payload.get("duration_sec", 0)
        if d and d > 60:
            warnings.append(f"{d}s > 60 → 완주율↓(30~45초 권장)")
            score -= 6
        elif d and d < 15:
            warnings.append(f"{d}s < 15 → 완주 절대량 미달로 도달↓(30~45초 권장)")
            score -= 6
    elif kind == "caption":
        n_tags = text.count("#")
        if n_tags < 3:
            warnings.append("해시태그 부족(<3)")
            score -= 5
        elif n_tags > 6:
            warnings.append(f"해시태그 과다({n_tags}개>6) → 2026 도달↓, 3~5개 권장")
            score -= 4
    elif kind == "x_post":
        if len(text) > 280:
            warnings.append("280자 초과")
            score -= 10

    score = max(0, min(100, score))
    grade = "우수" if score >= 85 else ("양호" if score >= 70 else "개선필요")
    return {"score": score, "grade": grade, "warnings": warnings}
