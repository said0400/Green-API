import json
import math
import os
import random
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import arabic_reshaper
from bidi.algorithm import get_display
from groq import Groq
from PIL import Image, ImageDraw, ImageFont
from rapidfuzz import fuzz
from moviepy.editor import (
    VideoFileClip,
    ImageClip,
    ColorClip,
    CompositeVideoClip,
    concatenate_videoclips,
)
import moviepy.video.fx.all as vfx


# =========================
# إعدادات عامة
# =========================
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
TEMP_DIR = ROOT / "temp"
OUT_DIR = ROOT / "out"
FONT_DIR = ROOT / "assets" / "fonts"

HISTORY_PATH = DATA_DIR / "history.json"
FONT_PATH = FONT_DIR / "Cairo-Bold.ttf"

FONT_URL = os.getenv(
    "CAIRO_FONT_URL",
    "https://raw.githubusercontent.com/google/fonts/main/ofl/cairo/Cairo-Bold.ttf",
)

WIDTH = 1080
HEIGHT = 1920
SCENE_DURATION = 3.0

VIDEO_DURATION_MIN = int(os.getenv("VIDEO_DURATION_MIN", "11"))
VIDEO_DURATION_MAX = int(os.getenv("VIDEO_DURATION_MAX", "15"))
READ_DESC_START = float(os.getenv("READ_DESC_START", "3.5"))

TOPIC_HINT = os.getenv("TOPIC_HINT", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL") or "llama-3.3-70b-versatile"
GREEN_API_BASE_URL = (os.getenv("GREEN_API_BASE_URL") or "https://api.green-api.com").rstrip("/")

BLUE_FILTER_RGB = (8, 27, 74)   # أزرق غامق
BLUE_FILTER_OPACITY = 0.35       # 35%

CLIENT = None


# =========================
# أدوات مساعدة
# =========================
def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    FONT_DIR.mkdir(parents=True, exist_ok=True)

    if not HISTORY_PATH.exists():
        HISTORY_PATH.write_text("[]", encoding="utf-8")


def clean_work_dirs():
    for folder in [TEMP_DIR, OUT_DIR]:
        if folder.exists():
            for item in folder.iterdir():
                if item.is_file():
                    item.unlink(missing_ok=True)
                elif item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
        folder.mkdir(parents=True, exist_ok=True)


def ensure_cairo_font():
    if FONT_PATH.exists():
        return

    print("Downloading Cairo font...")
    response = requests.get(FONT_URL, timeout=120)
    response.raise_for_status()
    FONT_PATH.write_bytes(response.content)

    if not FONT_PATH.exists() or FONT_PATH.stat().st_size < 10000:
        raise RuntimeError("Failed to download Cairo font correctly.")


def get_client() -> Groq:
    global CLIENT
    if CLIENT is None:
        CLIENT = Groq(api_key=require_env("GROQ_API_KEY"))
    return CLIENT


def load_history():
    try:
        return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_history(history):
    HISTORY_PATH.write_text(
        json.dumps(history, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def normalize_text(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s\u0600-\u06FF#]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def word_count(text: str) -> int:
    return len([w for w in text.strip().split() if w.strip()])


def normalize_hashtags(raw: str) -> str:
    tags = re.findall(r"#([A-Za-z0-9_\u0600-\u06FF]+)", raw)
    cleaned = []
    seen = set()

    for tag in tags:
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(f"#{tag}")

    return " ".join(cleaned[:15])


# =========================
# البرومبتات
# =========================
def build_content_prompt(previous_titles, topic_hint: str) -> str:
    previous_block = "\n".join(f"- {title}" for title in previous_titles[-30:]) if previous_titles else "- لا يوجد"

    topic_instruction = (
        f'Focus this output around this optional hint: "{topic_hint}".'
        if topic_hint else
        "Choose a fresh angle inside psychology, relationships, attraction, communication, emotional intelligence, or human behavior."
    )

    return f"""
You are the world's best viral relationship and psychology content creator.

Generate content in Arabic for a short video where only the title appears on screen and the audience is encouraged to read the description.

All written content must be in Arabic.
Hashtags may mix Arabic and English naturally.

{topic_instruction}

Generate:
1) ONE title in Arabic
2) ONE description in Arabic
3) 15 hashtags

TITLE RULES:
- Between 8 and 16 words
- No emojis
- No quotation marks
- No numbering
- Must create a powerful curiosity gap
- Must feel impossible to ignore
- Must make the viewer feel they are missing important information
- Must make the viewer want to read the description immediately
- Must feel natural, emotional, psychologically intriguing, and highly shareable
- Never reveal the answer in the title
- Never use completely false clickbait
- Use patterns such as:
إذا فعل هذا...
إذا قالت هذا...
معظم الرجال لا يعرفون...
معظم النساء لا يخبرن أحداً...
الخطأ الذي يجعل...
السبب الحقيقي وراء...
هناك كلمة واحدة...
إذا سمعت هذه العبارة...
تصرف بسيط يكشف...
علامة لا ينتبه لها أحد...
الشيء الذي لا يريدونك أن تعرفه...
ما يحدث عندما...
السبب الذي يجعل...
إذا اختفى فجأة...
إذا اعتذر رغم أنه...
إذا توقف عن...
إذا بدأت تفعل هذا...
إذا أخبرك بهذا...
إذا تجاهلت هذه الإشارة...
أغلب الناس يسيئون فهم...

DESCRIPTION RULES:
- Must take 40 to 60 seconds to read
- Must open with a strong curiosity hook
- Must expand on the idea introduced in the title
- Must use psychology, emotional intelligence, relationship dynamics, communication principles, or human behavior insights
- Must keep the reader engaged until the final sentence
- Must feel natural and conversational
- Must include at least one surprising insight
- Must end with a thought-provoking question that encourages comments
- Must NOT repeat generic advice
- Must NOT sound AI-generated
- Must feel like content from a top viral creator
- Generate content that triggers curiosity, emotional engagement, self-reflection, and discussion
- Prioritize psychological depth over superficial advice
- Every output should feel like a viral post with millions of views

UNIQUENESS RULE:
- You must generate a completely fresh idea
- Do not paraphrase or lightly reword an older idea
- Do not repeat the same core psychological mechanism, emotional angle, relationship dynamic, framing, or takeaway
- If the idea feels similar to a previous title, discard it and create a different one

PREVIOUS TITLES TO AVOID:
{previous_block}

HASHTAG RULES:
- Exactly 15 hashtags
- Optimized for TikTok, Instagram Reels, Facebook Reels, and YouTube Shorts
- Mix broad and niche hashtags

IMPORTANT:
Follow the output format exactly.
Do not add any extra text outside the format.

TITLE:
[title]

DESCRIPTION:
[description]

HASHTAGS:
#tag1 #tag2 #tag3 ...
""".strip()


def build_search_terms_prompt(title: str, description: str) -> str:
    return f"""
Generate 6 short English stock-video search queries for Pexels.

The background footage must visually fit this Arabic short-form psychology content.
Prefer realistic, cinematic, emotional, portrait-friendly visuals.
Avoid text, logos, obvious influencers, podcasts, microphones, and studio shots.

Return only 6 lines.
One search query per line.
No numbering.
No explanations.

TITLE:
{title}

DESCRIPTION:
{description}
""".strip()


# =========================
# توليد المحتوى
# =========================
CONTENT_PATTERN = re.compile(
    r"TITLE:\s*(.*?)\s*DESCRIPTION:\s*(.*?)\s*HASHTAGS:\s*(.*)",
    re.S | re.I
)


def call_groq(prompt: str, temperature=1.1, max_tokens=1400) -> str:
    client = get_client()
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content.strip()


def parse_content_response(text: str):
    match = CONTENT_PATTERN.search(text.strip())
    if not match:
        raise ValueError(f"Could not parse Groq response:\n{text}")

    title = match.group(1).strip()
    description = match.group(2).strip()
    hashtags = normalize_hashtags(match.group(3).strip())

    return title, description, hashtags


def validate_generated_content(title: str, description: str, hashtags: str):
    errors = []

    wc = word_count(title)
    if wc < 8 or wc > 16:
        errors.append(f"title word count out of range: {wc}")

    if len(description) < 350:
        errors.append("description too short")

    hashtag_count = len(hashtags.split())
    if hashtag_count < 15:
        errors.append(f"not enough hashtags: {hashtag_count}")

    if '"' in title or "“" in title or "”" in title:
        errors.append("title contains quotation marks")

    return errors


def is_duplicate(title: str, description: str, history) -> bool:
    new_title = normalize_text(title)
    new_desc = normalize_text(description[:600])

    for item in history[-80:]:
        old_title = normalize_text(item.get("title", ""))
        old_desc = normalize_text(item.get("description", "")[:600])

        title_score = fuzz.token_set_ratio(new_title, old_title)
        desc_score = fuzz.token_set_ratio(new_desc, old_desc)
        combo_score = fuzz.token_set_ratio(
            f"{new_title} {new_desc[:220]}",
            f"{old_title} {old_desc[:220]}"
        )

        if title_score >= 78 or combo_score >= 74 or (title_score >= 70 and desc_score >= 68):
            return True

    return False


def generate_unique_content(history):
    previous_titles = [item.get("title", "") for item in history if item.get("title")]

    last_error = None
    for attempt in range(1, 7):
        print(f"Generating content... attempt {attempt}")
        prompt = build_content_prompt(previous_titles, TOPIC_HINT)

        try:
            raw = call_groq(prompt, temperature=1.12, max_tokens=1500)
            title, description, hashtags = parse_content_response(raw)

            errors = validate_generated_content(title, description, hashtags)
            if errors:
                last_error = f"Validation failed: {errors}"
                print(last_error)
                continue

            if is_duplicate(title, description, history):
                last_error = "Generated content is too similar to previous history."
                print(last_error)
                continue

            return {
                "title": title,
                "description": description,
                "hashtags": hashtags,
            }

        except Exception as e:
            last_error = str(e)
            print(f"Generation error: {e}")

    raise RuntimeError(f"Failed to generate unique content. Last error: {last_error}")


def generate_search_terms(title: str, description: str):
    fallback_terms = [
        "sad woman thinking",
        "man alone night",
        "couple distance",
        "phone message stress",
        "emotional silence",
        "city night loneliness",
    ]

    try:
        raw = call_groq(build_search_terms_prompt(title, description), temperature=0.8, max_tokens=220)
        lines = []
        seen = set()

        for line in raw.splitlines():
            line = line.strip()
            line = re.sub(r"^[\-\*\d\.\)\s]+", "", line)
            line = line.strip("\"' ")
            if not line:
                continue

            key = line.lower()
            if key in seen:
                continue

            seen.add(key)
            lines.append(line)

        if not lines:
            return fallback_terms

        result = lines[:6]
        for item in fallback_terms:
            if item.lower() not in {x.lower() for x in result}:
                result.append(item)
            if len(result) >= 6:
                break

        return result[:6]

    except Exception as e:
        print(f"Search terms generation failed, using fallback terms. Error: {e}")
        return fallback_terms


# =========================
# Pexels
# =========================
def search_pexels_videos(query: str):
    api_key = require_env("PEXELS_API_KEY")
    url = "https://api.pexels.com/videos/search"
    headers = {"Authorization": api_key}
    params = {
        "query": query,
        "per_page": 8,
        "orientation": "portrait",
    }

    print(f"Searching Pexels for: {query}")
    response = requests.get(url, headers=headers, params=params, timeout=120)
    response.raise_for_status()
    return response.json().get("videos", [])


def choose_video_link(video: dict):
    files = [
        f for f in video.get("video_files", [])
        if f.get("file_type") == "video/mp4" and f.get("link")
    ]

    if not files:
        return None

    def score(file_item):
        w = file_item.get("width") or 0
        h = file_item.get("height") or 0
        portrait_penalty = 0 if h >= w else 1
        ratio = (w / h) if w and h else 0
        ratio_penalty = abs(ratio - (9 / 16))
        area = (w * h) if w and h else 10**12
        return (portrait_penalty, ratio_penalty, area)

    files = sorted(files, key=score)
    return files[0]["link"]


def download_binary(url: str, path: Path):
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)


def fetch_background_clips(search_terms, count_needed):
    clip_paths = []
    seen_video_ids = set()
    seen_links = set()

    extra_fallbacks = [
        "thinking person",
        "relationship tension",
        "woman looking out window",
        "man checking phone",
        "lonely silhouette",
        "city rain night",
    ]

    queries = search_terms + [q for q in extra_fallbacks if q.lower() not in {x.lower() for x in search_terms}]

    for query in queries:
        if len(clip_paths) >= count_needed:
            break

        try:
            videos = search_pexels_videos(query)
        except Exception as e:
            print(f"Pexels query failed: {query} | {e}")
            continue

        for video in videos:
            if len(clip_paths) >= count_needed:
                break

            video_id = video.get("id")
            if video_id in seen_video_ids:
                continue

            link = choose_video_link(video)
            if not link or link in seen_links:
                continue

            target_path = TEMP_DIR / f"bg_{len(clip_paths) + 1:02d}.mp4"

            try:
                print(f"Downloading clip {len(clip_paths) + 1}: {link}")
                download_binary(link, target_path)

                if not target_path.exists() or target_path.stat().st_size < 150000:
                    target_path.unlink(missing_ok=True)
                    continue

                clip_paths.append(target_path)
                seen_video_ids.add(video_id)
                seen_links.add(link)

            except Exception as e:
                print(f"Clip download failed: {e}")
                target_path.unlink(missing_ok=True)
                continue

    return clip_paths


# =========================
# تجهيز الفيديو
# =========================
def fit_clip_to_frame(clip, width, height):
    scale = max(width / clip.w, height / clip.h)
    clip = clip.resize(scale)
    clip = clip.crop(
        x_center=clip.w / 2,
        y_center=clip.h / 2,
        width=width,
        height=height
    )
    return clip


def prepare_clip(path: Path, target_duration: float):
    clip = VideoFileClip(str(path), audio=False)

    if clip.duration <= 0.3:
        raise RuntimeError(f"Clip too short: {path}")

    if clip.duration < target_duration:
        clip = clip.fx(vfx.loop, duration=target_duration)
    else:
        max_start = max(0, clip.duration - target_duration)
        start = random.uniform(0, max_start) if max_start > 0 else 0
        clip = clip.subclip(start, start + target_duration)

    clip = fit_clip_to_frame(clip, WIDTH, HEIGHT)
    clip = clip.fx(vfx.lum_contrast, lum=0, contrast=20, contrast_thr=127)
    clip = clip.fx(vfx.colorx, 0.95)
    clip = clip.set_duration(target_duration).set_fps(24)

    return clip


def build_fallback_background(durations):
    colors = [
        (14, 20, 38),
        (18, 27, 46),
        (11, 22, 40),
        (20, 24, 52),
    ]
    clips = []
    for i, dur in enumerate(durations):
        c = ColorClip((WIDTH, HEIGHT), color=colors[i % len(colors)], duration=dur).set_fps(24)
        clips.append(c.fadein(0.15).fadeout(0.15))
    return concatenate_videoclips(clips, method="compose")


def build_background_video(clip_paths, total_duration):
    durations = []
    remaining = total_duration

    while remaining > 0.01:
        d = min(SCENE_DURATION, remaining)
        durations.append(round(d, 3))
        remaining -= d

    if not clip_paths:
        return build_fallback_background(durations)

    processed = []
    for index, duration in enumerate(durations):
        path = clip_paths[index % len(clip_paths)]
        clip = prepare_clip(path, duration)
        clip = clip.fadein(0.12).fadeout(0.12)
        processed.append(clip)

    return concatenate_videoclips(processed, method="compose")


# =========================
# النصوص العربية
# =========================
def shape_arabic(text: str) -> str:
    return get_display(arabic_reshaper.reshape(text))


def wrap_arabic_text(text: str, font, max_width: int, stroke_width=0):
    probe = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    draw = ImageDraw.Draw(probe)

    words = text.split()
    if not words:
        return [text]

    lines = []
    current = words[0]

    for word in words[1:]:
        candidate = f"{current} {word}".strip()
        shaped = shape_arabic(candidate)
        bbox = draw.textbbox((0, 0), shaped, font=font, stroke_width=stroke_width)
        width = bbox[2] - bbox[0]

        if width <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word

    lines.append(current)
    return lines


def render_text_image(
    text: str,
    font_size: int,
    max_width: int,
    fill=(255, 255, 255, 255),
    box_fill=(0, 0, 0, 80),
    stroke_fill=(0, 0, 0, 230),
    stroke_width=3,
    shadow=True,
):
    font = ImageFont.truetype(str(FONT_PATH), font_size)

    logical_lines = wrap_arabic_text(text, font, max_width, stroke_width=stroke_width)

    probe = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    probe_draw = ImageDraw.Draw(probe)

    shaped_lines = []
    metrics = []

    for line in logical_lines:
        shaped = shape_arabic(line)
        bbox = probe_draw.textbbox((0, 0), shaped, font=font, stroke_width=stroke_width)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        shaped_lines.append(shaped)
        metrics.append((w, h))

    line_gap = 18
    content_w = max(w for w, _ in metrics)
    content_h = sum(h for _, h in metrics) + (line_gap * (len(metrics) - 1))

    pad_x = 44
    pad_y = 34

    img = Image.new("RGBA", (content_w + pad_x * 2, content_h + pad_y * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle(
        (0, 0, img.width - 1, img.height - 1),
        radius=32,
        fill=box_fill
    )

    y = pad_y
    for shaped, (w, h) in zip(shaped_lines, metrics):
        x = (img.width - w) // 2

        if shadow:
            draw.text((x + 4, y + 4), shaped, font=font, fill=(0, 0, 0, 110))

        draw.text(
            (x, y),
            shaped,
            font=font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill
        )
        y += h + line_gap

    return img


def render_title_image(title: str):
    last_img = None
    for size in [88, 82, 76, 70, 64]:
        img = render_text_image(
            title,
            font_size=size,
            max_width=int(WIDTH * 0.84),
            fill=(255, 255, 255, 255),
            box_fill=(0, 0, 0, 78),
            stroke_fill=(0, 0, 0, 230),
            stroke_width=3,
        )
        last_img = img
        if img.height <= 620:
            return img
    return last_img


def render_read_desc_image():
    return render_text_image(
        "اقرأ الوصف",
        font_size=58,
        max_width=int(WIDTH * 0.6),
        fill=(220, 235, 255, 255),
        box_fill=(0, 0, 0, 68),
        stroke_fill=(0, 0, 0, 230),
        stroke_width=2,
    )


def pop_scale(t: float):
    # حركة انبثاق / Pop-in
    if t < 0.12:
        return 0.60 + (1.18 - 0.60) * (t / 0.12)
    if t < 0.24:
        return 1.18 - (1.18 - 0.96) * ((t - 0.12) / 0.12)
    if t < 0.36:
        return 0.96 + (1.00 - 0.96) * ((t - 0.24) / 0.12)
    return 1.00


def create_video(title: str, search_terms):
    total_duration = random.randint(VIDEO_DURATION_MIN, VIDEO_DURATION_MAX)
    needed_clips = math.ceil(total_duration / SCENE_DURATION)

    print(f"Video duration selected: {total_duration}s")
    clip_paths = fetch_background_clips(search_terms, needed_clips)

    base_video = build_background_video(clip_paths, total_duration).set_duration(total_duration).set_fps(24)

    blue_overlay = ColorClip(
        size=(WIDTH, HEIGHT),
        color=BLUE_FILTER_RGB,
        duration=total_duration
    ).set_opacity(BLUE_FILTER_OPACITY)

    title_img = render_title_image(title)
    read_img = render_read_desc_image()

    title_png = TEMP_DIR / "title.png"
    read_png = TEMP_DIR / "read_desc.png"

    title_img.save(title_png)
    read_img.save(read_png)

    title_clip = ImageClip(str(title_png)).set_duration(total_duration)

    title_y = max(250, int((HEIGHT * 0.42) - (title_img.height / 2)))
    title_clip = title_clip.set_position(("center", title_y))

    read_start = min(READ_DESC_START, max(0.8, total_duration - 1.2))
    read_y = min(HEIGHT - 230, title_y + title_img.height + 36)

    read_clip = (
        ImageClip(str(read_png))
        .set_start(read_start)
        .set_duration(max(0.5, total_duration - read_start))
        .set_position(("center", read_y))
        .resize(lambda t: pop_scale(t))
        .fadein(0.08)
    )

    final = CompositeVideoClip(
        [base_video, blue_overlay, title_clip, read_clip],
        size=(WIDTH, HEIGHT)
    ).set_duration(total_duration)

    output_path = OUT_DIR / "final_video.mp4"

    print("Rendering final video...")
    final.write_videofile(
        str(output_path),
        fps=24,
        codec="libx264",
        audio=False,
        bitrate="2500k",
        preset="medium",
        threads=4,
    )

    final.close()
    base_video.close()

    return output_path, total_duration, clip_paths


# =========================
# حفظ النص
# =========================
def save_content_file(content, search_terms, total_duration):
    output_text = f"""TITLE:
{content['title']}

DESCRIPTION:
{content['description']}

HASHTAGS:
{content['hashtags']}

SEARCH_TERMS:
{chr(10).join(search_terms)}

DURATION:
{total_duration}s
"""
    (OUT_DIR / "content.txt").write_text(output_text, encoding="utf-8")


# =========================
# Green-API / WhatsApp
# =========================
def green_url(method_name: str) -> str:
    instance_id = require_env("GREEN_API_INSTANCE_ID")
    token = require_env("GREEN_API_TOKEN")
    return f"{GREEN_API_BASE_URL}/waInstance{instance_id}/{method_name}/{token}"


def send_video_only(video_path: Path):
    chat_id = require_env("WHATSAPP_CHAT_ID")
    url = green_url("sendFileByUpload")

    print("Sending video to WhatsApp...")

    with open(video_path, "rb") as f:
        files = {
            "file": (video_path.name, f, "video/mp4")
        }
        data = {
            "chatId": chat_id,
            "fileName": video_path.name,
            "caption": ""
        }
        response = requests.post(url, data=data, files=files, timeout=300)
        response.raise_for_status()
        return response.json()


def send_text_message(message: str):
    chat_id = require_env("WHATSAPP_CHAT_ID")
    url = green_url("sendMessage")

    print("Sending text message to WhatsApp...")
    response = requests.post(
        url,
        json={
            "chatId": chat_id,
            "message": message
        },
        timeout=120
    )
    response.raise_for_status()
    return response.json()


def send_to_whatsapp(video_path: Path, description: str, hashtags: str):
    # الرسالة الأولى: الفيديو فقط
    send_video_only(video_path)

    # انتظار بسيط حتى يصل أولاً قبل الرسالة النصية
    time.sleep(5)

    # الرسالة الثانية: الوصف + 7 أسطر فارغة + الهاشتاغات
    message = description.strip() + ("\n" * 8) + hashtags.strip()
    send_text_message(message)


# =========================
# التشغيل الرئيسي
# =========================
def main():
    print("Starting workflow...")
    ensure_dirs()
    clean_work_dirs()
    ensure_cairo_font()

    # تحقق من أهم المتغيرات
    require_env("GROQ_API_KEY")
    require_env("PEXELS_API_KEY")
    require_env("GREEN_API_INSTANCE_ID")
    require_env("GREEN_API_TOKEN")
    require_env("WHATSAPP_CHAT_ID")

    history = load_history()

    content = generate_unique_content(history)
    print(f"Generated title: {content['title']}")

    search_terms = generate_search_terms(content["title"], content["description"])
    print("Search terms:", search_terms)

    video_path, total_duration, _ = create_video(content["title"], search_terms)
    save_content_file(content, search_terms, total_duration)

    # حفظ المحتوى في السجل قبل الإرسال، حتى لا يتكرر حتى لو حصل خطأ لاحقًا
    history.append({
        "title": content["title"],
        "description": content["description"],
        "hashtags": content["hashtags"],
        "search_terms": search_terms,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    save_history(history)

    send_to_whatsapp(video_path, content["description"], content["hashtags"])

    print("Done successfully.")


if __name__ == "__main__":
    main()
