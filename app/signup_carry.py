"""진단 결과를 가입 너머로 실어 나른다 (2026-08-14 사장님 지시).

왜 필요한가:
  랜딩 진단에서 사장님 가게를 이미 찾아냈다 — 상호·지역·업종·주소, 그리고 블로그까지.
  그런데 가입하면 `/me`가 "딱 3가지만 알려주세요" 가게 등록 화면부터 띄운다.
  방금 우리가 다 알아낸 것을 사장님께 다시 입력시키는 셈이고,
  "가입하고 이 글 전체 받기"라는 약속도 거기서 끊긴다.

  → 가입 버튼을 누르는 순간 그 정보를 쿠키에 실어 보내고, 가입 직후 가게에 채운다.
    사장님은 확인만 하면 된다.

정직 게이트:
  · 자동으로 채우되 **어디서 온 값인지 밝히고**, 언제든 고칠 수 있게 둔다.
    (사장님이 직접 상호를 넣고, 동명이면 주소까지 고른 값이라 근거는 있다)
  · OAuth 왕복을 건너는 값이라 수명을 짧게 둔다(2시간).
  · 값이 없거나 깨지면 조용히 무시하고 평소 온보딩으로 간다 — 실패가 가입을 막지 않는다.
"""
from __future__ import annotations

import json
import logging

_log = logging.getLogger("shopcast.signup_carry")

COOKIE = "ollinda_onb"
MAX_AGE = 2 * 3600          # OAuth 왕복에 필요한 만큼만
_FIELDS = ("nm", "rg", "ind", "ad", "blog", "kw")
_LIMIT = 80                 # 필드당 길이 상한(쿠키 비대·주입 방지)


def pack(params) -> str:
    """쿼리 파라미터 → 쿠키 값(JSON). 실을 게 없으면 빈 문자열."""
    d = {}
    for k in _FIELDS:
        v = (params.get(k) or "").strip()[:_LIMIT]
        if v:
            d[k] = v
    if not d.get("nm"):         # 상호 없이는 가게를 특정할 수 없다 — 싣지 않는다
        return ""
    try:
        return json.dumps(d, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return ""


def unpack(raw: str) -> dict:
    """쿠키 값 → dict. 깨졌으면 빈 dict(가입을 막지 않는다)."""
    if not (raw or "").strip():
        return {}
    try:
        d = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(d, dict):
        return {}
    return {k: str(d[k])[:_LIMIT] for k in _FIELDS if d.get(k)}


def attach(resp, params) -> None:
    """로그인 진입 응답에 쿠키를 붙인다(있을 때만)."""
    val = pack(params)
    if not val:
        return
    try:
        from app import auth
        resp.set_cookie(COOKIE, val, max_age=MAX_AGE, httponly=True,
                        samesite="lax", secure=auth.cookie_secure())
    except Exception:
        _log.exception("[signup-carry] 쿠키 부착 실패")


def apply_to_tenant(t, data: dict) -> list:
    """가게에 비어 있는 칸만 채운다. 채운 항목 이름을 돌려준다(화면에 밝히기 위해).

    ★ 이미 값이 있으면 덮지 않는다 — 사장님이 직접 넣은 것이 우선이다.
    """
    from app import db
    filled = []
    # ★ 기존 저장 경로(rename_tenant·update_tenant_profile·set_tenant_blog)를 그대로 쓴다 —
    #   가게 정보를 쓰는 길을 새로 만들면 그게 경로 이중화다(canonical 원칙).
    nm = (data.get("nm") or "").strip()
    ind = (data.get("ind") or "").strip()
    rg = (data.get("rg") or "").strip()
    cur_nm = (getattr(t, "name", "") or "").strip()
    cur_ind = (getattr(t, "industry", "") or "").strip()
    cur_rg = (getattr(t, "region", "") or "").strip()
    new_nm = nm if (cur_nm in ("", "내 가게") and nm) else cur_nm
    new_ind = ind if (not cur_ind and ind) else cur_ind
    new_rg = rg if (not cur_rg and rg) else cur_rg
    if (new_nm, new_ind, new_rg) != (cur_nm, cur_ind, cur_rg):
        try:
            db.rename_tenant(t.id, new_nm, new_ind, new_rg)
            if new_nm != cur_nm:
                filled.append("가게 이름")
            if new_ind != cur_ind:
                filled.append("업종")
            if new_rg != cur_rg:
                filled.append("지역")
        except Exception:
            _log.exception("[signup-carry] 가게 기본정보 채우기 실패 t=%s", t.id)
    ad = (data.get("ad") or "").strip()
    if ad and not (getattr(t, "address", "") or "").strip():
        try:
            db.update_tenant_profile(t.id, getattr(t, "phone", "") or "", ad,
                                     getattr(t, "hours", "") or "",
                                     getattr(t, "map_url", "") or "")
            filled.append("주소")
        except Exception:
            _log.exception("[signup-carry] 주소 채우기 실패 t=%s", t.id)
    blog = (data.get("blog") or "").strip()
    if blog and not (getattr(t, "naver_blog_url", "") or "").strip():
        try:
            db.set_tenant_blog(t.id, f"https://blog.naver.com/{blog}", blog)
            filled.append("블로그")
        except Exception:
            _log.exception("[signup-carry] 블로그 채우기 실패 t=%s", t.id)
    return filled
