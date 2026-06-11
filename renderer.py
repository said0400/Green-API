"""
renderer.py
-----------
وحدة تحويل HTML إلى صور PNG شفافة باستخدام Playwright.
تدعم اللغة العربية بشكل كامل 100%.
تدعم ثيمات لونية متعددة.
"""
import base64
import logging
from pathlib import Path
from jinja2 import Template
from playwright.sync_api import sync_playwright

log = logging.getLogger("renderer")

ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = ROOT / "assets" / "templates"
FONT_PATH = ROOT / "assets" / "fonts" / "NotoNaskhArabic-Bold.ttf"


_FONT_BASE64 = None


def get_font_base64() -> str:
    """تحميل الخط وتحويله إلى Base64 (cached)."""
    global _FONT_BASE64
    if _FONT_BASE64 is None:
        if not FONT_PATH.exists():
            raise FileNotFoundError(f"Font not found: {FONT_PATH}")
        font_bytes = FONT_PATH.read_bytes()
        _FONT_BASE64 = base64.b64encode(font_bytes).decode("ascii")
        log.info(f"✓ Font encoded as Base64 ({len(_FONT_BASE64)} chars)")
    return _FONT_BASE64


class HTMLRenderer:
    """مدير وحيد لمتصفح Playwright."""

    def __init__(self, viewport_width=1080, viewport_height=1920):
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self._pw = None
        self._browser = None

    def __enter__(self):
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--font-render-hinting=none",
            ]
        )
        log.info("✓ Playwright browser launched")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()
        log.info("✓ Playwright browser closed")

    def render(self, html_content: str, output_path: Path, selector: str = "#content"):
        context = self._browser.new_context(
            viewport={"width": self.viewport_width, "height": self.viewport_height},
            device_scale_factor=1,
        )
        page = context.new_page()

        try:
            page.set_content(html_content, wait_until="networkidle")
            page.evaluate("document.fonts.ready")
            page.wait_for_timeout(300)
            page.wait_for_timeout(200)  # للـ SVG filter

            element = page.query_selector(selector)
            if not element:
                raise RuntimeError(f"Selector '{selector}' not found in HTML")

            element.screenshot(
                path=str(output_path),
                omit_background=True,
                type="png",
            )
            log.info(f"✓ Rendered: {output_path.name}")

        finally:
            page.close()
            context.close()


def load_template(template_name: str) -> Template:
    template_path = TEMPLATES_DIR / template_name
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")
    return Template(template_path.read_text(encoding="utf-8"))


def render_title_png(
    renderer: HTMLRenderer,
    title: str,
    output_path: Path,
    font_size: int = 48,
    max_width: int = 650,
    bg_color: str = "#FF1A1A",
    text_color: str = "#FFFFFF",
):
    """رسم عنوان بألوان مخصصة."""
    template = load_template("title.html")
    html = template.render(
        text=title,
        font_base64=get_font_base64(),
        font_size=font_size,
        max_width=max_width,
        bg_color=bg_color,
        text_color=text_color,
    )
    renderer.render(html, output_path, selector=".title-wrapper")


def render_read_desc_png(
    renderer: HTMLRenderer,
    text: str,
    output_path: Path,
    font_size: int = 38,
    bg_color: str = "#FF1A1A",
    text_color: str = "#FFFFFF",
):
    """رسم 'اقرأ الوصف' بألوان مخصصة."""
    template = load_template("read_desc.html")
    html = template.render(
        text=text,
        font_base64=get_font_base64(),
        font_size=font_size,
        bg_color=bg_color,
        text_color=text_color,
    )
    renderer.render(html, output_path, selector=".read-wrapper")


def auto_fit_title(
    renderer: HTMLRenderer,
    title: str,
    output_path: Path,
    max_height: int = 700,
    max_width: int = 650,
    preferred_size: int = 48,
    bg_color: str = "#FF1A1A",
    text_color: str = "#FFFFFF",
):
    """رسم العنوان مع auto-fit للحجم + ألوان مخصصة."""
    from PIL import Image

    sizes = [preferred_size, 44, 40, 36, 32, 28]

    for size in sizes:
        render_title_png(
            renderer, title, output_path,
            font_size=size,
            max_width=max_width,
            bg_color=bg_color,
            text_color=text_color,
        )
        with Image.open(output_path) as img:
            actual_height = img.height
            actual_width = img.width
            if actual_height <= max_height:
                log.info(
                    f"✓ Title fitted: size={size}px, "
                    f"dimensions={actual_width}x{actual_height}px"
                )
                return actual_height

    with Image.open(output_path) as img:
        log.warning(
            f"⚠️ Title used smallest size ({sizes[-1]}px), "
            f"height={img.height}px"
        )
        return img.height


# =========================
# Test
# =========================
if __name__ == "__main__":
    import sys
    from themes import pick_color_themes, ALL_THEMES

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    output_dir = ROOT / "test_output"
    output_dir.mkdir(exist_ok=True)

    test_title = "إذا اعتقدت أنك تحب شخص ما فهل أنت متأكد"

    # اختبار كل الثيمات
    with HTMLRenderer() as r:
        for theme in ALL_THEMES:
            out_path = output_dir / f"theme_{theme['name']}.png"
            auto_fit_title(
                r, test_title, out_path,
                preferred_size=48,
                max_width=650,
                bg_color=theme["bg"],
                text_color=theme["text"],
            )
            print(f"✓ {theme['name']}: bg={theme['bg']}, text={theme['text']}")

    print("\n✅ All themes tested! Check test_output/")
