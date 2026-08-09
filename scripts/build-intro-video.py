# 소개 영상 빌드 — 실제 프로덕션 화면 녹화 + 타이틀 카드 + TTS 나레이션 + BGM.
# 사용: ELEVENLABS_API_KEY=... python3 scripts/build-intro-video.py
# 출력: assets/docs/ollinda_intro.mp4 (랜딩 /docs/intro.mp4 로 서빙)
# 원칙: 화면은 전부 실물(ollinda.kr 실렌더), 나레이션 주장은 랜딩과 동일한 실측·정직 문구만.
import asyncio
import json
import os
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT = os.path.join(ROOT, "assets", "docs", "ollinda_intro.mp4")
BGM = os.path.join(ROOT, "app", "assets", "bgm", "clean_modern.mp3")
WORK = "/tmp/ollinda-intro"
VOICE = os.environ.get("ELEVENLABS_VOICE_ID", "lw2WS3FWBM6D1a3ATi9k").strip()
KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
W, H = 1920, 1080
FONT = "/System/Library/Fonts/AppleSDGothicNeo.ttc"

# 씬 계획 — (나레이션, 화면). 화면 sel: 타이틀/클로징 카드 또는 랜딩의 실섹션 텍스트 앵커.
SCENES = [
    ("사장님, 마케팅, 이제 사진 한 장이면 됩니다.", "title"),
    ("올린다가 네이버 검색에 유리한 글과 영상을 만들고, 발행 준비까지 끝냅니다.", "hero"),
    ("뭘 쓸지 모르셔도 됩니다. 손님들이 검색하는데 아직 답이 없는 질문을, 올린다가 찾아옵니다.", "저희가 찾아옵니다"),
    ("발행하고 끝이 아닙니다. 매일 순위를 실측으로 지켜보다가, 떨어지면 고친 글을 먼저 가져옵니다.", "떨어지는 날"),
    ("실제로, 발행 9일 만에 네이버 검색 1위에 오른 가게가 있습니다.", "실측 사례"),
    ("비밀번호는 받지 않고, 없는 이야기는 지어내지 않습니다.", "지어내지 않습니다"),
    ("올린다. 오늘 사진 한 장, 내일 손님으로.", "closing"),
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
    # 은은한 상단 보라 기운(브랜드 히어로와 동일 계열)
    for y in range(H // 2):
        a = int(18 * (1 - y / (H / 2)))
        d.line([(0, y), (W, y)], fill=(238 - a // 3, 242 - a // 3, 255))
    big = ImageFont.truetype(FONT, 96)
    mid = ImageFont.truetype(FONT, 44)
    sml = ImageFont.truetype(FONT, 34)
    # 로고
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
        center(760, "소상공인 AI 마케팅 · 올린다", mid, (100, 116, 139))
    else:
        center(470, "오늘 사진 한 장,", big, (15, 23, 42))
        center(590, "내일 손님으로", big, (99, 102, 241))
        center(760, "ollinda.kr — 가입 없이 무료 2회", mid, (100, 116, 139))
        center(830, "카카오 · 네이버 · 구글로 3초 시작", sml, (148, 163, 184))
    img.save(path)


async def record(sel, path, seconds):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": W, "height": H},
                                  record_video_dir=os.path.dirname(path),
                                  record_video_size={"width": W, "height": H})
        pg = await ctx.new_page()
        await pg.goto("https://ollinda.kr/", wait_until="networkidle", timeout=60000)
        await pg.evaluate("document.querySelectorAll('.reveal').forEach(e=>e.classList.add('show'))")
        if sel != "hero":
            el = await pg.query_selector(f"text={sel}")
            await el.scroll_into_view_if_needed()
            await pg.evaluate("window.scrollBy(0,-120)")
        await pg.wait_for_timeout(600)
        # 잔잔한 하강 스크롤 — 초당 ~55px
        await pg.evaluate(f"""new Promise(res => {{
            let n = 0, total = {int(seconds * 10)};
            const id = setInterval(() => {{ window.scrollBy(0, 5.5); if (++n >= total) {{ clearInterval(id); res(); }} }}, 100);
        }})""")
        await ctx.close()   # 비디오 저장
        v = await pg.video.path()
        await b.close()
        os.replace(v, path)


def main():
    if not KEY:
        sys.exit("ELEVENLABS_API_KEY 필요")
    os.makedirs(WORK, exist_ok=True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    # ① 나레이션
    lens = []
    for i, (line, _) in enumerate(SCENES):
        f = f"{WORK}/n{i}.mp3"
        if not os.path.exists(f):
            tts(line, f)
        lens.append(dur(f))
    tails = [1.0] * len(SCENES); tails[-1] = 2.0
    scene_len = [l + t for l, t in zip(lens, tails)]
    print("나레이션(초):", [round(x, 1) for x in lens], "→ 총", round(sum(scene_len), 1))
    # ② 화면 소스
    card("title", f"{WORK}/card0.png")
    card("closing", f"{WORK}/card9.png")
    for i, (_, sel) in enumerate(SCENES):
        if sel in ("title", "closing"):
            continue
        f = f"{WORK}/rec{i}.webm"
        if not os.path.exists(f):
            print("녹화:", sel)
            asyncio.run(record(sel, f, scene_len[i] + 0.8))
    # ③ 씬 클립(mp4, 정확한 길이)
    clips = []
    for i, (_, sel) in enumerate(SCENES):
        out = f"{WORK}/scene{i}.mp4"
        if sel == "title":
            src = ["-loop", "1", "-t", f"{scene_len[i]:.2f}", "-i", f"{WORK}/card0.png"]
        elif sel == "closing":
            src = ["-loop", "1", "-t", f"{scene_len[i]:.2f}", "-i", f"{WORK}/card9.png"]
        else:
            src = ["-t", f"{scene_len[i]:.2f}", "-i", f"{WORK}/rec{i}.webm"]
        sh("ffmpeg", "-y", "-v", "error", *src,
           "-vf", f"scale={W}:{H},fps=30,format=yuv420p", "-an",
           "-c:v", "libx264", "-preset", "medium", "-crf", "19", out)
        clips.append(out)
    # ④ 오디오 트랙: [나레이션+꼬리무음] 연쇄 → BGM 언더레이 → loudnorm
    aparts = []
    for i in range(len(SCENES)):
        f = f"{WORK}/a{i}.wav"
        sh("ffmpeg", "-y", "-v", "error", "-i", f"{WORK}/n{i}.mp3",
           "-af", f"aresample=48000,apad=pad_dur={tails[i]:.2f}", "-ac", "2", f)
        aparts.append(f)
    concat_list = f"{WORK}/alist.txt"
    open(concat_list, "w").write("".join(f"file '{p}'\n" for p in aparts))
    sh("ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", concat_list, "-c", "copy", f"{WORK}/narr.wav")
    total = sum(scene_len)
    sh("ffmpeg", "-y", "-v", "error", "-i", f"{WORK}/narr.wav", "-stream_loop", "-1", "-i", BGM,
       "-filter_complex",
       f"[1:a]aresample=48000,volume=0.10,atrim=0:{total:.2f},afade=t=out:st={total-2.5:.2f}:d=2.5[b];"
       f"[0:a][b]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[a]",
       "-map", "[a]", "-ac", "2", f"{WORK}/audio.wav")
    # ⑤ 최종 조립(하드컷 + 전체 페이드 인/아웃)
    vlist = f"{WORK}/vlist.txt"
    open(vlist, "w").write("".join(f"file '{p}'\n" for p in clips))
    sh("ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", vlist, "-i", f"{WORK}/audio.wav",
       "-vf", f"fade=t=in:d=0.6,fade=t=out:st={total-1.0:.2f}:d=1.0",
       "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", OUT)
    print("✅", OUT, os.path.getsize(OUT) // 1024, "KB, 총", round(total, 1), "초")


main()
