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

# (길이초, 카드 or 클립) — 관심 유도 구성(2026-08-09 사장님 지시): 결과 숫자 선공개 →
# 증거 영상 → 비결 공개 → 낮은 문턱 CTA. 성과 주장은 실측(9일 1위)만, 면책 병기.
SCENES = [
    (3.5, {"card": "hook"}),
    (4.5, {"clip": DEMO, "start": 0.8, "end": 5.3}),
    (3.5, {"card": "secret"}),
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

    from PIL import ImageFont as _F
    huge = _F.truetype(FONT, 190)
    if kind == "hook":
        # 결과 숫자 선공개 — 스크롤을 멈추게 하는 건 질문이 아니라 숫자다
        center(520, "9일 만에,", big, (255, 255, 255))
        center(680, "네이버", huge, (255, 255, 255))
        center(880, "검색 1위", huge, (129, 140, 248))
        center(1180, "부산 실제 가게의 기록입니다", mid, (203, 213, 225))
        center(1270, "2026년 8월 실측 · 결과는 가게마다 달라요", sml, (100, 116, 139))
    elif kind == "secret":
        center(560, "비결은,", mid, (100, 116, 139))
        center(680, "사진 한 장", huge, NAVY)
        center(950, "글 · 영상 · 발행 준비 · 순위 관리", mid, (71, 85, 105))
        center(1040, "나머지는 전부 AI가 합니다", mid, INDIGO)
    else:  # closing
        _logo(d, W // 2, 500, 140)
        center(780, "내 가게도 되는지,", big, NAVY)
        center(900, "무료로 확인해보세요", big, INDIGO)
        center(1120, "ollinda.kr", _F.truetype(FONT, 76), NAVY)
        center(1240, "가입 없이 무료 2회 · 사진만 올리면 끝", mid, (100, 116, 139))
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
