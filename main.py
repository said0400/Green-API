"""
Viral Short Generator v5.6
==========================
- Font: Noto Naskh Arabic Bold
- Rendering: HTML + Playwright (دعم عربي 100%)
- Titles: من ملف Excel (data/videos.xlsx)
- AI: Gemini (1.5-flash → 2.0-flash-exp) + Groq fallback
- 8 API keys total (4 Gemini × 2 models) + 1 Groq fallback
- Text Themes: 8 color themes (random)
- Video Filters: 6 color filters (random, unified per video)
- FFmpeg pipeline كامل
- Cache لـ Pexels (24h)
- بدون موسيقى
"""
import atexit
import hashlib
import json
import logging
import math
import os
import re
import secrets as pysecrets
import shutil
import subprocess
import sys
import time
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path

import httpx
from PIL import Image
from tqdm import tqdm

from renderer import (
    HTMLRenderer,
    render_title_png,
    render_read_desc_png,
    auto_fit_title,
)
from themes import pick_color_themes, pick_video_filter, VIDEO_FILTER_OPACITY
from excel_reader import get_next_video
from ai_client import smart_ai_call
from music_manager import get_next_music



# =========================
# Logging
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("viral")


# =========================
# المسارات والإعدادات
# =========================
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
TEMP_DIR = ROOT / "temp"
OUT_DIR = ROOT / "out"
FONT_DIR = ROOT / "assets" / "fonts"
TEMPLATES_DIR = ROOT / "assets" / "templates"
MUSIC_DIR = ROOT / "assets" / "music"


HISTORY_PATH = DATA_DIR / "history.json"
PEXELS_CACHE_PATH = DATA_DIR / "pexels_cache.json"
FONT_PATH = FONT_DIR / "NotoNaskhArabic-Bold.ttf"
EXCEL_PATH = DATA_DIR / "videos.xlsx"

# الفيديو
WIDTH = 1080
HEIGHT = 1920
FPS = 24
SCENE_DURATION = 3.0
VIDEO_DURATION_MIN = 11
VIDEO_DURATION_MAX = 15
READ_DESC_START = 3.5

# إعدادات الموسيقى
MUSIC_VOLUME = 1.0          # 1.0 = 100%
MUSIC_FADE_IN = 0.5         # ثانية
MUSIC_FADE_OUT = 1.0        # ثانية

# إعدادات النص
TITLE_FONT_SIZE = 48
TITLE_MAX_WIDTH = 780
TITLE_MAX_HEIGHT = 700
READ_DESC_FONT_SIZE = 38

# الكاش والـ history
HISTORY_MAX = 500
PEXELS_CACHE_TTL = 24 * 3600

# الجودة
MIN_VIDEO_WIDTH = 720
MIN_VIDEO_HEIGHT = 1280

# Retry للشبكة
NETWORK_RETRIES = 3
NETWORK_RETRY_BACKOFF = 2.0

# AI Validation Retries
AI_MAX_ATTEMPTS = 3

GREEN_API_BASE_URL = (os.getenv("GREEN_API_BASE_URL") or "https://api.green-api.com").rstrip("/")


# =========================
# HTTP Client
# =========================
HTTP = httpx.Client(
    timeout=httpx.Timeout(120.0, connect=20.0),
    follow_redirects=True,
    http2=True,
    headers={"User-Agent": "viral-short-generator/5.6"},
)


@atexit.register
def _close_http():
    with suppress(Exception):
        HTTP.close()


# =========================
# أدوات أساسية
# =========================
def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def ensure_dirs():
    for d in [DATA_DIR, TEMP_DIR, OUT_DIR, FONT_DIR, TEMPLATES_DIR, MUSIC_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    if not HISTORY_PATH.exists():
        HISTORY_PATH.write_text("[]", encoding="utf-8")
    if not PEXELS_CACHE_PATH.exists():
        PEXELS_CACHE_PATH.write_text("{}", encoding="utf-8")


def clean_temp_only():
    if TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR, ignore_errors=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)


def with_retry(label: str, fn, *args, retries=NETWORK_RETRIES, **kwargs):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_error = e
            wait = NETWORK_RETRY_BACKOFF ** (attempt - 1)
            log.warning(f"[retry] {label} failed ({attempt}/{retries}): {e}")
            if attempt < retries:
                time.sleep(wait)
    raise RuntimeError(f"{label} failed after {retries}: {last_error}")


def secure_uniform(a: float, b: float) -> float:
    if b <= a:
        return a
    return a + (pysecrets.randbelow(1_000_000) / 1_000_000) * (b - a)


def run_ffmpeg(args, label="ffmpeg"):
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + args
    log.info(f"[{label}] running...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error(f"ffmpeg stderr: {result.stderr[:1000]}")
        raise RuntimeError(f"ffmpeg {label} failed")
    return result


# =========================
# تحميل الملفات
# =========================
def download_file(url: str, path: Path, label="file"):
    with HTTP.stream("GET", url) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length") or 0)
        with open(path, "wb") as f, tqdm(
            total=total if total > 0 else None,
            unit="B", unit_scale=True, desc=label, ncols=80, leave=False,
        ) as bar:
            for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                f.write(chunk)
                if total > 0:
                    bar.update(len(chunk))


def ensure_font():
    if not FONT_PATH.exists():
        raise RuntimeError(f"Font file not found at {FONT_PATH}")
    if FONT_PATH.stat().st_size < 10000:
        raise RuntimeError(f"Font file at {FONT_PATH} seems corrupt")
    log.info(f"✓ Font loaded: {FONT_PATH.name} ({FONT_PATH.stat().st_size} bytes)")


def ensure_templates():
    required = ["title.html", "read_desc.html"]
    missing = [t for t in required if not (TEMPLATES_DIR / t).exists()]
    if missing:
        raise RuntimeError(f"Missing templates in {TEMPLATES_DIR}: {missing}")
    log.info(f"✓ HTML templates loaded ({len(required)} files)")


def ensure_excel():
    if not EXCEL_PATH.exists():
        raise RuntimeError(f"❌ Excel file not found: {EXCEL_PATH}")
    log.info(f"✓ Excel file found: {EXCEL_PATH.name}")
    

def ensure_music():
    """التحقق من وجود مجلد الموسيقى وملفاته."""
    if not MUSIC_DIR.exists():
        raise RuntimeError(
            f"❌ Music folder not found: {MUSIC_DIR}\n"
            f"Please upload .mp3 files to assets/music/"
        )
    music_files = list(MUSIC_DIR.glob("*.mp3"))
    if not music_files:
        raise RuntimeError(f"❌ No .mp3 files found in {MUSIC_DIR}")
    log.info(f"✓ Music library: {len(music_files)} files found")


def check_ai_keys():
    """التحقق من توفر مفتاح AI واحد على الأقل."""
    gemini_keys = [k for k in [
        "GEMINI_API_KEY_1", "GEMINI_API_KEY_2",
        "GEMINI_API_KEY_3", "GEMINI_API_KEY_4"
    ] if os.getenv(k, "").strip()]

    groq_key = bool(os.getenv("GROQ_API_KEY", "").strip())

    if not gemini_keys and not groq_key:
        raise RuntimeError(
            "❌ No AI keys configured!\n"
            "Set at least one of: GEMINI_API_KEY_1-4 or GROQ_API_KEY"
        )

    log.info(
        f"✓ AI configured: {len(gemini_keys)} Gemini key(s) + "
        f"{'Groq fallback' if groq_key else 'no Groq fallback'}"
    )


# =========================
# History
# =========================
def load_history():
    try:
        return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_history(history):
    history = history[-HISTORY_MAX:]
    HISTORY_PATH.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_hashtags(items) -> str:
    if isinstance(items, list):
        raw = " ".join(str(i) for i in items)
    else:
        raw = str(items or "")

    tags = re.findall(r"#?([A-Za-z0-9_\u0600-\u06FF]+)", raw)
    cleaned, seen = [], set()
    for tag in tags:
        if not tag or tag.isdigit():
            continue
        key = tag.lower()
        if key not in seen:
            seen.add(key)
            cleaned.append(f"#{tag}")
    return " ".join(cleaned[:15])


def word_count(text: str) -> int:
    return len([w for w in text.strip().split() if w.strip()])


# =========================
# Pexels Cache
# =========================
def load_pexels_cache():
    try:
        return json.loads(PEXELS_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_pexels_cache(cache):
    now = time.time()
    cache = {k: v for k, v in cache.items() if (now - v.get("ts", 0)) < PEXELS_CACHE_TTL}
    PEXELS_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


def cache_key(query: str) -> str:
    return hashlib.sha1(query.lower().strip().encode("utf-8")).hexdigest()[:16]


# =========================
# AI Prompts
# =========================
def build_description_prompt(title: str) -> str:
    """برومبت احترافي لتوليد وصف فيروسي + hashtags."""
    return f"""أنت كاتب محتوى فيروسي متخصص في كتابة أوصاف فيديوهات قصيرة لمنصات TikTok وYouTube Shorts وFacebook Reels.

مهمتك هي كتابة وصف فيديو واحد + 15 هاشتاج بناءً على العنوان الذي سأعطيك إياه.

🚨 قواعد إلزامية صارمة للوصف (description):

1. اللغة:
- استخدم اللغة العربية الفصحى البسيطة فقط.
- ممنوع استخدام أي لغة أخرى.
- ممنوع الرموز التعبيرية أو الإيموجي أو أي رموز غير نصية.

2. البنية:
- يجب أن يبدأ الوصف بـ 3 جمل قوية جدًا (Hook قوي جدًا).
- هذه الجمل الأولى يجب أن تكون خيالية، نفسية، مشوقة، وتخلق صدمة أو فضول عالي جدًا.
- الهدف منها هو إجبار أي شخص على إكمال القراءة.

3. بعد الـ 3 جمل الأولى:
- اكمل شرح الفكرة بشكل تدريجي وغامض قليلًا.
- حافظ على التشويق حتى النهاية.
- لا تكشف كل شيء بسرعة.

4. الأسلوب:
- أسلوب نفسي + تحليلي + غامض + بسيط.
- بدون تعقيد لغوي.
- بدون مبالغة ركيكة أو تكرار.

5. الطول:
- بين 80 إلى 150 كلمة تقريبًا (ليس قصير جدًا ولا طويل جدًا).

6. ممنوع:
- لا تستخدم أي رموز أو إيموجي.
- لا تستخدم أي لغات أجنبية.
- لا تكتب عناوين أو علامات أو تنسيقات.
- لا تشرح أنك ستقوم بالكتابة.

7. الهدف:
- جعل القارئ يكمل القراءة حتى النهاية.
- خلق فضول نفسي قوي جدًا مرتبط بعنوان الفيديو.

🚨 قواعد الهاشتاجات (hashtags):
- اكتب بالضبط 15 هاشتاج.
- مزيج بين العربية والإنجليزية.
- مرتبطة بموضوع العنوان (علم نفس، علاقات، تحليل سلوك).
- بدون رمز # في الـ JSON array.

⚠️ تحقّق قبل الإرسال:
- إذا لم تكن أول 3 جمل قوية جدًا ومثيرة للفضول، أعد كتابة الوصف من البداية حتى يصبح أكثر تأثيرًا نفسيًا.

🎯 عنوان الفيديو:
{title}

📤 الإخراج:
أرجع فقط JSON صالح بالشكل التالي، بدون أي شرح أو نص إضافي:

{{
  "description": "النص الكامل للوصف هنا (80-150 كلمة)",
  "hashtags": ["هاشتاج1", "هاشتاج2", "tag3", "...", "tag15"]
}}
"""


def build_search_terms_prompt(title: str, description: str) -> str:
    return f"""Generate English stock-video search queries for Pexels.

The footage must match this Arabic psychology short. Prefer cinematic, emotional, portrait-friendly visuals.
Avoid: text overlays, logos, podcasts, microphones, studio shots.

CRITICAL: Respond ONLY with valid JSON.

JSON SCHEMA:
{{
  "queries": ["query1", "query2", "query3", "query4", "query5", "query6"]
}}

TITLE: {title}
DESCRIPTION: {description[:400]}
"""


# =========================
# توليد المحتوى (Excel + AI)
# =========================
def generate_unique_content():
    """
    تحصل على العنوان والـ read_text من Excel.
    تولّد الوصف والـ hashtags عبر smart_ai_call (Gemini → Groq).
    """
    # 1️⃣ جلب العنوان من Excel
    excel_data = get_next_video()
    title_from_excel = excel_data["title"]
    read_text_from_excel = excel_data["read_text"]
    excel_number = excel_data["number"]

    log.info(f"📊 Excel video #{excel_number}: {title_from_excel}")

    # 2️⃣ توليد الوصف والـ hashtags
    last_error = None

    for attempt in range(1, AI_MAX_ATTEMPTS + 1):
        log.info(f"🎯 Generating viral content ({attempt}/{AI_MAX_ATTEMPTS})...")

        prompt = build_description_prompt(title_from_excel)

        try:
            data = smart_ai_call(prompt, temperature=1.15, max_tokens=1500)

            description = data.get("description", "").strip()
            hashtags_raw = data.get("hashtags", [])

            # التحقق من الوصف
            wc = word_count(description)
            if wc < 70:
                last_error = f"Description too short ({wc} words, need 80+)"
                log.warning(last_error)
                continue
            if wc > 200:
                last_error = f"Description too long ({wc} words, need <150)"
                log.warning(last_error)
                continue

            # التحقق من الـ hashtags
            if not isinstance(hashtags_raw, list) or len(hashtags_raw) < 12:
                last_error = (
                    f"Hashtags count invalid: "
                    f"{len(hashtags_raw) if isinstance(hashtags_raw, list) else 'not list'}"
                )
                log.warning(last_error)
                continue

            hashtags = normalize_hashtags(hashtags_raw)

            if len(hashtags.split()) < 12:
                last_error = "Not enough valid hashtags after normalization"
                log.warning(last_error)
                continue

            log.info(f"✅ Viral content generated ({wc} words, {len(description)} chars)")

            return {
                "title": title_from_excel,
                "description": description,
                "hashtags": hashtags,
                "read_text": read_text_from_excel,
                "excel_number": excel_number,
            }

        except Exception as e:
            last_error = str(e)
            log.warning(f"Error: {e}")

    raise RuntimeError(f"Content generation failed: {last_error}")


def generate_search_terms(title: str, description: str):
    """توليد كلمات البحث لـ Pexels عبر smart_ai_call."""
    fallback = [
        "sad woman thinking", "man alone night", "couple distance",
        "phone message stress", "emotional silence", "city night loneliness",
    ]
    try:
        data = smart_ai_call(
            build_search_terms_prompt(title, description),
            temperature=0.8, max_tokens=400,
        )
        queries = data.get("queries", [])
        if not isinstance(queries, list):
            return fallback

        terms, seen = [], set()
        for q in queries:
            q = str(q).strip().strip("\"' ")
            if q and q.lower() not in seen:
                seen.add(q.lower())
                terms.append(q)

        if not terms:
            return fallback

        for f in fallback:
            if len(terms) >= 6:
                break
            if f.lower() not in {t.lower() for t in terms}:
                terms.append(f)

        return terms[:6]
    except Exception as e:
        log.warning(f"Search terms fallback: {e}")
        return fallback


# =========================
# Pexels (مع cache)
# =========================
def _pexels_request(query: str):
    api_key = require_env("PEXELS_API_KEY")
    response = HTTP.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": api_key},
        params={"query": query, "per_page": 20, "orientation": "portrait"},
        timeout=60,
    )
    response.raise_for_status()
    return response.json().get("videos", [])


def search_pexels(query: str, cache: dict):
    key = cache_key(query)
    entry = cache.get(key)
    now = time.time()

    if entry and (now - entry.get("ts", 0)) < PEXELS_CACHE_TTL:
        log.info(f"[cache] {query}")
        return entry.get("videos", [])

    log.info(f"[pexels] {query}")
    try:
        videos = with_retry(f"pexels:{query}", _pexels_request, query)
        slim = []
        for v in videos:
            slim.append({
                "id": v.get("id"),
                "video_files": [
                    {
                        "file_type": f.get("file_type"),
                        "link": f.get("link"),
                        "width": f.get("width"),
                        "height": f.get("height"),
                    }
                    for f in v.get("video_files", [])
                ],
            })
        cache[key] = {"ts": now, "videos": slim, "query": query}
        return slim
    except Exception as e:
        log.warning(f"Pexels error: {e}")
        return entry.get("videos", []) if entry else []


def choose_video_link(video: dict):
    files = [
        f for f in video.get("video_files", [])
        if f.get("file_type") == "video/mp4" and f.get("link")
    ]
    if not files:
        return None

    quality = [
        f for f in files
        if (f.get("width") or 0) >= MIN_VIDEO_WIDTH
        and (f.get("height") or 0) >= MIN_VIDEO_HEIGHT
    ] or files

    def score(f):
        w, h = f.get("width", 0), f.get("height", 0)
        portrait_penalty = 0 if h >= w else 1
        ratio_penalty = abs((w / h if h else 0) - 9 / 16)
        area = (w * h) if w and h else 10**12
        return (portrait_penalty, ratio_penalty, area)

    return sorted(quality, key=score)[0]["link"]


def fetch_backgrounds(search_terms, count_needed, cache):
    paths, seen_ids, seen_links = [], set(), set()
    extra = [
        "thinking person", "relationship tension", "woman window",
        "man phone", "lonely silhouette", "city rain night",
    ]
    queries = search_terms + [q for q in extra if q.lower() not in {s.lower() for s in search_terms}]
    rng = pysecrets.SystemRandom()

    for query in queries:
        if len(paths) >= count_needed:
            break
        videos = search_pexels(query, cache)
        rng.shuffle(videos)

        for video in videos:
            if len(paths) >= count_needed:
                break
            vid = video.get("id")
            if vid in seen_ids:
                continue
            link = choose_video_link(video)
            if not link or link in seen_links:
                continue

            target = TEMP_DIR / f"bg_{len(paths) + 1:02d}.mp4"
            try:
                with_retry(f"bg {len(paths) + 1}", download_file, link, target, f"clip {len(paths) + 1}")
                if target.stat().st_size < 150000:
                    target.unlink(missing_ok=True)
                    continue
                paths.append(target)
                seen_ids.add(vid)
                seen_links.add(link)
            except Exception as e:
                log.warning(f"Download failed: {e}")
                target.unlink(missing_ok=True)

    return paths


# =========================
# FFmpeg Rendering
# =========================
def get_video_duration(path: Path) -> float:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(result.stdout.strip() or 0)
    except Exception:
        return 0


def render_scene(input_path: Path, output_path: Path, duration: float,
                 scene_index: int, video_filter: dict):
    """رندر مشهد واحد مع Ken Burns خفيف + فلتر ملوّن."""
    src_duration = get_video_duration(input_path)
    if src_duration <= 0:
        raise RuntimeError(f"Invalid source: {input_path}")

    if src_duration > duration:
        max_start = src_duration - duration
        start = secure_uniform(0, max_start)
    else:
        start = 0

    filter_hex = f"0x{video_filter['r']:02x}{video_filter['g']:02x}{video_filter['b']:02x}"

    vf = (
        f"scale={int(WIDTH * 1.15)}:{int(HEIGHT * 1.15)}:force_original_aspect_ratio=increase,"
        f"crop={int(WIDTH * 1.08)}:{int(HEIGHT * 1.08)},"
        f"crop=w={WIDTH}:h={HEIGHT}:"
        f"x='(in_w-out_w)/2 + sin(t/{duration}*PI)*20':"
        f"y='(in_h-out_h)/2 + cos(t/{duration}*PI)*20',"
        f"eq=contrast=1.08:saturation=0.95,"
        f"fade=t=in:st=0:d=0.15,"
        f"fade=t=out:st={duration - 0.15:.3f}:d=0.15"
    )

    fc = (
        f"[0:v]{vf}[bg];"
        f"color=c={filter_hex}@{VIDEO_FILTER_OPACITY}:s={WIDTH}x{HEIGHT}:d={duration}:r={FPS}[overlay];"
        f"[bg][overlay]overlay=format=auto[v]"
    )

    args = [
        "-ss", f"{start:.3f}",
        "-t", f"{duration:.3f}",
        "-i", str(input_path),
        "-filter_complex", fc,
        "-map", "[v]",
        "-an",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-r", str(FPS),
        "-b:v", "3000k",
        str(output_path),
    ]
    run_ffmpeg(args, label=f"scene{scene_index}")


def render_fallback_scene(output_path: Path, duration: float, scene_index: int,
                          color_rgb, video_filter: dict):
    """مشهد احتياطي بلون خلفية + فلتر ملوّن."""
    r, g, b = color_rgb
    color_hex = f"0x{r:02x}{g:02x}{b:02x}"
    filter_hex = f"0x{video_filter['r']:02x}{video_filter['g']:02x}{video_filter['b']:02x}"

    fc = (
        f"color=c={color_hex}:s={WIDTH}x{HEIGHT}:d={duration}:r={FPS}[bg];"
        f"color=c={filter_hex}@{VIDEO_FILTER_OPACITY}:s={WIDTH}x{HEIGHT}:d={duration}:r={FPS}[overlay];"
        f"[bg][overlay]overlay=format=auto,"
        f"fade=t=in:st=0:d=0.15,fade=t=out:st={duration - 0.15:.3f}:d=0.15[v]"
    )

    args = [
        "-filter_complex", fc,
        "-map", "[v]",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-r", str(FPS),
        str(output_path),
    ]
    run_ffmpeg(args, label=f"fallback{scene_index}")


def concat_scenes(scene_paths, output_path: Path):
    list_file = TEMP_DIR / "concat_list.txt"
    list_file.write_text(
        "\n".join(f"file '{p.resolve()}'" for p in scene_paths),
        encoding="utf-8",
    )
    args = [
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(output_path),
    ]
    run_ffmpeg(args, label="concat")


# =========================
# تركيب النصوص (بدون صوت)
# =========================
def overlay_texts(video_in: Path, video_out: Path,
                  title_img_path: Path, read_img_path: Path,
                  title_y: int, read_y: int,
                  duration: float, read_start: float,
                  music_path: Path = None):
    """
    تركيب نصوص + موسيقى خلفية:
    - title: ظاهر طوال الفيديو
    - read_desc: يظهر بعد read_start مع fade-in
    - music: من بداية الملف + fade in/out
    """
    inputs = [
        "-i", str(video_in),
        "-loop", "1", "-t", f"{duration}", "-i", str(title_img_path),
        "-loop", "1", "-t", f"{duration}", "-i", str(read_img_path),
    ]
    
    # إضافة الموسيقى إذا كانت متوفرة
    has_music = music_path is not None and music_path.exists()
    if has_music:
        inputs += ["-i", str(music_path)]
        log.info(f"🎵 Adding music: {music_path.name}")

    fade_dur = 0.35
    
    # Video filter complex
    fc = (
        f"[1:v]format=rgba[ttl];"
        f"[2:v]format=rgba,fade=t=in:st=0:d={fade_dur}:alpha=1[rd];"
        f"[0:v][ttl]overlay=x=(W-w)/2:y={title_y}:format=auto[t1];"
        f"[t1][rd]overlay=x=(W-w)/2:y={read_y}:format=auto:"
        f"enable='gte(t,{read_start})'[v]"
    )
    
    # إذا كانت هناك موسيقى، أضف audio filter
    if has_music:
        fade_out_start = max(0, duration - MUSIC_FADE_OUT)
        fc += (
            f";[3:a]volume={MUSIC_VOLUME},"
            f"afade=t=in:st=0:d={MUSIC_FADE_IN},"
            f"afade=t=out:st={fade_out_start:.3f}:d={MUSIC_FADE_OUT}[a]"
        )

    args = inputs + [
        "-filter_complex", fc,
        "-map", "[v]",
    ]
    
    if has_music:
        args += [
            "-map", "[a]",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
        ]
    else:
        args += ["-an"]
    
    args += [
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-r", str(FPS), "-b:v", "3500k",
        "-t", f"{duration}",
        "-movflags", "+faststart",
        str(video_out),
    ]

    run_ffmpeg(args, label="overlay")


# =========================
# Build Video
# =========================
def create_video(title: str, read_text: str, search_terms, cache):
    total_duration = pysecrets.choice(range(VIDEO_DURATION_MIN, VIDEO_DURATION_MAX + 1))
    needed = math.ceil(total_duration / SCENE_DURATION)
    log.info(f"Duration: {total_duration}s, scenes: {needed}")

    clip_paths = fetch_backgrounds(search_terms, needed, cache)
    log.info(f"📹 Downloaded {len(clip_paths)} background clips (needed {needed})")

    durations = []
    remaining = total_duration
    while remaining > 0.01:
        d = min(SCENE_DURATION, remaining)
        durations.append(round(d, 3))
        remaining -= d

    # 🎬 اختيار فلتر فيديو عشوائي (لون موحّد لكل المشاهد)
    video_filter = pick_video_filter()

    scene_paths = []
    fallback_colors = [(14, 20, 38), (18, 27, 46), (11, 22, 40), (20, 24, 52)]

    for i, dur in enumerate(durations):
        scene_out = TEMP_DIR / f"scene_{i + 1:02d}.mp4"
        try:
            if i < len(clip_paths):
                render_scene(
                    clip_paths[i % len(clip_paths)], scene_out, dur, i + 1,
                    video_filter=video_filter,
                )
            else:
                log.warning(f"⚠️ Scene {i + 1}: no clip available, using fallback color")
                render_fallback_scene(
                    scene_out, dur, i + 1, fallback_colors[i % 4],
                    video_filter=video_filter,
                )
        except Exception as e:
            log.warning(f"Scene {i + 1} failed: {e}, using fallback")
            render_fallback_scene(
                scene_out, dur, i + 1, fallback_colors[i % 4],
                video_filter=video_filter,
            )

        scene_paths.append(scene_out)

    concat_path = TEMP_DIR / "concat.mp4"
    concat_scenes(scene_paths, concat_path)

    # ===== توليد النصوص باستخدام HTML + Playwright =====
    title_png = TEMP_DIR / "title.png"
    read_png = TEMP_DIR / "read_desc.png"

    # 🎨 اختيار ثيمات لونية عشوائية للنصوص
    title_theme, read_theme = pick_color_themes()

    log.info("🎨 Rendering text overlays with Playwright (HTML)...")
    with HTMLRenderer(viewport_width=WIDTH, viewport_height=HEIGHT) as renderer:
        title_height = auto_fit_title(
            renderer, title, title_png,
            max_height=TITLE_MAX_HEIGHT,
            max_width=TITLE_MAX_WIDTH,
            preferred_size=TITLE_FONT_SIZE,
            bg_color=title_theme["bg"],
            text_color=title_theme["text"],
        )
        render_read_desc_png(
            renderer, read_text, read_png,
            font_size=READ_DESC_FONT_SIZE,
            bg_color=read_theme["bg"],
            text_color=read_theme["text"],
        )

    # حساب أبعاد الصور
    with Image.open(title_png) as ti:
        title_actual_h = ti.height
        title_actual_w = ti.width
    with Image.open(read_png) as ri:
        read_actual_h = ri.height
        read_actual_w = ri.width

    log.info(f"📐 Title: {title_actual_w}x{title_actual_h}px")
    log.info(f"📐 Read:  {read_actual_w}x{read_actual_h}px")

        title_y = max(250, int((HEIGHT * 0.42) - (title_actual_h / 2)))
    read_y = min(HEIGHT - 230, title_y + title_actual_h + 36)
    read_start = min(READ_DESC_START, max(0.8, total_duration - 1.2))

    # 🎵 اختيار ملف الموسيقى التالي
    music_path = get_next_music()

    final_path = OUT_DIR / "final_video.mp4"

    overlay_texts(
        concat_path, final_path,
        title_png, read_png,
        title_y, read_y,
        total_duration, read_start,
        music_path=music_path,
    )

    log.info(f"✓ Video saved: {final_path}")
    return final_path, total_duration, title_theme, read_theme, video_filter, music_path


def save_content_file(content, search_terms, duration, title_theme, read_theme, video_filter, music_path):
    text = f"""EXCEL NUMBER: #{content['excel_number']}

TITLE:
{content['title']}

READ TEXT:
{content['read_text']}

DESCRIPTION:
{content['description']}

HASHTAGS:
{content['hashtags']}

SEARCH_TERMS:
{chr(10).join(search_terms)}

DURATION: {duration}s

THEMES:
- Title:        {title_theme['name']} (bg={title_theme['bg']}, text={title_theme['text']})
- Read:         {read_theme['name']} (bg={read_theme['bg']}, text={read_theme['text']})
- Video Filter: {video_filter['name']} (rgb={video_filter['r']},{video_filter['g']},{video_filter['b']}) @ {int(VIDEO_FILTER_OPACITY * 100)}%
- Music:        {music_path.name}
"""
    (OUT_DIR / "content.txt").write_text(text, encoding="utf-8")

# =========================
# Green-API / WhatsApp
# =========================
def green_url(method: str) -> str:
    instance_id = require_env("GREEN_API_INSTANCE_ID")
    token = require_env("GREEN_API_TOKEN")
    return f"{GREEN_API_BASE_URL}/waInstance{instance_id}/{method}/{token}"


def _send_video_once(video_path: Path):
    chat_id = require_env("WHATSAPP_CHAT_ID")
    url = green_url("sendFileByUpload")
    with open(video_path, "rb") as f:
        files = {"file": (video_path.name, f, "video/mp4")}
        data = {"chatId": chat_id, "fileName": video_path.name, "caption": ""}
        response = HTTP.post(url, data=data, files=files, timeout=300)
        response.raise_for_status()
        return response.json()


def send_video(video_path: Path):
    log.info("Sending video to WhatsApp...")
    result = with_retry("whatsapp.video", _send_video_once, video_path)
    log.info(f"✓ Video sent: {result}")
    return result


def _send_text_once(message: str):
    chat_id = require_env("WHATSAPP_CHAT_ID")
    url = green_url("sendMessage")
    response = HTTP.post(
        url, json={"chatId": chat_id, "message": message}, timeout=120,
    )
    response.raise_for_status()
    return response.json()


def send_text(message: str):
    log.info("Sending text message...")
    result = with_retry("whatsapp.text", _send_text_once, message)
    log.info(f"✓ Text sent: {result}")
    return result


def send_to_whatsapp(video_path: Path, description: str, hashtags: str):
    send_video(video_path)
    time.sleep(5)
    message = description.strip() + ("\n" * 8) + hashtags.strip()
    send_text(message)


# =========================
# Main
# =========================
def main():
    log.info("=" * 60)
    log.info("🚀 Viral Short Generator v5.6 (Gemini + Groq Fallback)")
    log.info("=" * 60)

    ensure_dirs()
    clean_temp_only()
    ensure_font()
    ensure_templates()
    ensure_excel()
    ensure_music()
    check_ai_keys()

    # تحقق من الـ secrets الإلزامية (غير AI)
    for var in ["PEXELS_API_KEY", "GREEN_API_INSTANCE_ID",
                "GREEN_API_TOKEN", "WHATSAPP_CHAT_ID"]:
        require_env(var)

    history = load_history()
    cache = load_pexels_cache()
    log.info(f"History: {len(history)} | Pexels cache: {len(cache)}")

    # 1️⃣ جلب المحتوى (عنوان من Excel + وصف من AI)
    content = generate_unique_content()

    # 2️⃣ توليد كلمات البحث من AI
    search_terms = generate_search_terms(content["title"], content["description"])
    log.info(f"Search terms: {search_terms}")

    # 3️⃣ إنشاء الفيديو (مع موسيقى)
    video_path, duration, title_theme, read_theme, video_filter, music_path = create_video(
        content["title"], content["read_text"], search_terms, cache
    )

    # 4️⃣ حفظ المعلومات
    save_content_file(content, search_terms, duration, title_theme, read_theme, video_filter, music_path)

    history.append({
        "excel_number": content["excel_number"],
        "title": content["title"],
        "read_text": content["read_text"],
        "description": content["description"],
        "hashtags": content["hashtags"],
        "search_terms": search_terms,
        "duration": duration,
        "title_theme": title_theme["name"],
        "read_theme": read_theme["name"],
        "video_filter": video_filter["name"],
        "music": music_path.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    save_history(history)
    save_pexels_cache(cache)

    # 5️⃣ إرسال على واتساب
    send_to_whatsapp(video_path, content["description"], content["hashtags"])

    log.info("=" * 60)
    log.info("✅ Done successfully")
    log.info("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error(f"\n❌ FATAL: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
