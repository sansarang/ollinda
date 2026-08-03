"""
🌾 경험 자동 수확 — 묻기 전에 이미 아는 것을 캔다(2026-08-03 사장님 지시).

왜: 질문은 비용이다. 사장님이 이미 글에서 말한 것, 사진이 반복해서 보여준 것을
다시 묻는 건 잔소리다. 쌓일수록 질문이 줄어드는 구조가 맞다.

수확처(전부 자사 실데이터):
  ① 과거 발행 글 본문의 경험 문장 — 사장님이 이미 하신 말
  ② 사진 vision 판독의 반복 패턴 — 이 가게가 '실제로 하는 것'
  ③ 플레이스 리뷰(사장님이 올린 캡처) — 손님 발화. 출처를 반드시 붙인다.

★ 절대선(2026-08-03 정정): 검색은 취재다 — 확인된 사실·지식을 3인칭으로 쓰는 건 정상이다.
  금지는 그것을 1인칭 경험("저희가 해보니")으로 바꾸는 인칭 위조뿐이다.
  이 모듈은 '이 가게가 실제로 한 말'만 캔다(1인칭 재료) — 그래서 외부 소스가 없다.
★ 손님 발화는 사장 경험으로 위조하지 않는다 — 출처(kind='review')를 달아 구분한다.

업종 중립: 판정 재료는 그 가게의 글·사진 묘사뿐이다. 업종어 하드코딩 0.
"""
from __future__ import annotations

import logging
import re

from app import db

_log = logging.getLogger("shopcast.harvest")

# 1인칭 경험 신호 — '내가 했다/봤다/들었다'는 말투. 언어 규칙만(업종 무관).
_EXP_SIGN = re.compile(
    r"(직접|제가|저는|저희는|해보니|해봤|확인했|봤습니다|들었|말씀|하십니다|하시더|"
    r"권해드|보여드|알려드|잡아드|챙겨드|느꼈|겪었|경험)")
# 일반론·안내문 배제 — 누구나 쓸 수 있는 문장은 경험이 아니다
_GENERIC = re.compile(
    r"(문의|예약|상담|검색|저장|리뷰 ?남|방문자|찾아오는 ?길|주소|영업시간|"
    r"이 ?글|본문|아래|다음과 ?같|정리했|알아보|소개해)")
_MIN_LEN, _MAX_LEN = 18, 160


def _sentences(body: str) -> list[str]:
    txt = re.sub(r"\[[^\]]{1,20}\]", " ", body or "")        # [사진N]·마커 제거
    txt = re.sub(r"^[#>|\-\s].*$", " ", txt, flags=re.M)     # 소제목·표·목록 줄 제거
    out = []
    for s in re.split(r"(?<=[.!?])\s+|\n+", txt):
        s = " ".join(s.split())
        if _MIN_LEN <= len(s) <= _MAX_LEN:
            out.append(s)
    return out


def from_published(tenant_id: str, limit_posts: int = 20, _diag: dict = None) -> list[dict]:
    """① 과거 글에서 사장님이 이미 하신 말을 캔다.
    _diag를 주면 왜 0건인지(발행 행 수·본문 확보 수·문장 수) 남긴다 — 조용한 0 금지."""
    out, seen = [], set()
    try:
        rows = db.list_blog_publishes(tenant_id, limit=limit_posts) or []
    except Exception:
        return []
    if _diag is not None:
        _diag.update({"publishes": len(rows), "bodies": 0, "sentences": 0})
    for r in rows:
        pid = r.get("piece_id") or ""
        try:
            p = db.get_piece(pid) if pid else None
        except Exception:
            p = None
        body = ((p.payload or {}).get("body") if p else "") or ""
        kw = (r.get("target_kw") or "").strip()
        _ss = _sentences(body)
        if _diag is not None:
            _diag["bodies"] += 1 if body else 0
            _diag["sentences"] += len(_ss)
        for s in _ss:
            if not _EXP_SIGN.search(s) or _GENERIC.search(s):
                continue
            key = re.sub(r"\W+", "", s)[:40]
            if key in seen:
                continue
            seen.add(key)
            out.append({"kind": "owner", "text": s, "source": "과거 발행 글", "topic": kw})
    return out


# 우리 시스템이 프롬프트에 쓰는 말 — 사진 묘사가 아니다(업종어가 아니라 내부 어휘라 목록이 맞다).
_SYSWORD = {"사진", "키워드", "셀링포인트", "사장님", "추측", "미확인", "분석", "대상", "메뉴명",
            "본문", "제목", "장면", "부분", "상태", "모습", "표시", "확인", "작업"}
_JOSA_TAIL = re.compile(r"(을|를|이|가|은|는|의|에|에서|으로|로|와|과|도|만|까지|부터|처럼|보다)$")


def from_vision(tenant_id: str, limit_sets: int = 12, min_repeat: int = 2) -> list[dict]:
    """② 사진 묘사에서 반복되는 것 = 이 가게가 실제로 하는 일.
    한 번 나온 건 우연일 수 있다 — 여러 세트에서 반복될 때만 인정한다.

    ★ 실측 교정(2026-08-03): gen_source에는 사진 묘사 말고 프롬프트 지시문도 섞여 있다.
      전체를 훑었더니 '키워드·셀링포인트·사장님·추측이다' 같은 우리 시스템 어휘를 캤다.
      → [사진N] 뒤의 묘사 줄만 읽고, 조사 붙은 어절과 내부 어휘는 뺀다."""
    freq: dict = {}
    try:
        sets = db.list_sets(tenant_id=tenant_id, limit=limit_sets) or []
    except Exception:
        return []
    for s in sets:
        seen_here = set()
        for p in db.get_set_pieces(s.get("asset_id") or ""):
            src = (p.payload or {}).get("gen_source") or ""
            if not src:
                continue
            for m in re.finditer(r"\[사진\d+\]\s*([^\n]+)", src):     # 묘사 줄만
                for w in re.findall(r"[가-힣A-Za-z0-9]{3,}", m.group(1)):
                    w = _JOSA_TAIL.sub("", w)
                    if len(w) >= 3 and w not in _SYSWORD:
                        seen_here.add(w)
            break
        for w in seen_here:
            freq[w] = freq.get(w, 0) + 1
    hot = [w for w, n in sorted(freq.items(), key=lambda x: -x[1]) if n >= min_repeat]
    return [{"kind": "fact", "text": w, "source": "사진 반복 판독", "topic": ""} for w in hot[:40]]


def from_reviews(tenant_id: str, limit: int = 20) -> list[dict]:
    """③ 손님 발화 — 사장님이 올린 리뷰 캡처에서만. 출처를 반드시 붙인다.
    ★ 사장 경험으로 위조 금지: kind='review'로 구분하고, 인용 시 '손님 후기'로 표기한다."""
    out = []
    try:
        rows = db.list_owner_experience(tenant_id, limit=limit) or []
    except Exception:
        return []
    for e in rows:
        q = (e.get("question") or "")
        if "리뷰" not in q and "후기" not in q:
            continue
        out.append({"kind": "review", "text": (e.get("answer") or "")[:200],
                    "source": "손님 후기(사장님 등록)", "topic": ""})
    return out


def harvest(tenant_id: str, _diag: dict = None) -> dict:
    """전 수확처 통합. 반환 {owner:[], fact:[], review:[], covered:set-like list}."""
    owner = from_published(tenant_id, _diag=_diag)
    fact = from_vision(tenant_id)
    review = from_reviews(tenant_id)
    covered = set()
    for it in owner + fact:
        for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", it["text"]):
            covered.add(w)
    return {"owner": owner, "fact": fact, "review": review,
            "covered": sorted(covered)[:400],
            "counts": {"owner": len(owner), "fact": len(fact), "review": len(review)}}


# 1인칭 경험 주장 — 이 말을 쓰려면 사장님의 실제 답변·기록이 있어야 한다.
#   검색으로 안 사실을 이 말투로 옮기면 인칭 위조다(언어 규칙만, 업종 무관).
FIRST_PERSON = re.compile(
    r"(저희가|저희는|제가|우리가|우리 ?손님|저희 ?손님|직접 (해|확인|시공|검수)|"
    r"해보니|해봤|겪었|느꼈|들었습니다|하시더라|말씀하셨)")


def first_person_claims(text: str) -> list[str]:
    """본문에서 1인칭 경험 주장을 모두 뽑는다 — 근거 없는 글에 이 말이 있으면 위조다."""
    return [m.group(0) for m in FIRST_PERSON.finditer(text or "")]


def covers(tenant_id: str, topic: str, min_hit: int = 2) -> bool:
    """이 주제를 수확이 이미 커버하는가 — 커버하면 묻지 않는다.
    쌓일수록 질문이 줄어드는 구조의 핵심."""
    tk = {w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", topic or "")}
    if not tk:
        return False
    try:
        cov = set(harvest(tenant_id).get("covered") or [])
    except Exception:
        return False
    return len(tk & cov) >= min_hit


def as_note_block(tenant_id: str, topic: str = "", limit: int = 4) -> str:
    """생성 프롬프트에 넣을 수확 블록. 손님 발화는 출처를 달아 구분한다."""
    h = harvest(tenant_id)
    tk = {w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", topic or "")}

    def _rel(items):
        if not tk:
            return items[:limit]
        scored = [(len(tk & {w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", i["text"])}), i)
                  for i in items]
        scored.sort(key=lambda x: -x[0])
        return [i for n, i in scored if n > 0][:limit]

    lines = []
    for it in _rel(h["owner"]):
        lines.append(f"- (사장님이 전에 하신 말) {it['text']}")
    for it in _rel(h["review"]):
        lines.append(f"- (손님 후기 — 사장님 경험으로 쓰지 말고 '손님이 남긴 후기'로만 인용) {it['text']}")
    if not lines:
        return ""
    return ("\n[이 가게가 이미 말한 것 — 자사 기록에서 수확]\n" + "\n".join(lines) +
            "\n※ 여기 있는 말은 이 가게의 실제 기록이다. 새로 지어내지 말고 이것을 근거로 써라.")
