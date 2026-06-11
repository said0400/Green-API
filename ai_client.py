"""
ai_client.py
------------
نظام AI ذكي مع failover:
- Gemini 1.5-flash (4 API keys)
- Gemini 2.0-flash-exp (4 API keys)
- Groq (fallback نهائي)

التسلسل:
1. يجرّب gemini-1.5-flash على 4 مفاتيح
2. إذا فشل الكل، يجرّب gemini-2.0-flash-exp على نفس 4 مفاتيح
3. إذا فشل الكل، يرجع لـ Groq
"""
import json
import logging
import os
import re
import time
from typing import Optional

import google.generativeai as genai
from groq import Groq

log = logging.getLogger("ai_client")


# =========================
# إعدادات
# =========================
GEMINI_MODELS = [
    "gemini-1.5-flash",       # ← يُجرَّب أولاً
    "gemini-2.0-flash-exp",   # ← يُجرَّب ثانياً
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
def _get_gemini_keys() -> list[str]:
    """جلب مفاتيح Gemini من المتغيرات البيئية (يتجاهل الفارغة)."""
    keys = []
    for var in GEMINI_API_KEY_VARS:
        key = os.getenv(var, "").strip()
        if key:
            keys.append((var, key))
    return keys


def _clean_json_response(text: str) -> str:
    """تنظيف استجابة JSON من المتاحات (markdown, إلخ)."""
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
    """
    استدعاء واحد لـ Gemini API.
    يرفع Exception عند الفشل.
    """
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
# الدالة الرئيسية: smart_ai_call
# =========================
def smart_ai_call(
    prompt: str,
    temperature: float = 1.0,
    max_tokens: int = 2000,
) -> dict:
    """
    استدعاء AI ذكي مع failover كامل:
    1. يجرّب gemini-1.5-flash على 4 مفاتيح
    2. إذا فشل، يجرّب gemini-2.0-flash-exp على 4 مفاتيح
    3. إذا فشل، يرجع لـ Groq
    
    Returns:
        dict: JSON response parsed
    """
    gemini_keys = _get_gemini_keys()
    
    if not gemini_keys:
        log.warning("⚠️ No Gemini API keys found, using Groq directly")
        return _try_groq(prompt, temperature, max_tokens)
    
    log.info(f"🔑 Found {len(gemini_keys)} Gemini API key(s)")
    
    # ===== المحاولة 1: تجربة كل النماذج على كل المفاتيح =====
    for model_name in GEMINI_MODELS:
        log.info(f"🤖 === Trying model: {model_name} ===")
        
        for idx, (var_name, api_key) in enumerate(gemini_keys, 1):
            log.info(f"   🔑 [{model_name}] Attempt with {var_name} ({idx}/{len(gemini_keys)})...")
            
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
            
            except json.JSONDecode
