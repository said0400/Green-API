"""
renderer.py
-----------
وحدة تحويل HTML إلى صور PNG شفافة باستخدام Playwright.
تدعم اللغة العربية بشكل كامل 100%.
الخط يُحمَّل كـ Base64 لضمان عمله في جميع البيئات.
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


# =========================
# تحميل الخط مرة واحدة كـ Base64
# =========================
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


# =========================
# Playwright Browser Manager
# =========================
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
        يحوّل HTML إلى صورة PNG شفافة بحجم 1:1 (بدون تكبير).
        يقص الصورة على حجم العنصر المحدد بـ selector فقط.
        """
        context = self._browser.new_context(
            viewport={"width": self.viewport_width, "height": self.viewport_height},
            device_scale_factor=1,
        )
        page = context.new_page()

        try:
            # تحميل HTML
            page.set_content(html_content, wait_until="networkidle")
            
            # انتظار تحميل الخط
            page.evaluate("document.fonts.ready")
            page.wait_for_timeout(300)
            
            # انتظار إضافي لتطبيق SVG filters
            page.wait_for_timeout(200)

            # التقاط العنصر فقط (مع خلفية شفافة)
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


# =========================
# Template Loader
# =========================
def load_template(template_name: str) -> Template:
    """تحميل قالب Jinja2 من مجلد templates."""
    template_path = TEMPLATES_DIR / template_name
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")
    return Template(template_path.read_text(encoding="utf-8"))


# =========================
# Render Functions
# =========================
def render_title_png(
    renderer: HTMLRenderer,
    title: str,
    output_path: Path,
    font_size: int = 48,
    max_width: int = 900,
):
    """
    رسم عنوان رئيسي بخلفية عضوية موحّدة تتبع شكل النص.
    تستخدم SVG filter لدمج الأسطر في شكل واحد بحواف ناعمة.
    """
    template = load_template("title.html")
    html = template.render(
        text=title,
        font_base64=get_font_base64(),
        font_size=font_size,
        max_width=max_width,
    )
    renderer.render(html, output_path, selector=".title-wrapper")


def render_read_desc_png(
    renderer: HTMLRenderer,
    text: str,
    output_path: Path,
    font_size: int = 38,
):
    """رسم نص 'اقرأ الوصف' مع سهم متحرّك."""
    template = load_template("read_desc.html")
    html = template.render(
        text=text,
        font_base64=get_font_base64(),
        font_size=font_size,
    )
    renderer.render(html, output_path, selector=".read-wrapper")


def auto_fit_title(
    renderer: HTMLRenderer,
    title: str,
    output_path: Path,
    max_height: int = 700,
    max_width: int = 900,
    preferred_size: int = 48,
):
    """
    رسم العنوان مع تجربة عدة أحجام للخط حتى يناسب الارتفاع.
    يبدأ بـ preferred_size ثم يصغر إذا تجاوز max_height.
    يعيد ارتفاع الصورة النهائية بالبكسل.
    """
    from PIL import Image

    # سلسلة الأحجام: من المفضّل تنازلياً
    sizes = [preferred_size, 44, 40, 36, 32, 28]

    for size in sizes:
        render_title_png(
            renderer, title, output_path,
            font_size=size, max_width=max_width,
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

    # آخر محاولة بأصغر حجم
    with Image.open(output_path) as img:
        log.warning(
            f"⚠️ Title used smallest size ({sizes[-1]}px), "
            f"height={img.height}px (max was {max_height}px)"
        )
        return img.height


# =========================
# Test Function (Optional)
# =========================
if __name__ == "__main__":
    """اختبار سريع للتأكد من عمل الـ renderer."""
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    test_titles = [
        "إذا اعتقدت أنك تحب شخص ما فهل أنت متأكد من حقيقته",
        "السبب الحقيقي وراء صمته المفاجئ",
        "هناك كلمة واحدة تغير كل شيء في علاقتك",
    ]

    output_dir = ROOT / "test_output"
    output_dir.mkdir(exist_ok=True)

    with HTMLRenderer() as r:
        for i, title in enumerate(test_titles, 1):
            out_path = output_dir / f"test_title_{i}.png"
            auto_fit_title(r, title, out_path, preferred_size=48)
            print(f"✓ Saved: {out_path}")

        # اختبار "اقرأ الوصف"
        read_path = output_dir / "test_read_desc.png"
        render_read_desc_png(r, "اقرأ الوصف", read_path)
        print(f"✓ Saved: {read_path}")

    print("\n✅ All tests done! Check test_output/ folder")
