# 소개 영상 v2 — "실사용 과정" 시나리오: 실제 프로그램을 조작·생성하며 녹화한 화면으로 조립.
# (v1은 랜딩 스크롤 영상이었음 — 2026-08-09 사장님 지적으로 전면 재구성: 과정이 보여야 한다)
# 사용: ELEVENLABS_API_KEY=... python3 scripts/build-intro-video.py
# 출력: assets/docs/ollinda_intro.mp4
# 씬 소스(/tmp/luma-video/*.webm)는 실작동 세션 녹화물 — 재녹화 절차는 2026-08-09 세션 기록 참조.
# 나레이션 주장은 실제 동작·실측만(날조 금지). 목소리: Bella(2026-08-09 사장님 교체 지시).
import json
import os
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT = os.path.join(ROOT, "assets", "docs", "ollinda_intro.mp4")
BGM = os.path.join(ROOT, "app", "assets", "bgm", "clean_modern.mp3")
WORK = "/tmp/ollinda-intro2"
REC = "/tmp/luma-video"
VOICE = os.environ.get("ELEVENLABS_VOICE_ID", "hpp4J3VqNfWAUOO0d1Us").strip()   # Bella
KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
W, H = 1920, 1080
FONT = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
BGCOLOR = "0xEEF2FF"   # 브랜드 연보라 — 세로 폰화면 좌우 패드

# (나레이션, 소스) — clip: (파일, 시작, 끝, 배속). 시작·끝은 원본 타임라인 실측값(프레임 그리드 대조).
# ⚠ webm은 트림 시 프레임 유실(백지)이 나서 반드시 mp4 정규화(30fps) 후 자른다.
SCENES = [
    ("사장님이 하는 일은, 사진을 올리는 것뿐입니다.", {"kind": "card", "which": "title"}),
    ("방금 시공 사진 다섯 장을 올렸습니다. AI가 사진을 확인하고, 바로 만들기 시작합니다.",
     {"kind": "clip", "file": f"{REC}/scene2b-upload.webm", "start": 29.0, "end": 33.0, "speed": 0.52}),
    ("몇 분 동안 사진을 다듬고, 검색어를 고르고, 글을 씁니다.",
     {"kind": "clip", "file": f"{REC}/scene2b-upload.webm", "start": 33.2, "end": 39.0, "speed": 1.0}),
    ("완성됐습니다. 네이버 블로그 글과 인스타 캡션, 상위노출 점수까지 전부 자동입니다.",
     {"kind": "clip", "file": f"{REC}/scene4-result.webm", "start": 10.6, "end": 23.6, "speed": 1.4}),
    ("발행은 복사해서 붙여넣기만 하면 됩니다. 버튼 하나가 순서대로 안내합니다.",
     {"kind": "clip", "file": f"{REC}/scene5-wizard.webm", "start": 1.0, "end": 12.0, "speed": 1.2}),
    ("영상이 필요하면, 나레이션과 자막까지 넣어 함께 만들어 드립니다.",
     {"kind": "clip", "file": f"{REC}/scene6-video.webm", "start": 2.0, "end": 11.0, "speed": 1.0}),
    ("올린다. 오늘 사진 한 장, 내일 손님으로.", {"kind": "card", "which": "closing"}),
]


def sh(*cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:6])}... 실패:\n{r.stderr[-800:]}")
    return r.stdout


def dur(path):
    out = sh("ffprobe", "-v", "quiet", "-of", "json", "-show_format", path)
    return float(json.loads(out)["format"]["duration"])


def tts(text, path):
    import urllib.request
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE}",
        data=json.dumps({"text": text, "model_id": "eleven_multilingual_v2",
                         "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}}).encode(),
        headers={"xi-api-key": KEY, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        open(path, "wb").write(r.read())


def card(kind, path):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    for y in range(H // 2):
        a = int(18 * (1 - y / (H / 2)))
        d.line([(0, y), (W, y)], fill=(238 - a // 3, 242 - a // 3, 255))
    big = ImageFont.truetype(FONT, 96)
    mid = ImageFont.truetype(FONT, 44)
    sml = ImageFont.truetype(FONT, 34)
    lx, ly, ls = W // 2 - 60, 250, 120
    d.rounded_rectangle([lx, ly, lx + ls, ly + ls], radius=34, fill=(99, 102, 241))
    s = ls / 32
    d.line([(lx + 8 * s, ly + 21 * s), (lx + 14 * s, ly + 14 * s), (lx + 18 * s, ly + 18 * s),
            (lx + 24 * s, ly + 9 * s)], fill="white", width=int(2.6 * s), joint="curve")
    r = 2.3 * s
    d.ellipse([lx + 24 * s - r, ly + 9 * s - r, lx + 24 * s + r, ly + 9 * s + r], fill="white")
    def center(y, txt, font, fill):
        w = d.textlength(txt, font=font)
        d.text(((W - w) / 2, y), txt, font=font, fill=fill)
    if kind == "title":
        center(470, "사진만 올리면,", big, (15, 23, 42))
        center(590, "네이버 검색 상위로", big, (99, 102, 241))
        center(760, "실제 사용 과정을 그대로 보여드립니다", mid, (100, 116, 139))
    else:
        center(470, "오늘 사진 한 장,", big, (15, 23, 42))
        center(590, "내일 손님으로", big, (99, 102, 241))
        center(760, "ollinda.kr — 가입 없이 무료 2회", mid, (100, 116, 139))
        center(830, "카카오 · 네이버 · 구글로 3초 시작", sml, (148, 163, 184))
    img.save(path)


def main():
    if not KEY:
        sys.exit("ELEVENLABS_API_KEY 필요")
    os.makedirs(WORK, exist_ok=True)
    # ① 나레이션(Bella)
    lens = []
    for i, (line, _) in enumerate(SCENES):
        f = f"{WORK}/n{i}.mp3"
        if not os.path.exists(f):
            tts(line, f)
        lens.append(dur(f))
    tails = [0.8] * len(SCENES); tails[-1] = 2.0
    scene_len = [l + t for l, t in zip(lens, tails)]
    print("나레이션(초):", [round(x, 1) for x in lens], "→ 총", round(sum(scene_len), 1))
    # ② 카드
    card("title", f"{WORK}/card0.png")
    card("closing", f"{WORK}/card9.png")
    # ③ 씬 클립 — 폰 화면(세로)은 높이 1080 스케일 + 브랜드색 패드
    clips = []
    for i, (_, src) in enumerate(SCENES):
        out = f"{WORK}/scene{i}.mp4"
        L = scene_len[i]
        if src["kind"] == "card":
            which = "card0.png" if src["which"] == "title" else "card9.png"
            sh("ffmpeg", "-y", "-v", "error", "-loop", "1", "-t", f"{L:.2f}", "-i", f"{WORK}/{which}",
               "-vf", f"scale={W}:{H},fps=30,format=yuv420p", "-an",
               "-c:v", "libx264", "-preset", "medium", "-crf", "19", out)
        else:
            # 정규화(webm→mp4 30fps) — 트림 프레임 유실 방지. 파일별 1회 캐시.
            nrm = f"{WORK}/nrm-{os.path.basename(src['file'])}.mp4"
            if not os.path.exists(nrm):
                sh("ffmpeg", "-y", "-v", "error", "-i", src["file"], "-r", "30",
                   "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", "-an", nrm)
            sp = src.get("speed", 1.0)
            vf = (f"trim=start={src['start']}:end={src['end']},setpts=(PTS-STARTPTS)/{sp},"
                  f"scale=-2:{H},pad={W}:{H}:(ow-iw)/2:0:color={BGCOLOR},fps=30,"
                  f"tpad=stop_mode=clone:stop_duration={L:.2f},trim=end={L:.2f},format=yuv420p")
            sh("ffmpeg", "-y", "-v", "error", "-i", nrm,
               "-vf", vf, "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "19", out)
        clips.append(out)
    # ④ 오디오: [나레이션+꼬리무음] 연쇄 → BGM 언더레이 → loudnorm
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
    sh("ffmpeg", "-y", "-v", "error", "-i", f"{WORK}/narr.wav", "-stream_loop", "-1", "-i", BGM,
       "-filter_complex",
       f"[1:a]aresample=48000,volume=0.09,atrim=0:{total:.2f},afade=t=out:st={total-2.5:.2f}:d=2.5[b];"
       f"[0:a][b]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[a]",
       "-map", "[a]", "-ac", "2", f"{WORK}/audio.wav")
    # ⑤ 조립
    open(f"{WORK}/vlist.txt", "w").write("".join(f"file '{p}'\n" for p in clips))
    sh("ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", f"{WORK}/vlist.txt",
       "-i", f"{WORK}/audio.wav",
       "-vf", f"fade=t=in:d=0.6,fade=t=out:st={total-1.0:.2f}:d=1.0",
       "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", OUT)
    print("✅", OUT, os.path.getsize(OUT) // 1024, "KB, 총", round(total, 1), "초")


main()
