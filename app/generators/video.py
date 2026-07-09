"""
숏폼(릴스/쇼츠) 생성기 v3 — '글 → 씬' 자동변환 + 씬별 TTS 싱크 + PIL 자막(키워드 강조)
+ 켄번스 모션 + 훅/아웃트로 카드 + AI 이미지 자동채움 + 사업형태(셀러/소상공인) 템플릿.

비디오스튜류 벤치마크 반영:
  A1 본문(내레이션)을 문장 단위 '씬'으로 분할
  A2 씬별 TTS 길이를 측정해 씬 지속시간 자동 결정(자막·음성 싱크)
  B3 PIL 자막(Pretendard, 핵심 키워드 색강조)  B4 켄번스 줌  B5 0~3초 훅 + CTA 아웃트로
  C6 사진 부족 시 AI 이미지 자동 생성으로 채움   C7 셀러=구매 CTA / 소상공인=방문 CTA
  D8 9:16 세로(1080x1920)
실패 시 기존 슬라이드쇼로 graceful 폴백(영상이 아예 안 나오는 일은 없게).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import uuid

from app.domain.models import (Asset, Channel, ContentKind, ContentPiece,
                               ContentStatus, Tenant)
from app.generators.base import Generator
from app.generators.text_claude import MODEL, _call_llm, _parse_sections
from app.industries import resolve_industry
from app.strategies import resolve_strategy, buy_block
from app.formats import pick_format, format_directive
from app.media import bgm as bgm_lib
from app.media import tts as tts_lib
from app.media import ai_image
from app import seo

W, H, FPS = 1080, 1920, 30
MAX_SCENES = 6           # 씬(=문장) 최대 — TTS 호출/길이 제어
MAX_AI_FILL = 2          # 사진 부족 시 AI 이미지 생성 최대 장수(비용 제어)
MIN_SCENE, MAX_SCENE = 2.2, 9.0   # 씬 길이 클램프(초) — 음성이 잘리지 않게 상한 넉넉히
PER_IMAGE_SECONDS = 3
MAX_SHORT_SECONDS = 58

_FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "fonts")
_SYS_FONTS = [
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
]


def _per_image(n: int) -> float:
    n = max(n, 1)
    return min(PER_IMAGE_SECONDS, MAX_SHORT_SECONDS / n)


def _font_path(weight: str = "Bold") -> str | None:
    p = os.path.join(_FONT_DIR, f"Pretendard-{weight}.otf")
    if os.path.exists(p):
        return p
    for f in _SYS_FONTS:
        if os.path.exists(f):
            return f
    return None


def _pil_font(size: int, weight: str = "Bold"):
    from PIL import ImageFont
    fp = _font_path(weight)
    try:
        return ImageFont.truetype(fp, size) if fp else ImageFont.load_default()
    except Exception:
        return ImageFont.load_default()


def _probe_dur(path: str) -> float:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", path], capture_output=True, timeout=20)
        return float(r.stdout.decode().strip() or 0)
    except Exception:
        return 0.0


def _split_sentences(text: str) -> list[str]:
    """내레이션/본문을 문장 단위로 분할(씬 텍스트)."""
    text = re.sub(r"\[[^\]]*\]", " ", text or "")        # [사진N] 등 마커 제거
    text = re.sub(r"#\S+", " ", text)                    # 해시태그 제거
    parts = re.split(r"(?<=[.!?。！？])\s+|\n+", text)
    out = []
    for s in parts:
        s = s.strip(" -·•\t")
        if len(s) >= 4:
            out.append(s)
    return out


# ── 브랜드 테마(사업형태별) + ASS 카라오케 자막 + 로고 ──
_THEME = {"seller": (245, 179, 1), "local": (16, 185, 129), "hybrid": (99, 102, 241)}


def _theme_rgb(key: str):
    return _THEME.get(key or "local", _THEME["local"])


def _ass_color(rgb) -> str:
    r, g, b = rgb
    return f"&H00{b:02X}{g:02X}{r:02X}"


def _ts(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600); sec -= h * 3600
    m = int(sec // 60); s = sec - m * 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _build_ass(scenes, kws, theme_key, out) -> str:
    """본문 씬을 단어 단위 카라오케 자막(.ass)으로 — 말하는 단어가 차오르며 강조(프로 시그니처)."""
    sung, unsung = "&H00FFFFFF", "&H00B8B8B8"
    theme = _ass_color(_theme_rgb(theme_key))
    kws_low = [k.lower() for k in (kws or []) if k and len(k) >= 2]
    head = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, "
        "Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Cap,Pretendard,84,{sung},{unsung},&H00141014,&H64000000,-1,0,0,0,100,100,0,0,"
        "1,6,3,2,90,90,360,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    lines = []
    for start, dur, text in scenes:
        words = [w for w in re.split(r"\s+", (text or "").strip()) if w]
        if not words:
            continue
        tot = sum(max(1, len(w)) for w in words)
        body = ""
        for w in words:
            cs = max(8, int(round(dur * 100 * len(w) / tot)))   # 단어별 강조 시간(센티초)
            wl = w.lower()
            hot = any((k in wl) or (wl in k) for k in kws_low) if kws_low else False
            if hot:   # 키워드는 테마색으로 강조
                body += "{\\1c" + theme + "\\k" + str(cs) + "}" + w + "{\\1c" + sung + "} "
            else:
                body += "{\\k" + str(cs) + "}" + w + " "
        lines.append("Dialogue: 0," + _ts(start) + "," + _ts(start + dur) + ",Cap,,0,0,0,," + body.strip())
    with open(out, "w") as f:
        f.write(head + "\n".join(lines) + "\n")
    return out


def _brand_logo_png(out, theme_key) -> str:
    """우상단 로고 워터마크(브랜드 일관성)."""
    from PIL import Image, ImageDraw
    rgb = _theme_rgb(theme_key)
    img = Image.new("RGBA", (340, 104), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 8, 340, 96], 44, fill=(10, 12, 20, 150))   # 어떤 배경에서도 보이게 다크 pill
    d.rounded_rectangle([18, 24, 82, 88], 16, fill=rgb + (255,))
    d.line([30, 72, 44, 52, 56, 62, 74, 38], fill="white", width=7, joint="curve")
    d.ellipse([68, 34, 80, 46], fill="white")
    f = _pil_font(46, "ExtraBold")
    d.text((100, 32), "올린다", font=f, fill=(255, 255, 255, 245))
    img.save(out)
    return out


class ShortVideoGenerator(Generator):
    kind = ContentKind.SHORT

    def __init__(self, model: str = MODEL):
        self.model = model

    def generate(self, tenant: Tenant, asset: Asset,
                 images: list[str] | None = None) -> ContentPiece:
        imgs = [p for p in (images or [asset.path]) if p and os.path.exists(p)][:8]
        prof = resolve_industry(tenant.industry)
        strat = resolve_strategy(tenant)
        kws = seo.target_keywords(prof.name, tenant.region, asset.note,
                                  axis=strat.keyword_axis, brand=tenant.brand_name)
        buy = buy_block(tenant)
        cta_hint = (f"마지막 자막/내레이션은 구매 유도: {buy}" if strat.closing in ("buy", "both") and buy
                    else "마지막 자막/내레이션은 방문·예약 유도(지역/연락)")
        fmt = pick_format(strat.key, asset.note)   # 이미 터진 영상의 검증된 포맷 접목
        prompt = (
            f"[가게] {tenant.name} ({prof.name}, {tenant.region})\n"
            f"[사업형태] {strat.label} — {strat.goal}\n"
            f"[페르소나] {prof.persona}\n[입력 정보] {asset.note}\n[사진 {len(imgs)}장]\n"
            f"[CTA] {strat.cta}\n{cta_hint}\n"
            f"{seo.speaker_frame(strat.key)}\n"
            f"{format_directive(fmt)}\n"
            f"{seo.keywords_line(kws)}\n\n"
            f"{seo.SHORT_DIRECTIVES_SELLER if strat.key == 'seller' else seo.SHORT_DIRECTIVES}\n{seo.FACTS_RULE}\n\n"
            "위 규칙으로 인스타 릴스/유튜브 쇼츠를 기획하라. 아래 형식 그대로(대괄호 머리표 유지):\n"
            "[제목]\n(후킹 제목)\n[길이]\n(예: 25초)\n[플랫폼]\n(인스타 릴스/유튜브 쇼츠)\n"
            "[훅]\n(0~3초에 띄울 강한 한 줄, 12자 내외)\n"
            "[내레이션]\n(한 문장씩 줄바꿈. 각 문장이 한 장면이 됨. 5~6문장, 구어체, 마지막은 CTA)\n"
            "[장면]\n1) 0-3초 | 비주얼: .. | 자막: .. | 내레이션: ..\n2) .."
        )
        raw = _call_llm(prompt, self.model, 1500)
        d = _parse_sections(raw, ["제목", "길이", "플랫폼", "훅", "내레이션", "장면"])
        scenes_meta = _parse_scenes(d.get("장면", ""))
        title = d.get("제목") or (asset.note[:30] or "shorts")
        hook = (d.get("훅") or (scenes_meta[0]["on_screen_text"] if scenes_meta else asset.note[:18])).strip()
        narration = d.get("내레이션", "")

        # 씬 텍스트 = 내레이션 문장(없으면 장면 자막 → 메모)
        sent = _split_sentences(narration)
        if not sent:
            sent = [s["on_screen_text"] for s in scenes_meta if s.get("on_screen_text")]
        if not sent:
            sent = _split_sentences(asset.note) or [asset.note[:30] or title]
        sent = sent[:MAX_SCENES]

        if strat.closing in ("buy", "both") and buy:
            outro_cta = buy                                    # 구매 링크
        elif (getattr(tenant, "biz_type", "local") or "local") == "seller":
            outro_cta = "🔗 프로필 링크에서 구매하세요"
        else:
            outro_cta = (f"📍 네이버 '{tenant.name}' 검색\n방문·예약 환영" if tenant.name else "방문·예약 환영")

        video_path, note, dur_sec, cover_path = self._build_scene_video(
            imgs, hook, sent, kws, tenant, strat, title, outro_cta)
        # 폴백: 씬 파이프라인 실패 → 기존 슬라이드쇼 + 단일자막 + 오디오
        if not video_path:
            per = _per_image(len(imgs))
            video_path, note = self._assemble_legacy(imgs, hook, tenant.id, per)
            video_path, _t, _b, _ = self._add_audio(video_path, narration, tenant.id)
            dur_sec = round(max(len(imgs), 1) * per)
            cover_path = imgs[0] if imgs else asset.path
        # 다중 화면비(1:1·4:5) 변형 자동 생성 (#1)
        out_dir = os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage"), tenant.id)
        variants = self._aspect_variants(video_path, out_dir) if video_path else {}

        return ContentPiece(
            id=str(uuid.uuid4()), tenant_id=tenant.id, asset_id=asset.id,
            channel=Channel.YOUTUBE, kind=self.kind,
            payload={
                "title": title, "video_title": title,
                "duration": d.get("길이", f"{dur_sec}초"),
                "target_platform": d.get("플랫폼", "인스타 릴스/유튜브 쇼츠"),
                "hook_strategy": hook, "subtitle": hook,
                "narration": narration, "scenes": scenes_meta, "script": raw,
                "scene_texts": sent, "outro_cta": outro_cta, "viral_format": fmt.name,
                "trending_sound_tip": "발행 시 인스타/유튜브 앱에서 '트렌딩 사운드'를 입히면 도달이 크게 늘어요(공식 API 미지원→앱에서 1탭).",
                "biz_type": strat.key, "target_keywords": kws,
                "video_path": video_path, "image_path": imgs[0] if imgs else asset.path,
                "image_paths": imgs, "duration_sec": dur_sec, "cover_path": cover_path,
                "video_variants": variants,    # {square, feed45} 다중 화면비
                "assemble_note": note,
            },
            status=ContentStatus.DRAFT)

    # ───────────────────── 씬 기반 빌드 (핵심) ─────────────────────
    def _build_scene_video(self, imgs, hook, sentences, kws, tenant, strat, title, outro_cta):
        """글→씬 변환 영상. 영상은 씬별 클립으로, 오디오는 '하나의 연속 트랙'으로 만들어
        정확히 mux → 씬마다 음성·화면이 어긋나지 않음. 성공 시 (path,note,dur)."""
        if not shutil.which("ffmpeg"):
            return None, "ffmpeg 미설치", 0, None
        try:
            from PIL import Image  # noqa: F401
        except Exception:
            return None, "Pillow 미설치", 0, None
        out_dir = os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage"), tenant.id)
        work = os.path.join(out_dir, f"scenes_{uuid.uuid4().hex}")
        os.makedirs(work, exist_ok=True)
        try:
            visuals = self._visuals_for(imgs, sentences, kws, work, strat.key)
            if not visuals:
                return None, "사용 가능한 이미지 없음", 0, None
            vclips: list[str] = []     # 영상(무음) 클립
            awavs: list[str] = []      # 씬별 오디오(PCM, 정확히 dur초)
            ass_scenes = []            # (start, dur, text) — 본문 자막 타이밍
            t = 0.0
            # 0) 훅 카드(펀치인 줌)
            hook_png = os.path.join(work, "hook.png")
            self._card_png(hook_png, big=hook or title, small=tenant.name,
                           accent=strat.key, kind="hook")
            hook_tts = tts_lib.synthesize(hook, work) if hook else None
            ht = _probe_dur(hook_tts) if hook_tts else 0
            hdur = self._clamp((ht + 0.5) if ht > 0.3 else (len(hook or "") * 0.14 + 1.4))
            v = self._scene_card_video(hook_png, hdur, os.path.join(work, "v_hook.mp4"), punch=True)
            aw = self._audio_segment(hook_tts, hdur, os.path.join(work, "a_hook.wav"))
            if v and aw:
                vclips.append(v); awavs.append(aw); t += hdur
            # 1) 본문 씬들 — 자막은 ASS 카라오케로 별도(여기선 영상+켄번스+색보정만)
            for i, text in enumerate(sentences):
                img = visuals[i % len(visuals)]
                seg_tts = tts_lib.synthesize(text, work)
                td = _probe_dur(seg_tts) if seg_tts else 0
                sdur = self._clamp((td + 0.4) if td > 0.3 else (len(text) * 0.13 + 1.2))
                v = self._scene_video(img, sdur, i, os.path.join(work, f"v{i}.mp4"))
                aw = self._audio_segment(seg_tts, sdur, os.path.join(work, f"a{i}.wav"))
                if v and aw:
                    ass_scenes.append((t, sdur, text))
                    vclips.append(v); awavs.append(aw); t += sdur
            # 2) 아웃트로 CTA 카드(무음) — 셀러는 판매 QR(추적링크) 삽입 → 스캔 시 성과 집계
            qr_url = ""
            if strat.key == "seller":
                dest = getattr(tenant, "buy_url", "") or getattr(tenant, "map_url", "")
                if dest:
                    try:
                        from app import db as _db
                        _base = os.environ.get("SHOPCAST_BASE", "https://ollinda.kr").rstrip("/")
                        _tl = _db.ensure_track_link(tenant.id, dest, "스토어")
                        qr_url = (_base + "/r/" + _tl["code"]) if _tl else dest
                    except Exception:
                        qr_url = dest
            outro_png = os.path.join(work, "outro.png")
            self._card_png(outro_png, big=outro_cta, small=tenant.name,
                           accent=strat.key, kind="outro", qr_url=qr_url)
            odur = 2.8
            v = self._scene_card_video(outro_png, odur, os.path.join(work, "v_outro.mp4"))
            aw = self._audio_segment(None, odur, os.path.join(work, "a_outro.wav"))
            if v and aw:
                vclips.append(v); awavs.append(aw); t += odur
            if not vclips:
                return None, "씬 클립 생성 실패", 0, None
            total = t
            # 3) 영상 concat(copy) + 오디오 concat(PCM copy — 드리프트 없음)
            video_only = self._concat(vclips, os.path.join(work, "video.mp4"))
            full_wav = self._concat(awavs, os.path.join(work, "audio.wav"))
            if not (video_only and full_wav):
                return None, "concat 실패", 0, None
            # 4) ASS 단어자막 + 로고 워터마크 + 진행바 오버레이
            ass = _build_ass(ass_scenes, kws, strat.key, os.path.join(work, "cap.ass"))
            logo = _brand_logo_png(os.path.join(work, "logo.png"), strat.key)
            fx = self._post_overlay(video_only, ass, logo, total, strat.key)
            # 5) 영상+연속오디오 mux (+BGM) — 길이 동일 → 정확히 싱크
            final = self._mux(fx, full_wav, out_dir)
            # 6) 커버(썸네일) = 훅 카드
            cover = os.path.join(out_dir, f"cover_{uuid.uuid4().hex}.png")
            try:
                shutil.copy(hook_png, cover)
            except Exception:
                cover = None
            try:
                shutil.rmtree(work, ignore_errors=True)   # 씬 작업폴더(wav·중간mp4·ass) 정리 — 디스크 누수 차단
            except Exception:
                pass
            note = (f"씬 {len(sentences)}개 · 단어자막(ASS) · 켄번스+색보정 · 로고/진행바 · 브랜드테마 · "
                    f"{'TTS싱크' if tts_lib.configured() else '무음'}"
                    f"{' · AI이미지' if len(visuals) > len(imgs) else ''}")
            return final, note, round(total), cover
        except Exception as e:
            try:
                shutil.rmtree(work, ignore_errors=True)   # 실패해도 작업폴더 정리
            except Exception:
                pass
            return None, f"씬 빌드 오류: {str(e)[:120]}", 0, None

    def _clamp(self, v: float) -> float:
        return max(MIN_SCENE, min(MAX_SCENE, v or MIN_SCENE))

    def _visuals_for(self, imgs, sentences, kws, work, theme_key="local") -> list[str]:
        """씬 수에 맞춰 비주얼 확보. 사진 부족→AI 이미지(최대 MAX_AI_FILL),
        사진 0장→그라데이션 텍스트카드 배경(정보카드형 영상 #4)."""
        vis = list(imgs)
        need = min(len(sentences), MAX_SCENES)
        if len(vis) < need and len(vis) < 3:
            base_kw = ", ".join(kws[:3]) or "제품, 매장"
            for j in range(min(MAX_AI_FILL, need - len(vis))):
                prompt = (f"고품질 세로형 사진, {base_kw}, 한국 소상공인/제품 마케팅용, "
                          f"밝고 선명, 텍스트 없음, 광고 감성 #{j+1}")
                p = ai_image.generate(prompt, work)
                if p and os.path.exists(p):
                    vis.append(p)
        if not vis:   # 사진이 아예 없으면 → 텍스트카드 배경으로 영상 구성
            for j in range(max(1, need)):
                cp = os.path.join(work, f"cardbg{j}.png")
                self._gradient_bg(cp, j, theme_key)
                vis.append(cp)
        return vis

    def _gradient_bg(self, out, idx, theme_key="local") -> None:
        """텍스트카드형 배경(사진 없을 때) — 테마색 그라데이션."""
        from PIL import Image, ImageDraw
        rgb = _theme_rgb(theme_key)
        dark = (12, 14, 22)
        c2 = tuple(int(rgb[k] * 0.45 + dark[k] * 0.55) for k in range(3))
        top = ((28, 24, 46), c2) if idx % 2 == 0 else (c2, (16, 16, 26))
        img = Image.new("RGB", (W, H), top[0]); ov = Image.new("RGB", (W, H), top[1])
        m = Image.new("L", (W, H)); md = ImageDraw.Draw(m)
        for y in range(H):
            md.line([(0, y), (W, y)], fill=int(255 * y / H))
        img.paste(ov, (0, 0), m)
        img.save(out)

    def _aspect_variants(self, video, out_dir) -> dict:
        """9:16 최종본 → 1:1(피드)·4:5(피드) 자동 리사이즈(블러 배경). #1 다중 화면비."""
        out = {}
        if not (video and os.path.exists(video) and shutil.which("ffmpeg")):
            return out
        os.makedirs(out_dir, exist_ok=True)
        for key, (tw, th) in {"square": (1080, 1080), "feed45": (1080, 1350)}.items():
            dst = os.path.join(out_dir, f"{key}_{uuid.uuid4().hex}.mp4")
            fc = (f"[0:v]split=2[a][b];"
                  f"[b]scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th},boxblur=22:2[bg];"
                  f"[a]scale={tw}:{th}:force_original_aspect_ratio=decrease[fg];"
                  f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v]")
            cmd = ["ffmpeg", "-y", "-i", video, "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
                   "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1", "-pix_fmt", "yuv420p", "-c:a", "aac", dst]
            r = subprocess.run(cmd, capture_output=True, timeout=180)
            if r.returncode == 0 and os.path.exists(dst):
                out[key] = dst
        return out

    # ───────────────────── PIL 렌더 ─────────────────────
    def _caption_png(self, out: str, text: str, kws: list[str]) -> None:
        """하단 자막 PNG(투명 1080x1920). 키워드는 강조색. 둥근 반투명 박스."""
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        font = _pil_font(62, "Bold")
        accent = (255, 224, 77)        # 키워드 강조(노랑)
        lines = self._wrap_lines(d, text, font, W - 150)[:4]
        lh = 84
        block_h = lh * len(lines)
        y0 = H - 470 - block_h
        # 반투명 박스
        pad = 34
        d.rounded_rectangle([60, y0 - pad, W - 60, y0 + block_h + pad - 10], 28,
                            fill=(10, 12, 20, 165))
        kw_low = [k.lower() for k in kws if k]
        for li, line in enumerate(lines):
            self._draw_highlighted(d, line, font, y0 + li * lh, kw_low, accent)
        img.save(out)

    def _draw_highlighted(self, d, line, font, y, kw_low, accent):
        """한 줄을 가운데 정렬해 그리되, 키워드 토큰만 강조색."""
        toks = self._tokenize(line, kw_low)
        total = sum(d.textlength(t[0], font=font) for t in toks)
        x = (W - total) / 2
        for txt, hot in toks:
            col = accent if hot else (255, 255, 255)
            # 외곽선(가독성)
            for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
                d.text((x + dx, y + dy), txt, font=font, fill=(0, 0, 0, 220))
            d.text((x, y), txt, font=font, fill=col)
            x += d.textlength(txt, font=font)

    def _tokenize(self, line: str, kw_low: list[str]):
        """라인을 (텍스트, 강조여부) 런으로 분할 — 키워드 부분만 True."""
        if not kw_low:
            return [(line, False)]
        low = line.lower()
        marks = [False] * len(line)
        for kw in kw_low:
            start = 0
            while kw and (idx := low.find(kw, start)) != -1:
                for i in range(idx, idx + len(kw)):
                    marks[i] = True
                start = idx + len(kw)
        runs, cur, curm = [], "", None
        for ch, m in zip(line, marks):
            if curm is None or m == curm:
                cur += ch; curm = m
            else:
                runs.append((cur, curm)); cur, curm = ch, m
        if cur:
            runs.append((cur, curm))
        return runs

    def _card_png(self, out: str, big: str, small: str, accent: str, kind: str, qr_url: str = "") -> None:
        """훅/아웃트로 풀스크린 카드(그라데이션 + 큰 문구 + 셀러 판매 QR)."""
        from PIL import Image, ImageDraw
        c1, c2 = ((18, 18, 30), (60, 30, 110)) if kind == "hook" else ((60, 30, 110), (12, 14, 22))
        if accent == "seller":
            c2 = (140, 90, 10) if kind == "hook" else (12, 14, 22)
        img = Image.new("RGB", (W, H), c1)
        top = Image.new("RGB", (W, H), c2)
        mask = Image.new("L", (W, H))
        md = ImageDraw.Draw(mask)
        for y in range(H):
            md.line([(0, y), (W, y)], fill=int(255 * y / H))
        img.paste(top, (0, 0), mask)
        d = ImageDraw.Draw(img)
        tag = "잠깐!" if kind == "hook" else "지금"
        ft = _pil_font(48, "ExtraBold")
        d.text(((W - d.textlength(tag, font=ft)) / 2, H // 2 - 360), tag,
               font=ft, fill=(255, 224, 77))
        fb = _pil_font(92, "ExtraBold")
        lines = self._wrap_lines(d, big, fb, W - 160)[:4]
        y = H // 2 - 180
        for ln in lines:
            d.text(((W - d.textlength(ln, font=fb)) / 2, y), ln, font=fb, fill="white")
            y += 120
        if small:
            fs = _pil_font(50, "SemiBold")
            d.text(((W - d.textlength(small, font=fs)) / 2, y + 40), small,
                   font=fs, fill=(200, 205, 230))
            y += 100
        # 셀러 판매 QR — 영상 끝에서 손님이 폰으로 스캔 → 바로 스토어
        if qr_url and kind == "outro":
            try:
                import qrcode
                qsz = 340
                qr = qrcode.make(qr_url).convert("RGB").resize((qsz, qsz))
                pad = Image.new("RGB", (qsz + 44, qsz + 44), "white")
                pad.paste(qr, (22, 22))
                qx, qy = (W - qsz - 44) // 2, y + 120
                img.paste(pad, (qx, qy))
                fq = _pil_font(46, "ExtraBold")
                cap = "스캔하면 바로 구매 →"
                d.text(((W - d.textlength(cap, font=fq)) / 2, qy + qsz + 70), cap,
                       font=fq, fill=(255, 224, 77))
            except Exception:
                pass
        img.save(out)

    def _wrap_lines(self, d, text, font, maxw):
        out, cur = [], ""
        for ch in text:
            if ch == "\n":
                if cur:
                    out.append(cur); cur = ""
                continue
            if d.textlength(cur + ch, font=font) <= maxw:
                cur += ch
            else:
                out.append(cur); cur = ch
        if cur:
            out.append(cur)
        return out

    # ───────────────────── ffmpeg: 영상(무음) + 오디오(연속) 분리 ─────────────────────
    def _fade(self, dur: float) -> str:
        """씬 전환용 페이드 인/아웃(딥) — 클립 길이 불변이라 오디오 싱크 영향 없음."""
        if dur < 0.9:
            return ""
        return f",fade=t=in:st=0:d=0.22,fade=t=out:st={max(0.0, dur - 0.25):.2f}:d=0.22"

    def _scene_video(self, img, dur, idx, out) -> str | None:
        """이미지 → 켄번스 + 색보정(통일감) + 페이드 전환, 정확히 dur초 무음 영상. 자막은 ASS로 별도."""
        frames = max(1, int(dur * FPS))
        zdir = "min(zoom+0.0012,1.12)" if idx % 2 == 0 else "if(eq(on,1),1.12,max(zoom-0.0012,1.0))"
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,"
              f"eq=contrast=1.06:saturation=1.12:brightness=0.02,"
              f"zoompan=z='{zdir}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
              f"d={frames}:s={W}x{H}:fps={FPS}" + self._fade(dur))
        cmd = ["ffmpeg", "-y", "-loop", "1", "-t", f"{dur:.2f}", "-i", img, "-vf", vf,
               "-map", "0:v", "-t", f"{dur:.2f}", "-r", str(FPS), "-pix_fmt", "yuv420p",
               "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1", "-an", out]
        r = subprocess.run(cmd, capture_output=True, timeout=120)
        return out if (r.returncode == 0 and os.path.exists(out)) else None

    def _scene_card_video(self, png, dur, out, punch=False) -> str | None:
        """카드(훅/아웃트로) → 정확히 dur초 무음 영상. punch=True면 천천히 줌인."""
        frames = max(1, int(dur * FPS))
        if punch:
            vf = (f"scale={W}:{H},setsar=1,zoompan=z='min(zoom+0.0018,1.10)':"
                  f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS}")
        else:
            vf = f"scale={W}:{H},setsar=1,fps={FPS}"
        vf += self._fade(dur)
        cmd = ["ffmpeg", "-y", "-loop", "1", "-t", f"{dur:.2f}", "-i", png, "-vf", vf,
               "-t", f"{dur:.2f}", "-r", str(FPS), "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1", "-an", out]
        r = subprocess.run(cmd, capture_output=True, timeout=120)
        return out if (r.returncode == 0 and os.path.exists(out)) else None

    def _post_overlay(self, video, ass, logo, total, theme_key) -> str:
        """ASS 단어자막 + 로고 워터마크 + 상단 진행바 합성. 단계적 폴백(자막 우선 보존)."""
        rgb = _theme_rgb(theme_key)
        hexcol = "0x%02X%02X%02X" % rgb
        out = os.path.join(os.path.dirname(video), "video_fx.mp4")
        assp = ass.replace("\\", "/")
        fontsdir = _FONT_DIR.replace("\\", "/")
        subs = f"subtitles=filename='{assp}':fontsdir='{fontsdir}'"
        bar = f"drawbox=x=0:y=0:w='iw*t/{total:.2f}':h=12:color={hexcol}@0.92:t=fill"
        attempts = [
            f"[0:v]{subs},{bar}[bv];[bv][1:v]overlay=W-w-26:28[v]",   # 자막+진행바+로고
            f"[0:v]{subs}[bv];[bv][1:v]overlay=W-w-26:28[v]",         # 자막+로고
        ]
        for fc in attempts:
            # 로고는 -loop 1 로 전 구간 유지. 길이는 -t {total}로 정확히 고정(-shortest 금지: 무한 로고와 충돌해 잘림)
            cmd = ["ffmpeg", "-y", "-i", video, "-loop", "1", "-i", logo,
                   "-filter_complex", fc, "-map", "[v]", "-t", f"{total:.2f}", "-r", str(FPS),
                   "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1", out]
            r = subprocess.run(cmd, capture_output=True, timeout=240)
            if r.returncode == 0 and os.path.exists(out) and _probe_dur(out) > total * 0.8:
                return out
        return video   # 전부 실패 시 원본(자막 없이) 반환

    def _audio_segment(self, tts, dur, out_wav) -> str | None:
        """그 씬 오디오를 정확히 dur초 PCM으로. TTS 있으면 사용, 없거나 실패하면 무음으로 폴백
        (절대 None으로 두지 않아 씬이 드롭되지 않음 → TTS 장애에도 풀길이 보장)."""
        if tts and os.path.exists(tts) and os.path.getsize(tts) > 200:
            cmd = ["ffmpeg", "-y", "-i", tts, "-af", "apad", "-t", f"{dur:.2f}",
                   "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", out_wav]
            r = subprocess.run(cmd, capture_output=True, timeout=60)
            if r.returncode == 0 and os.path.exists(out_wav):
                return out_wav
        # 폴백: 무음
        cmd = ["ffmpeg", "-y", "-f", "lavfi", "-t", f"{dur:.2f}",
               "-i", "anullsrc=r=44100:cl=stereo", "-c:a", "pcm_s16le", out_wav]
        r = subprocess.run(cmd, capture_output=True, timeout=60)
        return out_wav if (r.returncode == 0 and os.path.exists(out_wav)) else None

    def _concat(self, files, out) -> str | None:
        """동일 규격 파일들을 concat. PCM/동일코덱이라 copy로 무손실·무드리프트."""
        listf = out + ".list.txt"
        with open(listf, "w") as f:
            for c in files:
                f.write(f"file '{os.path.abspath(c)}'\n")
        r = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listf,
                            "-c", "copy", out], capture_output=True, timeout=240)
        if (r.returncode != 0 or not os.path.exists(out)) and out.endswith(".mp4"):
            r = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listf,
                                "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1", "-pix_fmt", "yuv420p", "-an", out],
                               capture_output=True, timeout=300)
        return out if os.path.exists(out) else None

    def _mux(self, video, full_wav, out_dir) -> str:
        """무음영상 + 연속오디오(+BGM) → 최종. 둘 길이가 같아 정확히 싱크."""
        bgm = bgm_lib.pick()
        out = os.path.join(out_dir, f"short_{uuid.uuid4().hex}.mp4")
        if bgm:
            cmd = ["ffmpeg", "-y", "-i", video, "-i", full_wav, "-stream_loop", "-1", "-i", bgm,
                   "-filter_complex", "[2:a]volume=0.10[b];[1:a][b]amix=inputs=2:duration=first[a]",
                   "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-ar", "44100",
                   "-shortest", out]
        else:
            cmd = ["ffmpeg", "-y", "-i", video, "-i", full_wav, "-map", "0:v", "-map", "1:a",
                   "-c:v", "copy", "-c:a", "aac", "-ar", "44100", "-shortest", out]
        r = subprocess.run(cmd, capture_output=True, timeout=180)
        return out if (r.returncode == 0 and os.path.exists(out)) else video

    # ───────────────────── 레거시 폴백 ─────────────────────
    def _add_audio(self, video_path, narration, tenant_id):
        if not (video_path and os.path.exists(video_path)):
            return video_path, None, None, "영상 없음"
        out_dir = os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage"), tenant_id)
        tts_path = tts_lib.synthesize(narration, out_dir)
        bgm_path = bgm_lib.pick()
        if not tts_path and not bgm_path:
            return video_path, None, None, "무음"
        out = os.path.join(out_dir, f"shortav_{uuid.uuid4().hex}.mp4")
        cmd = ["ffmpeg", "-y", "-i", video_path]
        if tts_path:
            cmd += ["-i", tts_path]
        if bgm_path:
            cmd += ["-stream_loop", "-1", "-i", bgm_path]
        if tts_path and bgm_path:
            fc, amap = "[2:a]volume=0.15[bg];[1:a][bg]amix=inputs=2:duration=first[a]", "[a]"
        elif tts_path:
            fc, amap = None, "1:a"
        else:
            fc, amap = "[1:a]volume=0.3[a]", "[a]"
        if fc:
            cmd += ["-filter_complex", fc]
        cmd += ["-map", "0:v", "-map", amap, "-c:v", "copy", "-c:a", "aac", "-shortest", out]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=120)
            if r.returncode != 0 or not os.path.exists(out):
                return video_path, tts_path, bgm_path, "오디오 합성 실패→무음"
            return out, tts_path, bgm_path, "오디오 합성됨"
        except Exception as e:
            return video_path, tts_path, bgm_path, f"오디오 오류: {str(e)[:60]}"

    def _assemble_legacy(self, images, subtitle, tenant_id, per=PER_IMAGE_SECONDS):
        if not shutil.which("ffmpeg"):
            return None, "ffmpeg 미설치"
        imgs = [p for p in images if p and os.path.exists(p)]
        if not imgs:
            return None, "원본 이미지 없음"
        out_dir = os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage"), tenant_id)
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, f"short_{uuid.uuid4().hex}.mp4")
        cmd = ["ffmpeg", "-y"]
        for p in imgs:
            cmd += ["-loop", "1", "-t", f"{per:.2f}", "-i", p]
        parts, labels = [], ""
        for i in range(len(imgs)):
            parts.append(f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                         f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[v{i}]")
            labels += f"[v{i}]"
        parts.append(f"{labels}concat=n={len(imgs)}:v=1:a=0[cat]")
        cmd += ["-filter_complex", ";".join(parts), "-map", "[cat]",
                "-r", str(FPS), "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1", out]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=180)
            if r.returncode != 0 or not os.path.exists(out):
                return None, "ffmpeg 실패: " + r.stderr.decode()[-120:]
            return out, f"{len(imgs)}장 슬라이드쇼(폴백)"
        except Exception as e:
            return None, f"영상 조립 오류: {str(e)[:100]}"


def _parse_scenes(block: str) -> list[dict]:
    scenes = []
    for line in block.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        time_range = parts[0].split(")", 1)[-1].strip() if ")" in parts[0] else parts[0]
        sc = {"time_range": time_range, "visual_description": "", "camera_movement": "",
              "on_screen_text": "", "narration_segment": ""}
        for p in parts[1:]:
            if p.startswith("비주얼:"):
                sc["visual_description"] = p[4:].strip()
            elif p.startswith("카메라:"):
                sc["camera_movement"] = p[4:].strip()
            elif p.startswith("자막:"):
                sc["on_screen_text"] = p[3:].strip()
            elif p.startswith("내레이션:"):
                sc["narration_segment"] = p[5:].strip()
        scenes.append(sc)
    return scenes
