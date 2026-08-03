"""
📷 사진 묘사 단일 파서(2026-08-03 — 캡션 10회 재발 종결).

사고: gen_source의 `[사진N]` 줄을 캡션은 '첫 매치'로 읽었다. 그런데 vision 응답에는
묘사 대신 제목·라벨·마크다운 잔해가 섞여 오고("사진 분석 (썬팅 업종 관점)", "피사체/제품", "**"),
분석 배치가 여러 번 이어붙어 같은 번호가 10번까지 나온다.
첫 줄이 헤더인 번호는 그 헤더가 그대로 캡션이 됐다 — 20장 중 8장.

★ 같은 재료를 읽는 소비자가 둘 이상이면 파서를 하나로 만든다.
  영상 자막에는 이미 방어가 있었는데(video._lines_for_photos) 캡션에는 없었다.
  한쪽만 고쳐서 같은 결함이 10회 재발했다. 이 모듈이 유일한 파서다.

원칙(침묵 폴백 금지): 쓸 만한 묘사가 없으면 **빈 문자열**을 준다.
템플릿·업종명·키워드로 채우지 않는다 — 채우는 순간 사장님은 결함을 못 본다.
"""
from __future__ import annotations

import re

# 묘사가 아닌 줄 — vision이 제목·섹션명으로 뱉는 형태(언어 규칙, 업종어 하드코딩 0).
#   '마케팅'·'사진 분석'은 우리 프롬프트 어휘이기도 하다: 사진 내용이 아니라 분석 행위를 가리킨다.
META = re.compile(r"(사진 ?분석|분석 ?결과|분석입니다|마케팅|관점|촬영 ?팁|추천 ?활용|"
                  r"다음과 ?같|요약하면|아래와 ?같)")
# 라벨·머리표 — '피사체/제품', '* 피사체:' 처럼 값이 아니라 항목 이름만 온 줄
LABEL = re.compile(r"^\s*[*\-•#>]*\s*[가-힣A-Za-z/·]{2,14}\s*[:：]?\s*$")
_MD = re.compile(r"^[\s*_~`#>-]+$")               # '**', '---' 같은 마크다운 잔해


def is_description(line: str) -> bool:
    """이 줄이 '사진에 무엇이 보이는가'를 말하는가 — 아니면 버린다."""
    s = " ".join((line or "").split())
    if len(s) < 8 or _MD.match(s) or LABEL.match(s):
        return False
    if META.search(s):
        return False
    return bool(re.search(r"[가-힣]{2,}", s))


def clean(line: str) -> str:
    """묘사 줄에서 서식 잔해만 걷어낸다(내용은 건드리지 않는다)."""
    s = re.sub(r"^\s*[*\-•#>]+\s*", "", line or "")
    s = re.sub(r"^\s*[가-힣A-Za-z/·]{2,12}\s*[:：]\s*", "", s)   # '피사체/차종:' 류 머리표
    return " ".join(s.split()).strip(" .,·—-")


def best_line(gen_source: str, n: int) -> str:
    """사진 n번의 '가장 묘사다운' 줄. 없으면 빈 문자열(채우지 않는다).

    ★ 첫 매치를 쓰지 않는다 — 배치가 이어붙어 첫 줄이 헤더인 경우가 실측으로 확인됐다.
      후보 전체에서 묘사인 것만 남기고, 그중 가장 정보량이 많은(긴) 것을 고른다.
    """
    cands = [m.group(1) for m in
             re.finditer(rf"\[사진{n}\]\s*([^\n]+)", gen_source or "")]
    good = [clean(c) for c in cands if is_description(c)]
    good = [g for g in good if len(g) >= 8]
    return max(good, key=len) if good else ""


def alternates(gen_source: str, n: int) -> list:
    """사진 n번의 다른 묘사 후보들(긴 순). 중복 캡션을 '다른 실제 묘사'로 가르는 데 쓴다 —
    키워드를 덧붙여 구분하는 것은 침묵 폴백이다(조항)."""
    cands = [clean(m.group(1)) for m in re.finditer(rf"\[사진{n}\]\s*([^\n]+)", gen_source or "")
             if is_description(m.group(1))]
    seen, out = set(), []
    for c in sorted(cands, key=len, reverse=True):
        k = re.sub(r"\s", "", c)[:30]
        if k and k not in seen:
            seen.add(k)
            out.append(c)
    return out


def desc_map(gen_source: str, count: int) -> dict:
    """{번호: 최선 묘사} — 없는 번호는 키가 없다(빈 값으로 채우지 않는다)."""
    out = {}
    for i in range(1, max(0, count) + 1):
        b = best_line(gen_source, i)
        if b:
            out[i] = b
    return out
