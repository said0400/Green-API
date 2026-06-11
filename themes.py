"""
themes.py
---------
نظام إدارة الثيمات اللونية للنصوص.
يختار ثيم عشوائي للعنوان وثيم مختلف لـ "اقرأ الوصف".
"""
import logging
import secrets as pysecrets

log = logging.getLogger("themes")


# =========================
# قائمة الثيمات اللونية
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

# كل الثيمات في قائمة واحدة
ALL_THEMES = WHITE_TEXT_THEMES + BLACK_TEXT_THEMES


def _secure_choice(items):
    """اختيار عشوائي آمن من قائمة."""
    if not items:
        raise ValueError("Cannot choose from empty list")
    return items[pysecrets.randbelow(len(items))]


def pick_color_themes() -> tuple[dict, dict]:
    """
    يختار ثيمين مختلفين:
    - الأول للعنوان (title)
    - الثاني لـ "اقرأ الوصف" (read_desc)
    
    يضمن أن يكونا مختلفين في الاسم (لون الخلفية).
    """
    title_theme = _secure_choice(ALL_THEMES)
    
    # اختيار ثيم مختلف لـ read_desc
    remaining = [t for t in ALL_THEMES if t["name"] != title_theme["name"]]
    read_theme = _secure_choice(remaining)
    
    log.info(
        f"🎨 Themes selected: "
        f"title=[{title_theme['name']} bg={title_theme['bg']}] | "
        f"read=[{read_theme['name']} bg={read_theme['bg']}]"
    )
    
    return title_theme, read_theme
