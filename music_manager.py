"""
music_manager.py
----------------
نظام إدارة الموسيقى الخلفية:
- يقرأ ملفات الموسيقى من assets/music/
- يختار التالي بالترتيب (a1 → a2 → ... → a70 → a1)
- يحفظ المؤشر في data/music_pointer.json
"""
import json
import logging
import re
from pathlib import Path

log = logging.getLogger("music_manager")

ROOT = Path(__file__).resolve().parent
MUSIC_DIR = ROOT / "assets" / "music"
DATA_DIR = ROOT / "data"
POINTER_PATH = DATA_DIR / "music_pointer.json"


def ensure_music_dir():
    """التحقق من وجود مجلد الموسيقى وأنه يحتوي على ملفات."""
    if not MUSIC_DIR.exists():
        raise FileNotFoundError(
            f"❌ Music folder not found: {MUSIC_DIR}\n"
            f"Please create it and upload .mp3 files"
        )


def _natural_sort_key(path: Path) -> tuple:
    """
    مفتاح فرز طبيعي للملفات.
    يضمن أن a2.mp3 يأتي قبل a10.mp3 (وليس a10 قبل a2).
    """
    name = path.stem  # بدون .mp3
    # استخراج الرقم من الاسم (a1 → 1, a23 → 23)
    match = re.search(r'\d+', name)
    if match:
        return (0, int(match.group()))
    return (1, name)  # ملفات بدون أرقام تأتي في النهاية


def list_all_music() -> list[Path]:
    """
    قائمة بكل ملفات الموسيقى مرتّبة بالترتيب الطبيعي.
    a1.mp3, a2.mp3, ..., a10.mp3, ..., a70.mp3
    """
    ensure_music_dir()
    
    files = list(MUSIC_DIR.glob("*.mp3"))
    if not files:
        raise RuntimeError(f"❌ No .mp3 files found in {MUSIC_DIR}")
    
    # فرز طبيعي
    files.sort(key=_natural_sort_key)
    
    return files


def load_pointer() -> int:
    """تحميل آخر رقم تم استخدامه."""
    try:
        if POINTER_PATH.exists():
            data = json.loads(POINTER_PATH.read_text(encoding="utf-8"))
            return int(data.get("last_used", 0))
    except Exception as e:
        log.warning(f"Failed to load music pointer: {e}, starting from 0")
    return 0


def save_pointer(last_used: int):
    """حفظ آخر رقم تم استخدامه."""
    POINTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    POINTER_PATH.write_text(
        json.dumps({"last_used": last_used}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_next_music() -> Path:
    """
    جلب ملف الموسيقى التالي بالترتيب.
    عند الوصول للنهاية، يعود للأول.
    
    Returns:
        Path: مسار ملف الموسيقى
    """
    all_music = list_all_music()
    total = len(all_music)
    
    last_used = load_pointer()
    log.info(f"🎵 Music library: {total} files | Last used: #{last_used}")
    
    # حساب الـ index التالي (يدور عند النهاية)
    next_index = last_used % total
    selected = all_music[next_index]
    
    new_last_used = last_used + 1
    save_pointer(new_last_used)
    
    log.info(
        f"🎵 Selected music: {selected.name} "
        f"(#{next_index + 1}/{total}, iteration {new_last_used})"
    )
    
    return selected


def reset_pointer():
    """إعادة تعيين المؤشر للبداية."""
    save_pointer(0)
    log.info("✓ Music pointer reset to 0")


# =========================
# Test
# =========================
if __name__ == "__main__":
    import sys
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    
    print("=" * 60)
    print("🎵 Music Manager Test")
    print("=" * 60)
    
    try:
        # عرض كل الملفات
        files = list_all_music()
        print(f"\n📁 Found {len(files)} music files:")
        for i, f in enumerate(files[:10], 1):
            print(f"  {i:3d}. {f.name}")
        if len(files) > 10:
            print(f"  ... and {len(files) - 10} more")
        
        # اختبار get_next_music (5 مرات)
        print("\n" + "=" * 60)
        print("🎲 Testing get_next_music (5 calls):")
        print("=" * 60)
        
        for i in range(1, 6):
            print(f"\n--- Call {i} ---")
            music = get_next_music()
            print(f"  Selected: {music.name}")
            print(f"  Size: {music.stat().st_size / 1024:.1f} KB")
        
        print("\n✅ Test completed!")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)
