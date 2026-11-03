#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ماژول نرمال‌سازی متن، ارقام، کدهای ردیف و واحدهای فهرست بها (normalize.py).
شامل:
- تبدیل ارقام فارسی و عربی به لاتین
- یکسان‌سازی ي/ی و ك/ک
- نرمال‌سازی جداکننده هزارگان و اعشار
- استانداردسازی کد ۶ رقمی رسمی و کد داخلی
- استانداردسازی واحدها طبق تصمیم ۵ کارفرما (فقط حذف فاصله و نویسه RLM اضافی در ۶ مورد مشخص‌شده
  بدون هیچ‌گونه ادغام معنایی دیگر)
- نرمال‌سازی متن بندهای PDF (`normalize_clause_text`) برای ستون `text_normalized` جدول `notes`
  (با حفظ خروجی دست‌نخورده `pdf_fix` در ستون `text_raw`): اصلاح قاعده‌مند «ال» معکوس به «لا»
  و حذف فاصله‌های شکسته درون‌کلمه‌ای (مانند «باالسری» -> «بالاسری»، «ع ملیات» -> «عملیات»،
  «آزمایشگا ه» -> «آزمایشگاه»، «مترمربع - ماه» -> «مترمربع-ماه»).
"""

import math
import re
from typing import Optional, Tuple, Any

# جدول تبدیل ارقام فارسی و عربی به لاتین
_PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
_LATIN_DIGITS = "0123456789"

_DIGIT_TRANS = str.maketrans(
    _PERSIAN_DIGITS + _ARABIC_DIGITS,
    _LATIN_DIGITS + _LATIN_DIGITS,
)

# جدول یکسان‌سازی حروف عربی به فارسی استاندارد
_CHAR_TRANS = str.maketrans(
    {
        "ي": "ی",
        "ى": "ی",
        "ئ": "ئ",
        "ك": "ک",
    }
)

# نویسه‌های کنترلی جهت‌دهی و فاصله‌های نامرئی برای تشخیص پیشوند شرح ردیف
_BIDI_AND_SPACE_RE = re.compile(r"[\u200b\u200c\u200d\u200e\u200f\xa0\s]+")

# نگاشت دقیق استانداردسازی واحدها طبق تصمیم ۵ کارفرما (صرفاً حذف فاصله و RLM اضافی)
UNIT_STANDARDIZATION_MAP = {
    "متر مربع": "مترمربع",
    "متر طول": "مترطول",
    "کیلو گرم": "کیلوگرم",
    "دسی‌متر \u200fمکعب": "دسی‌مترمکعب",
    "دسی\u200cمتر \u200fمکعب": "دسی‌مترمکعب",
    "تن -  کیلومتر ": "تن-کیلومتر",
    "تن -  کیلومتر": "تن-کیلومتر",
    "مترمکعب -  کیلومتر\u200f": "مترمکعب-کیلومتر",
    "مترمکعب -  کیلومتر": "مترمکعب-کیلومتر",
    "مترمربع - ماه": "مترمربع-ماه",
}

MISSING_UNIT_STATUS = "نیازمند بررسی - فاقد واحد در مبدأ"
VALID_UNIT_STATUS = "valid"

# فهرست صریح و تست‌شده واژگان دارای لیگاتور معکوس «ال» در فونت‌های B Lotus / HM FLotoos
# توجه: کلماتی که واقعاً دارای «ال» هستند (مانند الزامات، الف، دال، مصالح، قالب، کانال، سال، حالت، مالی، الخ)
# به هیچ عنوان در این جدول نیستند و دست‌نخورده می‌مانند.
_LA_LIGATURE_RULES: list[tuple[str, str]] = [
    ("باالترین", "بالاترین"),
    ("باالتر", "بالاتر"),
    ("باالرونده", "بالارونده"),
    ("باالبرنده", "بالابرنده"),
    ("باالسری", "بالاسری"),
    ("باالسر", "بالاسر"),
    ("باال", "بالا"),
    ("فوالدی", "فولادی"),
    ("فوالد", "فولاد"),
    ("مالت", "ملات"),
    ("مالک", "ملاک"),
    ("خالصه", "خلاصه"),
    ("اقالمی", "اقلامی"),
    ("اقالم", "اقلام"),
    ("اطالعاتی", "اطلاعاتی"),
    ("اطالعات", "اطلاعات"),
    ("اطالع", "اطلاع"),
    ("اطالق", "اطلاق"),
    ("ابالغ", "ابلاغ"),
    ("اتالف", "اتلاف"),
    ("اختالط", "اختلاط"),
    ("اختالف", "اختلاف"),
    ("استهالک", "استهلاک"),
    ("اصالحیه", "اصلاحیه"),
    ("اصالحی", "اصلاحی"),
    ("آالت", "آلات"),
    ("پالستیکی", "پلاستیکی"),
    ("پالستیک", "پلاستیک"),
    ("پالک", "پلاک"),
    ("کاالها", "کالاها"),
    ("کاالی", "کالای"),
    ("کاال", "کالا"),
    ("کالف", "کلاف"),
    ("الشه", "لاشه"),
    ("الیه", "لایه"),
    ("اموالیت", "امولایت"),
    ("االستیسیته", "الاستیسیته"),
    ("احتماال", "احتمالا"),
    ("مسئوالن", "مسئولان"),
    ("مسووالن", "مسوولان"),
    ("لوالیی", "لولایی"),
    ("غالف", "غلاف"),
    ("عالمت", "علامت"),
    ("عالوه", "علاوه"),
    ("دالیل", "دلایل"),
    ("مدوالر", "مدولار"),
    ("الزم", "لازم"),
]

# فهرست قاعده‌مند واژگان شکسته و جابه‌جاشده در استخراج خطی فونت‌های PDF
_BROKEN_WORD_RULES: list[tuple[str, str]] = [
    (r"مترمربع\s*-\s*ماه", "مترمربع-ماه"),
    (r"تن\s*-\s*کیلومتر", "تن-کیلومتر"),
    (r"مترمکعب\s*-\s*کیلومتر", "مترمکعب-کیلومتر"),
    (r"\bع\s+ملیات\b", "عملیات"),
    (r"\bعمل\s+ی\s*ات\b", "عملیات"),
    (r"\bعملی\s+ات\b", "عملیات"),
    (r"\bآزمایشگا\s+ه\b", "آزمایشگاه"),
    (r"\bکارگا\s+ه\b", "کارگاه"),
    (r"\bگرو\s+ه\b", "گروه"),
    (r"\bنبود\s+ه\b", "نبوده"),
    (r"\bستار\s+ه\b", "ستاره"),
    (r"\bد\s+ه\b", "ده"),
    (r"\bکا\s+ربرد\b", "کاربرد"),
    (r"\bف\s+صل\b", "فصل"),
    (r"\bف\s+نی\b", "فنی"),
    (r"\bفن\s+ی\b", "فنی"),
    (r"\bم\s+ی\b", "می"),
    (r"\bرد\s+فی\b", "ردیف"),
    (r"\bرد\s+یف\b", "ردیف"),
    (r"\bیس\s+مان\b", "سیمان"),
    (r"\bس\s+ی\s+مان\b", "سیمان"),
    (r"\bسی\s+مان\b", "سیمان"),
    (r"\bهز\s+ی\s+نه\b", "هزینه"),
    (r"\bته\s+هی\b", "تهیه"),
    (r"\bته\s+یه\b", "تهیه"),
    (r"\bتأ\s+یی\s*د\b", "تأیید"),
    (r"\bتا\s+یی\s*د\b", "تایید"),
    (r"\bتع\s+یی\s*ن\b", "تعیین"),
    (r"\bکی\s+لوگرم\b", "کیلوگرم"),
    (r"\bکی\s+لومتر\b", "کیلومتر"),
    (r"\bیغ\s+رپا\s+هی\b", "غیرپایه"),
    (r"\bیغ\s+رعمران\s+ی\b", "غیرعمرانی"),
    (r"\bعمران\s+ی\b", "عمرانی"),
    (r"\bباالسر\s+ی\b", "بالاسری"),
    (r"\bبالاسر\s+ی\b", "بالاسری"),
    (r"\bفوالد\s+ی\b", "فولادی"),
    (r"\bفولاد\s+ی\b", "فولادی"),
    (r"\bضر\s+یب\b", "ضریب"),
    (r"\bضر\s+بی\b", "ضریب"),
    (r"\bب\s+رابر\b", "برابر"),
    (r"\bتشر\s+ی\s+فات\b", "تشریفات"),
    (r"\bپ\s+ی\s+وست\b", "پیوست"),
    (r"\bبها\s+ی\b", "بهای"),
    (r"\bبرا\s+ی\b", "برای"),
    (r"\bاجرا\s+ی\b", "اجرای"),
    (r"\bکارها\s+یی\b", "کارهایی"),
    (r"\bها\s+ی\b", "های"),
    (r"\bقالب\s+بند\s+ی\b", "قالب‌بندی"),
    (r"\bبتن\s+ر\s*یزی\b", "بتن‌ریزی"),
]


def digits_to_latin(text: str) -> str:
    """تبدیل تمام ارقام فارسی و عربی موجود در رشته به ارقام لاتین (0-9)."""
    if not text:
        return ""
    return str(text).translate(_DIGIT_TRANS)


def unify_persian_chars(text: str) -> str:
    """یکسان‌سازی حروف ي/ى عربی به ی فارسی و ك عربی به ک فارسی."""
    if not text:
        return ""
    return str(text).translate(_CHAR_TRANS)


def normalize_text(text: Optional[str]) -> str:
    """
    نرمال‌سازی پایه متن فارسی (شرح ردیف‌ها و متن خام پس از pdf_fix):
    - یکسان‌سازی ی و ک
    - تبدیل ارقام فارسی/عربی به لاتین
    - حذف نویسه‌های جهت‌دهی مزاحم (RLM/LRM) و یکسان‌سازی فاصله‌های متوالی
    """
    if text is None:
        return ""
    s = str(text)
    s = unify_persian_chars(s)
    s = digits_to_latin(s)
    # حذف RLM/LRM (\u200e, \u200f) اما حفظ نیم‌فاصله (\u200c)
    s = s.replace("\u200f", "").replace("\u200e", "").replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s).strip()
    return s


def normalize_clause_text(raw_text: Optional[str]) -> str:
    """
    نرمال‌سازی پیشرفته متن بندهای PDF صرفاً برای ستون `text_normalized` در جدول `notes`
    (بدون دست زدن به `text_raw`):
    ۱. اعمال نرمال‌سازی پایه (ی/ک و ارقام لاتین)
    ۲. اصلاح فاصله‌های شکسته درون‌کلمه‌ای طبق قواعد صریح `_BROKEN_WORD_RULES`
    ۳. اصلاح لیگاتور معکوس «ال» به «لا» صرفاً روی واژگان ثبت‌شده در `_LA_LIGATURE_RULES`
       (بدون تخریب واژگانی که واقعاً دارای «ال» هستند مانند الزامات، الف، دال، مصالح، قالب، کانال)
    ۴. تنظیم فاصله قبل از علائم نگارشی (ویرگول و نقطه)
    """
    if raw_text is None:
        return ""
    s = normalize_text(raw_text)
    for pat, repl in _BROKEN_WORD_RULES:
        s = re.sub(pat, repl, s)
    for wrong_la, right_la in _LA_LIGATURE_RULES:
        s = s.replace(wrong_la, right_la)
    # اجرای مجدد قواعد شکسته که پس از اصلاح ال کامل می‌شوند
    for pat, repl in _BROKEN_WORD_RULES:
        s = re.sub(pat, repl, s)
    s = re.sub(r"\s+([،.؛:])", r"\1", s)
    s = re.sub(r"[ \t]+", " ", s).strip()
    return s


# نام مستعار برای سازگاری کامل با اسکریپت‌ها و ایمپورت‌های قبلی
normalize_pdf_note_text = normalize_clause_text


def normalize_number_str(val: Any) -> Optional[float]:
    """
    تبدیل رشته یا مقدار عددی (شامل ارقام فارسی، جداکننده هزارگان ',' یا '،' و ممیز '/')
    به عدد اعشاری پایتون (float). در صورت خالی بودن یا NaN، مقدار None برمی‌گرداند.
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        if isinstance(val, float) and math.isnan(val):
            return None
        return float(val)
    s = digits_to_latin(str(val)).strip()
    if not s or s.lower() == "nan":
        return None
    # حذف جداکننده‌های هزارگان لاتین و فارسی
    s = s.replace(",", "").replace("،", "").replace("٬", "").replace(" ", "")
    # تبدیل ممیز فارسی به نقطه در صورت وجود
    if "/" in s and "." not in s:
        s = s.replace("/", ".")
    try:
        return float(s)
    except ValueError:
        return None


def normalize_item_code(raw_code: Any, internal_prefix: str = "11") -> Tuple[str, str]:
    """
    نرمال‌سازی کد ردیف فهرست بها:
    - خروجی اول: code_official (متن ۶ رقمی با صفر ابتدایی، مثل '010101')
    - خروجی دوم: code_internal (با پیشوند رشته، مثل '11010101')
    """
    s = digits_to_latin(str(raw_code)).strip()
    # اگر کد ۸ رقمی با پیشوند رشته وارد شده باشد، ۶ رقم آخر کد رسمی است
    if len(s) == 8 and s.startswith(internal_prefix):
        code_official = s[2:]
    else:
        code_official = s.zfill(6)
    code_internal = f"{internal_prefix}{code_official}"
    return code_official, code_internal


def normalize_unit(raw_unit: Any) -> Tuple[Optional[str], Optional[str], str]:
    """
    استانداردسازی واحد ردیف طبق تصمیم ۵ کارفرما:
    - خروجی اول (unit_raw): واحد خام دقیق اکسل (یا None اگر در مبدأ خالی است)
    - خروجی دوم (unit): واحد استاندارد پس از حذف فاصله و RLM اضافی طبق نگاشت مصوب
    - خروجی سوم (unit_status): 'valid' یا 'نیازمند بررسی - فاقد واحد در مبدأ'
    """
    if raw_unit is None or (isinstance(raw_unit, float) and math.isnan(raw_unit)):
        return None, None, MISSING_UNIT_STATUS
    raw_str = str(raw_unit)
    if raw_str.strip() == "" or raw_str.strip().lower() == "nan":
        return None, None, MISSING_UNIT_STATUS

    # بررسی در جدول نگاشت صریح تصمیم ۵ کارفرما
    unit_clean_key = unify_persian_chars(raw_str)
    if raw_str in UNIT_STANDARDIZATION_MAP:
        std_unit = UNIT_STANDARDIZATION_MAP[raw_str]
    elif unit_clean_key in UNIT_STANDARDIZATION_MAP:
        std_unit = UNIT_STANDARDIZATION_MAP[unit_clean_key]
    else:
        # حذف RLM اضافی و فاصله ابتدا/انتها و یکسان‌سازی ی/ک بدون هیچ ادغام معنایی دیگر
        std_unit = unify_persian_chars(raw_str).replace("\u200f", "").strip()
        if std_unit in UNIT_STANDARDIZATION_MAP:
            std_unit = UNIT_STANDARDIZATION_MAP[std_unit]

    return raw_str, std_unit, VALID_UNIT_STATUS


def classify_item_category(description: str, unit_price: Optional[float]) -> str:
    """
    طبقه‌بندی ردیف فهرست بها به یکی از سه دستهٔ اصلی معیار پذیرش:
    - 'deduction': ردیف‌های کسربها (شروع شرح با «کسربها» پس از حذف فاصله/نیم‌فاصله/RLM، یا قیمت منفی)
    - 'additional': ردیف‌های اضافه‌بها (شروع شرح با «اضافه‌بها» پس از حذف فاصله/نیم‌فاصله/RLM)
    - 'base': سایر ردیف‌ها (ردیف‌های پایه)
    """
    cleaned = _BIDI_AND_SPACE_RE.sub("", unify_persian_chars(str(description or "")))
    if (unit_price is not None and unit_price < 0) or cleaned.startswith("کسربها"):
        return "deduction"
    if cleaned.startswith("اضافهبها"):
        return "additional"
    return "base"
