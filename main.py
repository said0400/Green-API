"""
Viral Short Generator v4.2 (No Audio)
- JSON output من Groq
- FFmpeg pipeline كامل
- Cache لـ Pexels (24h)
- Cache لـ shape_arabic
- Logging احترافي
- بدون موسيقى — فيديوهات صامتة
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
from functools import lru_cache
from pathlib import Path

import httpx
import arabic_reshaper
from bidi.algorithm import get_display
from groq import Groq
from PIL import Image, ImageDraw, ImageFont
from rapidfuzz import fuzz
from tqdm import tqdm


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

HISTORY_PATH = DATA_DIR / "history.json"
PEXELS_CACHE_PATH = DATA_DIR / "pexels_cache.json"
FONT_PATH = FONT_DIR / "Cairo-Bold.ttf"

# الفيديو
WIDTH = 1080
HEIGHT = 1920
FPS = 24
SCENE_DURATION = 3.0
VIDEO_DURATION_MIN = 11
VIDEO_DURATION_MAX = 15
READ_DESC_START = 3.5

# الفلتر الأزرق
BLUE_FILTER_R = 8
BLUE_FILTER_G = 27
BLUE_FILTER_B = 74
BLUE_FILTER_OPACITY = 0.35

# الكاش والـ history
HISTORY_MAX = 500
DUPLICATE_LOOKBACK = 300
PEXELS_CACHE_TTL = 24 * 3600  # 24 ساعة

# الجودة
MIN_VIDEO_WIDTH = 720
MIN_VIDEO_HEIGHT = 1280

# Retry
NETWORK_RETRIES = 3
NETWORK_RETRY_BACKOFF = 2.0
GROQ_TIMEOUT = 90
GROQ_MAX_ATTEMPTS = 4

TOPIC_HINT = os.getenv("TOPIC_HINT", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL") or "llama-3.3-70b-versatile"
GREEN_API_BASE_URL = (os.getenv("GREEN_API_BASE_URL") or "https://api.green-api.com").rstrip("/")


# =========================
# HTTP Client
# =========================
HTTP = httpx.Client(
    timeout=httpx.Timeout(120.0, connect=20.0),
    follow_redirects=True,
    http2=True,
    headers={"User-Agent": "viral-short-generator/4.2"},
)


@atexit.register
def _close_http():
    with suppress(Exception):
        HTTP.close()


_GROQ_CLIENT = None


# =========================
# أدوات أساسية
# =========================
def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def ensure_dirs():
    for d in [DATA_DIR, TEMP_DIR, OUT_DIR, FONT_DIR]:
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


def ensure_cairo_font():
    """الخط مرفوع يدويًا في الريبو."""
    if not FONT_PATH.exists():
        raise RuntimeError(
            f"Font file not found at {FONT_PATH}\n"
            f"Please upload Cairo-Bold.ttf to assets/fonts/"
        )
    if FONT_PATH.stat().st_size < 10000:
        raise RuntimeError(
            f"Font file at {FONT_PATH} seems corrupt (size: {FONT_PATH.stat().st_size} bytes)"
        )
    log.info(f"✓ Font loaded: {FONT_PATH.name} ({FONT_PATH.stat().st_size} bytes)")


def get_groq_client() -> Groq:
    global _GROQ_CLIENT
    if _GROQ_CLIENT is None:
        _GROQ_CLIENT = Groq(api_key=require_env("GROQ_API_KEY"), timeout=GROQ_TIMEOUT)
    return _GROQ_CLIENT


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


def normalize_text(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s\u0600-\u06FF]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def word_count(text: str) -> int:
    return len([w for w in text.strip().split() if w.strip()])


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
# Groq Prompts (JSON)
# =========================
def build_content_prompt(previous_titles, topic_hint: str) -> str:
    previous_block = (
        "\n".join(f"- {t}" for t in previous_titles[-30:])
        if previous_titles else "- لا يوجد"
    )
    topic_instruction = (
        f'Focus around: "{topic_hint}".'
        if topic_hint else
        "Choose a fresh angle inside psychology, relationships, attraction, communication, emotional intelligence, or human behavior."
    )

    return f"""You are the world's best viral relationship and psychology content creator.

Generate content in Arabic for a short video where only the title appears on screen.

{topic_instruction}

TITLE RULES:
- Between 8 and 16 words
- No emojis, no quotes, no numbering
- Powerful curiosity gap
- Never reveal the answer
- Use patterns like:
إذا فعل هذا...
إذا قالت هذا...
معظم الرجال لا يعرفون...
الخطأ الذي يجعل...
السبب الحقيقي وراء...
هناك كلمة واحدة...
إذا سمعت هذه العبارة...
علامة لا ينتبه لها أحد...
الشيء الذي لا يريدونك أن تعرفه...
إذا اختفى فجأة...
إذا اعتذر رغم أنه...
إذا توقف عن...
أغلب الناس يسيئون فهم...

DESCRIPTION RULES:
- 40 to 60 seconds reading time (around 120-180 words)
- Strong curiosity hook
- Psychological depth
- At least one surprising insight
- End with a thought-provoking question
- Natural, conversational, NOT AI-sounding

UNIQUENESS RULE:
Must be completely different from previous titles below.
Do not paraphrase or reuse the same psychological mechanism.

PREVIOUS TITLES TO AVOID:
{previous_block}

HASHTAGS:
- Exactly 15 hashtags (mix Arabic and English)
- Without the # symbol in the JSON array

CRITICAL: Respond ONLY with a valid JSON object. No markdown, no extra text.

JSON SCHEMA:
{{
  "title": "string in Arabic (8-16 words)",
  "description": "string in Arabic (120-180 words)",
  "hashtags": ["tag1", "tag2", "...", "tag15"]
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


def _call_groq_once(prompt: str, temperature: float, max_tokens: int) -> str:
    client = get_groq_client()
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content.strip()


def call_groq_json(prompt: str, temperature=1.1, max_tokens=1500) -> dict:
    raw = with_retry("groq", _call_groq_once, prompt, temperature, max_tokens)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.M).strip()
    return json.loads(raw)


# =========================
# توليد المحتوى
# =========================
def validate_content(data: dict):
    errors = []
    title = data.get("title", "").strip()
    description = data.get("description", "").strip()
    hashtags = data.get("hashtags", [])

    wc = word_count(title)
    if wc < 8 or wc > 16:
        errors.append(f"title words: {wc}")
    if len(description) < 350:
        errors.append("description too short")
    if not isinstance(hashtags, list) or len(hashtags) < 12:
        errors.append(f"hashtags count: {len(hashtags) if isinstance(hashtags, list) else 'invalid'}")

    return errors


def is_duplicate(title: str, description: str, history) -> bool:
    new_title = normalize_text(title)
    new_desc = normalize_text(description[:500])

    for item in history[-DUPLICATE_LOOKBACK:]:
        old_title = normalize_text(item.get("title", ""))
        old_desc = normalize_text(item.get("description", "")[:500])
        if fuzz.token_set_ratio(new_title, old_title) >= 78:
            return True
        if fuzz.token_set_ratio(
            new_title + " " + new_desc[:200],
            old_title + " " + old_desc[:200]
        ) >= 74:
            return True
    return False


def generate_unique_content(history):
    previous_titles = [item.get("title", "") for item in history if item.get("title")]
    last_error = None

    for attempt in range(1, GROQ_MAX_ATTEMPTS + 1):
        log.info(f"Generating content ({attempt}/{GROQ_MAX_ATTEMPTS})...")
        prompt = build_content_prompt(previous_titles, TOPIC_HINT)

        try:
            data = call_groq_json(prompt, temperature=1.15, max_tokens=1500)

            errors = validate_content(data)
            if errors:
                last_error = f"Validation: {errors}"
                log.warning(last_error)
                continue

            title = data["title"].strip().strip('"').strip("'")
            description = data["description"].strip()
            hashtags = normalize_hashtags(data["hashtags"])

            if len(hashtags.split()) < 12:
                last_error = "Not enough valid hashtags after normalization"
                log.warning(last_error)
                continue

            if is_duplicate(title, description, history):
                last_error = "Duplicate detected"
                log.warning(last_error)
                continue

            log.info(f"✓ Title: {title}")
            return {"title": title, "description": description, "hashtags": hashtags}

        except json.JSONDecodeError as e:
            last_error = f"JSON parse: {e}"
            log.warning(last_error)
        except Exception as e:
            last_error = str(e)
            log.warning(f"Error: {e}")

    raise RuntimeError(f"Content generation failed: {last_error}")


def generate_search_terms(title: str, description: str):
    fallback = [
        "sad woman thinking", "man alone night", "couple distance",
        "phone message stress", "emotional silence", "city night loneliness",
    ]
    try:
        data = call_groq_json(
            build_search_terms_prompt(title, description),
            temperature=0.8, max_tokens=300,
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


def render_scene(input_path: Path, output_path: Path, duration: float, scene_index: int):
    """رندر مشهد واحد مع Ken Burns خفيف + فلتر أزرق."""
    src_duration = get_video_duration(input_path)
    if src_duration <= 0:
        raise RuntimeError(f"Invalid source: {input_path}")

    if src_duration > duration:
        max_start = src_duration - duration
        start = secure_uniform(0, max_start)
    else:
        start = 0

    blue_hex = f"0x{BLUE_FILTER_R:02x}{BLUE_FILTER_G:02x}{BLUE_FILTER_B:02x}"

    # Ken Burns خفيف عبر scale ثابت + crop ديناميكي (sin/cos)
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
        f"color=c={blue_hex}@{BLUE_FILTER_OPACITY}:s={WIDTH}x{HEIGHT}:d={duration}:r={FPS}[blue];"
        f"[bg][blue]overlay=format=auto[v]"
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


def render_fallback_scene(output_path: Path, duration: float, scene_index: int, color_rgb):
    """مشهد احتياطي بلون ثابت."""
    r, g, b = color_rgb
    color_hex = f"0x{r:02x}{g:02x}{b:02x}"
    blue_hex = f"0x{BLUE_FILTER_R:02x}{BLUE_FILTER_G:02x}{BLUE_FILTER_B:02x}"

    fc = (
        f"color=c={color_hex}:s={WIDTH}x{HEIGHT}:d={duration}:r={FPS}[bg];"
        f"color=c={blue_hex}@{BLUE_FILTER_OPACITY}:s={WIDTH}x{HEIGHT}:d={duration}:r={FPS}[blue];"
        f"[bg][blue]overlay=format=auto,"
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
    """دمج المشاهد عبر ffmpeg concat demuxer."""
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
# النصوص العربية
# =========================
@lru_cache(maxsize=512)
def shape_arabic(text: str) -> str:
    return get_display(arabic_reshaper.reshape(text))


def wrap_text(text: str, font, max_width: int, stroke_width=0):
    probe = Image.new("RGBA", (10, 10))
    draw = ImageDraw.Draw(probe)
    words = text.split()
    if not words:
        return [text]

    lines, current = [], words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        shaped = shape_arabic(candidate)
        bbox = draw.textbbox((0, 0), shaped, font=font, stroke_width=stroke_width)
        if (bbox[2] - bbox[0]) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def render_text_image(text, font_size, max_width,
                      fill=(255, 255, 255, 255), box_fill=(0, 0, 0, 80),
                      stroke_fill=(0, 0, 0, 230), stroke_width=3, shadow=True):
    font = ImageFont.truetype(str(FONT_PATH), font_size)
    logical = wrap_text(text, font, max_width, stroke_width=stroke_width)

    probe = Image.new("RGBA", (10, 10))
    pdraw = ImageDraw.Draw(probe)
    shaped_lines, metrics = [], []
    for line in logical:
        s = shape_arabic(line)
        bbox = pdraw.textbbox((0, 0), s, font=font, stroke_width=stroke_width)
        shaped_lines.append(s)
        metrics.append((bbox[2] - bbox[0], bbox[3] - bbox[1]))

    line_gap = 18
    content_w = max(w for w, _ in metrics)
    content_h = sum(h for _, h in metrics) + line_gap * (len(metrics) - 1)
    pad_x, pad_y = 44, 34

    img = Image.new("RGBA", (content_w + pad_x * 2, content_h + pad_y * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=32, fill=box_fill)

    y = pad_y
    for shaped, (w, h) in zip(shaped_lines, metrics):
        x = (img.width - w) // 2
        if shadow:
            draw.text((x + 4, y + 4), shaped, font=font, fill=(0, 0, 0, 110))
        draw.text((x, y), shaped, font=font, fill=fill,
                  stroke_width=stroke_width, stroke_fill=stroke_fill)
        y += h + line_gap
    return img


def render_title_image(title: str):
    last = None
    for size in [88, 82, 76, 70, 64]:
        img = render_text_image(
            title, font_size=size, max_width=int(WIDTH * 0.84),
            box_fill=(0, 0, 0, 78), stroke_width=3,
        )
        last = img
        if img.height <= 620:
            return img
    return last


def render_read_desc_image():
    return render_text_image(
        "اقرأ الوصف", font_size=58, max_width=int(WIDTH * 0.6),
        fill=(220, 235, 255, 255), box_fill=(0, 0, 0, 68), stroke_width=2,
    )


# =========================
# تركيب النصوص (بدون صوت)
# =========================
def overlay_texts(video_in: Path, video_out: Path,
                  title_img_path: Path, read_img_path: Path,
                  title_y: int, read_y: int,
                  duration: float, read_start: float):
    """
    تركيب نصوص بدون صوت:
    - title: ظاهر طوال الفيديو
    - read_desc: يظهر بعد read_start مع fade-in animation
    """
    inputs = [
        "-i", str(video_in),
        "-loop", "1", "-t", f"{duration}", "-i", str(title_img_path),
        "-loop", "1", "-t", f"{duration}", "-i", str(read_img_path),
    ]

    fade_dur = 0.35
    fc = (
        f"[1:v]format=rgba[ttl];"
        f"[2:v]format=rgba,fade=t=in:st=0:d={fade_dur}:alpha=1[rd];"
        f"[0:v][ttl]overlay=x=(W-w)/2:y={title_y}:format=auto[t1];"
        f"[t1][rd]overlay=x=(W-w)/2:y={read_y}:format=auto:"
        f"enable='gte(t,{read_start})'[v]"
    )

    args = inputs + [
        "-filter_complex", fc,
        "-map", "[v]",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-r", str(FPS), "-b:v", "3500k",
        "-an",
        "-t", f"{duration}",
        "-movflags", "+faststart",
        str(video_out),
    ]

    run_ffmpeg(args, label="overlay")


# =========================
# Build Video
# =========================
def create_video(title: str, search_terms, cache):
    total_duration = pysecrets.choice(range(VIDEO_DURATION_MIN, VIDEO_DURATION_MAX + 1))
    needed = math.ceil(total_duration / SCENE_DURATION)
    log.info(f"Duration: {total_duration}s, scenes: {needed}")

    clip_paths = fetch_backgrounds(search_terms, needed, cache)

    durations = []
    remaining = total_duration
    while remaining > 0.01:
        d = min(SCENE_DURATION, remaining)
        durations.append(round(d, 3))
        remaining -= d

    scene_paths = []
    fallback_colors = [(14, 20, 38), (18, 27, 46), (11, 22, 40), (20, 24, 52)]

    for i, dur in enumerate(durations):
        scene_out = TEMP_DIR / f"scene_{i + 1:02d}.mp4"
        try:
            if i < len(clip_paths):
                render_scene(clip_paths[i % len(clip_paths)], scene_out, dur, i + 1)
            else:
                render_fallback_scene(scene_out, dur, i + 1, fallback_colors[i % 4])
        except Exception as e:
            log.warning(f"Scene {i + 1} failed: {e}, using fallback")
            render_fallback_scene(scene_out, dur, i + 1, fallback_colors[i % 4])

        scene_paths.append(scene_out)

    concat_path = TEMP_DIR / "concat.mp4"
    concat_scenes(scene_paths, concat_path)

    title_img = render_title_image(title)
    read_img = render_read_desc_image()

    title_png = TEMP_DIR / "title.png"
    read_png = TEMP_DIR / "read_desc.png"
    title_img.save(title_png)
    read_img.save(read_png)

    title_y = max(250, int((HEIGHT * 0.42) - (title_img.height / 2)))
    read_y = min(HEIGHT - 230, title_y + title_img.height + 36)
    read_start = min(READ_DESC_START, max(0.8, total_duration - 1.2))

    final_path = OUT_DIR / "final_video.mp4"

    overlay_texts(
        concat_path, final_path,
        title_png, read_png,
        title_y, read_y,
        total_duration, read_start,
    )

    log.info(f"✓ Video saved: {final_path}")
    return final_path, total_duration


def save_content_file(content, search_terms, duration):
    text = f"""TITLE:
{content['title']}

DESCRIPTION:
{content['description']}

HASHTAGS:
{content['hashtags']}

SEARCH_TERMS:
{chr(10).join(search_terms)}

DURATION: {duration}s
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
    log.info("=" * 50)
    log.info("🚀 Viral Short Generator v4.2 (No Audio)")
    log.info("=" * 50)

    ensure_dirs()
    clean_temp_only()
    ensure_cairo_font()

    for var in ["GROQ_API_KEY", "PEXELS_API_KEY",
                "GREEN_API_INSTANCE_ID", "GREEN_API_TOKEN", "WHATSAPP_CHAT_ID"]:
        require_env(var)

    history = load_history()
    cache = load_pexels_cache()
    log.info(f"History: {len(history)} | Pexels cache: {len(cache)}")

    content = generate_unique_content(history)
    search_terms = generate_search_terms(content["title"], content["description"])
    log.info(f"Search terms: {search_terms}")

    video_path, duration = create_video(content["title"], search_terms, cache)
    save_content_file(content, search_terms, duration)

    history.append({
        "title": content["title"],
        "description": content["description"],
        "hashtags": content["hashtags"],
        "search_terms": search_terms,
        "duration": duration,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    save_history(history)
    save_pexels_cache(cache)

    send_to_whatsapp(video_path, content["description"], content["hashtags"])

    log.info("=" * 50)
    log.info("✅ Done successfully")
    log.info("=" * 50)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error(f"\n❌ FATAL: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
