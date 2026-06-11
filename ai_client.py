"""
ai_client.py
------------
نظام AI ذكي مع failover كامل:

التسلسل:
1. gemini-1.5-flash على 4 API keys
2. gemini-2.0-flash-exp على 4 API keys
3. Groq (fallback نهائي)

المجموع: 8 محاولات Gemini + Groq = 9 فرص للنجاح
"""
import json
import logging
import os
import re
from typing import Optional

import google.generativeai as genai
from groq import Groq

log = logging.getLogger("ai_client")


# =========================
# إعدادات
# =========================
GEMINI_MODELS = [
    "gemini-1.5-flash",       # ← يُجرَّب أولاً (الأكثر استقراراً)
    "gemini-2.0-flash-exp",   # ← يُجرَّب ثانياً (الأحدث)
]

GEMINI_API_KEY_VARS = [
    "GEMINI_API_KEY_1",
    "GEMINI_API_KEY_2",
    "GEMINI_API_KEY_3",
    "GEMINI_API_KEY_4",
]

GEMINI_TIMEOUT = 60
GROQ_TIMEOUT = 90
GROQ_MODEL = os.getenv("GROQ_MODEL") or "llama-3.3-70b-versatile"


# =========================
# مساعدات
# =========================
def _get_gemini_keys() -> list[tuple[str, str]]:
    """جلب مفاتيح Gemini المتوفرة من المتغيرات البيئية."""
    keys = []
    for var in GEMINI_API_KEY_VARS:
        key = os.getenv(var, "").strip()
        if key:
            keys.append((var, key))
    return keys


def _clean_json_response(text: str) -> str:
    """تنظيف استجابة JSON من markdown وتنسيقات إضافية."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.M).strip()
    return text


# =========================
# Gemini Client
# =========================
def _call_gemini_once(
    api_key: str,
    model_name: str,
    prompt: str,
    temperature: float = 1.0,
    max_tokens: int = 2000,
) -> str:
    """استدعاء واحد لـ Gemini API."""
    genai.configure(api_key=api_key)

    model = genai.GenerativeModel(
        model_name=model_name,
        generation_config={
            "temperature": temperature,
            "max_output_tokens": max_tokens,
            "response_mime_type": "application/json",
        },
    )

    response = model.generate_content(
        prompt,
        request_options={"timeout": GEMINI_TIMEOUT},
    )

    if not response or not response.text:
        raise RuntimeError("Empty response from Gemini")

    return response.text.strip()


# =========================
# Groq Client (Fallback)
# =========================
_GROQ_CLIENT = None


def _get_groq_client() -> Groq:
    global _GROQ_CLIENT
    if _GROQ_CLIENT is None:
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("GROQ_API_KEY not set (fallback unavailable)")
        _GROQ_CLIENT = Groq(api_key=api_key, timeout=GROQ_TIMEOUT)
    return _GROQ_CLIENT


def _call_groq_once(
    prompt: str,
    temperature: float = 1.0,
    max_tokens: int = 2000,
) -> str:
    """استدعاء واحد لـ Groq API."""
    client = _get_groq_client()
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content.strip()


# =========================
# Try Groq (مع التحقق)
# =========================
def _try_groq(prompt: str, temperature: float, max_tokens: int) -> dict:
    """محاولة استخدام Groq كـ fallback نهائي."""
    log.info("🔄 === Falling back to Groq ===")
    try:
        raw = _call_groq_once(prompt, temperature, max_tokens)
        cleaned = _clean_json_response(raw)
        data = json.loads(cleaned)
        log.info("✅ [Groq] SUCCESS (fallback worked)")
        return data
    except Exception as e:
        log.error(f"❌ [Groq] FAILED: {type(e).__name__}: {e}")
        raise RuntimeError(f"All AI providers failed. Last error (Groq): {e}")


# =========================
# الدالة الرئيسية: smart_ai_call
# =========================
def smart_ai_call(
    prompt: str,
    temperature: float = 1.0,
    max_tokens: int = 2000,
) -> dict:
    """
    استدعاء AI ذكي مع failover كامل.
    
    التسلسل:
    1. gemini-1.5-flash على 4 مفاتيح (4 محاولات)
    2. gemini-2.0-flash-exp على 4 مفاتيح (4 محاولات)
    3. Groq (محاولة واحدة كـ fallback)
    
    Args:
        prompt: النص المُرسَل للـ AI
        temperature: درجة الإبداع (0.0 - 2.0)
        max_tokens: الحد الأقصى للـ tokens
    
    Returns:
        dict: JSON response مُحلَّل
    
    Raises:
        RuntimeError: إذا فشلت كل المحاولات
    """
    gemini_keys = _get_gemini_keys()

    if not gemini_keys:
        log.warning("⚠️ No Gemini API keys found, using Groq directly")
        return _try_groq(prompt, temperature, max_tokens)

    log.info(f"🔑 Found {len(gemini_keys)} Gemini API key(s)")

    # ===== المرور على كل نموذج Gemini =====
    for model_name in GEMINI_MODELS:
        log.info(f"🤖 === Trying model: {model_name} ===")

        for idx, (var_name, api_key) in enumerate(gemini_keys, 1):
            log.info(
                f"   🔑 [{model_name}] Attempt {idx}/{len(gemini_keys)} "
                f"with {var_name}..."
            )

            try:
                raw = _call_gemini_once(
                    api_key=api_key,
                    model_name=model_name,
                    prompt=prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

                cleaned = _clean_json_response(raw)
                data = json.loads(cleaned)

                log.info(f"   ✅ [{model_name}] SUCCESS with {var_name}")
                return data

            except json.JSONDecodeError as e:
                log.warning(
                    f"   ⚠️ [{model_name}] {var_name} returned invalid JSON: {e}"
                )
            except Exception as e:
                error_type = type(e).__name__
                error_msg = str(e)[:200]
                log.warning(
                    f"   ⚠️ [{model_name}] {var_name} failed: "
                    f"{error_type}: {error_msg}"
                )

        log.warning(f"⚠️ All keys failed for {model_name}, trying next model...")

    # ===== كل Gemini فشل، استخدم Groq =====
    log.warning("⚠️ All Gemini models/keys exhausted")
    return _try_groq(prompt, temperature, max_tokens)


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
    print("🧪 AI Client Test")
    print("=" * 60)

    # عرض المفاتيح المتوفرة
    keys = _get_gemini_keys()
    print(f"\n🔑 Available Gemini keys: {len(keys)}")
    for var, _ in keys:
        print(f"   ✓ {var}")
    
    groq_available = bool(os.getenv("GROQ_API_KEY"))
    print(f"\n🤖 Groq fallback: {'✓ Available' if groq_available else '✗ Not configured'}")

    # اختبار بسيط
    test_prompt = """قم بإنشاء JSON يحتوي على وصف قصير لفيديو.

أرجع فقط JSON بالشكل التالي:
{
  "description": "وصف قصير من جملتين"
}
"""

    print("\n" + "=" * 60)
    print("📝 Running test prompt...")
    print("=" * 60)

    try:
        result = smart_ai_call(test_prompt, temperature=0.7, max_tokens=500)
        print(f"\n✅ Result:")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
