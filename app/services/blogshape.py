"""글 골격 단일 관문 — 같은 가게에서 같은 뼈대가 반복되는 것을 막는다.

왜 생겼나 (2026-08-19 사장님 지적: "글쓰기 형식이 전부다 똑같잔항"):
  실측 — 루마썬팅 발행글 8편이 전부 같은 뼈대였다.
      본문 2~3덩이 → [FAQ] → [요약] → 함께 보면 좋은 글
  6개 업종으로 새로 만들어 본 글 6편도 마찬가지였다.

  2026-08-16에 이미 한 번 손댔다. 그때는 **섹션 이름만** 돌렸다(services/sections.py) —
  '한눈 요약/요약하면/핵심만 정리'. 옷만 갈아입힌 셈이라 뼈대는 그대로 남았다.
  같은 계열 결함 2회째이므로 표면이 아니라 **구성 규칙 자체**를 바꾼다(헌법).

원인:
  프롬프트가 `[필수 섹션] ① FAQ(Q&A 3쌍) ② 요약(3줄)`을 **모든 글에** 강제했다.
  시공 기록 글에 '자주 묻는 질문'이 붙는 어색함이 여기서 나왔다.

설계 두 가지:
  ① 골격마다 마무리 블록이 다르다 — 문답 중심 글에 FAQ를 또 붙이지 않는다.
  ② **그 가게가 아직 안 쓴 골격을 먼저 쓴다.** sections.py는 asset_id 해시로만
     골라서 연속으로 같은 값이 나올 수 있다. 골격은 이력을 본다 —
     8편까지는 전부 다른 뼈대로 나가고, 그 뒤에는 가장 오래된 것부터 다시 돈다.

★ 선택은 **결정적**이다 — 같은 글은 언제 다시 봐도 같은 골격.
  무작위면 재생성 때마다 달라져 검증이 불가능하고 게이트와 어긋난다.

★ 업종어를 넣지 않는다(헌법: 업종 중립). '시공·매물' 같은 말 대신
  '한 일·다루는 것'으로 쓴다 — 헬스장·펜션·동물병원에도 그대로 통해야 한다.
"""
from __future__ import annotations

#: 골격 카탈로그. flow는 프롬프트에 그대로 들어간다.
#:   faq/summary — 이 골격이 마무리 블록을 요구하는가(게이트도 이 값을 본다).
#:   summary="short" 는 3줄이 아니라 1~2줄로 짧게.
SHAPES: tuple[dict, ...] = (
    {"id": "record", "name": "현장 기록형",
     "flow": "오늘 실제로 한 일을 시간 순서대로 적어라 — 준비 → 진행 → 마무리 → 확인. "
             "각 단계에서 눈으로 본 것과 손으로 한 것을 쓰고, 설명·조언으로 새지 마라.",
     "faq": False, "summary": "short"},
    {"id": "problem", "name": "고민 해결형",
     "flow": "손님이 자주 하는 고민 하나를 앞에 두고 — 왜 그 고민이 생기는지 → "
             "무엇을 보면 풀리는지 → 실제로 그렇게 한 사례 순으로 전개하라.",
     "faq": True, "summary": True},
    {"id": "compare", "name": "비교 선택형",
     "flow": "선택지 둘(또는 셋)을 나란히 놓고 — 각각 어떤 경우에 맞는지 → "
             "무엇을 기준으로 고르면 되는지 → 우리는 어떻게 권하는지 순으로 써라. "
             "한쪽을 깎아내리지 말고 '언제 맞는가'로만 갈라라.",
     "faq": True, "summary": True},
    {"id": "checklist", "name": "체크리스트형",
     "flow": "고르거나 준비할 때 봐야 할 것 3~4가지를 항목으로 세우고, "
             "항목마다 왜 그것이 중요한지 근거를 한 문단씩 붙여라.",
     "faq": False, "summary": True},
    {"id": "beforeafter", "name": "전후 비교형",
     "flow": "이전 상태 → 무엇을 했는지 → 무엇이 달라졌는지 순으로 써라. "
             "달라진 점은 사진으로 확인되는 것만 쓰고, 느낌말로 부풀리지 마라.",
     "faq": False, "summary": "short"},
    {"id": "myth", "name": "오해 정정형",
     "flow": "그 분야에서 흔한 오해 2~3개를 하나씩 꺼내 — 왜 그렇게 알려졌는지 → "
             "실제로는 어떤지 순으로 바로잡아라. 단정보다 근거를 앞에 둬라.",
     "faq": True, "summary": True},
    {"id": "deep", "name": "한 가지 깊게",
     "flow": "주제 하나만 골라 끝까지 파라. 곁가지로 새지 말고, "
             "그 하나에 대해 남들이 안 쓰는 데까지 들어가라.",
     "faq": False, "summary": True},
    {"id": "qna", "name": "문답 중심형",
     "flow": "실제로 받는 질문 3~4개를 소제목으로 세우고 각각에 답하는 형식으로 본문을 구성하라. "
             "본문 자체가 문답이므로 뒤에 질문 섹션을 또 만들지 마라.",
     "faq": False, "summary": True},
)

BY_ID = {s["id"]: s for s in SHAPES}
DEFAULT = SHAPES[1]          # 고민 해결형 — 기존 글에 가장 가까운 형태(하위호환)

#: 8종을 다 쓴 뒤 재사용할 때, 오래된 쪽 이만큼에서만 고른다(직전 글과 안 겹치게).
AVOID_RECENT = 4


def _hash(seed: str) -> int:
    h = 0
    for ch in (seed or ""):
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return h


def pick(seed: str, recent: "list[str] | tuple" = ()) -> dict:
    """골격 선택 — 같은 seed면 항상 같은 결과(결정적).

    recent: 그 가게가 최근에 쓴 골격 id 목록(최신순).

    ★ '최근 N개 회피'가 아니라 **안 쓴 것 우선**이다.
      회피만 하면 8종인데도 6편째에 1편과 같은 골격이 돌아왔다(실측).
      안 쓴 것을 먼저 소진하면 **8편까지 전부 다른 뼈대**가 나간다.
      다 쓴 뒤에는 가장 오래전에 쓴 것부터 다시 돈다 — 직전 글과 겹치지 않는다.
    """
    used = [s for s in recent if s]
    unused = [s for s in SHAPES if s["id"] not in used]
    if unused:
        pool = unused
    else:
        # ★ 첫 등장 인덱스만 쓴다(0 = 가장 최근).
        #   dict 컴프리헨션으로 만들면 같은 id가 두 번 나올 때 **나중 것(더 오래된 인덱스)**이
        #   덮어써서, 방금 쓴 골격이 '가장 오래된 것'으로 잘못 분류된다.
        #   그래서 10편째에 직전과 같은 골격이 나왔다(골든이 잡았다).
        order: dict = {}
        for i, sid in enumerate(used):
            order.setdefault(sid, i)
        ranked = sorted(SHAPES, key=lambda s: -order.get(s["id"], len(used)))
        pool = [s for s in ranked if s["id"] != used[0]][:AVOID_RECENT] or ranked[:1]
    if not (seed or "").strip():
        return pool[0]
    return pool[_hash(seed) % len(pool)]


def get(shape_id: str) -> dict:
    return BY_ID.get((shape_id or "").strip(), DEFAULT)


def needs_faq(shape_id: str) -> bool:
    """이 골격이 질문 섹션을 요구하는가 — 게이트가 이걸 보고 검사 여부를 정한다."""
    return bool(get(shape_id).get("faq"))


def needs_summary(shape_id: str) -> bool:
    return bool(get(shape_id).get("summary"))


def summary_len(shape_id: str) -> str:
    """'short' | 'full' — 요약을 몇 줄로 쓸지."""
    v = get(shape_id).get("summary")
    return "short" if v == "short" else "full"


def prompt_block(shape_id: str, faq_head: str, summary_head: str) -> str:
    """프롬프트에 넣을 구성 지시 — 골격의 전개 + 이 글이 쓸 마무리 블록만."""
    s = get(shape_id)
    lines = [f"[이 글의 구성 — {s['name']}] {s['flow']}"]
    # ★ 본문 소제목은 골격과 무관하게 **항상** 요구한다(2026-08-19 실측으로 추가).
    #   골격을 도입하면서 옛 [필수 섹션] 지시를 지웠는데, 그 지시가 소제목을 강제하던
    #   유일한 곳이었다. 그래서 체크리스트형 글이 소제목 0개로 나왔다(GEO 33점).
    #   소제목은 장식이 아니라 **검색에서 문단 단위로 뜨는 단위**다 — 없으면 그 노출이 불가능하다.
    lines.append("[소제목] 본문을 2~4개의 소제목으로 나눠라. 소제목은 '## '로 시작하고 "
                 "**반드시 줄 맨 앞에** 둔다(문장 끝에 이어 붙이지 마라). "
                 "소제목은 그 덩이의 질문에 답하는 말로 쓴다 — 번호·제목 흉내는 금지.")
    req = []
    if s.get("faq"):
        req.append(f"'## {faq_head}'(Q&A 정확히 3쌍)")
    if s.get("summary"):
        n = "1~2줄" if s["summary"] == "short" else "핵심 3줄 목록"
        req.append(f"'## {summary_head}'({n} — GEO)")
    if req:
        lines.append("[마무리 섹션] " + " · ".join(req)
                     + "\n  이것만 만들고 관리용 섹션을 임의로 늘리지 마라. "
                       "소제목 이름은 위에 준 그대로 써라.")
    else:
        lines.append("[마무리 섹션] 이 글에는 요약·질문 섹션을 붙이지 마라. "
                     "본문이 끝나면 그대로 마친다 — 억지로 정리 블록을 만들지 마라.")
    return "\n".join(lines) + "\n"
