# 홍보 영상 빌드 — 광고 톤(훅→문제→약속→증거→가격→CTA). 사용설명 영상과 별개.
# 사용: ELEVENLABS_API_KEY=... python3 scripts/build-promo-video.py
# 출력: ~/Downloads/올린다_홍보영상.mp4 (배포 아님 — 영업·공유용)
# 정직: 성과 주장은 실측 타임라인(7/31→8/9 1위)만, 면책 병기. 결과물 증거는 실제 생성 영상.
# 비용: TTS 약 400자(~$0.12) — Veo 0(기존 산출물 재사용).
import json
import os
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT = os.path.expanduser("~/Downloads/올린다_홍보영상.mp4")
BGM = os.path.join(ROOT, "app", "assets", "bgm", "warm_upbeat.mp3")
DEMO = os.path.join(ROOT, "app", "static", "demo", "local_short.mp4")   # 실제 생성물(EV6 무빙)
WORK = "/tmp/ollinda-promo"
VOICE = os.environ.get("ELEVENLABS_VOICE_ID", "hpp4J3VqNfWAUOO0d1Us").strip()   # Bella
KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
W, H = 1920, 1080
FONT = "/System/Library/Fonts/AppleSDGothicNeo.ttc"

NAVY = (15, 23, 42)
INDIGO = (99, 102, 241)
LIGHT = (238, 242, 255)

# (나레이션, 카드 스펙 or 실사 클립)
SCENES = [
    ("사장님, 네이버에서 가게를 검색하면, 나오시나요?",
     {"card": "dark", "lines": [("네이버에서 검색하면,", "w"), ("나오시나요?", "i")]}),
    ("손님은 검색해서 옵니다. 검색에 안 보이면, 없는 가게나 마찬가지죠.",
     {"card": "dark", "lines": [("검색에 안 보이면,", "w"), ("없는 가게입니다", "i")],
      "sub": "손님은 네이버에서 검색하고, 첫 화면의 가게로 갑니다"}),
    ("올린다는 다릅니다. 사진 한 장만 올리세요. 나머지는 전부 자동입니다.",
     {"card": "light", "lines": [("사진 한 장이면", "n"), ("끝", "i")],
      "sub": "글 · 영상 · 발행 준비 — 전부 올린다가 합니다"}),
    ("네이버에 유리한 글과, 이런 영상까지 만들어 드립니다.",
     {"clip": DEMO, "start": 0.8, "end": 7.5}),
    ("실제 가게의 실제 기록. 발행 9일 만에, 네이버 검색 1위.",
     {"card": "timeline"}),
    ("발행 후에도 매일 지켜보고, 순위가 떨어지면 고친 글을 먼저 가져옵니다.",
     {"card": "dark", "lines": [("떨어지는 순간,", "w"), ("먼저 압니다", "i")],
      "sub": "매일 순위 실측 · 떨어지면 개선안을 자동 제안"}),
    ("대행 월 삼사십만 원 대신, 월 십이만 구천 원부터.",
     {"card": "light", "lines": [("월 129,000원부터", "i")],
      "sub": "블로그 대행 월 30~50만 원과 비교해 보세요 · 영상까지 포함"}),
    ("올린다. 오늘 사진 한 장, 내일 손님으로.",
     {"card": "closing"}),
]


def sh(*cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:6])}... 실패:\n{r.stderr[-800:]}")
    return r.stdout


def dur(path):
    return float(json.loads(sh("ffprobe", "-v", "quiet", "-of", "json",
                               "-show_format", path))["format"]["duration"])


def tts(text, path):
    import urllib.request
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE}",
        data=json.dumps({"text": text, "model_id": "eleven_multilingual_v2",
                         "voice_settings": {"stability": 0.38, "similarity_boost": 0.8,
                                            "style": 0.4, "use_speaker_boost": True}}).encode(),
        headers={"xi-api-key": KEY, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        open(path, "wb").write(r.read())


def _logo(d, cx, y, size):
    s = size / 32.0
    d.rounded_rectangle([cx - size / 2, y, cx + size / 2, y + size], radius=int(9 * s), fill=INDIGO)
    x0 = cx - size / 2
    d.line([(x0 + 8 * s, y + 21 * s), (x0 + 14 * s, y + 14 * s), (x0 + 18 * s, y + 18 * s),
            (x0 + 24 * s, y + 9 * s)], fill="white", width=int(2.6 * s), joint="curve")
    r = 2.3 * s
    d.ellipse([x0 + 24 * s - r, y + 9 * s - r, x0 + 24 * s + r, y + 9 * s + r], fill="white")


def card(spec, path):
    from PIL import Image, ImageDraw, ImageFont
    kind = spec.get("card")
    dark = kind == "dark"
    bg = NAVY if dark else (255, 255, 255)
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    if not dark:                                     # 라이트 카드 상단 연보라 기운
        for y in range(H // 2):
            a = 1 - y / (H / 2)
            d.line([(0, y), (W, y)], fill=(int(238 + 17 * (1 - a) * 0),
                                           int(242 + 0), 255) if False else
                   (238 + int(17 * (1 - a)) if False else 240, 243, 255))
    huge = ImageFont.truetype(FONT, 128)
    big = ImageFont.truetype(FONT, 112)
    mid = ImageFont.truetype(FONT, 46)
    sml = ImageFont.truetype(FONT, 36)

    def center(y, txt, font, fill):
        w = d.textlength(txt, font=font)
        d.text(((W - w) / 2, y), txt, font=font, fill=fill)
        return y

    if kind == "timeline":
        d.rectangle([0, 0, W, H], fill=(245, 243, 255))
        center(140, "실제 가게의 실제 기록", mid, (100, 116, 139))
        rows = [("7 / 31", "글 발행", (71, 85, 105)),
                ("8 / 2", "네이버 블로그검색 12위", (71, 85, 105)),
                ("8 / 9", "1위", INDIGO)]
        y = 300
        for i, (dt, label, color) in enumerate(rows):
            f = huge if i == 2 else ImageFont.truetype(FONT, 64)
            d.ellipse([560 - 14, y + (30 if i < 2 else 60) - 14, 560 + 14,
                       y + (30 if i < 2 else 60) + 14],
                      fill=INDIGO if i == 2 else (199, 210, 254))
            d.text((620, y), dt, font=ImageFont.truetype(FONT, 52), fill=(148, 163, 184))
            d.text((900, y - (18 if i == 2 else 0)), label, font=f, fill=color)
            y += 150 if i < 1 else 180
        center(H - 150, "2026년 8월 실측 · 개별 결과는 가게·키워드에 따라 다릅니다", sml, (148, 163, 184))
        img.save(path)
        return
    if kind == "closing":
        _logo(d, W // 2, 220, 130)
        center(430, "오늘 사진 한 장,", big, NAVY)
        center(560, "내일 손님으로", big, INDIGO)
        center(740, "ollinda.kr — 가입 없이 무료 2회", mid, (100, 116, 139))
        center(810, "카카오 · 네이버 · 구글로 3초 시작", sml, (148, 163, 184))
        img.save(path)
        return
    colors = {"w": (255, 255, 255) if dark else NAVY, "i": (129, 140, 248) if dark else INDIGO,
              "n": NAVY}
    lines = spec.get("lines", [])
    total_h = len(lines) * 150
    y = (H - total_h) / 2 - (40 if spec.get("sub") else 0)
    for txt, tone in lines:
        f = huge if len(txt) <= 10 else big
        center(y, txt, f, colors.get(tone, NAVY))
        y += 155
    if spec.get("sub"):
        center(y + 40, spec["sub"], mid,
               (148, 163, 184) if dark else (100, 116, 139))
    img.save(path)


def main():
    if not KEY:
        sys.exit("ELEVENLABS_API_KEY 필요")
    os.makedirs(WORK, exist_ok=True)
    lens = []
    for i, (line, _) in enumerate(SCENES):
        f = f"{WORK}/n{i}.mp3"
        if not os.path.exists(f):
            tts(line, f)
        lens.append(dur(f))
    tails = [0.7] * len(SCENES)
    tails[-1] = 2.2
    scene_len = [l + t for l, t in zip(lens, tails)]
    print("나레이션(초):", [round(x, 1) for x in lens], "→ 총", round(sum(scene_len), 1))
    clips = []
    for i, (_, spec) in enumerate(SCENES):
        out = f"{WORK}/s{i}.mp4"
        L = scene_len[i]
        if "clip" in spec:                            # 실제 생성물 — 세로를 어두운 배경 중앙에
            vf = (f"trim=start={spec['start']}:end={spec['end']},setpts=PTS-STARTPTS,"
                  f"scale=-2:{H},pad={W}:{H}:(ow-iw)/2:0:color=0x0F172A,fps=30,"
                  f"tpad=stop_mode=clone:stop_duration={L:.2f},trim=end={L:.2f},format=yuv420p")
            sh("ffmpeg", "-y", "-v", "error", "-i", spec["clip"], "-vf", vf, "-an",
               "-c:v", "libx264", "-preset", "medium", "-crf", "19", out)
        else:                                        # 카드 — 느린 줌 인(정지 티 제거)
            png = f"{WORK}/c{i}.png"
            card(spec, png)
            frames = int(L * 30)
            vf = (f"scale={W * 2}:{H * 2},zoompan=z='min(zoom+0.00035,1.05)':d={frames}"
                  f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps=30,"
                  f"trim=end={L:.2f},format=yuv420p")
            sh("ffmpeg", "-y", "-v", "error", "-loop", "1", "-t", f"{L + 0.5:.2f}", "-i", png,
               "-vf", vf, "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "19", out)
        clips.append(out)
    aparts = []
    for i in range(len(SCENES)):
        f = f"{WORK}/a{i}.wav"
        sh("ffmpeg", "-y", "-v", "error", "-i", f"{WORK}/n{i}.mp3",
           "-af", f"aresample=48000,apad=pad_dur={tails[i]:.2f}", "-ac", "2", f)
        aparts.append(f)
    open(f"{WORK}/alist.txt", "w").write("".join(f"file '{p}'\n" for p in aparts))
    sh("ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", f"{WORK}/alist.txt",
       "-c", "copy", f"{WORK}/narr.wav")
    total = sum(scene_len)
    # 데모 클립 원음(나레이션 있는 실생성물)은 끄고 홍보 나레이션+BGM만 — 두 목소리 충돌 방지
    sh("ffmpeg", "-y", "-v", "error", "-i", f"{WORK}/narr.wav", "-stream_loop", "-1", "-i", BGM,
       "-filter_complex",
       f"[1:a]aresample=48000,volume=0.13,atrim=0:{total:.2f},afade=t=out:st={total-2.5:.2f}:d=2.5[b];"
       f"[0:a][b]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-15.5:TP=-1.5:LRA=11[a]",
       "-map", "[a]", "-ac", "2", f"{WORK}/audio.wav")
    open(f"{WORK}/vlist.txt", "w").write("".join(f"file '{p}'\n" for p in clips))
    sh("ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", f"{WORK}/vlist.txt",
       "-i", f"{WORK}/audio.wav",
       "-vf", f"fade=t=in:d=0.5,fade=t=out:st={total-1.0:.2f}:d=1.0",
       "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", OUT)
    print("✅", OUT, os.path.getsize(OUT) // 1024, "KB, 총", round(total, 1), "초")


main()
