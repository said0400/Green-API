"""
themes.py
---------
نظام إدارة الثيمات اللونية للمشروع:
- ثيمات النصوص (8 ثيمات للعنوان + اقرأ الوصف)
- فلاتر الفيديو (6 فلاتر لونية للفيديو كاملاً)
"""
import logging
import secrets as pysecrets

log = logging.getLogger("themes")


# =========================
# ثيمات النصوص
# =========================

# ثيمات النص الأبيض (خلفيات داكنة/قوية)
WHITE_TEXT_THEMES = [
    {"name": "black",   "bg": "#000000", "text": "#FFFFFF"},
    {"name": "purple",  "bg": "#5E17EB", "text": "#FFFFFF"},
    {"name": "orange",  "bg": "#FF751F", "text": "#FFFFFF"},
    {"name": "red",     "bg": "#FF3131", "text": "#FFFFFF"},
]

# ثيمات النص الأسود (خلفيات فاتحة/مشرقة)
BLACK_TEXT_THEMES = [
    {"name": "yellow",  "bg": "#FFED00", "text": "#000000"},
    {"name": "cyan",    "bg": "#5CE1E6", "text": "#000000"},
    {"name": "green",   "bg": "#C1FF72", "text": "#000000"},
    {"name": "pink",    "bg": "#FF66C4", "text": "#000000"},
]

# كل ثيمات النصوص
ALL_THEMES = WHITE_TEXT_THEMES + BLACK_TEXT_THEMES


# =========================
# فلاتر الفيديو (طبقات ملوّنة)
# =========================
VIDEO_FILTERS = [
    {"name": "dark_blue",   "r": 8,   "g": 27,  "b": 74},   # 🌃 أزرق غامق
    {"name": "black",       "r": 0,   "g": 0,   "b": 0},    # ⚫ أسود
    {"name": "red",         "r": 90,  "g": 10,  "b": 10},   # 🔴 أحمر داكن
    {"name": "dark_green",  "r": 10,  "g": 50,  "b": 20},   # 🌲 أخضر غامق
    {"name": "purple",      "r": 50,  "g": 10,  "b": 80},   # 🟣 بنفسجي
    {"name": "gray",        "r": 45,  "g": 45,  "b": 50},   # ⬜ رمادي
]

# شفافية فلتر الفيديو (50%)
VIDEO_FILTER_OPACITY = 0.50


# =========================
# دوال مساعدة
# =========================
def _secure_choice(items):
    """اختيار عشوائي آمن من قائمة باستخدام secrets."""
    if not items:
        raise ValueError("Cannot choose from empty list")
    return items[pysecrets.randbelow(len(items))]


def pick_color_themes() -> tuple[dict, dict]:
    """
    يختار ثيمين مختلفين للنصوص:
    - الأول للعنوان (title)
    - الثاني لـ "اقرأ الوصف" (read_desc)
    
    يضمن أن الثيمين مختلفين تماماً (لون خلفية مختلف).
    
    Returns:
        tuple: (title_theme, read_theme)
        كل ثيم dict يحتوي على: name, bg, text
    """
    # اختيار ثيم العنوان من كل القائمة
    title_theme = _secure_choice(ALL_THEMES)
    
    # اختيار ثيم "اقرأ الوصف" مختلف عن ثيم العنوان
    remaining = [t for t in ALL_THEMES if t["name"] != title_theme["name"]]
    read_theme = _secure_choice(remaining)
    
    log.info(
        f"🎨 Text themes selected: "
        f"title=[{title_theme['name']} bg={title_theme['bg']}] | "
        f"read=[{read_theme['name']} bg={read_theme['bg']}]"
    )
    
    return title_theme, read_theme


def pick_video_filter() -> dict:
    """
    يختار فلتر فيديو عشوائي من القائمة.
    الفلتر المختار يُطبَّق على جميع مشاهد الفيديو
    (لضمان لون موحّد للفيديو بأكمله).
    
    Returns:
        dict يحتوي على: name, r, g, b
    """
    video_filter = _secure_choice(VIDEO_FILTERS)
    log.info(
        f"🎬 Video filter selected: {video_filter['name']} "
        f"rgb({video_filter['r']},{video_filter['g']},{video_filter['b']}) "
        f"@ {int(VIDEO_FILTER_OPACITY * 100)}%"
    )
    return video_filter


# =========================
# دوال إضافية للاستخدام المتقدّم
# =========================
def get_theme_by_name(name: str) -> dict | None:
    """الحصول على ثيم نص بالاسم."""
    for theme in ALL_THEMES:
        if theme["name"].lower() == name.lower():
            return theme
    return None


def get_video_filter_by_name(name: str) -> dict | None:
    """الحصول على فلتر فيديو بالاسم."""
    for vf in VIDEO_FILTERS:
        if vf["name"].lower() == name.lower():
            return vf
    return None


def list_all_themes() -> list[str]:
    """قائمة بأسماء كل ثيمات النصوص."""
    return [t["name"] for t in ALL_THEMES]


def list_all_video_filters() -> list[str]:
    """قائمة بأسماء كل فلاتر الفيديو."""
    return [vf["name"] for vf in VIDEO_FILTERS]


# =========================
# Test / Debug
# =========================
if __name__ == "__main__":
    """اختبار سريع للتحقق من عمل الوحدة."""
    import sys
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    
    print("=" * 60)
    print("🎨 Themes Module Test")
    print("=" * 60)
    
    # عرض كل الثيمات
    print(f"\n📝 Total text themes: {len(ALL_THEMES)}")
    print("   White text themes:")
    for t in WHITE_TEXT_THEMES:
        print(f"     - {t['name']:10s} bg={t['bg']} text={t['text']}")
    print("   Black text themes:")
    for t in BLACK_TEXT_THEMES:
        print(f"     - {t['name']:10s} bg={t['bg']} text={t['text']}")
    
    print(f"\n🎬 Total video filters: {len(VIDEO_FILTERS)}")
    for vf in VIDEO_FILTERS:
        print(f"   - {vf['name']:12s} rgb({vf['r']:3d},{vf['g']:3d},{vf['b']:3d})")
    
    print(f"\n⚙️  Video filter opacity: {int(VIDEO_FILTER_OPACITY * 100)}%")
    
    # اختبار الاختيار العشوائي (5 مرات)
    print("\n" + "=" * 60)
    print("🎲 Random selection test (5 iterations):")
    print("=" * 60)
    
    for i in range(1, 6):
        print(f"\n--- Iteration {i} ---")
        title_t, read_t = pick_color_themes()
        vf = pick_video_filter()
    
    print("\n" + "=" * 60)
    print("✅ All tests passed!")
    print("=" * 60)
