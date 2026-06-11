"""
ai_client.py v3.0
-----------------
نظام AI ذكي مع failover كامل + Structured Outputs:

التسلسل:
1. gemini-2.5-flash على 4 API keys
2. gemini-2.0-flash على 4 API keys
3. gemini-flash-latest على 4 API keys
4. Groq (fallback نهائي)

التحسينات:
- Structured Outputs (Pydantic schema) لـ Gemini
- JSON Sanitization متقدّم
- Repair JSON عند الفشل
"""
import json
import logging
import os
import re
from typing import Optional, Type

import google.generativeai as genai
from groq import Groq
from pydantic import BaseModel, Field

log = logging.getLogger("ai_client")


# =========================
# Pydantic Schemas
# =========================
class ViralContent(BaseModel):
    """Schema للوصف الفيروسي + الـ hashtags."""
    description: str = Field(
        description="الوصف الكامل للفيديو بالعربية (100-140 كلمة)"
    )
    hashtags: list[str] = Field(
        description="15 هاشتاج (مزيج عربي وإنجليزي) بدون رمز #"
    )


class SearchQueries(BaseModel):
    """Schema لكلمات البحث في Pexels."""
    queries: list[str] = Field(
        description="6 كلمات بحث بالإنجليزية لمقاطع فيديو"
    )


# =========================
# إعدادات
# =========================
GEMINI_MODELS = [
    "gemini-2.5-flash-lite", 
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-flash-latest",
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
# JSON Sanitization & Repair
# =========================
def _clean_json_response(text: str) -> str:
    """تنظيف استجابة JSON من markdown وتنسيقات إضافية."""
    text = text.strip()
    
    # إزالة ```json ... ```
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.M).strip()
    
    # إزالة أي نص قبل أول { وبعد آخر }
    first_brace = text.find('{')
    last_brace = text.rfind('}')
    if first_brace != -1 and last_brace != -1:
        text = text[first_brace:last_brace + 1]
    
    return text.strip()


def _repair_json(text: str) -> str:
    """محاولة إصلاح JSON المكسور."""
    # إصلاح الأسطر الجديدة العشوائية داخل القيم النصية
    text = re.sub(r'(?<!\\)\n(?=[^"]*"(?:[^"\\]|\\.)*$)', '\\n', text)
    
    # إصلاح علامات الاقتباس غير المغلقة
    text = text.replace('\r', '')
    
    # إزالة الفواصل الزائدة قبل ] أو }
    text = re.sub(r',(\s*[\]\}])', r'\1', text)
    
    return text


def _safe_json_parse(text: str) -> dict:
    """تحليل JSON آمن مع محاولات إصلاح متعددة."""
    cleaned = _clean_json_response(text)
    
    # المحاولة 1: تحليل مباشر
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        log.warning(f"⚠️ First JSON parse failed: {e}")
    
    # المحاولة 2: إصلاح وتحليل
    try:
        repaired = _repair_json(cleaned)
        return json.loads(repaired)
    except json.JSONDecodeError as e:
        log.warning(f"⚠️ Repair attempt failed: {e}")
    
    # المحاولة 3: استبدال الأسطر الجديدة
    try:
        escaped = cleaned.replace('\n', '\\n').replace('\r', '')
        return json.loads(escaped)
    except json.JSONDecodeError as e:
        log.error(f"❌ All JSON repair attempts failed: {e}")
        raise


# =========================
# مساعدات
# =========================
def _get_gemini_keys() -> list[tuple[str, str]]:
    keys = []
    for var in GEMINI_API_KEY_VARS:
        key = os.getenv(var, "").strip()
        if key:
            keys.append((var, key))
    return keys


# =========================
# Gemini Client (مع Structured Outputs)
# =========================
def _call_gemini_once(
    api_key: str,
    model_name: str,
    prompt: str,
    temperature: float = 1.0,
    max_tokens: int = 2000,
    response_schema: Optional[Type[BaseModel]] = None,
) -> str:
    """
    استدعاء واحد لـ Gemini API مع Structured Outputs (إذا تم تمرير schema).
    """
    genai.configure(api_key=api_key)

    generation_config = {
        "temperature": temperature,
        "max_output_tokens": max_tokens,
        "response_mime_type": "application/json",
    }
    
    # إضافة Pydantic schema إذا متوفر (يجبر Gemini على إخراج JSON متوافق)
    if response_schema is not None:
        generation_config["response_schema"] = response_schema

    model = genai.GenerativeModel(
        model_name=model_name,
        generation_config=generation_config,
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
    client = _get_groq_client()
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content.strip()


def _try_groq(prompt: str, temperature: float, max_tokens: int) -> dict:
    log.info("🔄 === Falling back to Groq ===")
    try:
        raw = _call_groq_once(prompt, temperature, max_tokens)
        data = _safe_json_parse(raw)
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
    response_schema: Optional[Type[BaseModel]] = None,
) -> dict:
    """
    استدعاء AI ذكي مع failover كامل + Structured Outputs.
    
    Args:
        prompt: النص المُرسَل للـ AI
        temperature: درجة الإبداع (0.0 - 2.0)
        max_tokens: الحد الأقصى للـ tokens
        response_schema: Pydantic schema لإجبار JSON متوافق (اختياري)
    
    Returns:
        dict: JSON response مُحلَّل
    """
    gemini_keys = _get_gemini_keys()

    if not gemini_keys:
        log.warning("⚠️ No Gemini API keys found, using Groq directly")
        return _try_groq(prompt, temperature, max_tokens)

    log.info(f"🔑 Found {len(gemini_keys)} Gemini API key(s)")
    
    if response_schema:
        log.info(f"📋 Using structured output schema: {response_schema.__name__}")

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
                    response_schema=response_schema,
                )

                # تحليل JSON الآمن مع إصلاح تلقائي
                data = _safe_json_parse(raw)

                log.info(f"   ✅ [{model_name}] SUCCESS with {var_name}")
                return data

            except json.JSONDecodeError as e:
                log.warning(
                    f"   ⚠️ [{model_name}] {var_name} returned invalid JSON "
                    f"(even after repair): {str(e)[:100]}"
                )
            except Exception as e:
                error_type = type(e).__name__
                error_msg = str(e)[:200]
                log.warning(
                    f"   ⚠️ [{model_name}] {var_name} failed: "
                    f"{error_type}: {error_msg}"
                )

        log.warning(f"⚠️ All keys failed for {model_name}, trying next model...")

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
    print("🧪 AI Client Test v3.0 (Structured Outputs)")
    print("=" * 60)

    keys = _get_gemini_keys()
    print(f"\n🔑 Available Gemini keys: {len(keys)}")
    for var, _ in keys:
        print(f"   ✓ {var}")
    
    groq_available = bool(os.getenv("GROQ_API_KEY"))
    print(f"\n🤖 Groq fallback: {'✓ Available' if groq_available else '✗ Not configured'}")

    print(f"\n📋 Gemini models to try:")
    for i, m in enumerate(GEMINI_MODELS, 1):
        print(f"   {i}. {m}")

    # اختبار مع Structured Output
    test_prompt = """اكتب وصف قصير لفيديو عن علم النفس.

يجب أن يكون الناتج JSON صالحاً 100%.
استخدم علامات اقتباس مفردة داخل النص العربي بدلاً من المزدوجة.

أرجع فقط JSON بالشكل:
{
  "description": "وصف من 80-100 كلمة عن علم النفس النفسي",
  "hashtags": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6", "tag7", "tag8", "tag9", "tag10", "tag11", "tag12", "tag13", "tag14", "tag15"]
}
"""

    print("\n" + "=" * 60)
    print("📝 Running test with structured output...")
    print("=" * 60)

    try:
        result = smart_ai_call(
            test_prompt,
            temperature=0.9,
            max_tokens=1500,
            response_schema=ViralContent,
        )
        print(f"\n✅ Result:")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"\n📊 Stats:")
        print(f"   Description: {len(result.get('description', '').split())} words")
        print(f"   Hashtags: {len(result.get('hashtags', []))}")
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
