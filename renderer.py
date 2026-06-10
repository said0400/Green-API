"""
renderer.py
-----------
وحدة تحويل HTML إلى صور PNG شفافة باستخدام Playwright.
تدعم اللغة العربية بشكل كامل 100%.
"""
import logging
from pathlib import Path
from jinja2 import Template
from playwright.sync_api import sync_playwright

log = logging.getLogger("renderer")

ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = ROOT / "assets" / "templates"
FONT_PATH = ROOT / "assets" / "fonts" / "NotoNaskhArabic-Bold.ttf"


class HTMLRenderer:
    """مدير وحيد لمتصفح Playwright (يفتح مرة واحدة فقط)."""

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
        """
        يحوّل HTML إلى صورة PNG شفافة.
        يقص الصورة على حجم العنصر المحدد بـ selector فقط.
        """
        context = self._browser.new_context(
            viewport={"width": self.viewport_width, "height": self.viewport_height},
            device_scale_factor=2,  # جودة Retina للنص الحاد
        )
        page = context.new_page()

        try:
            # تحميل HTML
            page.set_content(html_content, wait_until="networkidle")

            # انتظار تحميل الخط
            page.evaluate("document.fonts.ready")
            page.wait_for_timeout(150)  # تأكيد إضافي

            # التقاط العنصر فقط (مع خلفية شفافة)
            element = page.query_selector(selector)
            if not element:
                raise RuntimeError(f"Selector '{selector}' not found in HTML")

            element.screenshot(
                path=str(output_path),
                omit_background=True,  # خلفية شفافة
                type="png",
            )
            log.info(f"✓ Rendered: {output_path.name}")

        finally:
            page.close()
            context.close()


def load_template(template_name: str) -> Template:
    """تحميل قالب Jinja2 من مجلد templates."""
    template_path = TEMPLATES_DIR / template_name
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")
    return Template(template_path.read_text(encoding="utf-8"))


def render_title_png(
    renderer: HTMLRenderer,
    title: str,
    output_path: Path,
    font_size: int = 74,
    max_width: int = 880,
    pad_x: int = 52,
    pad_y: int = 36,
    radius: int = 42,
):
    """رسم عنوان رئيسي بصندوق أحمر مرجاني."""
    template = load_template("title.html")
    html = template.render(
        text=title,
        font_path=str(FONT_PATH.resolve()).replace("\\", "/"),
        font_size=font_size,
        max_width=max_width,
        pad_x=pad_x,
        pad_y=pad_y,
        radius=radius,
    )
    renderer.render(html, output_path, selector=".title-box")


def render_read_desc_png(
    renderer: HTMLRenderer,
    text: str,
    output_path: Path,
):
    """رسم نص 'اقرأ الوصف' مع سهم."""
    template = load_template("read_desc.html")
    html = template.render(
        text=text,
        font_path=str(FONT_PATH.resolve()).replace("\\", "/"),
    )
    renderer.render(html, output_path, selector=".read-box")


def auto_fit_title(
    renderer: HTMLRenderer,
    title: str,
    output_path: Path,
    max_height: int = 620,
):
    """
    رسم العنوان مع تجربة عدة أحجام للخط حتى يناسب الارتفاع.
    يعيد ارتفاع الصورة النهائية.
    """
    from PIL import Image

    for size in [78, 72, 66, 60, 54, 48]:
        render_title_png(renderer, title, output_path, font_size=size)
        with Image.open(output_path) as img:
            if img.height <= max_height * 2:  # *2 بسبب device_scale_factor=2
                log.info(f"✓ Title fitted at font_size={size}")
                return img.height // 2
    # آخر محاولة بأصغر حجم
    with Image.open(output_path) as img:
        return img.height // 2
