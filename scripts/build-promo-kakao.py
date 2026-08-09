# 카카오톡 프로필 배경용 홍보영상 — 세로 1080×1920, 15초 이내, 무음 재생 전제(텍스트 완결).
# 사용: python3 scripts/build-promo-kakao.py
# 출력: ~/Downloads/올린다_홍보영상_카톡프로필.mp4 (가로 원본은 유지 — 별도 파일)
# 구성: 훅 카드 → 실생성물 세로 풀블리드 → 실측 1위 타임라인 → CTA. 비용 0(BGM만, TTS·Veo 없음).
import json
import os
import subprocess

ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT = os.path.expanduser("~/Downloads/올린다_홍보영상_카톡프로필.mp4")
BGM = os.path.join(ROOT, "app", "assets", "bgm", "warm_upbeat.mp3")
DEMO = os.path.join(ROOT, "app", "static", "demo", "local_short.mp4")   # 실생성물(세로)
WORK = "/tmp/ollinda-promo-kakao"
W, H = 1080, 1920
FONT = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
NAVY = (15, 23, 42)
INDIGO = (99, 102, 241)

# (길이초, 카드 or 클립)
SCENES = [
    (3.5, {"card": "hook"}),
    (4.5, {"clip": DEMO, "start": 0.8, "end": 5.3}),
    (3.5, {"card": "timeline"}),
    (3.5, {"card": "closing"}),
]


def sh(*cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:6])}... 실패:\n{r.stderr[-600:]}")


def _logo(d, cx, y, size):
    s = size / 32.0
    d.rounded_rectangle([cx - size / 2, y, cx + size / 2, y + size], radius=int(9 * s), fill=INDIGO)
    x0 = cx - size / 2
    d.line([(x0 + 8 * s, y + 21 * s), (x0 + 14 * s, y + 14 * s), (x0 + 18 * s, y + 18 * s),
            (x0 + 24 * s, y + 9 * s)], fill="white", width=int(2.6 * s), joint="curve")
    r = 2.3 * s
    d.ellipse([x0 + 24 * s - r, y + 9 * s - r, x0 + 24 * s + r, y + 9 * s + r], fill="white")


def card(kind, path):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (W, H), NAVY if kind == "hook" else (255, 255, 255))
    d = ImageDraw.Draw(img)
    big = ImageFont.truetype(FONT, 96)
    mid = ImageFont.truetype(FONT, 50)
    sml = ImageFont.truetype(FONT, 36)

    def center(y, txt, font, fill):
        w = d.textlength(txt, font=font)
        d.text(((W - w) / 2, y), txt, font=font, fill=fill)

    if kind == "hook":
        _logo(d, W // 2, 430, 120)
        center(700, "네이버에서", big, (255, 255, 255))
        center(820, "검색하면,", big, (255, 255, 255))
        center(980, "나오시나요?", big, (129, 140, 248))
        center(1200, "안 보이면, 없는 가게입니다", mid, (148, 163, 184))
    elif kind == "timeline":
        d.rectangle([0, 0, W, H], fill=(245, 243, 255))
        center(360, "실제 가게의 실제 기록", mid, (100, 116, 139))
        rows = [("7/31", "글 발행", 64, (71, 85, 105)),
                ("8/2", "블로그검색 12위", 64, (71, 85, 105)),
                ("8/9", "1위", 160, INDIGO)]
        y = 560
        from PIL import ImageFont as _F
        for i, (dt, label, size, color) in enumerate(rows):
            f = _F.truetype(FONT, size)
            d.ellipse([120 - 13, y + 28 - 13, 120 + 13, y + 28 + 13],
                      fill=INDIGO if i == 2 else (199, 210, 254))
            d.text((180, y), dt, font=_F.truetype(FONT, 52), fill=(148, 163, 184))
            d.text((400, y - (34 if i == 2 else 0)), label, font=f, fill=color)
            y += 170 if i < 1 else 230
        center(1330, "발행 9일 만에 네이버 블로그검색 1위", mid, NAVY)
        center(1430, "2026년 8월 실측 · 개별 결과는 다를 수 있어요", sml, (148, 163, 184))
    else:  # closing
        _logo(d, W // 2, 500, 140)
        center(780, "오늘 사진 한 장,", big, NAVY)
        center(900, "내일 손님으로", big, INDIGO)
        center(1100, "올린다 · ollinda.kr", mid, (100, 116, 139))
        center(1180, "가입 없이 무료 2회", sml, (148, 163, 184))
    img.save(path)


def main():
    os.makedirs(WORK, exist_ok=True)
    clips = []
    for i, (L, spec) in enumerate(SCENES):
        out = f"{WORK}/s{i}.mp4"
        if "clip" in spec:
            vf = (f"trim=start={spec['start']}:end={spec['end']},setpts=PTS-STARTPTS,"
                  f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps=30,"
                  f"tpad=stop_mode=clone:stop_duration={L:.2f},trim=end={L:.2f},format=yuv420p")
            sh("ffmpeg", "-y", "-v", "error", "-i", spec["clip"], "-vf", vf, "-an",
               "-c:v", "libx264", "-preset", "medium", "-crf", "20", out)
        else:
            png = f"{WORK}/c{i}.png"
            card(spec["card"], png)
            frames = int(L * 30)
            vf = (f"scale={W * 2}:{H * 2},zoompan=z='min(zoom+0.0005,1.06)':d={frames}"
                  f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps=30,"
                  f"trim=end={L:.2f},format=yuv420p")
            sh("ffmpeg", "-y", "-v", "error", "-loop", "1", "-t", f"{L + 0.5:.2f}", "-i", png,
               "-vf", vf, "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "20", out)
        clips.append(out)
    total = sum(L for L, _ in SCENES)
    open(f"{WORK}/vlist.txt", "w").write("".join(f"file '{p}'\n" for p in clips))
    sh("ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", f"{WORK}/vlist.txt",
       "-stream_loop", "-1", "-i", BGM,
       "-vf", f"fade=t=in:d=0.4,fade=t=out:st={total-0.8:.2f}:d=0.8",
       "-af", f"volume=0.5,atrim=0:{total:.2f},afade=t=out:st={total-1.2:.2f}:d=1.2,"
              "loudnorm=I=-16:TP=-1.5:LRA=11",
       "-t", f"{total:.2f}",
       "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", OUT)
    print("✅", OUT, os.path.getsize(OUT) // 1024, "KB, 총", total, "초")


main()
