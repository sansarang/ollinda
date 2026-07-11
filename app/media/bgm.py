"""
BGM — 저작권프리 음악을 로컬 라이브러리(assets/bgm/*.mp3)에서 선택.
사장님/운영자가 상업용 라이선스 확보한 mp3를 폴더에 넣어두면 랜덤 선택.
파일 없으면 None(무음). ※ 상업사용 라이선스는 사용자 책임.
"""
from __future__ import annotations

import glob
import os
import random

# app/assets/bgm — 폰트(_FONT_DIR)와 동일 위치(배포 확인됨). 상대경로/루트 assets는 프로덕션에서 못 찾음.
BGM_DIR = os.environ.get("SHOPCAST_BGM_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "bgm")


def available() -> bool:
    return bool(glob.glob(os.path.join(BGM_DIR, "*.mp3")))


# 업종 → BGM 분위기(영상강화 PHASE 3). 파일명 키워드/무드 폴더로 매칭.
# calm(차분: 카페·뷰티·의료), upbeat(경쾌: 음식·이벤트·리테일), trust(신뢰: 자동차·법률·기술)
_MOOD_FILE_HINTS = {"calm": ["calm", "cafe", "soft", "chill"],
                    "upbeat": ["upbeat", "warm", "happy", "pop"],
                    "trust": ["clean", "modern", "corporate", "tech"]}
_MOOD_BY_INDUSTRY = [
    (("카페", "베이커리", "디저트", "미용", "네일", "피부", "요가", "필라테스", "꽃", "병원", "치과", "한의원"), "calm"),
    (("식당", "고기", "국밥", "치킨", "분식", "주점", "펍", "헬스", "이벤트", "마트", "옷", "패션"), "upbeat"),
    (("자동차", "썬팅", "정비", "타이어", "법률", "세무", "부동산", "인테리어", "전자", "수리", "학원"), "trust"),
]


def mood_for(industry: str) -> str:
    ind = (industry or "").strip()
    for keys, mood in _MOOD_BY_INDUSTRY:
        if any(k in ind for k in keys):
            return mood
    return "calm"                        # 기본: 차분(내레이션 방해 최소)


def pick(mood: str = "") -> str | None:
    """분위기 우선 선택 — ① 무드 하위폴더(bgm/{mood}/*.mp3) ② 파일명 키워드 ③ 전체 랜덤.
    ※ assets/bgm에는 상업용 라이선스 확보(저작권 안전) mp3만 넣는다 — 소스 관리는 운영자 책임."""
    if mood:
        sub = glob.glob(os.path.join(BGM_DIR, mood, "*.mp3"))
        if sub:
            return random.choice(sub)
        hints = _MOOD_FILE_HINTS.get(mood, [])
        named = [f for f in glob.glob(os.path.join(BGM_DIR, "*.mp3"))
                 if any(h in os.path.basename(f).lower() for h in hints)]
        if named:
            return random.choice(named)
    files = glob.glob(os.path.join(BGM_DIR, "*.mp3"))
    return random.choice(files) if files else None
