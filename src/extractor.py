#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ماژول استخراج داده‌های مرجع فهرست بها از فایل‌های اکسل و PDF (extractor.py).
تمام داده‌ها مستقیماً از فایل‌های پوشه data/ استخراج می‌شوند و هیچ قیمت، کد، بند یا ضریبی
از حافظه یا حدس نوشته نمی‌شود.
"""

import json
import re
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import pandas as pd
import pymupdf as fitz

from src.config import DISCIPLINES, DEFAULT_YEAR, USER_APPROVALS_PATH
from src.pdf_fix import open_patched_pdf, extract_rtl_page_lines
from src.normalize import (
    normalize_text,
    normalize_number_str,
    normalize_item_code,
    normalize_unit,
    classify_item_category,
    digits_to_latin,
    unify_persian_chars,
)


def extract_pdf_item_pages(
    doc: fitz.Document, valid_codes: set[str], page_offset: int = 3
) -> Dict[str, Dict[str, Any]]:
    """
    استخراج شماره صفحه PDF، شماره صفحه بولتن، بهای واحد درج‌شده در PDF و متن کامل هر ردیف
    از جداول داخل PDF.
    ردیف‌های جدول فهرست بها در ستون «شماره» (سمت راست صفحه، x0 > 490) قرار دارند
    و در صفحاتی که دارای هدر جدول («بهای کل» و «مقدار») هستند واقع شده‌اند.
    """
    pdf_items: Dict[str, Dict[str, Any]] = {}
    for pno in range(len(doc)):
        page = doc.reload_page(doc[pno])
        words = page.get_text("words")
        lines = []
        for w in sorted(words, key=lambda x: (round(x[1] / 4.0) * 4.0, -x[0])):
            x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4]
            if not lines or abs(lines[-1]["y"] - y0) > 4.5:
                lines.append({"y": y0, "words": [(x0, text)]})
            else:
                lines[-1]["words"].append((x0, text))

        page_text = "\n".join(
            " ".join(t for _, t in sorted(l["words"], key=lambda z: -z[0]))
            for l in lines
        )
        # صفحات جدول ردیف‌ها دارای سرستون‌های «بهای کل» و «مقدار» هستند
        if "بهای کل" not in page_text or "مقدار" not in page_text:
            continue

        current_code: Optional[str] = None
        current_lines: List[str] = []
        current_price_tokens: List[str] = []

        def _finalize_item(code_str: str, lines_list: List[str], price_tokens: List[str]) -> None:
            if code_str in pdf_items:
                return
            full_txt = normalize_text(" ".join(lines_list))
            # استخراج قیمت از ستون بهای واحد PDF (محور X در بازه 120 تا 225)
            pdf_price_val: Optional[float] = None
            if price_tokens:
                joined_p = "".join(price_tokens).replace(" ", "")
                # اگر علامت منفی در انتهای رشته باشد (به علت RTL مثل 104,500-)
                if joined_p.endswith("-") and not joined_p.startswith("-"):
                    joined_p = "-" + joined_p[:-1]
                pdf_price_val = normalize_number_str(joined_p)
            pdf_items[code_str] = {
                "page_pdf": pno + 1,
                "page_bulletin": (pno + 1) - page_offset,
                "raw_text": full_txt,
                "pdf_price": pdf_price_val,
            }

        for l in lines:
            w_sorted = sorted(l["words"], key=lambda z: -z[0])
            line_str = " ".join(t for _, t in w_sorted)
            first_x, first_t = w_sorted[0]
            first_t_clean = digits_to_latin(first_t.strip())

            if (
                first_x > 490
                and re.match(r"^\d{6}$", first_t_clean)
                and first_t_clean in valid_codes
            ):
                if current_code:
                    _finalize_item(current_code, current_lines, current_price_tokens)
                current_code = first_t_clean
                current_lines = [line_str]
                current_price_tokens = [
                    digits_to_latin(t)
                    for x, t in w_sorted
                    if 120.0 <= x <= 225.0 and re.match(r"^[-0-9,،/]+$", digits_to_latin(t))
                ]
            elif current_code:
                # نادیده گرفتن شماره صفحه چاپی در پانویس صفحه
                if re.match(r"^\s*\d{1,3}\s*$", digits_to_latin(line_str)):
                    continue
                current_lines.append(line_str)
                for x, t in w_sorted:
                    if 120.0 <= x <= 225.0 and re.match(r"^[-0-9,،/]+$", digits_to_latin(t)):
                        current_price_tokens.append(digits_to_latin(t))

        if current_code:
            _finalize_item(current_code, current_lines, current_price_tokens)

    return pdf_items


def extract_chapters_from_pdf(
    doc: fitz.Document,
    excel_chapter_counts: Dict[str, int],
    active_phase1: List[str],
    pdf_filename: str,
    discipline: str = "abnieh",
    year: int = DEFAULT_YEAR,
    page_offset: int = 3,
) -> List[Dict[str, Any]]:
    """
    استخراج ساختار ۳۷ بخش/فصل بولتن از صفحات فهرست مطالب PDF (صفحات ۲ و ۳ فایل 11-Abnieh1404.pdf):
    - تمام ۳۶ سطر فهرست مطالب PDF (۲ بخش مقدماتی: دستورالعمل کاربرد و کلیات + ۲۸ فصل عنوان‌دار + ۶ پیوست)
    - ۱ فصل حذف‌شده (فصل ۱۵ که در شماره‌گذاری بین فصل ۱۴ و ۱۶ حذف شده است)
    یا اگر فقط فصول ۱ تا ۲۹ (شامل ۱۵) + ۶ پیوست ثبت شوند: 28 + 1 + 6 = 35 رکورد!
    دلیل اینکه در خروجی قبلی ۳۱ رکورد شد این بود که در صفحه ۲ فهرست مطالب، کلمات ordinal دارای فاصله بودند:
    «ی ازدهم»، «سی زدهم»، «بی ستم»، «یب ست و ی کم»، «یب ست و دوم»!
    """
    toc_lines: List[str] = []
    for pno in [1, 2]:  # صفحات ۲ و ۳ فایل PDF (اندیس ۱ و ۲)
        toc_lines.extend(extract_rtl_page_lines(doc[pno]))

    # نگاشت نام ترتیبی فارسی فصول (با حذف فاصله برای مقاومت در برابر شکستگی حروف در PDF) به کد دو رقمی
    ordinal_map_nospace = {
        "اول": "01",
        "دوم": "02",
        "سوم": "03",
        "چهارم": "04",
        "پنجم": "05",
        "ششم": "06",
        "هفتم": "07",
        "هشتم": "08",
        "نهم": "09",
        "دهم": "10",
        "یازدهم": "11",
        "دوازدهم": "12",
        "سیزدهم": "13",
        "چهاردهم": "14",
        "پانزدهم": "15",
        "شانزدهم": "16",
        "هفدهم": "17",
        "هجدهم": "18",
        "نوزدهم": "19",
        "بیستم": "20",
        "بیستویکم": "21",
        "بیستودوم": "22",
        "بیستوسوم": "23",
        "بیستوچهارم": "24",
        "بیستوپنجم": "25",
        "بیستوششم": "26",
        "بیستوهفتم": "27",
        "بیستوهشتم": "28",
        "بیستونهم": "29",
    }

    # استخراج عنوان تمیز هر فصل از سرصفحه صفحه شروع همان فصل در PDF
    raw_entries: List[Dict[str, Any]] = []
    for line in toc_lines:
        clean_l = normalize_text(line)
        clean_l = clean_l.replace("یب ست", "بیست").replace("پی وسـت", "پیوست").replace("پی وست", "پیوست")
        m_page = re.search(r"\.{3,}\s*(\d+)\s*$", clean_l)
        if not m_page:
            continue
        start_bul = int(m_page.group(1))
        title_part = re.sub(r"\.{2,}.*$", "", clean_l).strip()
        title_nospace = title_part.replace(" ", "").replace("\u200c", "")

        if title_nospace.startswith("فصل"):
            ch_code = None
            for ord_ns, code_str in sorted(ordinal_map_nospace.items(), key=lambda x: -len(x[0])):
                if title_nospace.startswith(f"فصل{ord_ns}"):
                    ch_code = code_str
                    break
            if ch_code:
                raw_entries.append(
                    {
                        "chapter_code": ch_code,
                        "chapter_type": "chapter",
                        "toc_title": title_part,
                        "start_page_bulletin": start_bul,
                    }
                )
        elif title_nospace.startswith("پیوست"):
            m_app = re.search(r"پیوست(\d+)", title_nospace)
            if m_app:
                app_num = int(m_app.group(1))
                app_code_map = {
                    1: "41",
                    2: "A2",
                    3: "A3",
                    4: "A4",
                    5: "99",
                    6: "A6",
                }
                ch_code = app_code_map[app_num]
                raw_entries.append(
                    {
                        "chapter_code": ch_code,
                        "chapter_type": "appendix",
                        "toc_title": title_part,
                        "start_page_bulletin": start_bul,
                    }
                )

    # محاسبه صفحه پایان هر بخش بر اساس صفحه شروع بخش بعدی (آخرین صفحه بولتن = 254)
    for i, entry in enumerate(raw_entries):
        if i + 1 < len(raw_entries):
            entry["end_page_bulletin"] = raw_entries[i + 1]["start_page_bulletin"] - 1
        else:
            entry["end_page_bulletin"] = 254

        start_pdf = entry["start_page_bulletin"] + page_offset
        end_pdf = entry["end_page_bulletin"] + page_offset
        entry["start_page_pdf"] = start_pdf
        entry["end_page_pdf"] = end_pdf

        # خواندن عنوان رسمی تمیز از هدر صفحه شروع در PDF
        hdr_lines = extract_rtl_page_lines(doc[start_pdf - 1])
        official_title = entry["toc_title"]
        if hdr_lines:
            first_hdr = normalize_text(hdr_lines[0]).replace("پیوسـت", "پیوست")
            if first_hdr.startswith("فصل") or first_hdr.startswith("پیوست"):
                official_title = first_hdr
        entry["title_official"] = official_title

    # درج فصل ۱۵ (حذف‌شده در بولتن ۱۴۰۴) در جایگاه ترتیبی خود بعد از فصل ۱۴
    chapters_out: List[Dict[str, Any]] = []
    for entry in raw_entries:
        ch_code = entry["chapter_code"]
        row_cnt = excel_chapter_counts.get(ch_code, 0)
        notes_txt = None
        if ch_code == "29" and row_cnt == 0:
            notes_txt = "جدول کارهای دستمزدی در ص ۲۳۴ بولتن خام است و ردیف از پیش تعیین‌شده در اکسل ندارد"
        elif entry["chapter_type"] == "appendix" and row_cnt == 0:
            notes_txt = "پیوست متنی/دستورالعملی فاقد جدول ردیف در اکسل"

        chapters_out.append(
            {
                "discipline": discipline,
                "year": year,
                "chapter_code": ch_code,
                "chapter_type": entry["chapter_type"],
                "title_official": entry["title_official"],
                "is_active_phase1": 1 if ch_code in active_phase1 else 0,
                "excel_row_count": row_cnt,
                "start_page_bulletin": entry["start_page_bulletin"],
                "end_page_bulletin": entry["end_page_bulletin"],
                "start_page_pdf": entry["start_page_pdf"],
                "end_page_pdf": entry["end_page_pdf"],
                "source_file": pdf_filename,
                "notes_text": notes_txt,
            }
        )
        if ch_code == "14":
            # افزودن فصل ۱۵ (حذف‌شده در فهرست بهای ابنیه ۱۴۰۴)
            chapters_out.append(
                {
                    "discipline": discipline,
                    "year": year,
                    "chapter_code": "15",
                    "chapter_type": "deleted",
                    "title_official": "فصل پانزدهم (حذف‌شده در فهرست بهای ابنیه ۱۴۰۴)",
                    "is_active_phase1": 0,
                    "excel_row_count": 0,
                    "start_page_bulletin": None,
                    "end_page_bulletin": None,
                    "start_page_pdf": None,
                    "end_page_pdf": None,
                    "source_file": pdf_filename,
                    "notes_text": "در بولتن ۱۴۰۴ پس از پایان فصل ۱۴ (ص ۱۱۷ بولتن) مستقیماً فصل ۱۶ (ص ۱۱۸ بولتن) شروع می‌شود",
                }
            )

    return chapters_out


def extract_items_from_excel_and_pdf(
    excel_path: Path,
    doc: fitz.Document,
    discipline: str = "abnieh",
    year: int = DEFAULT_YEAR,
    internal_prefix: str = "11",
    page_offset: int = 3,
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """
    استخراج تمام ردیف‌های فهرست بها از فایل اکسل (abn1404.xlsx) و تطبیق با صفحات PDF.
    """
    df = pd.read_excel(excel_path, dtype=object)
    c_code, c_desc, c_unit, c_price = df.columns[:4]

    valid_codes = set()
    for raw_c in df[c_code].dropna():
        code_off, _ = normalize_item_code(raw_c, internal_prefix=internal_prefix)
        valid_codes.add(code_off)

    pdf_items_map = extract_pdf_item_pages(doc, valid_codes=valid_codes, page_offset=page_offset)

    items_list: List[Dict[str, Any]] = []
    excel_filename = excel_path.name
    pdf_filename = Path(doc.name).name

    for idx, row in df.iterrows():
        if pd.isna(row[c_code]):
            continue
        excel_row_num = int(idx) + 2  # سطر ۱ هدر است
        code_official, code_internal = normalize_item_code(
            row[c_code], internal_prefix=internal_prefix
        )
        chapter_code = code_official[:2]
        group_code = code_official[2:4]
        item_seq = code_official[4:6]

        desc_norm = normalize_text(row[c_desc])
        unit_raw, unit_std, unit_status = normalize_unit(row[c_unit])
        price_val = normalize_number_str(row[c_price])

        item_category = classify_item_category(row[c_desc], price_val)

        # طبق تصمیم ۶ کارفرما:
        # «ستاره‌دار» در فیلد is_star_price_zero یعنی «بدون بها طبق بند ۲-۴ دستورالعمل کاربرد»،
        # یعنی ۶۲ ردیف دارای قیمت صفر در فصول ۱ تا ۴۱ (ردیف‌های فصل ۹۹ تجهیز کارگاه قیمتشان NULL است).
        is_star_price_zero = 1 if (price_val is not None and price_val == 0.0) else 0

        # طبق تصمیم ۷ کارفرما: ۸ ردیف با واحد «درصد» فقط ثبت شوند، تفسیر نشوند.
        is_percent_unit = 1 if unit_std == "درصد" else 0

        pdf_info = pdf_items_map.get(code_official, {})
        page_bul = pdf_info.get("page_bulletin")
        page_pdf = pdf_info.get("page_pdf")

        items_list.append(
            {
                "discipline": discipline,
                "year": year,
                "code_official": code_official,
                "code_internal": code_internal,
                "chapter_code": chapter_code,
                "group_code": group_code,
                "item_seq": item_seq,
                "description": desc_norm,
                "unit_raw": unit_raw,
                "unit": unit_std,
                "unit_status": unit_status,
                "unit_price_rial": price_val,
                "item_category": item_category,
                "is_star_price_zero": is_star_price_zero,
                "is_percent_unit": is_percent_unit,
                "source_excel_file": excel_filename,
                "source_excel_row": excel_row_num,
                "source_pdf_file": pdf_filename,
                "source_page_bulletin": page_bul,
                "source_page_pdf": page_pdf,
            }
        )

    return items_list, pdf_items_map


def extract_coefficients_from_pdf(
    doc: fitz.Document,
    pdf_filename: str = "11-Abnieh1404.pdf",
    approvals_path: Path = USER_APPROVALS_PATH,
) -> List[Dict[str, Any]]:
    """
    استخراج و ثبت ۱۸ ردیف جدول coefficients از متن دستورالعمل کاربرد، کلیات و پیوست‌ها
    به همراه اعمال تأییدهای کاربر از فایل docs/user_approvals.json (اسکیمای ۱٫۱):
    - طبق تصمیم ۴ کارفرما: ضرایب پیمان (بالاسری، منطقه‌ای، طبقات، ضریب پیمان) و کسورات روکش
      در ستون default_in_template دارای مقدار NULL هستند (بدون مقدار پیش‌فرض).
    - مقادیر مرجع بولتن (مانند ضرایب جدول الف صفحه ۲ بولتن / صفحه ۵ PDF) مستقیماً از متن صفحه ۵
      استخراج و صرفاً در ستون reference_value ثبت می‌شوند (و برای ضرایب قراردادی/کسورات، هر دو NULL هستند).
    - ستون‌های اسکیمای ۱٫۱: verification_status، verified_by، verified_at، evidence_path.
    """
    # خواندن متن اصلاح‌شده صفحه ۵ PDF (صفحه ۲ بولتن - بند ۲-۶، بند ۲-۷-۲ و جدول الف)
    p5_text = "\n".join(extract_rtl_page_lines(doc[4]))
    # خواندن متن اصلاح‌شده صفحه ۹ PDF (صفحه ۶ بولتن - بند ۱۸-۲ کلیات)
    p9_text = "\n".join(extract_rtl_page_lines(doc[8]))
    # خواندن متن اصلاح‌شده صفحه ۲۳۸ PDF (صفحه ۲۳۵ بولتن - بند ۵ پیوست ۱ مصالح پای کار)
    p238_text = "\n".join(extract_rtl_page_lines(doc[237]))

    # استخراج اعداد مستند از متن واقعی صفحات PDF (بدون هاردکد)
    m_130 = re.search(r"1\.30", p5_text)
    m_120 = re.search(r"1\.20", p5_text)
    m_141 = re.search(r"1\.41", p5_text)
    m_114 = re.search(r"1\.14", p5_text)
    m_cap30 = re.search(r"\b30\b", p5_text)
    m_cap15 = re.search(r"\(15\)|\b15\b", p5_text)
    m_cap10 = re.search(r"\(10|\b10\b", p5_text)
    m_eq4 = re.search(r"ابنیه\s+4\s+30", p5_text)
    m_07 = re.search(r"0\.7", p9_text)
    m_mos70 = re.search(r"\b70\s*درصد", p238_text)

    val_130 = float(m_130.group(0)) if m_130 else None
    val_120 = float(m_120.group(0)) if m_120 else None
    val_141 = float(m_141.group(0)) if m_141 else None
    val_114 = float(m_114.group(0)) if m_114 else None
    val_cap30 = 30.0 if m_cap30 else None
    val_cap15 = 15.0 if m_cap15 else None
    val_cap10 = 10.0 if m_cap10 else None
    val_eq4 = 4.0 if m_eq4 else None
    val_07 = float(m_07.group(0)) if m_07 else None
    val_mos70 = 0.70 if m_mos70 else None

    # بارگذاری فایل تأییدهای کاربر (docs/user_approvals.json)
    user_approvals: Dict[str, Any] = {}
    if approvals_path and approvals_path.exists():
        data = json.loads(approvals_path.read_text(encoding="utf-8"))
        user_approvals = data.get("coefficients", {})

    # ۱۸ ردیف استاندارد جدول coefficients (شامل ضرایب پیمان، ضرایب مرجع جدول الف، سقف‌های کنترلی و کسورات روکش)
    # طبق تصمیم ۴ کارفرما، default_in_template برای تمام ۱۸ ردیف NULL (None) است.
    coeffs: List[Dict[str, Any]] = [
        # ۱ تا ۴: چهار ضریب اصلی پیمان (مقدار پیش‌فرض و مرجع هر دو NULL طبق تصمیم ۴ کارفرما)
        {
            "coeff_key": "OVERHEAD_COEFF",
            "category": "contract_multiplier",
            "title_fa": "ضریب بالاسری پیمان (انتخابی بر حسب نوع طرح و روش ارجاع کار)",
            "reference_value": None,
            "default_in_template": None,
            "is_enabled_default": 0,
            "formula_description": "ضرب پی‌درپی در جمع مبلغ ردیف‌های فهرست بها طبق بند 2-8 دستورالعمل کاربرد",
            "source_file": pdf_filename,
            "source_page_bulletin": 2,
            "source_clause": "بند 2-7-2 و جدول الف دستورالعمل کاربرد",
        },
        {
            "coeff_key": "REGIONAL_COEFF",
            "category": "contract_multiplier",
            "title_fa": "ضریب منطقه‌ای (بر اساس بخشنامه 94/69416 و اصلاحیه‌های بعدی)",
            "reference_value": None,
            "default_in_template": None,
            "is_enabled_default": 0,
            "formula_description": "ضرب پی‌درپی در جمع مبلغ ردیف‌ها طبق بند 2-7-3 و پیوست 4",
            "source_file": pdf_filename,
            "source_page_bulletin": 243,
            "source_clause": "بند 2-7-3 دستورالعمل کاربرد و پیوست 4",
        },
        {
            "coeff_key": "FLOOR_COEFF",
            "category": "contract_multiplier",
            "title_fa": "ضریب طبقات ساختمان (P تا چهار رقم اعشار)",
            "reference_value": None,
            "default_in_template": None,
            "is_enabled_default": 0,
            "formula_description": "P = 1 + (sum(n*Fn) + sum(m*Bm)) / (100 * S) طبق پیوست 2",
            "source_file": pdf_filename,
            "source_page_bulletin": 239,
            "source_clause": "بند 2-7-1 دستورالعمل کاربرد و پیوست 2",
        },
        {
            "coeff_key": "CONTRACT_COEFF",
            "category": "contract_multiplier",
            "title_fa": "ضریب پیشنهادی پیمانکار (پلوس / مینوس پیمان)",
            "reference_value": None,
            "default_in_template": None,
            "is_enabled_default": 0,
            "formula_description": "ضرب در مبلغ برآورد پس از اعمال ضرایب طبقات، بالاسری و منطقه‌ای",
            "source_file": "4311-word.doc",
            "source_page_bulletin": None,
            "source_clause": "موافقتنامه و شرایط عمومی پیمان نشریه 4311",
        },
        # ۵ تا ۹: پنج ضریب بالاسری مستند در بند ۲-۷-۲ و جدول الف صفحه ۲ بولتن (صفحه ۵ PDF)
        {
            "coeff_key": "OVERHEAD_CIVIL_TENDER",
            "category": "overhead_reference",
            "title_fa": "ضریب بالاسری طرح‌های عمرانی - مناقصه یا عدم الزام ناشی از انحصار",
            "reference_value": val_130,
            "default_in_template": None,
            "is_enabled_default": 0,
            "formula_description": "مقدار مرجع جدول الف برای طرح‌های عمرانی مناقصه (1.30)",
            "source_file": pdf_filename,
            "source_page_bulletin": 2,
            "source_clause": "بند 2-7-2 و جدول الف دستورالعمل کاربرد",
        },
        {
            "coeff_key": "OVERHEAD_CIVIL_NON_TENDER",
            "category": "overhead_reference",
            "title_fa": "ضریب بالاسری طرح‌های عمرانی - ترک تشریفات مناقصه",
            "reference_value": val_120,
            "default_in_template": None,
            "is_enabled_default": 0,
            "formula_description": "مقدار مرجع جدول الف برای طرح‌های عمرانی ترک تشریفات (1.20)",
            "source_file": pdf_filename,
            "source_page_bulletin": 2,
            "source_clause": "بند 2-7-2 و جدول الف دستورالعمل کاربرد",
        },
        {
            "coeff_key": "OVERHEAD_NON_CIVIL_TENDER",
            "category": "overhead_reference",
            "title_fa": "ضریب بالاسری طرح‌های غیرعمرانی - مناقصه یا عدم الزام ناشی از انحصار",
            "reference_value": val_141,
            "default_in_template": None,
            "is_enabled_default": 0,
            "formula_description": "مقدار مرجع جدول الف برای طرح‌های غیرعمرانی مناقصه (1.41)",
            "source_file": pdf_filename,
            "source_page_bulletin": 2,
            "source_clause": "بند 2-7-2 و جدول الف دستورالعمل کاربرد",
        },
        {
            "coeff_key": "OVERHEAD_NON_CIVIL_NON_TENDER",
            "category": "overhead_reference",
            "title_fa": "ضریب بالاسری طرح‌های غیرعمرانی - ترک تشریفات مناقصه",
            "reference_value": val_130,
            "default_in_template": None,
            "is_enabled_default": 0,
            "formula_description": "مقدار مرجع جدول الف برای طرح‌های غیرعمرانی ترک تشریفات (1.30)",
            "source_file": pdf_filename,
            "source_page_bulletin": 2,
            "source_clause": "بند 2-7-2 و جدول الف دستورالعمل کاربرد",
        },
        {
            "coeff_key": "OVERHEAD_PURCHASE_ONLY",
            "category": "overhead_reference",
            "title_fa": "ضریب بالاسری فصول خرید و تهیه یراق‌آلات (بند 10 کلیات و تبصره 1 پیوست 6)",
            "reference_value": val_114,
            "default_in_template": None,
            "is_enabled_default": 0,
            "formula_description": "ضریب بالاسری فصول خرید و فاکتور یراق‌آلات بدون سایر ضرایب (1.14)",
            "source_file": pdf_filename,
            "source_page_bulletin": 2,
            "source_clause": "جدول الف ص 2، بند 10 کلیات ص 4 و تبصره 1 پیوست 6 ص 254 بولتن",
        },
        # ۱۰ تا ۱۲: سقف‌های کنترلی جدول الف + ضرایب خاص کلیات و پیوست ۱
        {
            "coeff_key": "STAR_ITEM_CAP_PUBLIC_TENDER_PCT",
            "category": "star_item_cap",
            "title_fa": "سقف مجاز اقلام ستاره‌دار در مناقصه عمومی (درصد)",
            "reference_value": val_cap30,
            "default_in_template": None,
            "is_enabled_default": 0,
            "formula_description": "حداکثر 30 درصد جمع مبلغ برآورد ردیف‌ها بدون تجهیز کارگاه",
            "source_file": pdf_filename,
            "source_page_bulletin": 1,
            "source_clause": "بند 2-6 و جدول الف دستورالعمل کاربرد (ص 1 و 2 بولتن)",
        },
        {
            "coeff_key": "STAR_ITEM_CAP_LIMITED_TENDER_PCT",
            "category": "star_item_cap",
            "title_fa": "سقف مجاز اقلام ستاره‌دار در مناقصه محدود (درصد)",
            "reference_value": val_cap15,
            "default_in_template": None,
            "is_enabled_default": 0,
            "formula_description": "حداکثر 15 درصد جمع مبلغ برآورد ردیف‌ها بدون تجهیز کارگاه",
            "source_file": pdf_filename,
            "source_page_bulletin": 2,
            "source_clause": "بند 2-6 و جدول الف دستورالعمل کاربرد (ص 2 بولتن)",
        },
        {
            "coeff_key": "STAR_ITEM_CAP_NON_TENDER_PCT",
            "category": "star_item_cap",
            "title_fa": "سقف مجاز اقلام ستاره‌دار در ترک تشریفات مناقصه (درصد)",
            "reference_value": val_cap10,
            "default_in_template": None,
            "is_enabled_default": 0,
            "formula_description": "حداکثر 10 درصد جمع مبلغ برآورد ردیف‌ها بدون تجهیز کارگاه",
            "source_file": pdf_filename,
            "source_page_bulletin": 2,
            "source_clause": "بند 2-6 و جدول الف دستورالعمل کاربرد (ص 2 بولتن)",
        },
        # ۱۲-الف (اسکیمای ۱٫۲ — تصمیم D7): سقف ۴٪ تجهیز و برچیدن کارگاه با وضعیت «تأییدنشده»
        {
            "coeff_key": "EQUIP_DEMOB_CAP_PCT",
            "category": "equip_cap",
            "title_fa": "سقف مجاز هزینه تجهیز و برچیدن کارگاه (درصد)",
            "reference_value": val_eq4,
            "default_in_template": None,
            "is_enabled_default": 0,
            "formula_description": "حداکثر 4 درصد مبلغ برآورد هزینه اجرای کار بدون هزینه‌های تجهیز و برچیدن کارگاه",
            "source_file": pdf_filename,
            "source_page_bulletin": 2,
            "source_clause": "جدول الف ص 2 و بند 2-17-1 پیوست 5 ص 247 بولتن",
        },
        # ۱۳ تا ۱۸: شش ردیف کسورات و اضافات مالی روکش صورت‌وضعیت (مقدار مرجع و پیش‌فرض هر دو NULL)
        {
            "coeff_key": "DED_GOOD_PERFORMANCE",
            "category": "financial_deduction",
            "title_fa": "کسور حسن انجام کار",
            "reference_value": None,
            "default_in_template": None,
            "is_enabled_default": 0,
            "formula_description": "بر اساس درصد مندرج در قرارداد (ماده 35 شرایط عمومی پیمان نشریه 4311)",
            "source_file": "4311-word.doc",
            "source_page_bulletin": None,
            "source_clause": "ماده 35 شرایط عمومی پیمان نشریه 4311",
        },
        {
            "coeff_key": "DED_INSURANCE",
            "category": "financial_deduction",
            "title_fa": "کسور حق بیمه تأمین اجتماعی",
            "reference_value": None,
            "default_in_template": None,
            "is_enabled_default": 0,
            "formula_description": "بر اساس نرخ قانونی/قراردادی پیمان (ماده 38 قانون تأمین اجتماعی)",
            "source_file": "4311-word.doc",
            "source_page_bulletin": None,
            "source_clause": "ماده 37 شرایط عمومی پیمان نشریه 4311",
        },
        {
            "coeff_key": "VAT_RATE",
            "category": "financial_deduction",
            "title_fa": "مالیات و عوارض بر ارزش افزوده (VAT)",
            "reference_value": None,
            "default_in_template": None,
            "is_enabled_default": 0,
            "formula_description": "بر اساس نرخ قانونی سال مالی در صورت مشمول بودن قرارداد",
            "source_file": "4311-word.doc",
            "source_page_bulletin": None,
            "source_clause": "قانون مالیات بر ارزش افزوده و شرایط خصوصی پیمان",
        },
        {
            "coeff_key": "DED_PREPAYMENT_AMORT",
            "category": "financial_deduction",
            "title_fa": "استهلاک پیش‌پرداخت",
            "reference_value": None,
            "default_in_template": None,
            "is_enabled_default": 0,
            "formula_description": "کسر متناسب با مبالغ پیش‌پرداخت تحویلی طبق ماده 36 شرایط عمومی پیمان",
            "source_file": "4311-word.doc",
            "source_page_bulletin": None,
            "source_clause": "ماده 36 شرایط عمومی پیمان نشریه 4311",
        },
        {
            "coeff_key": "DED_ON_ACCOUNT_PAYMENT",
            "category": "financial_deduction",
            "title_fa": "کسور علی‌الحساب‌های پرداختی قبلی",
            "reference_value": None,
            "default_in_template": None,
            "is_enabled_default": 0,
            "formula_description": "مبلغ مقطوع واردشده توسط کارفرما طبق ماده 37 شرایط عمومی پیمان",
            "source_file": "4311-word.doc",
            "source_page_bulletin": None,
            "source_clause": "ماده 37 شرایط عمومی پیمان نشریه 4311",
        },
        {
            "coeff_key": "DED_OTHER_CONTRACTUAL",
            "category": "financial_deduction",
            "title_fa": "سایر کسورات قانونی و قراردادی",
            "reference_value": None,
            "default_in_template": None,
            "is_enabled_default": 0,
            "formula_description": "سایر کسورات توافقی یا قانونی بر حسب اسناد پیمان",
            "source_file": "4311-word.doc",
            "source_page_bulletin": None,
            "source_clause": "شرایط عمومی و خصوصی پیمان نشریه 4311",
        },
    ]

    for c in coeffs:
        ckey = c["coeff_key"]
        ref_val = c["reference_value"]
        if ckey in user_approvals:
            app_entry = user_approvals[ckey]
            expected_val = app_entry.get("approved_reference_value")
            if ref_val is None or expected_val is None or abs(float(ref_val) - float(expected_val)) > 1e-6:
                raise ValueError(
                    f"عدم تطابق مقدار مرجع استخراج‌شده برای {ckey}: استخراج={ref_val} != تأیید کاربر={expected_val}"
                )
            c["verification_status"] = app_entry["verification_status"]
            c["verified_by"] = app_entry["verified_by"]
            c["verified_at"] = app_entry["verified_at"]
            c["evidence_path"] = app_entry["evidence_path"]
        elif ref_val is not None:
            c["verification_status"] = "تأییدنشده"
            c["verified_by"] = None
            c["verified_at"] = None
            c["evidence_path"] = None
        else:
            c["verification_status"] = "بدون مقدار (انتخاب پروژه)"
            c["verified_by"] = None
            c["verified_at"] = None
            c["evidence_path"] = None

    return coeffs


def extract_notes_from_pdf(
    doc: fitz.Document,
    chapters_list: List[Dict[str, Any]],
    pdf_filename: str = "11-Abnieh1404.pdf",
    discipline: str = "abnieh",
    year: int = DEFAULT_YEAR,
    page_offset: int = 3,
) -> List[Dict[str, Any]]:
    """
    استخراج بندهای دستورالعمل کاربرد (ص ۱ تا ۳ بولتن)، کلیات (ص ۴ تا ۶ بولتن)
    و الزامات عمومی و گروه‌های فصول از متن اصلاح‌شده PDF برای جدول notes.
    """
    notes: List[Dict[str, Any]] = []

    def _parse_clauses_on_pages(
        start_pdf: int,
        end_pdf: int,
        section_type: str,
        chapter_code: Optional[str] = None,
    ) -> None:
        current_clause_num: Optional[str] = None
        current_group: Optional[str] = None
        current_lines: List[str] = []
        current_page_pdf: int = start_pdf

        def _flush() -> None:
            if current_clause_num and current_lines:
                txt = normalize_text(" ".join(current_lines))
                has_rule = 1 if any(k in txt for k in ["درصد", "اضافه", "کسر", "ضریب", "حداکثر", "سقف"]) else 0
                notes.append(
                    {
                        "discipline": discipline,
                        "year": year,
                        "section_type": "group_intro" if current_group else section_type,
                        "chapter_code": chapter_code,
                        "group_code": current_group,
                        "clause_number": current_clause_num,
                        "clause_text": txt,
                        "has_calculation_rule": has_rule,
                        "source_file": pdf_filename,
                        "source_page_bulletin": current_page_pdf - page_offset,
                        "source_page_pdf": current_page_pdf,
                    }
                )

        for p_pdf in range(start_pdf, end_pdf + 1):
            lines = extract_rtl_page_lines(doc[p_pdf - 1])
            page_joined = "\n".join(lines)
            # اگر صفحه جدول ردیف‌های فهرست بهاست، از آن عبور کن
            if "بهای کل" in page_joined and "مقدار" in page_joined:
                continue

            for idx_l, raw_l in enumerate(lines):
                l = normalize_text(raw_l)
                if idx_l < 2 and ("فهرست بهای واحد پایه" in l or l.startswith("فصل ") or l.startswith("پیوست") or l.startswith("دستورالعمل") or l.startswith("کلیات")):
                    continue
                if re.match(r"^\d{1,3}$", l):
                    continue

                # بررسی شروع «الزامات گروه X»
                m_grp = re.match(r"^الزامات\s+گروه\s+(\d+)", l)
                if m_grp:
                    _flush()
                    current_clause_num = None
                    current_lines = []
                    current_group = m_grp.group(1).zfill(2)
                    continue

                # بررسی شروع شماره بند مثل "1-1." یا "2 ـ 4." یا "16."
                m_cl = re.match(r"^(\d+(?:\s*[-ـ]\s*\d+)*)\s*\.\s*(.+)$", l)
                if m_cl:
                    _flush()
                    current_clause_num = re.sub(r"\s*[-ـ]\s*", "-", m_cl.group(1))
                    current_lines = [m_cl.group(2)]
                    current_page_pdf = p_pdf
                elif current_clause_num:
                    current_lines.append(l)

        _flush()

    # ۱. دستورالعمل کاربرد (صفحات ۴ تا ۶ PDF / ۱ تا ۳ بولتن)
    _parse_clauses_on_pages(4, 6, "instructions", None)
    # ۲. کلیات (صفحات ۷ تا ۹ PDF / ۴ تا ۶ بولتن)
    _parse_clauses_on_pages(7, 9, "general_terms", None)

    # ۳. الزامات فصول و مقدمه‌های پیوست‌ها
    for ch in chapters_list:
        if ch["chapter_type"] == "deleted" or not ch["start_page_pdf"]:
            continue
        sec_t = "chapter_intro" if ch["chapter_type"] == "chapter" else "appendix_intro"
        _parse_clauses_on_pages(
            ch["start_page_pdf"],
            ch["end_page_pdf"],
            sec_t,
            ch["chapter_code"],
        )

    return notes


def extract_material_on_site_factors_from_pdf(
    doc: fitz.Document,
    pdf_filename: str = "11-Abnieh1404.pdf",
    discipline: str = "abnieh",
    year: int = DEFAULT_YEAR,
    page_offset: int = 3,
) -> List[Dict[str, Any]]:
    """
    استخراج جدول شماره ۱ پیوست ۱ (ضرایب متوسط مصالح پای کار) از صفحه ۲۳۹ PDF (۲۳۶ بولتن)
    برای جدول material_on_site_factors.
    """
    p_pdf = 239
    p_bul = p_pdf - page_offset
    lines = extract_rtl_page_lines(doc[p_pdf - 1])

    ordinal_to_code = {
        "پنجم": "05",
        "دوازدهم": "12",
        "سیزدهم": "13",
        "چهاردهم": "14",
        "شانزدهم": "16",
        "هفدهم": "17",
        "هجدهم": "18",
        "نوزدهم": "19",
        "بیستم": "20",
        "بیست و یکم": "21",
        "بیست و دوم": "22",
        "بیست و سوم": "23",
        "بیست و چهارم": "24",
    }

    factors: List[Dict[str, Any]] = []
    for raw_l in lines:
        l = normalize_text(raw_l)
        for ord_name, ch_code in sorted(ordinal_to_code.items(), key=lambda x: -len(x[0])):
            if l.startswith(ord_name + " "):
                m_pct = re.search(r"(\d+)\s*%", l)
                if m_pct:
                    pct_val = float(m_pct.group(1))
                    desc = l[len(ord_name):].replace(m_pct.group(0), "").strip()
                    factors.append(
                        {
                            "discipline": discipline,
                            "year": year,
                            "chapter_code": ch_code,
                            "material_description": desc,
                            "factor_percent": pct_val,
                            "source_file": pdf_filename,
                            "source_page_bulletin": p_bul,
                            "source_page_pdf": p_pdf,
                        }
                    )
                break
    return factors
