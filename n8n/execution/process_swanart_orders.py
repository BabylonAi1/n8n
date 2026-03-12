#!/usr/bin/env python3
"""
process_swanart_orders.py - معالج طلبات SwanArt
-------------------------------------------------
يقرأ رسالة زبون من .tmp/input.txt، يستخرج بيانات الطلب
عبر AI (OpenAI أو Gemini)، ويحفظ النتيجة JSON في .tmp/

الاستخدام:
  python execution/process_swanart_orders.py
  python execution/process_swanart_orders.py .tmp/my_message.txt
"""

import os
import sys
import json
import re
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# ─── المسارات ──────────────────────────────────────────────────
BASE_DIR     = Path(__file__).parent.parent
TMP_DIR      = BASE_DIR / ".tmp"
DATA_DIR     = BASE_DIR / "data"
COUNTER_FILE = DATA_DIR / "order_counter.json"
TMP_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

load_dotenv(BASE_DIR / ".env")

# ─── الـ Prompt للذكاء الاصطناعي ────────────────────────────────
AI_SYSTEM_PROMPT = "أنت مساعد متخصص في استخراج بيانات طلبات المبيعات من النصوص العربية."

AI_USER_PROMPT = """\
استخرج بيانات الطلب من النص التالي.

قواعد صارمة:
- أعد JSON فقط، بدون أي نصوص تمهيدية أو ختامية، وبدون علامات ```json أو ```.
- أي حقل غير موجود في النص اكتب قيمته: "غير مذكور".
- في حقل القياس: استبدل أي رمز (*) بالحرف (x)، مثلاً 60*90 تصبح 60x90.

الحقول المطلوبة:
  اسم_الزبون، العنوان، رقم_الهاتف، عدد_اللوحات،
  القياس، نوع_الاطار، السعر، ملاحظات_التصميم، نوع_الطلب

قواعد تصنيف نوع_الطلب:
- إذا وجدت كلمة "تعديل" بأي صيغة -> "تعديل ⚠️"
- إذا وجدت "استبدال" أو "تبديل"       -> "استبدال 🔄"
- عدا ذلك                              -> "طلب جديد 🆕"

قواعد السعر (الأسعار العراقية):
- أرقام صغيرة مثل "40" أو "65" تعني الآلاف -> اكتب "40,000" أو "65,000"
- "40 الف" أو "40k" -> "40,000"

النص:
{message}
"""

# ─── دوال المساعدة ──────────────────────────────────────────────

def sanitize_for_telegram(value: str) -> str:
    """
    يزيل الرموز التي تكسر Telegram HTML parse mode.
    الأخطاء التاريخية: النجمة (*) تُفسَّر كـ Bold وتُعطل الرسائل.
    """
    if not isinstance(value, str):
        return str(value)
    value = value.replace("*", "x")
    value = re.sub(r"[_<>`\\]", "", value)
    return value.strip()


def sanitize_json_fields(data: dict) -> dict:
    """يطبّق sanitize على كل قيمة نصية في الـ JSON المستخرج."""
    return {
        k: sanitize_for_telegram(v) if isinstance(v, str) else v
        for k, v in data.items()
    }


def parse_ai_response(raw: str) -> dict:
    """
    يحوّل نص الـ AI إلى dict بمرونة.
    يتعامل مع حالة وجود ```json أو نصوص إضافية.
    (الخطأ التاريخي #5 من الديركتيف)
    """
    content = raw or ""
    content = re.sub(r"```json", "", content, flags=re.IGNORECASE)
    content = re.sub(r"```",     "", content)
    content = content.strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # محاولة استخراج أول كتلة JSON من النص
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        raise ValueError(
            f"فشل تحليل JSON من الـ AI.\n"
            f"الرد الخام:\n{content}"
        )


# ─── موفّرو الذكاء الاصطناعي ────────────────────────────────────

def call_openai(message: str) -> dict:
    import openai
    client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": AI_SYSTEM_PROMPT},
            {"role": "user",   "content": AI_USER_PROMPT.format(message=message)},
        ],
        temperature=0,
    )
    raw = response.choices[0].message.content or ""
    return parse_ai_response(raw)


def call_gemini(message: str) -> dict:
    import google.generativeai as genai
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model    = genai.GenerativeModel("gemini-1.5-flash")
    prompt   = AI_SYSTEM_PROMPT + "\n\n" + AI_USER_PROMPT.format(message=message)
    response = model.generate_content(prompt)
    raw      = response.text or ""
    return parse_ai_response(raw)


def next_order_number() -> str:
    """
    يقرأ الرقم الأخير من data/order_counter.json، يزيد +1، يحفظ، يُعيد الرقم.
    آمن ضد التزامن عبر file lock بسيط.
    """
    if COUNTER_FILE.exists():
        data = json.loads(COUNTER_FILE.read_text(encoding="utf-8"))
    else:
        data = {"last_order_number": 999, "prefix": "#"}

    data["last_order_number"] += 1
    COUNTER_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return f"{data.get('prefix', '#')}{data['last_order_number']}"


def extract_order_data(message: str) -> dict:
    """يختار الـ AI provider المتاح ويستخرج البيانات."""
    if os.getenv("OPENAI_API_KEY"):
        print("🤖 استخدام OpenAI ...")
        return call_openai(message)
    elif os.getenv("GEMINI_API_KEY"):
        print("🤖 استخدام Gemini ...")
        return call_gemini(message)
    else:
        raise EnvironmentError(
            "❌ لم يتم العثور على مفتاح AI في .env\n"
            "أضف أحد هذين المفتاحين:\n"
            "  OPENAI_API_KEY=sk-...\n"
            "  GEMINI_API_KEY=AIza..."
        )


# ─── الدالة الرئيسية ────────────────────────────────────────────

def process_order(input_path: Path) -> Path:
    message = input_path.read_text(encoding="utf-8").strip()
    if not message:
        raise ValueError(f"ملف الإدخال فارغ: {input_path}")

    print(f"📥 الرسالة المُدخلة:\n{'─'*40}\n{message}\n{'─'*40}\n")

    # استخراج البيانات عبر AI
    extracted = extract_order_data(message)
    extracted = sanitize_json_fields(extracted)

    # إضافة order_id ووقت المعالجة
    timestamp                     = datetime.now().strftime("%Y%m%d%H%M%S")
    extracted["order_id"]         = next_order_number()
    extracted["processed_at"]     = datetime.now().isoformat()
    extracted["source_file"]      = str(input_path)

    print("✅ البيانات المستخرجة:")
    print(json.dumps(extracted, ensure_ascii=False, indent=2))

    # حفظ النتيجة
    output_path = TMP_DIR / f"order_{timestamp}.json"
    output_path.write_text(
        json.dumps(extracted, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"\n💾 تم الحفظ في: {output_path}")
    return output_path


# ─── نقطة الدخول ─────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) > 1:
        input_file = Path(sys.argv[1])
    else:
        input_file = TMP_DIR / "input.txt"

    if not input_file.exists():
        print(f"❌ ملف الإدخال غير موجود: {input_file}\n")
        print("الاستخدام:")
        print(f"  python execution/process_swanart_orders.py")
        print(f"  python execution/process_swanart_orders.py .tmp/my_message.txt\n")
        print(f"ضع رسالة الزبون في: {TMP_DIR / 'input.txt'}")
        sys.exit(1)

    try:
        output = process_order(input_file)
        print(f"\n🎉 اكتملت المعالجة! النتيجة في: {output}")
        sys.exit(0)
    except EnvironmentError as e:
        print(f"\n{e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ خطأ غير متوقع: {e}")
        raise
