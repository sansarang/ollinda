"""
BGM — 저작권프리 음악을 로컬 라이브러리(assets/bgm/*.mp3)에서 선택.
사장님/운영자가 상업용 라이선스 확보한 mp3를 폴더에 넣어두면 랜덤 선택.
파일 없으면 None(무음). ※ 상업사용 라이선스는 사용자 책임.
"""
from __future__ import annotations

import glob
import os
import random

BGM_DIR = os.environ.get("SHOPCAST_BGM_DIR", "assets/bgm")


def available() -> bool:
    return bool(glob.glob(os.path.join(BGM_DIR, "*.mp3")))


def pick() -> str | None:
    files = glob.glob(os.path.join(BGM_DIR, "*.mp3"))
    return random.choice(files) if files else None
