#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ماژول استخراج داده‌های مرحله ۳ فهرست بها (stage3_extractor.py).
منطبق با تصمیم‌های D1 تا D7 کارفرما در گام ج (مرحله ۳):
- D1: دامنه notes و rules فقط برش مصوب (دستورالعمل کاربرد، کلیات، فصول 01, 02, 03, 05, 06, 07, 08, 28 و پیوست‌های 2, 3, 4)؛
      دامنه item_dependencies تمام ۳۲۴ ردیف additional/deduction کل فصول.
- D2: بدون کد جعلی؛ ثبت ستون‌های target_kind، target_text، resolution_status، source_clause_ref در item_dependencies؛
      تولید docs/unresolved_dependencies.md برای ۱۱۵ ردیف بدون کد ۶ رقمی صریح؛ ثبت بندهای استثنا با rule_type='exclusion'.
- D3: ثبت ردیف 030601 در برابر بند 6-1 و 030603 با is_ambiguous=1 و status='pending_user_decision'.
- D4: شرط پله‌ای عمق/طول (020201، 020304، 030603، 031204) فقط به صورت داده ساخت‌یافته در condition_json.
- D5: بندهای 11-1 و 11-2 فصل ۶ هر کدام یک rule مستقل با متن اصلی.
- D6: اقلام درصدی/ضریبی داخل بندها با rule_type='clause_derived_item' بدون تغییر در ۱۵۶۵ ردیف جدول items.
- D7: استخراج ۴ جدول مرجع جدید (mortar_table، overhead_table، floor_factor_params، region_factors).
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import pymupdf as fitz

from src.config import (
    DEFAULT_YEAR,
    STAGE3_SLICE_CHAPTERS,
    STAGE3_SLICE_APPENDICES,
    UNRESOLVED_DEPS_PATH,
    USER_APPROVALS_PATH,
)
from src.pdf_fix import extract_rtl_page_lines
from src.normalize import (
    normalize_text,
    normalize_clause_text,
    digits_to_latin,
    unify_persian_chars,
)


def extract_notes_stage3_slice(
    doc: fitz.Document,
    chapters_list: List[Dict[str, Any]],
    pdf_filename: str = "11-Abnieh1404.pdf",
    discipline: str = "abnieh",
    year: int = DEFAULT_YEAR,
    page_offset: int = 3,
    include_out_of_slice: bool = True,
) -> List[Dict[str, Any]]:
    """
    استخراج بندهای جدول `notes` طبق تصمیم D1 کارفرما:
    - دامنه برش مصوب D1: دستورالعمل کاربرد (ص ۱ تا ۳ بولتن / ۴ تا ۶ PDF)، کلیات (ص ۴ تا ۶ بولتن / ۷ تا ۹ PDF)،
      فصول 01, 02, 03, 05, 06, 07, 08, 28 و پیوست‌های A2 (پیوست ۲)، A3 (پیوست ۳)، A4 (پیوست ۴)
    - طبق دستور صریح D1 («توجه: notes فعلی (۱۱۸۳) همه فصل‌ها را پوشش می‌دهد. حذفش نکن. گزارش کن که چند ردیفش در دامنه برش است و چند ردیف بیرون است»):
      تمام ۷۶۱ ردیف خارج از برش D1 نیز حفظ می‌شوند (با status='out_of_slice') و ۴۳۵ ردیف داخل برش D1
      (شامل ۴۳۳ بند فعال با status='active' و ۲ بند خالی منبع `9.` دستورالعمل کاربرد و `7.` کلیات با status='empty_in_source')
      مجموعاً ۱٬۱۹۶ رکورد را تشکیل می‌دهند.
    - شامل ستون `text_raw` (خروجی مستقیم `pdf_fix` بدون ویرایش واژگانی)، `text_normalized` (با اصلاح قاعده‌مند «ال/لا» و فاصله‌های شکسته)،
      `source_file`, `source_page_bulletin`, `source_page_pdf`, `clause_ref`.
    """
    notes: List[Dict[str, Any]] = []

    def _build_clause_ref(
        sec_type: str,
        ch_code: Optional[str],
        grp_code: Optional[str],
        cl_num: str,
        p_bul: int,
    ) -> str:
        if sec_type == "instructions":
            return f"دستورالعمل کاربرد - بند {cl_num} (ص {p_bul} بولتن)"
        if sec_type == "general_terms":
            return f"کلیات - بند {cl_num} (ص {p_bul} بولتن)"
        if sec_type == "appendix_intro":
            app_label = {"A2": "پیوست 2", "A3": "پیوست 3", "A4": "پیوست 4"}.get(
                ch_code or "", f"پیوست {ch_code}"
            )
            return f"{app_label} - بند {cl_num} (ص {p_bul} بولتن)"
        if grp_code:
            return f"فصل {ch_code} - الزامات گروه {grp_code} - بند {cl_num} (ص {p_bul} بولتن)"
        return f"فصل {ch_code} - الزامات عمومی - بند {cl_num} (ص {p_bul} بولتن)"

    def _parse_clauses_on_pages(
        start_pdf: int,
        end_pdf: int,
        section_type: str,
        chapter_code: Optional[str] = None,
    ) -> None:
        current_clause_num: Optional[str] = None
        current_group: Optional[str] = None
        current_raw_lines: List[str] = []
        current_page_pdf: int = start_pdf

        def _flush() -> None:
            if current_clause_num is None:
                return
            raw_joined = " ".join(s.strip() for s in current_raw_lines if s.strip()).strip()
            norm_txt = normalize_clause_text(raw_joined) if raw_joined else ""
            status_val = "empty_in_source" if norm_txt == "" else "active"
            sec_actual = "group_intro" if current_group else section_type
            p_bul = current_page_pdf - page_offset
            cl_ref = _build_clause_ref(
                sec_actual, chapter_code, current_group, current_clause_num, p_bul
            )
            has_rule = (
                1
                if norm_txt
                and any(
                    k in norm_txt
                    for k in [
                        "درصد",
                        "اضافه",
                        "کسر",
                        "ضریب",
                        "حداکثر",
                        "سقف",
                        "تعلق نمی",
                        "منظور نمی",
                    ]
                )
                else 0
            )
            notes.append(
                {
                    "discipline": discipline,
                    "year": year,
                    "section_type": sec_actual,
                    "chapter_code": chapter_code,
                    "group_code": current_group,
                    "clause_number": current_clause_num,
                    "clause_ref": cl_ref,
                    "text_raw": raw_joined,
                    "text_normalized": norm_txt,
                    "clause_text": norm_txt,
                    "status": status_val,
                    "has_calculation_rule": has_rule,
                    "source_file": pdf_filename,
                    "source_page_bulletin": p_bul,
                    "source_page_pdf": current_page_pdf,
                }
            )

        for p_pdf in range(start_pdf, end_pdf + 1):
            lines = extract_rtl_page_lines(doc[p_pdf - 1])
            page_joined = "\n".join(lines)
            # عبور از صفحات جدول ردیف‌های فهرست بها
            if "بهای کل" in page_joined and "مقدار" in page_joined:
                continue

            for idx_l, raw_l in enumerate(lines):
                l_base = normalize_text(raw_l)
                # حذف سرصفحه و شماره صفحه پایین بولتن
                if idx_l < 3 and (
                    "فهرست بهای واحد پایه" in l_base
                    or l_base.startswith("فصل ")
                    or l_base.startswith("پیوست")
                    or l_base.startswith("دستورالعمل")
                    or l_base.startswith("کلیات")
                    or l_base in ("الزامات عمومی", "لزامات عمومی")
                ):
                    continue
                if l_base in ("الزامات عمومی", "لزامات عمومی"):
                    continue
                if re.match(r"^\d{1,3}$", l_base):
                    continue

                # تشخیص شروع «الزامات گروه X» (شامل حالت شکسته «الزامات گرو ه X»)
                m_grp = re.match(r"^الزامات\s+گرو\s*ه\s+(\d+)", l_base)
                if m_grp:
                    _flush()
                    current_clause_num = None
                    current_raw_lines = []
                    current_group = m_grp.group(1).zfill(2)
                    continue

                # تشخیص بندهای خالی منبع (مانند "9 ." در ص ۳ بولتن و "7 ." در ص ۴ بولتن)
                m_empty = re.match(r"^(\d+(?:\s*[-ـ]\s*\d+)*)\s*\.\s*$", l_base)
                if m_empty:
                    _flush()
                    current_clause_num = re.sub(r"\s*[-ـ]\s*", "-", m_empty.group(1))
                    current_raw_lines = []
                    current_page_pdf = p_pdf
                    _flush()
                    current_clause_num = None
                    current_raw_lines = []
                    continue

                # تشخیص تبصره‌ها و توضیحات در پیوست‌های ۲ و ۳ (مانند «تبصره 1)» یا «توضیح 1)»)
                m_tab = re.match(r"^(تبصره|توضیح)\s*(\d+)\s*\)\s*(.+)$", l_base)
                if m_tab:
                    _flush()
                    current_clause_num = f"{m_tab.group(1)} {m_tab.group(2)}"
                    # در text_raw متن خام بدون پیشوند شماره تبصره ذخیره می‌شود
                    raw_body = re.sub(r"^(تبصره|توضیح)\s*\d+\s*\)\s*", "", raw_l.strip())
                    current_raw_lines = [raw_body]
                    current_page_pdf = p_pdf
                    continue

                # تشخیص شماره بند عددی استاندارد یا بند ۱ فصل ۵ («1 استفاده از قالب چوبی ... ..»)
                m_cl = re.match(
                    r"^(\d+(?:\s*[-ـ]\s*\d+)*)(?:\s*\.+|\s+(?=استفاده\s+از\s+قالب\s+چوبی|\s*نحوه\s+تعیین\s+هزینه\s+حمل\s+خاک|\s*ضرایب\s+باالسری))\s*(.+)$",
                    l_base,
                )
                if m_cl:
                    cand_num = re.sub(r"\s*[-ـ]\s*", "-", m_cl.group(1))
                    # جلوگیری از اشتباه گرفتن سطرهای جدول گروه‌ها (۰۱ تا ۱۲) یا اجزای فرمول ریاضی
                    is_group_table_row = (
                        "." not in raw_l
                        and "-" not in cand_num
                        and "ـ" not in m_cl.group(1)
                        and not l_base.startswith("1 استفاده از قالب چوبی")
                    )
                    if not is_group_table_row:
                        _flush()
                        current_clause_num = cand_num
                        raw_body = re.sub(
                            r"^\s*\d+(?:\s*[-ـ]\s*\d+)*\s*\.*\s*", "", raw_l.strip()
                        )
                        current_raw_lines = [raw_body]
                        current_page_pdf = p_pdf
                        continue

                if current_clause_num is not None:
                    current_raw_lines.append(raw_l.strip())

        _flush()

    # ۱. دستورالعمل کاربرد (ص ۱ تا ۳ بولتن / ۴ تا ۶ PDF)
    _parse_clauses_on_pages(4, 6, "instructions", None)
    # ۲. کلیات (ص ۴ تا ۶ بولتن / ۷ تا ۹ PDF)
    _parse_clauses_on_pages(7, 9, "general_terms", None)

    # ۳. فصول برش مصوب (01, 02, 03, 05, 06, 07, 08, 28) و پیوست‌های برش (A2, A3, A4)
    allowed_codes = set(STAGE3_SLICE_CHAPTERS) | set(STAGE3_SLICE_APPENDICES)
    for ch in chapters_list:
        ch_code = ch["chapter_code"]
        if ch_code not in allowed_codes or not ch["start_page_pdf"]:
            continue
        sec_t = "chapter_intro" if ch["chapter_type"] == "chapter" else "appendix_intro"
        _parse_clauses_on_pages(
            ch["start_page_pdf"],
            ch["end_page_pdf"],
            sec_t,
            ch_code,
        )

    # ۴. حفظ ۷۶۱ ردیف خارج از برش D1 از استخراج مرحله ۲ (طبق دستور صریح D1: «حذفش نکن»)
    if include_out_of_slice:
        from src.extractor import extract_notes_from_pdf

        all_stage2_notes = extract_notes_from_pdf(
            doc=doc,
            chapters_list=chapters_list,
            pdf_filename=pdf_filename,
            discipline=discipline,
            year=year,
            page_offset=page_offset,
        )
        for old_n in all_stage2_notes:
            if (
                old_n["section_type"] not in ("instructions", "general_terms")
                and old_n["chapter_code"] not in allowed_codes
            ):
                raw_t = old_n["clause_text"]
                norm_t = normalize_clause_text(raw_t)
                cl_ref = _build_clause_ref(
                    old_n["section_type"],
                    old_n["chapter_code"],
                    old_n["group_code"],
                    old_n["clause_number"],
                    old_n["source_page_bulletin"],
                )
                notes.append(
                    {
                        "discipline": old_n["discipline"],
                        "year": old_n["year"],
                        "section_type": old_n["section_type"],
                        "chapter_code": old_n["chapter_code"],
                        "group_code": old_n["group_code"],
                        "clause_number": old_n["clause_number"],
                        "clause_ref": cl_ref,
                        "text_raw": raw_t,
                        "text_normalized": norm_t,
                        "clause_text": norm_t,
                        "status": "out_of_slice",
                        "has_calculation_rule": old_n["has_calculation_rule"],
                        "source_file": old_n["source_file"],
                        "source_page_bulletin": old_n["source_page_bulletin"],
                        "source_page_pdf": old_n["source_page_pdf"],
                    }
                )

    return notes


def extract_group_titles_from_pdf(
    doc: fitz.Document, chapters_list: List[Dict[str, Any]]
) -> Dict[Tuple[str, str], str]:
    """
    استخراج جدول «شماره و شرح مختصر گروه‌های فصل» از صفحات مقدمه تمام فصول در PDF
    صرفاً برای نمایش گروه‌های کاندیدای قابل دیدن در فایل `docs/unresolved_dependencies.md`.
    """
    group_titles: Dict[Tuple[str, str], str] = {}
    for ch in chapters_list:
        if ch["chapter_type"] != "chapter" or not ch["start_page_pdf"]:
            continue
        ch_code = ch["chapter_code"]
        in_table = False
        last_g_code: Optional[str] = None
        for p_pdf in range(ch["start_page_pdf"], ch["end_page_pdf"] + 1):
            lines = extract_rtl_page_lines(doc[p_pdf - 1])
            page_txt = "\n".join(lines)
            if "بهای کل" in page_txt and "مقدار" in page_txt:
                break
            for raw_l in lines:
                l_clean = normalize_clause_text(raw_l)
                if "شماره" in l_clean and "گروه" in l_clean and "شرح" in l_clean:
                    in_table = True
                    last_g_code = None
                    continue
                if re.match(r"^الزامات\s+گروه", l_clean) or re.match(r"^\d{1,3}$", l_clean):
                    if re.match(r"^الزامات\s+گروه", l_clean):
                        in_table = False
                        last_g_code = None
                    continue
                if in_table:
                    m = re.match(r"^(\d{1,2})\s+(.+)$", l_clean)
                    if m and 1 <= int(m.group(1)) <= 35:
                        g_code = m.group(1).zfill(2)
                        group_titles[(ch_code, g_code)] = m.group(2).strip()
                        last_g_code = g_code
                    elif last_g_code and not re.match(r"^\d+", l_clean):
                        group_titles[(ch_code, last_g_code)] += " " + l_clean.strip()
    return group_titles


def extract_all_item_dependencies(
    items_list: List[Dict[str, Any]],
    group_titles: Dict[Tuple[str, str], str],
    unresolved_doc_path: Path = UNRESOLVED_DEPS_PATH,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    استخراج کامل جدول `item_dependencies` برای تمام ۳۲۴ ردیف `additional` و `deduction` کل فصول
    طبق تصمیم‌های D1 و D2 کارفرما:
    - بدون کد جعلی در `parent_code_official` (برای ردیف‌های بدون کد ۶ رقمی صریح، `parent_code_official = None`)
    - فقط کدهای ۶ رقمی که خود متن همان ردیف صریحاً نوشته حل‌شده (`resolved_explicit_code`) حساب می‌شوند:
      * تک‌کد (`target_kind = 'item'`): ۱۰۸ ردیف
      * بازه با «تا» (`target_kind = 'range'`): ۴۳ ردیف
      * فهرست با «و»/«،» یا ترکیبی (`target_kind = 'list'`): ۵۸ ردیف (۵۶ فهرست + ۲ ترکیبی)
      جمع حل‌شده با کد صریح = ۲۰۹ ردیف.
    - ۱۱۵ ردیف بدون کد ۶ رقمی صریح (۸۲ ردیف دارای «به ردیف(های) [موضوع]» + ۳۳ ردیف بدون «به ردیف»)
      با `target_kind = 'text_topic'`، `parent_code_official = None` و `resolution_status = 'unresolved_topic'` ثبت می‌شوند.
    - تولید فایل `docs/unresolved_dependencies.md` شامل کد، شرح کامل، `target_text` و گروه‌های کاندیدای قابل دیدن در همان فصل.
    """
    valid_codes = sorted({it["code_official"] for it in items_list})
    valid_codes_set = set(valid_codes)

    non_base_items = [
        it for it in items_list if it["item_category"] in ("additional", "deduction")
    ]
    non_base_items.sort(key=lambda x: x["code_official"])

    dep_rows: List[Dict[str, Any]] = []
    unresolved_records: List[Dict[str, Any]] = []

    counts_by_pattern = {
        "single_item": 0,
        "range": 0,
        "list": 0,
        "combo_range_list": 0,
        "topic_with_beradif": 0,
        "topic_without_beradif": 0,
    }

    for it in non_base_items:
        code = it["code_official"]
        ch_code = it["chapter_code"]
        cat = it["item_category"]
        desc = it["description"]
        p_bul = it["source_page_bulletin"]
        p_pdf = it["source_page_pdf"]
        pdf_file = it["source_pdf_file"] or "11-Abnieh1404.pdf"
        clause_ref = f"شرح ردیف {code} (ص {p_bul} بولتن / ص {p_pdf} PDF)"

        clean = re.sub(r"[\u200c\s]+", " ", desc).strip()
        codes_in_desc = re.findall(r"(?<!\d)\d{6}(?!\d)", clean)

        if codes_in_desc:
            has_ta = bool(re.search(r"(?<!\d)\d{6}\s*تا\s*\d{6}(?!\d)", clean))
            if len(codes_in_desc) == 1:
                t_kind = "item"
                counts_by_pattern["single_item"] += 1
            elif has_ta and len(codes_in_desc) == 2:
                t_kind = "range"
                counts_by_pattern["range"] += 1
            elif has_ta and len(codes_in_desc) > 2:
                t_kind = "list"
                counts_by_pattern["combo_range_list"] += 1
            else:
                t_kind = "list"
                counts_by_pattern["list"] += 1

            # استخراج عبارت دقیق ارجاع از متن ردیف
            m_phrase = re.search(
                r"((?:نسبت\s+)?به\s*ردیف(?:\s*های)?\s+[^،.]+(?:\s*،\s*\d{6}|\s+و\s+\d{6}(?:\s*تا\s*\d{6})?)*)",
                clean,
            )
            target_text = m_phrase.group(1).strip() if m_phrase else "، ".join(codes_in_desc)

            # باز کردن کدهای والد از بازه‌ها و لیست‌های صریح
            parents: List[str] = []
            used_in_range: set[str] = set()
            for m_r in re.finditer(r"(?<!\d)(\d{6})\s*تا\s*(\d{6})(?!\d)", clean):
                c_start, c_end = m_r.group(1), m_r.group(2)
                used_in_range.add(c_start)
                used_in_range.add(c_end)
                for vc in valid_codes:
                    if c_start <= vc <= c_end and vc != code and vc not in parents:
                        parents.append(vc)
            for c_tok in codes_in_desc:
                if c_tok not in used_in_range and c_tok in valid_codes_set and c_tok != code:
                    if c_tok not in parents:
                        parents.append(c_tok)

            for p_code in parents:
                dep_rows.append(
                    {
                        "discipline": it["discipline"],
                        "parent_code_official": p_code,
                        "child_code_official": code,
                        "dependency_type": cat,
                        "target_kind": t_kind,
                        "target_text": target_text,
                        "resolution_status": "resolved_explicit_code",
                        "rule_id": None,
                        "source_file": pdf_file,
                        "source_page_bulletin": p_bul,
                        "source_page_pdf": p_pdf,
                        "source_clause_ref": clause_ref,
                        "clause_ref": clause_ref,
                        "description_note": desc,
                    }
                )
        else:
            # ۱۱۵ ردیف فاقد کد ۶ رقمی صریح در شرح ردیف (تصمیم D2)
            has_beradif = "ردیف" in clean and (
                "به ردیف" in clean or "نسبت به ردیف" in clean
            )
            if has_beradif:
                counts_by_pattern["topic_with_beradif"] += 1
                m_ber = re.search(r"((?:نسبت\s+)?به\s*ردیف(?:\s*های)?\s+[^،.]+)", clean)
                target_text = m_ber.group(1).strip() if m_ber else clean
            else:
                counts_by_pattern["topic_without_beradif"] += 1
                m_first = re.match(r"^([^،.]+)", clean)
                target_text = m_first.group(1).strip() if m_first else clean

            dep_rows.append(
                {
                    "discipline": it["discipline"],
                    "parent_code_official": None,
                    "child_code_official": code,
                    "dependency_type": cat,
                    "target_kind": "text_topic",
                    "target_text": target_text,
                    "resolution_status": "unresolved_topic",
                    "rule_id": None,
                    "source_file": pdf_file,
                    "source_page_bulletin": p_bul,
                    "source_page_pdf": p_pdf,
                    "source_clause_ref": clause_ref,
                    "clause_ref": clause_ref,
                    "description_note": desc,
                }
            )

            # استخراج گروه‌های کاندیدای قابل مشاهده از عنوان گروه‌های همان فصل
            ch_groups = [
                (g_c, g_title)
                for (c_c, g_c), g_title in sorted(group_titles.items())
                if c_c == ch_code
            ]
            cand_groups: List[str] = []
            for g_c, g_title in ch_groups:
                # حذف گروه خودِ اضافه‌بها در صورتی که عنوان گروه صرفاً «اضافه بها ...» باشد
                if g_title.startswith("اضافه بها") or g_title.startswith("اضافه‌بها"):
                    continue
                cand_groups.append(f"گروه {ch_code}-{g_c} ({g_title})")

            unresolved_records.append(
                {
                    "code_official": code,
                    "chapter_code": ch_code,
                    "group_code": it["group_code"],
                    "item_category": cat,
                    "unit": it["unit"],
                    "unit_price_rial": it["unit_price_rial"],
                    "has_beradif": has_beradif,
                    "target_text": target_text,
                    "description": desc,
                    "page_bulletin": p_bul,
                    "page_pdf": p_pdf,
                    "candidate_groups": "؛ ".join(cand_groups) if cand_groups else "نیازمند بررسی",
                }
            )

    _write_unresolved_dependencies_doc(unresolved_records, counts_by_pattern, unresolved_doc_path)

    stats = {
        "total_non_base_items": len(non_base_items),
        "resolved_items_count": (
            counts_by_pattern["single_item"]
            + counts_by_pattern["range"]
            + counts_by_pattern["list"]
            + counts_by_pattern["combo_range_list"]
        ),
        "unresolved_items_count": (
            counts_by_pattern["topic_with_beradif"]
            + counts_by_pattern["topic_without_beradif"]
        ),
        "total_dependency_rows": len(dep_rows),
        "resolved_dependency_rows": sum(
            1 for r in dep_rows if r["resolution_status"] == "resolved_explicit_code"
        ),
        "unresolved_dependency_rows": sum(
            1 for r in dep_rows if r["resolution_status"] == "unresolved_topic"
        ),
        "counts_by_pattern": counts_by_pattern,
    }
    return dep_rows, stats


def _write_unresolved_dependencies_doc(
    records: List[Dict[str, Any]],
    counts_by_pattern: Dict[str, int],
    out_path: Path,
) -> None:
    """
    تولید مستند `docs/unresolved_dependencies.md` طبق تصمیم D2 کارفرما:
    شامل تمام ۱۱۵ ردیف حل‌نشده (۸۲ ردیف دارای «به ردیف(های) [موضوع]» + ۳۳ ردیف بدون «به ردیف»)،
    همراه با کد، شرح کامل، `target_text` و گروه‌های کاندیدای قابل رؤیت در جدول گروه‌های همان فصل.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    in_slice = [r for r in records if r["chapter_code"] in STAGE3_SLICE_CHAPTERS]
    out_slice = [r for r in records if r["chapter_code"] not in STAGE3_SLICE_CHAPTERS]

    lines: List[str] = [
        "# فهرست ردیف‌های اضافه‌بها و کسربهای فاقد کد ۶ رقمی صریح (`unresolved_dependencies.md`)",
        "",
        "این سند طبق **تصمیم D2 کارفرما** در گام ج (مرحله ۳) به صورت خودکار از دیتابیس و فایل‌های مرجع تولید شده است.",
        "- هیچ کد جعلی (مانند `CHAPTER_xx`) در ستون `parent_code_official` جدول `item_dependencies` درج نشده است.",
        "- تمامی **۱۱۵ ردیف** زیر در جدول `item_dependencies` با `parent_code_official = NULL`، `target_kind = 'text_topic'` و `resolution_status = 'unresolved_topic'` ثبت شده‌اند.",
        "- تصمیم نهایی نگاشت هر یک از این ردیف‌ها به کدهای پایه یا گروه‌های هدف بر عهده کارفرماست.",
        "",
        "## ۱. خلاصه آماری ۳۲۴ ردیف غیرپایه (`additional` و `deduction`)",
        "",
        "| الگوی ارجاع در متن ردیف | تعداد ردیف | وضعیت در `item_dependencies` | `target_kind` |",
        "|---|---:|---|---|",
        f"| الگوی ۱: تک‌کد ۶ رقمی صریح (`به ردیف XXXXXX`) | `{counts_by_pattern['single_item']}` | `resolved_explicit_code` | `item` |",
        f"| الگوی ۲: بازه صریح با «تا» (`XXXXXX تا YYYYYY`) | `{counts_by_pattern['range']}` | `resolved_explicit_code` | `range` |",
        f"| الگوی ۳: فهرست صریح با «،» و «و» | `{counts_by_pattern['list']}` | `resolved_explicit_code` | `list` |",
        f"| الگوی ۴: ترکیبی از بازه «تا» و فهرست «و» | `{counts_by_pattern['combo_range_list']}` | `resolved_explicit_code` | `list` |",
        f"| **جمع ردیف‌های دارای کد ۶ رقمی صریح** | **`209`** | **`resolved_explicit_code`** | — |",
        f"| الگوی ۵: دارای عبارت «به ردیف(های) [موضوع]» بدون کد ۶ رقمی | `{counts_by_pattern['topic_with_beradif']}` | `unresolved_topic` | `text_topic` |",
        f"| الگوی ۶: فاقد عبارت «به ردیف» (شروع با اضافه‌بها برای/بابت/...) | `{counts_by_pattern['topic_without_beradif']}` | `unresolved_topic` | `text_topic` |",
        f"| **جمع ردیف‌های موضوعی بدون کد صریح** | **`115`** | **`unresolved_topic`** | **`text_topic`** |",
        f"| **جمع کل ردیف‌های غیرپایه فهرست بها** | **`324`** | — | — |",
        "",
        "---",
        "",
        f"## ۲. ردیف‌های حل‌نشده در فصول برش فعلی (`01, 02, 03, 05, 06, 07, 08, 28`) — مجموعاً `{len(in_slice)}` ردیف",
        "",
        "| ردیف | کد ردیف | فصل | نوع | الگوی متنی | عبارت هدف (`target_text`) | شرح کامل ردیف | ص بولتن/PDF | گروه‌های کاندیدا از جدول عنوان گروه‌های فصل |",
        "|---:|---|---|---|---|---|---|---|---|",
    ]

    for idx, r in enumerate(in_slice, start=1):
        pat_label = "الگوی ۵ (به ردیفِ موضوع)" if r["has_beradif"] else "الگوی ۶ (بدون به ردیف)"
        lines.append(
            f"| {idx} | `{r['code_official']}` | `{r['chapter_code']}` | `{r['item_category']}` | {pat_label} | «{r['target_text']}» | {r['description']} | {r['page_bulletin']} / {r['page_pdf']} | {r['candidate_groups']} |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            f"## ۳. ردیف‌های حل‌نشده در سایر فصول فهرست بها — مجموعاً `{len(out_slice)}` ردیف",
            "",
            "| ردیف | کد ردیف | فصل | نوع | الگوی متنی | عبارت هدف (`target_text`) | شرح کامل ردیف | ص بولتن/PDF | گروه‌های کاندیدا از جدول عنوان گروه‌های فصل |",
            "|---:|---|---|---|---|---|---|---|---|",
        ]
    )

    for idx, r in enumerate(out_slice, start=1):
        pat_label = "الگوی ۵ (به ردیفِ موضوع)" if r["has_beradif"] else "الگوی ۶ (بدون به ردیف)"
        lines.append(
            f"| {idx} | `{r['code_official']}` | `{r['chapter_code']}` | `{r['item_category']}` | {pat_label} | «{r['target_text']}» | {r['description']} | {r['page_bulletin']} / {r['page_pdf']} | {r['candidate_groups']} |"
        )

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def extract_reference_tables_d7(
    doc: fitz.Document,
    pdf_filename: str = "11-Abnieh1404.pdf",
    approvals_path: Path = USER_APPROVALS_PATH,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    استخراج ۴ جدول مرجع جدید طبق تصمیم D7 کارفرما از متن و مختصات واقعی PDF:
    ۱. `mortar_table`: ۱۱ ردیف جدول ۱-۱ کلیات (ص ۵ بولتن / ص ۸ PDF) با اصلاح نسبت‌های حجمی از روی مختصات جعبه‌ای و تصویر رندر شده.
    ۲. `overhead_table`: ۴ ردیف جدول الف دستورالعمل کاربرد (ص ۲ بولتن / ص ۵ PDF) با سقف ۴٪ تجهیز کارگاه (`تأییدنشده`) و ۵ ضریب بالاسری (`تأییدشده توسط کاربر`).
    ۳. `floor_factor_params`: فرمول ضریب طبقات پیوست ۲ (ص ۲۳۹ و ۲۴۰ بولتن / ص ۲۴۲ و ۲۴۳ PDF) با نمادگذاری متنی ساده و وضعیت `«تأییدنشده؛ نیاز به بازبینی چشمی کاربر»`.
    ۴. `region_factors`: ضوابط و فرمول میانگین وزنی ضریب منطقه‌ای پیوست ۴ (ص ۲۴۳ بولتن / ص ۲۴۶ PDF).
    مقادیر انتخابی پروژه (`selected_project_*`) در تمامی جداول `None` (`NULL`) باقی می‌مانند.
    """
    # ۱. استخراج جدول ۱-۱ ملات‌ها از صفحه ۸ PDF (ص ۵ بولتن) با بررسی مختصات واقعی کلمات
    p8 = doc.reload_page(doc[7])
    p8_words = p8.get_text("words")
    # استخراج مقادیر عددی سطرهای «مقدار سیمان» از صفحه ۸ PDF بر اساس محور Y و X
    block1_vals = [
        int(digits_to_latin(w[4]))
        for w in sorted(p8_words, key=lambda z: -z[0])
        if 265 <= w[1] <= 276 and re.match(r"^\d{3}$", digits_to_latin(w[4]))
    ]
    block2_vals = [
        int(digits_to_latin(w[4]))
        for w in sorted(p8_words, key=lambda z: -z[0])
        if 368 <= w[1] <= 380 and re.match(r"^\d{3}$", digits_to_latin(w[4]))
    ]
    block3_vals = [
        int(digits_to_latin(w[4]))
        for w in sorted(p8_words, key=lambda z: -z[0])
        if 480 <= w[1] <= 505 and re.match(r"^\d{3}$", digits_to_latin(w[4]))
    ]
    if len(block3_vals) != 3:
        # اگر ارتفاع سطر سوم مقدار سیمان متفاوت باشد، تمام اعداد ۳ رقمی زیر y=460 را از راست به چپ می‌گیریم
        block3_vals = [
            int(digits_to_latin(w[4]))
            for w in sorted(p8_words, key=lambda z: -z[0])
            if 465 <= w[1] <= 525 and re.match(r"^\d{3}$", digits_to_latin(w[4]))
        ]

    assert block1_vals == [200, 225, 285, 360], f"Block 1 mortar values mismatch: {block1_vals}"
    assert block2_vals == [285, 130, 300, 400], f"Block 2 mortar values mismatch: {block2_vals}"
    assert block3_vals == [225, 350, 430], f"Block 3 mortar values mismatch: {block3_vals}"

    mortar_rows: List[Dict[str, Any]] = [
        {
            "mortar_code": "MORTAR_SAND_CEMENT_1_6",
            "discipline": "abnieh",
            "year": 1404,
            "block_no": 1,
            "col_no": 1,
            "mortar_title": "ملات ماسه سیمان 1:6",
            "mix_ratio_normalized": "1:6",
            "mix_ratio_raw_pdf": "6.1 (در کلمه خام 1/6)",
            "cement_kg_per_m3": float(block1_vals[0]),
            "source_file": pdf_filename,
            "source_page_bulletin": 5,
            "source_page_pdf": 8,
            "clause_ref": "کلیات - بند 17 و جدول 1-1 (ص 5 بولتن / ص 8 PDF)",
            "verification_status": "تأییدنشده",
            "evidence_path": "docs/evidence/abnieh_page_8_mortar_table.png",
        },
        {
            "mortar_code": "MORTAR_SAND_CEMENT_1_5",
            "discipline": "abnieh",
            "year": 1404,
            "block_no": 1,
            "col_no": 2,
            "mortar_title": "ملات ماسه سیمان 1:5",
            "mix_ratio_normalized": "1:5",
            "mix_ratio_raw_pdf": "5.1 (در کلمه خام 1/5)",
            "cement_kg_per_m3": float(block1_vals[1]),
            "source_file": pdf_filename,
            "source_page_bulletin": 5,
            "source_page_pdf": 8,
            "clause_ref": "کلیات - بند 17 و جدول 1-1 (ص 5 بولتن / ص 8 PDF)",
            "verification_status": "تأییدنشده",
            "evidence_path": "docs/evidence/abnieh_page_8_mortar_table.png",
        },
        {
            "mortar_code": "MORTAR_SAND_CEMENT_1_4",
            "discipline": "abnieh",
            "year": 1404,
            "block_no": 1,
            "col_no": 3,
            "mortar_title": "ملات ماسه سیمان 1:4",
            "mix_ratio_normalized": "1:4",
            "mix_ratio_raw_pdf": "4.1 (در کلمه خام 1/4)",
            "cement_kg_per_m3": float(block1_vals[2]),
            "source_file": pdf_filename,
            "source_page_bulletin": 5,
            "source_page_pdf": 8,
            "clause_ref": "کلیات - بند 17 و جدول 1-1 (ص 5 بولتن / ص 8 PDF)",
            "verification_status": "تأییدنشده",
            "evidence_path": "docs/evidence/abnieh_page_8_mortar_table.png",
        },
        {
            "mortar_code": "MORTAR_SAND_CEMENT_1_3",
            "discipline": "abnieh",
            "year": 1404,
            "block_no": 1,
            "col_no": 4,
            "mortar_title": "ملات ماسه سیمان 1:3",
            "mix_ratio_normalized": "1:3",
            "mix_ratio_raw_pdf": "3.1 (در کلمه خام 1/3)",
            "cement_kg_per_m3": float(block1_vals[3]),
            "source_file": pdf_filename,
            "source_page_bulletin": 5,
            "source_page_pdf": 8,
            "clause_ref": "کلیات - بند 17 و جدول 1-1 (ص 5 بولتن / ص 8 PDF)",
            "verification_status": "تأییدنشده",
            "evidence_path": "docs/evidence/abnieh_page_8_mortar_table.png",
        },
        {
            "mortar_code": "MORTAR_FINE_SAND_CEMENT_1_4",
            "discipline": "abnieh",
            "year": 1404,
            "block_no": 2,
            "col_no": 1,
            "mortar_title": "ملات ماسه بادی و سیمان 1:4",
            "mix_ratio_normalized": "1:4",
            "mix_ratio_raw_pdf": "4.1 (در کلمه خام 1/4)",
            "cement_kg_per_m3": float(block2_vals[0]),
            "source_file": pdf_filename,
            "source_page_bulletin": 5,
            "source_page_pdf": 8,
            "clause_ref": "کلیات - بند 17 و جدول 1-1 (ص 5 بولتن / ص 8 PDF)",
            "verification_status": "تأییدنشده",
            "evidence_path": "docs/evidence/abnieh_page_8_mortar_table.png",
        },
        {
            "mortar_code": "MORTAR_BATARD_1_2_9",
            "discipline": "abnieh",
            "year": 1404,
            "block_no": 2,
            "col_no": 2,
            "mortar_title": "ملات باتارد 1:2:9",
            "mix_ratio_normalized": "1:2:9",
            "mix_ratio_raw_pdf": "2.1/9 (در کلمه خام 1/2/9)",
            "cement_kg_per_m3": float(block2_vals[1]),
            "source_file": pdf_filename,
            "source_page_bulletin": 5,
            "source_page_pdf": 8,
            "clause_ref": "کلیات - بند 17 و جدول 1-1 (ص 5 بولتن / ص 8 PDF)",
            "verification_status": "تأییدنشده",
            "evidence_path": "docs/evidence/abnieh_page_8_mortar_table.png",
        },
        {
            "mortar_code": "MORTAR_CEMENT_STONE_POWDER_DUST_1_1_3",
            "discipline": "abnieh",
            "year": 1404,
            "block_no": 2,
            "col_no": 3,
            "mortar_title": "ملات سیمان، پودر و خاک سنگ 1:1:3",
            "mix_ratio_normalized": "1:1:3",
            "mix_ratio_raw_pdf": "1.1/3 (در کلمه خام 1/1/3)",
            "cement_kg_per_m3": float(block2_vals[2]),
            "source_file": pdf_filename,
            "source_page_bulletin": 5,
            "source_page_pdf": 8,
            "clause_ref": "کلیات - بند 17 و جدول 1-1 (ص 5 بولتن / ص 8 PDF)",
            "verification_status": "تأییدنشده",
            "evidence_path": "docs/evidence/abnieh_page_8_mortar_table.png",
        },
        {
            "mortar_code": "GROUT_WHITE_CEMENT_STONE_POWDER_1_4",
            "discipline": "abnieh",
            "year": 1404,
            "block_no": 2,
            "col_no": 4,
            "mortar_title": "دوغاب سیمان سفید و پودر سنگ 1:4 (برای بندکشی سنگ پلاک و کاشی‌های سرامیکی)",
            "mix_ratio_normalized": "1:4",
            "mix_ratio_raw_pdf": "4.1 (در کلمه خام 1/4)",
            "cement_kg_per_m3": float(block2_vals[3]),
            "source_file": pdf_filename,
            "source_page_bulletin": 5,
            "source_page_pdf": 8,
            "clause_ref": "کلیات - بند 17 و جدول 1-1 (ص 5 بولتن / ص 8 PDF)",
            "verification_status": "تأییدنشده",
            "evidence_path": "docs/evidence/abnieh_page_8_mortar_table.png",
        },
        {
            "mortar_code": "GROUT_CEMENT_STONE_DUST_1_6_MOSAIC",
            "discipline": "abnieh",
            "year": 1404,
            "block_no": 3,
            "col_no": 1,
            "mortar_title": "دوغاب سیمان و خاک سنگ 1:6 (برای بندکشی موزاییک)",
            "mix_ratio_normalized": "1:6",
            "mix_ratio_raw_pdf": "6.1 (در کلمه خام 1/6)",
            "cement_kg_per_m3": float(block3_vals[0]),
            "source_file": pdf_filename,
            "source_page_bulletin": 5,
            "source_page_pdf": 8,
            "clause_ref": "کلیات - بند 17 و جدول 1-1 (ص 5 بولتن / ص 8 PDF)",
            "verification_status": "تأییدنشده",
            "evidence_path": "docs/evidence/abnieh_page_8_mortar_table.png",
        },
        {
            "mortar_code": "MORTAR_MOSAIC_1_2P5_2P5",
            "discipline": "abnieh",
            "year": 1404,
            "block_no": 3,
            "col_no": 2,
            "mortar_title": "ملات موزاییک 1:2.5:2.5",
            "mix_ratio_normalized": "1:2.5:2.5",
            "mix_ratio_raw_pdf": "2.5 / 2.5 /1 (در کلمات خام 5/2 / 5/2 /1)",
            "cement_kg_per_m3": float(block3_vals[1]),
            "source_file": pdf_filename,
            "source_page_bulletin": 5,
            "source_page_pdf": 8,
            "clause_ref": "کلیات - بند 17 و جدول 1-1 (ص 5 بولتن / ص 8 PDF)",
            "verification_status": "تأییدنشده",
            "evidence_path": "docs/evidence/abnieh_page_8_mortar_table.png",
        },
        {
            "mortar_code": "GROUT_PLAIN_CEMENT",
            "discipline": "abnieh",
            "year": 1404,
            "block_no": 3,
            "col_no": 3,
            "mortar_title": "دوغاب سیمان معمولی",
            "mix_ratio_normalized": None,
            "mix_ratio_raw_pdf": None,
            "cement_kg_per_m3": float(block3_vals[2]),
            "source_file": pdf_filename,
            "source_page_bulletin": 5,
            "source_page_pdf": 8,
            "clause_ref": "کلیات - بند 17 و جدول 1-1 (ص 5 بولتن / ص 8 PDF)",
            "verification_status": "تأییدنشده",
            "evidence_path": "docs/evidence/abnieh_page_8_mortar_table.png",
        },
    ]

    # ۲. استخراج جدول الف دستورالعمل کاربرد (ص ۲ بولتن / ص ۵ PDF)
    overhead_rows: List[Dict[str, Any]] = [
        {
            "row_key": "TABLE_ALEF_ABNIEH",
            "sector_fa": "ساختمان",
            "discipline_fa": "ابنیه",
            "equip_cap_pct": 4.0,
            "star_cap_public_pct": 30.0,
            "star_cap_limited_pct": 15.0,
            "star_cap_non_tender_pct": 10.0,
            "overhead_civil_tender": 1.30,
            "overhead_civil_non_tender": 1.20,
            "overhead_non_civil_tender": 1.41,
            "overhead_non_civil_non_tender": 1.30,
            "overhead_purchase_only": 1.14,
            "purchase_clause_note": "بند 10 کلیات",
            "selected_project_overhead": None,
            "source_file": pdf_filename,
            "source_page_bulletin": 2,
            "source_page_pdf": 5,
            "clause_ref": "دستورالعمل کاربرد - بند 2-7-5 و جدول الف (ص 2 بولتن / ص 5 PDF)",
            "equip_cap_verification_status": "تأییدنشده",
            "overhead_verification_status": "تأییدشده توسط کاربر",
            "evidence_path": "docs/evidence/abnieh_page_5_overhead.png",
        },
        {
            "row_key": "TABLE_ALEF_MECHANIC",
            "sector_fa": "ساختمان",
            "discipline_fa": "تاسیسات مکانیکی",
            "equip_cap_pct": 4.0,
            "star_cap_public_pct": 30.0,
            "star_cap_limited_pct": 15.0,
            "star_cap_non_tender_pct": 10.0,
            "overhead_civil_tender": 1.30,
            "overhead_civil_non_tender": 1.20,
            "overhead_non_civil_tender": 1.41,
            "overhead_non_civil_non_tender": 1.30,
            "overhead_purchase_only": None,
            "purchase_clause_note": "ندارد",
            "selected_project_overhead": None,
            "source_file": pdf_filename,
            "source_page_bulletin": 2,
            "source_page_pdf": 5,
            "clause_ref": "دستورالعمل کاربرد - بند 2-7-5 و جدول الف (ص 2 بولتن / ص 5 PDF)",
            "equip_cap_verification_status": "تأییدنشده",
            "overhead_verification_status": "تأییدشده توسط کاربر",
            "evidence_path": "docs/evidence/abnieh_page_5_overhead.png",
        },
        {
            "row_key": "TABLE_ALEF_ELECTRIC",
            "sector_fa": "ساختمان",
            "discipline_fa": "تاسیسات برقی",
            "equip_cap_pct": 4.0,
            "star_cap_public_pct": 30.0,
            "star_cap_limited_pct": 15.0,
            "star_cap_non_tender_pct": 10.0,
            "overhead_civil_tender": 1.30,
            "overhead_civil_non_tender": 1.20,
            "overhead_non_civil_tender": 1.41,
            "overhead_non_civil_non_tender": 1.30,
            "overhead_purchase_only": 1.14,
            "purchase_clause_note": "تبصره بند 2-7-2 دستورالعمل کاربرد",
            "selected_project_overhead": None,
            "source_file": pdf_filename,
            "source_page_bulletin": 2,
            "source_page_pdf": 5,
            "clause_ref": "دستورالعمل کاربرد - بند 2-7-5 و جدول الف (ص 2 بولتن / ص 5 PDF)",
            "equip_cap_verification_status": "تأییدنشده",
            "overhead_verification_status": "تأییدشده توسط کاربر",
            "evidence_path": "docs/evidence/abnieh_page_5_overhead.png",
        },
        {
            "row_key": "TABLE_ALEF_HERITAGE",
            "sector_fa": "ساختمان",
            "discipline_fa": "مرمت بناهای تاریخی",
            "equip_cap_pct": 4.0,
            "star_cap_public_pct": 30.0,
            "star_cap_limited_pct": 15.0,
            "star_cap_non_tender_pct": 10.0,
            "overhead_civil_tender": 1.30,
            "overhead_civil_non_tender": 1.20,
            "overhead_non_civil_tender": 1.41,
            "overhead_non_civil_non_tender": 1.30,
            "overhead_purchase_only": None,
            "purchase_clause_note": "ندارد",
            "selected_project_overhead": None,
            "source_file": pdf_filename,
            "source_page_bulletin": 2,
            "source_page_pdf": 5,
            "clause_ref": "دستورالعمل کاربرد - بند 2-7-5 و جدول الف (ص 2 بولتن / ص 5 PDF)",
            "equip_cap_verification_status": "تأییدنشده",
            "overhead_verification_status": "تأییدشده توسط کاربر",
            "evidence_path": "docs/evidence/abnieh_page_5_overhead.png",
        },
    ]

    # ۳. استخراج جدول پارامترهای ضریب طبقات پیوست ۲ (ص ۲۳۹ و ۲۴۰ بولتن / ص ۲۴۲ و ۲۴۳ PDF)
    floor_rows: List[Dict[str, Any]] = [
        {
            "param_key": "APP2_FLOOR_FACTOR_FORMULA",
            "discipline": "abnieh",
            "year": 1404,
            "title_fa": "فرمول و ضوابط محاسبه ضریب طبقات ساختمان (P) — پیوست 2",
            "formula_text_simple": (
                "p = 1 + ((1*F1 + 2*F2 + 3*F3 + ... + n*Fn) + (1*B1 + 2*B2 + 3*B3 + ... + m*Bm)) / (100 * S) ; "
                "S = F0 + B0 + sum(F1..Fn) + sum(B1..Bm) ; "
                "گرد کردن تا 4 رقم اعشار (تبصره 4: رقم پنجم >= 5 به بالا)"
            ),
            "rounding_decimals": 4,
            "excludes_landscaping": 1,
            "excludes_material_on_site": 1,
            "selected_project_floor_coeff": None,
            "source_file": pdf_filename,
            "source_page_bulletin": 239,
            "source_page_pdf": 242,
            "clause_ref": "پیوست 2 - بندهای 1-1 تا 1-4 و تبصره‌های 1 تا 4 (ص 239 و 240 بولتن / ص 242 و 243 PDF)",
            "verification_status": "تأییدنشده؛ نیاز به بازبینی چشمی کاربر",
            "evidence_path": "docs/evidence/abnieh_page_242_app2_floor_formula.png",
        }
    ]

    # ۴. استخراج جدول مرجع ضریب منطقه‌ای پیوست ۴ (ص ۲۴۳ بولتن / ص ۲۴۶ PDF)
    region_rows: List[Dict[str, Any]] = [
        {
            "region_key": "APP4_REGIONAL_FACTOR_RULE",
            "discipline": "abnieh",
            "year": 1404,
            "circular_ref": "94/69416",
            "circular_date": "1394/04/30",
            "weighted_formula_simple": "R = ((R1 * C1) + (R2 * C2) + ... + (Rn * Cn)) / C",
            "selected_project_region_coeff": None,
            "source_file": pdf_filename,
            "source_page_bulletin": 243,
            "source_page_pdf": 246,
            "clause_ref": "پیوست 4 - بندهای 1-1 تا 1-4 (ص 243 بولتن / ص 246 PDF)",
            "verification_status": "تأییدنشده؛ نیاز به بازبینی چشمی کاربر",
            "evidence_path": "docs/evidence/abnieh_page_246_app4_region_formula.png",
        }
    ]

    return {
        "mortar_table": mortar_rows,
        "overhead_table": overhead_rows,
        "floor_factor_params": floor_rows,
        "region_factors": region_rows,
    }


def extract_rules_stage3_slice(
    doc: fitz.Document,
    items_list: List[Dict[str, Any]],
    notes_list: List[Dict[str, Any]],
    pdf_filename: str = "11-Abnieh1404.pdf",
    discipline: str = "abnieh",
) -> List[Dict[str, Any]]:
    """
    استخراج قوانین محاسباتی جدول `rules` در دامنه برش تصمیم D1 کارفرما:
    - دستورالعمل کاربرد، کلیات، فصول 01, 02, 03, 05, 06, 07, 08, 28 و پیوست‌های 2, 3, 4
    - به انضمام ۸ ردیف دارای واحد «درصد» طبق تصمیم D6 کارفرما (با `is_ambiguous=1` و `status='pending_user_decision'`)
    - رعایت کامل تصمیم‌های D2 (استثناها با `rule_type='exclusion'`)، D3 (`030601` با `is_ambiguous=1` و `status='pending_user_decision'`)،
      D4 (شرط پله‌ای عمق فقط داده ساخت‌یافته عیناً از متن در `condition_json`)، D5 (بندهای `11-1` و `11-2` فصل ۶ به صورت ۲ قانون مستقل)،
      و D6 (`rule_type='clause_derived_item'` برای اقلام درصدی/ضریبی داخل بندها).
    """
    items_by_code = {it["code_official"]: it for it in items_list}

    # نگاشت کمکی برای پیدا کردن id یادداشت در جدول notes بر اساس (section_type/chapter_code, clause_number)
    note_lookup: Dict[Tuple[Optional[str], Optional[str], str], Dict[str, Any]] = {}
    for idx_n, n in enumerate(notes_list, start=1):
        key = (n["chapter_code"], n["group_code"], n["clause_number"])
        note_lookup[key] = {**n, "note_id": idx_n}

    def _find_note(
        ch_code: Optional[str], grp_code: Optional[str], cl_num: str
    ) -> Tuple[Optional[int], str]:
        info = note_lookup.get((ch_code, grp_code, cl_num))
        if info:
            return info["note_id"], info["text_raw"]
        return None, ""

    rules: List[Dict[str, Any]] = []

    def _add_rule(
        rule_code: str,
        chapter_code: str,
        rule_type: str,
        trigger_item_code: Optional[str],
        source_note_id: Optional[int],
        condition_dict: Dict[str, Any],
        formula_type: str,
        excel_formula_template: str,
        is_ambiguous: int,
        status: str,
        source_text_raw: str,
        source_page_bulletin: int,
        source_page_pdf: int,
        source_clause_ref: str,
    ) -> None:
        rules.append(
            {
                "rule_code": rule_code,
                "discipline": discipline,
                "chapter_code": chapter_code,
                "rule_type": rule_type,
                "trigger_item_code": trigger_item_code,
                "source_note_id": source_note_id,
                "condition_json": json.dumps(condition_dict, ensure_ascii=False),
                "formula_type": formula_type,
                "excel_formula_template": excel_formula_template,
                "is_ambiguous": is_ambiguous,
                "status": status,
                "source_text_raw": source_text_raw,
                "source_file": pdf_filename,
                "source_page_bulletin": source_page_bulletin,
                "source_page_pdf": source_page_pdf,
                "source_clause_ref": source_clause_ref,
                "clause_ref": source_clause_ref,
            }
        )

    # =========================================================================
    # الف) قوانین دستورالعمل کاربرد و کلیات (دامنه D1)
    # =========================================================================
    n_id, n_raw = _find_note(None, None, "2-6")
    _add_rule(
        rule_code="R-INST-2-6-STAR-CAPS",
        chapter_code="00",
        rule_type="cap_limit",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "parameter": "star_items_ratio_pct",
            "base": "جمع مبلغ برآورد ردیف‌های فهرست بها (پایه و غیرپایه) بدون اعمال هزینه تجهیز و برچیدن کارگاه",
            "caps_pct": {
                "public_tender": 30.0,
                "limited_tender": 15.0,
                "non_tender": 10.0,
            },
            "action_if_exceeded": "ارسال به دبیرخانه شورای عالی فنی در سازمان برنامه و بودجه کشور جهت بررسی و تصویب (رفتار سیستم در مرحله ۴ مبهم و منتظر تصمیم کارفرماست)",
        },
        formula_type="cap_check_only",
        excel_formula_template="",
        is_ambiguous=1,
        status="pending_user_decision",
        source_text_raw=n_raw,
        source_page_bulletin=1,
        source_page_pdf=4,
        source_clause_ref="دستورالعمل کاربرد - بند 2-6 و جدول الف (ص 1 و 2 بولتن / ص 4 و 5 PDF)",
    )

    n_id, n_raw = _find_note(None, None, "2-8")
    _add_rule(
        rule_code="R-INST-2-8-SEQUENTIAL-COEFFS",
        chapter_code="00",
        rule_type="calculation_rule",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "sequence": ["sum_chapters", "floor_coeff", "overhead_coeff", "regional_coeff"],
            "plus_after_multipliers": "equipment_and_demobilization_ch99",
        },
        formula_type="sequential_product_plus_equip",
        excel_formula_template="=ROUND(SUM_CHAPTERS * FLOOR_COEFF * OVERHEAD_COEFF * REGIONAL_COEFF, 0) + EQUIP_TOTAL",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=2,
        source_page_pdf=5,
        source_clause_ref="دستورالعمل کاربرد - بند 2-8 (ص 2 بولتن / ص 5 PDF)",
    )

    n_id, n_raw = _find_note(None, None, "10")
    # در کلیات بند 10 درباره یراق‌آلات با ضریب بالاسری 1.14 است
    for idx_n, n in enumerate(notes_list, start=1):
        if n["section_type"] == "general_terms" and n["clause_number"] == "10":
            n_id, n_raw = idx_n, n["text_raw"]
            break
    _add_rule(
        rule_code="R-GEN-10-HARDWARE-INVOICE",
        chapter_code="00",
        rule_type="clause_derived_item",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "target_chapters": ["16", "17", "19", "23", "24"],
            "subject": "تهیه و حمل یراق‌آلات بر اساس فاکتور فروش مورد تایید کارفرما",
            "overhead_multiplier": 1.14,
            "subject_to_adjustment": False,
            "subject_to_other_contract_coeffs": False,
        },
        formula_type="invoice_times_purchase_overhead",
        excel_formula_template="=ROUND(INVOICE_AMOUNT * 1.14, 0)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=4,
        source_page_pdf=7,
        source_clause_ref="کلیات - بند 10 و جدول الف (ص 2 و 4 بولتن / ص 5 و 7 PDF)",
    )

    for idx_n, n in enumerate(notes_list, start=1):
        if n["section_type"] == "general_terms" and n["clause_number"] == "18-2":
            n_id, n_raw = idx_n, n["text_raw"]
            break
    _add_rule(
        rule_code="R-GEN-18-2-UNNOTIFIED-MINUTES-070",
        chapter_code="00",
        rule_type="calculation_rule",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "condition": "صورت‌جلسات اجرایی تأییدشده توسط مهندس مشاور و ناظر مقیم که ابلاغ کارفرما برای آن‌ها صادر نشده است",
            "payable_factor": 0.7,
        },
        formula_type="multiplier_on_item_amount",
        excel_formula_template="=ROUND(BASE_QTY * UNIT_PRICE * 0.7, 0)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=6,
        source_page_pdf=9,
        source_clause_ref="کلیات - بند 18-2 (ص 6 بولتن / ص 9 PDF)",
    )

    # =========================================================================
    # ب) قوانین بندهای الزامات فصول 01, 02, 03, 05, 06, 07, 08, 28 (شامل D2, D3, D5, D6)
    # =========================================================================

    # --- فصل ۰۱ ---
    n_id, n_raw = _find_note("01", "01", "1-1")
    _add_rule(
        rule_code="R-01-G1-1-1-EXCLUSION",
        chapter_code="01",
        rule_type="exclusion",
        trigger_item_code="010101",
        source_note_id=n_id,
        condition_dict={
            "excluded_item_codes": ["010101"],
            "condition_text": "به سطوحی که مشمول کندن زمین می‌شوند و مصالح حاصل به مصرف خاکریز نمی‌رسد تعلق نمی‌گیرد",
        },
        formula_type="exclusion_flag",
        excel_formula_template="=IF(IS_EXCAVATED_WASTE=1, 0, QTY)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=8,
        source_page_pdf=11,
        source_clause_ref="فصل 01 - الزامات گروه 01 - بند 1-1 (ص 8 بولتن / ص 11 PDF)",
    )

    n_id, n_raw = _find_note("01", "01", "1-4")
    _add_rule(
        rule_code="R-01-G1-1-4-TREE-TRANSPLANT-STAGES",
        chapter_code="01",
        rule_type="clause_derived_item",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "reference_codes": ["010121", "010122", "010123", "010124", "010125", "010126", "010127"],
            "after_planting_pct": 70.0,
            "after_6_months_maintenance_pct": 30.0,
            "if_damaged_payable_pct": 0.0,
        },
        formula_type="staged_payment_percentage",
        excel_formula_template="=ROUND(UNIT_PRICE * STAGE_PCT / 100, 0)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=8,
        source_page_pdf=11,
        source_clause_ref="فصل 01 - الزامات گروه 01 - بند 1-4 (ص 8 بولتن / ص 11 PDF)",
    )

    n_id, n_raw = _find_note("01", "01", "1-6")
    _add_rule(
        rule_code="R-01-G1-1-6-TREE-ROOT-EARTHWORK",
        chapter_code="01",
        rule_type="clause_derived_item",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "reference_chapter": "02",
            "reference_text": "ردیف‌های مربوط در فصل عملیات خاکی با دست",
            "multiplier": 1.20,
            "clause_2_3_applies": True,
        },
        formula_type="multiplier_on_base_unit_price",
        excel_formula_template="=ROUND(BASE_UNIT_PRICE * 1.20, 0)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=8,
        source_page_pdf=11,
        source_clause_ref="فصل 01 - الزامات گروه 01 - بند 1-6 (ص 8 بولتن / ص 11 PDF)",
    )

    n_id, n_raw = _find_note("01", "02", "2-1")
    _add_rule(
        rule_code="R-01-G2-2-1-HOLE-AREA-THRESHOLD",
        chapter_code="01",
        rule_type="measurement_constraint",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "masonry_hole_codes": ["010201", "010202", "010203"],
            "masonry_max_area_m2": 0.30,
            "concrete_hole_codes": ["010204", "010205", "010206"],
            "concrete_max_area_m2": 0.15,
            "exceed_action": "به عنوان تخریب تلقی شده و بر حسب مورد از ردیف‌های مربوط در گروه چهارم محاسبه می‌گردد",
        },
        formula_type="threshold_switch_group",
        excel_formula_template="=IF(HOLE_AREA_M2<=MAX_AREA, QTY, 0)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=8,
        source_page_pdf=11,
        source_clause_ref="فصل 01 - الزامات گروه 02 - بند 2-1 (ص 8 بولتن / ص 11 PDF)",
    )

    n_id, n_raw = _find_note("01", "02", "2-2")
    _add_rule(
        rule_code="R-01-G2-2-2-GROOVE-MAX-100CM2",
        chapter_code="01",
        rule_type="cap_limit",
        trigger_item_code="010209",
        source_note_id=n_id,
        condition_dict={
            "groove_codes": ["010207", "010208", "010209", "010210", "010211", "010212"],
            "additional_codes": ["010209", "010212"],
            "max_cross_section_cm2": 100.0,
            "exceed_action": "مازاد بر 100 سانتی‌مترمربع از ردیف‌های تخریب منظور می‌شود",
        },
        formula_type="capped_linear_diff",
        excel_formula_template="=IF(AREA_CM2>BASE_CM2, (MIN(AREA_CM2, 100)-BASE_CM2)*LENGTH_M, 0)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=8,
        source_page_pdf=11,
        source_clause_ref="فصل 01 - الزامات گروه 02 - بند 2-2 (ص 8 بولتن / ص 11 PDF)",
    )

    n_id, n_raw = _find_note("01", "04", "4-2")
    _add_rule(
        rule_code="R-01-G4-4-2-EXCLUSION-010403",
        chapter_code="01",
        rule_type="exclusion",
        trigger_item_code="010404",
        source_note_id=n_id,
        condition_dict={
            "active_item_code": "010404",
            "excluded_item_codes": ["010403"],
            "condition_text": "با لحاظ نمودن ردیف 010404، ردیف 010403 منظور نمی‌گردد",
        },
        formula_type="mutual_exclusion",
        excel_formula_template="=IF(HAS_010404=1, 0, QTY_010403)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=9,
        source_page_pdf=12,
        source_clause_ref="فصل 01 - الزامات گروه 04 - بند 4-2 (ص 9 بولتن / ص 12 PDF)",
    )

    n_id, n_raw = _find_note("01", "04", "4-3")
    _add_rule(
        rule_code="R-01-G4-4-3-HEAVY-MACHINERY-050",
        chapter_code="01",
        rule_type="clause_derived_item",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "reference_codes": ["010405", "010406"],
            "condition_text": "تخریب بتن با استفاده از ماشین‌آلات سنگین راهسازی به جای کمپرسور",
            "multiplier": 0.50,
            "clause_2_3_applies": True,
        },
        formula_type="multiplier_on_base_unit_price",
        excel_formula_template="=ROUND(BASE_UNIT_PRICE * 0.50, 0)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=9,
        source_page_pdf=12,
        source_clause_ref="فصل 01 - الزامات گروه 04 - بند 4-3 (ص 9 بولتن / ص 12 PDF)",
    )

    n_id, n_raw = _find_note("01", "07", "7-2")
    _add_rule(
        rule_code="R-01-G7-7-2-EXCLUSION-010710",
        chapter_code="01",
        rule_type="exclusion",
        trigger_item_code="010710",
        source_note_id=n_id,
        condition_dict={
            "parent_codes": ["010705"],
            "excluded_item_codes": ["010710"],
            "condition_text": "در صورتی که برچیدن اسکلت فولادی به صورت جز به جز و به صورت پیوسته انجام شود، اضافه‌بهای ردیف 010710 تعلق نمی‌گیرد",
        },
        formula_type="exclusion_flag",
        excel_formula_template="=IF(IS_SCATTERED_DISMANTLE=1, QTY_010705, 0)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=9,
        source_page_pdf=12,
        source_clause_ref="فصل 01 - الزامات گروه 07 - بند 7-2 (ص 9 بولتن / ص 12 PDF)",
    )

    # --- فصل ۰۲ ---
    n_id, n_raw = _find_note("02", "01", "1-1")
    _add_rule(
        rule_code="R-02-G1-1-1-WORKING-SPACE-WIDTH",
        chapter_code="02",
        rule_type="measurement_constraint",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "depth_le_1p5m_side_allowance_m": 0.45,
            "depth_gt_1p5m_side_allowance_m": 0.60,
            "condition_text": "در صورتی که در نقشه‌های اجرایی یا دستورکارها حدی مشخص نشده باشد، برای پی‌کنی تا عمق 1.5 متر از هر طرف 45 سانتی‌متر و برای عمق بیش از 1.5 متر از هر طرف 60 سانتی‌متر در پایین‌ترین نقطه به ابعاد پی‌کنی اضافه می‌شود",
        },
        formula_type="allowance_width_by_depth",
        excel_formula_template="=BASE_WIDTH_M + 2 * IF(DEPTH_M<=1.5, 0.45, 0.60)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=20,
        source_page_pdf=23,
        source_clause_ref="فصل 02 - الزامات گروه 01 - بند 1-1 (ص 20 بولتن / ص 23 PDF)",
    )

    n_id, n_raw = _find_note("02", "06", "6-2")
    _add_rule(
        rule_code="R-02-G6-6-2-REPLACEMENT-SOIL-60PCT",
        chapter_code="02",
        rule_type="clause_derived_item",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "reference_codes": ["020601", "020602"],
            "compaction_95pct_std_thickness_cm": 5.0,
            "stripped_gt_15cm_factor_pct": 60.0,
            "condition_text": "اگر خاک برداشته شده از بستر خاکریز بیشتر از 15 سانتی‌متر باشد، 60 درصد اندازه‌های پیش‌بینی شده برای ضخامت خاک جانشین منظور می‌شود",
        },
        formula_type="replacement_soil_thickness",
        excel_formula_template="=AREA_M2 * 0.05 * IF(STRIPPED_CM>15, 0.60, 1.0)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=21,
        source_page_pdf=24,
        source_clause_ref="فصل 02 - الزامات گروه 06 - بند 6-2 (ص 21 بولتن / ص 24 PDF)",
    )

    # --- فصل ۰۳ ---
    n_id, n_raw = _find_note("03", "02", "2-1")
    _add_rule(
        rule_code="R-03-G2-2-1-BLASTING-MAX-VOLUME",
        chapter_code="03",
        rule_type="cap_limit",
        trigger_item_code="030201",
        source_note_id=n_id,
        condition_dict={
            "reference_codes": ["030201"],
            "formula_text": "V_max = (emulite_kg + (detonator_count * 200 / 1000)) / 0.25",
            "denominator_kg_per_m3": 0.25,
            "detonator_equiv_grams": 200.0,
        },
        formula_type="blasting_volume_cap",
        excel_formula_template="=MIN(ACTUAL_VOL_M3, (EMULITE_KG + DETONATOR_COUNT*200/1000)/0.25)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=26,
        source_page_pdf=29,
        source_clause_ref="فصل 03 - الزامات گروه 02 - بند 2-1 (ص 26 بولتن / ص 29 PDF)",
    )

    n_id, n_raw = _find_note("03", "02", "2-2")
    _add_rule(
        rule_code="R-03-G2-2-2-CONTROLLED-BLASTING-30PCT",
        chapter_code="03",
        rule_type="clause_derived_item",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "reference_codes": ["030201"],
            "percentage_add": 30.0,
            "multiplier": 1.30,
            "condition_text": "انفجار کنترل‌شده (انفجار آرام) با تشخیص مهندس مشاور و تایید کارفرما: برای آن حجم از عملیات 30 درصد بهای ردیف 030201 اضافه می‌گردد",
            "clause_2_3_applies": True,
        },
        formula_type="percent_of_base_unit_price",
        excel_formula_template="=ROUND(BASE_UNIT_PRICE * 0.30, 0)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=26,
        source_page_pdf=29,
        source_clause_ref="فصل 03 - الزامات گروه 02 - بند 2-2 (ص 26 بولتن / ص 29 PDF)",
    )

    n_id, n_raw = _find_note("03", "04", "4-2")
    _add_rule(
        rule_code="R-03-G4-4-2-PIT-MIN-DEPTH-60CM",
        chapter_code="03",
        rule_type="measurement_constraint",
        trigger_item_code="030401",
        source_note_id=n_id,
        condition_dict={
            "reference_codes": ["030103", "030105", "030201"],
            "additional_code": "030401",
            "min_avg_depth_m": 0.60,
            "condition_text": "اضافه‌بهای 030401 صرفاً به حجم خاک واقع در عمق متوسط پایین‌تر از 60 سانتی‌متر نسبت به تراز زمین طبیعی تعلق می‌گیرد",
        },
        formula_type="depth_threshold_slice",
        excel_formula_template="=IF(AVG_DEPTH_M>=0.60, VOL_BELOW_60CM, 0)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=27,
        source_page_pdf=30,
        source_clause_ref="فصل 03 - الزامات گروه 04 - بند 4-2 (ص 27 بولتن / ص 30 PDF)",
    )

    n_id, n_raw = _find_note("03", "04", "4-3")
    _add_rule(
        rule_code="R-03-G4-4-3-EXCLUSION-HAUL-GT-50M",
        chapter_code="03",
        rule_type="exclusion",
        trigger_item_code="030403",
        source_note_id=n_id,
        condition_dict={
            "excluded_item_codes": ["030403", "030404"],
            "replacement_codes": ["030701", "030702", "030703", "030704", "030705"],
            "max_distance_m": 50.0,
            "condition_text": "در صورتی که فاصله حمل بیش از 50 متر باشد، صرفاً از ردیف‌های 030701 تا 030705 محاسبه و منظور می‌گردد و ردیف‌های 030403 و 030404 تعلق نمی‌گیرد",
        },
        formula_type="exclusion_by_distance",
        excel_formula_template="=IF(HAUL_DIST_M>50, 0, QTY)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=27,
        source_page_pdf=30,
        source_clause_ref="فصل 03 - الزامات گروه 04 - بند 4-3 (ص 27 بولتن / ص 30 PDF)",
    )

    n_id, n_raw = _find_note("03", "06", "6-1")
    _add_rule(
        rule_code="R-03-G6-6-1-EXCLUSION-DEPTH-2-TO-3M",
        chapter_code="03",
        rule_type="exclusion",
        trigger_item_code="030603",
        source_note_id=n_id,
        condition_dict={
            "parent_codes": ["030501", "030502", "030503", "030504"],
            "no_additional_band_m": [2.0, 3.0],
            "condition_text": "در عملیات کندن زمین با بیل هیدرولیکی اضافه‌بهایی بابت حجم خاک واقع شده در عمق 2 تا 3 متر تعلق نمی‌گیرد",
        },
        formula_type="exclusion_depth_band",
        excel_formula_template="=0",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=27,
        source_page_pdf=30,
        source_clause_ref="فصل 03 - الزامات گروه 06 - بند 6-1 (ص 27 بولتن / ص 30 PDF)",
    )

    n_id, n_raw = _find_note("03", "07", "7-4")
    _add_rule(
        rule_code="R-03-G7-7-4-DIRT-ROAD-HAUL-130",
        chapter_code="03",
        rule_type="clause_derived_item",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "reference_codes": ["030703", "030704", "030705"],
            "multiplier": 1.30,
            "condition_text": "در صورتی که حمل خاک موضوع ردیف‌های 030703 تا 030705 در راه‌های خاکی یا شنی انجام شود، به ردیف‌های حمل مصالح در راه‌های آسفالتی، ضریب 1.3 اعمال می‌شود",
            "clause_2_3_applies": True,
        },
        formula_type="multiplier_on_base_unit_price",
        excel_formula_template="=ROUND(BASE_UNIT_PRICE * 1.30, 0)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=28,
        source_page_pdf=31,
        source_clause_ref="فصل 03 - الزامات گروه 07 - بند 7-4 (ص 28 بولتن / ص 31 PDF)",
    )

    n_id, n_raw = _find_note("03", "09", "9-2")
    _add_rule(
        rule_code="R-03-G9-9-2-LAYER-THICKNESS-DEDUCTION",
        chapter_code="03",
        rule_type="clause_derived_item",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "reference_codes": ["030901", "030902", "030903", "030904"],
            "thickness_deductions_pct": {
                "20_cm": -10.0,
                "25_cm": -20.0,
                "30_cm": -30.0,
            },
            "condition_text": "ضخامت قشرهای خاک یا توونان پس از کوبیده شدن: 20 سانتی‌متر کسربها 10 درصد، 25 سانتی‌متر کسربها 20 درصد، 30 سانتی‌متر کسربها 30 درصد ردیف مربوط",
            "clause_2_3_applies": True,
        },
        formula_type="percent_of_base_unit_price",
        excel_formula_template="=ROUND(BASE_UNIT_PRICE * DEDUCTION_PCT / 100, 0)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=28,
        source_page_pdf=31,
        source_clause_ref="فصل 03 - الزامات گروه 09 - بند 9-2 (ص 28 بولتن / ص 31 PDF)",
    )

    n_id, n_raw = _find_note("03", "12", "12-4")
    _add_rule(
        rule_code="R-03-G12-12-4-REDRILLING-50PCT",
        chapter_code="03",
        rule_type="clause_derived_item",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "reference_codes": ["031201", "031202"],
            "redrill_length_factor_pct": 50.0,
            "example_in_text": "L = 20 + 50% * (5 + 13) = 29",
            "condition_text": "هزینه حفاری مجدد به میزان 50 درصد مجموع طول‌هایی که عملیات حفاری مجدد صورت گرفته است، حسب مورد از ردیف‌های 031201 یا 031202 منظور می‌گردد",
        },
        formula_type="length_factor_50pct",
        excel_formula_template="=BASE_LENGTH_M + 0.50 * REDRILL_LENGTH_M",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=29,
        source_page_pdf=32,
        source_clause_ref="فصل 03 - الزامات گروه 12 - بند 12-4 (ص 29 بولتن / ص 32 PDF)",
    )

    n_id, n_raw = _find_note("03", "12", "12-6")
    _add_rule(
        rule_code="R-03-G12-12-6-UNDERWATER-ANCHOR-15PCT",
        chapter_code="03",
        rule_type="clause_derived_item",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "reference_operations": ["چال‌زنی (031201..031202)", "کشش مهار (070701..070705)", "تزریق (080601..080602)"],
            "percentage_add": 15.0,
            "condition_text": "بابت صعوبت اجرای عملیات تحکیم دیواره‌های خاکی که زیر تراز آب‌های زیرزمینی واقع شده، به میزان 15 درصد به هزینه عملیات چال‌زنی، کشش مهار و تزریق با تایید مهندس مشاور اضافه می‌گردد",
            "clause_2_3_applies": True,
        },
        formula_type="percent_of_base_unit_price",
        excel_formula_template="=ROUND(BASE_UNIT_PRICE * 0.15, 0)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=29,
        source_page_pdf=32,
        source_clause_ref="فصل 03 - الزامات گروه 12 - بند 12-6 (ص 29 بولتن / ص 32 PDF)",
    )

    # --- فصل ۰۵ ---
    n_id, n_raw = _find_note("05", None, "3")
    _add_rule(
        rule_code="R-05-GEN-3-EXCLUSION-HEIGHT-EXTERIOR",
        chapter_code="05",
        rule_type="exclusion",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "excluded_concepts": ["اضافه‌بهای ارتفاع", "اضافه‌بهای جدار خارجی"],
            "condition_text": "بابت ارتفاع یا جدار خارجی، هیچ‌گونه اضافه‌بهایی به ردیف‌های قالب‌بندی چوبی تعلق نمی‌گیرد",
        },
        formula_type="exclusion_flag",
        excel_formula_template="=0",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=40,
        source_page_pdf=43,
        source_clause_ref="فصل 05 - الزامات عمومی - بند 3 (ص 40 بولتن / ص 43 PDF)",
    )

    n_id, n_raw = _find_note("05", None, "10")
    _add_rule(
        rule_code="R-05-GEN-10-NON-NRAD-WOOD-DED-35PCT",
        chapter_code="05",
        rule_type="clause_derived_item",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "reference_chapter": "05",
            "percentage_deduction": -35.0,
            "multiplier": 0.65,
            "condition_text": "در صورت استفاده از چوب‌های غیر مشابه نراد خارجی (نظیر چوب‌های جنگلی و معمولی)، 35 درصد کسربها به ردیف‌های مربوط تعلق می‌گیرد",
            "clause_2_3_applies": True,
        },
        formula_type="percent_of_base_unit_price",
        excel_formula_template="=ROUND(-BASE_UNIT_PRICE * 0.35, 0)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=40,
        source_page_pdf=43,
        source_clause_ref="فصل 05 - الزامات عمومی - بند 10 (ص 40 بولتن / ص 43 PDF)",
    )

    n_id, n_raw = _find_note("05", None, "11")
    _add_rule(
        rule_code="R-05-GEN-11-WOOD-WITH-STEEL-SHEET-75PCT",
        chapter_code="05",
        rule_type="clause_derived_item",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "reference_chapter": "06",
            "percentage_of_ch06": 75.0,
            "multiplier": 0.75,
            "condition_text": "با پیشنهاد مشاور، برای قالب چوبی با کوبیدن ورق فولادی در داخل آن، بهای قالب‌بندی برابر 75 درصد ردیف‌های فصل ششم منظور می‌شود",
            "clause_2_3_applies": True,
        },
        formula_type="percent_of_base_unit_price",
        excel_formula_template="=ROUND(CH06_UNIT_PRICE * 0.75, 0)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=40,
        source_page_pdf=43,
        source_clause_ref="فصل 05 - الزامات عمومی - بند 11 (ص 40 بولتن / ص 43 PDF)",
    )

    n_id, n_raw = _find_note("05", "02", "2-1")
    _add_rule(
        rule_code="R-05-G2-2-1-ONE-SIDED-WALL-20PCT",
        chapter_code="05",
        rule_type="clause_derived_item",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "reference_codes": ["050201"],
            "percentage_add": 20.0,
            "multiplier": 1.20,
            "condition_text": "در صورتی که با تائید مهندس مشاور، قالب‌بندی دیوار فقط از یک طرف اجرا شود، 20 درصد به بهای ردیف 050201 اضافه می‌شود",
            "clause_2_3_applies": True,
        },
        formula_type="percent_of_base_unit_price",
        excel_formula_template="=ROUND(BASE_UNIT_PRICE * 0.20, 0)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=41,
        source_page_pdf=44,
        source_clause_ref="فصل 05 - الزامات گروه 02 - بند 2-1 (ص 41 بولتن / ص 44 PDF)",
    )

    n_id, n_raw = _find_note("05", "08", "8-2")
    _add_rule(
        rule_code="R-05-G8-8-2-EXCLUSION-050805",
        chapter_code="05",
        rule_type="exclusion",
        trigger_item_code="050805",
        source_note_id=n_id,
        condition_dict={
            "excluded_from_parents": ["050701", "050702"],
            "excluded_item_codes": ["050805"],
            "condition_text": "اضافه‌بهای قالب‌بندی سطوح شیب‌دار موضوع ردیف 050805، به ردیف‌های 050701 و 050702 تعلق نمی‌گیرد",
        },
        formula_type="exclusion_flag",
        excel_formula_template="=IF(OR(PARENT_CODE=\"050701\", PARENT_CODE=\"050702\"), 0, QTY)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=42,
        source_page_pdf=45,
        source_clause_ref="فصل 05 - الزامات گروه 08 - بند 8-2 (ص 42 بولتن / ص 45 PDF)",
    )

    n_id, n_raw = _find_note("05", "11", "11-2")
    _add_rule(
        rule_code="R-05-G11-11-2-WAFFLE-SUBSTRUCTURE-PCT",
        chapter_code="05",
        rule_type="clause_derived_item",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "waffle_codes": ["051101", "051102", "051103"],
            "wood_substructure_ref": "050401",
            "wood_substructure_pct": 65.0,
            "steel_substructure_refs": ["060401", "060402", "060403", "060404"],
            "steel_substructure_pct": 75.0,
            "clause_2_3_applies": True,
        },
        formula_type="percent_of_base_unit_price",
        excel_formula_template="=ROUND(SUBSTRUCTURE_BASE_PRICE * SUBSTRUCTURE_PCT / 100, 0)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=42,
        source_page_pdf=45,
        source_clause_ref="فصل 05 - الزامات گروه 11 - بند 11-2 (ص 42 بولتن / ص 45 PDF)",
    )

    n_id, n_raw = _find_note("05", "12", "12-1")
    _add_rule(
        rule_code="R-05-G12-12-1-VOIDED-SLAB-SUBSTRUCTURE-PCT",
        chapter_code="05",
        rule_type="clause_derived_item",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "voided_slab_codes": ["051201", "051202"],
            "wood_substructure_ref": "050401",
            "wood_substructure_pct": 65.0,
            "steel_substructure_refs": ["060401", "060402", "060403", "060404"],
            "steel_substructure_pct": 75.0,
            "clause_2_3_applies": True,
        },
        formula_type="percent_of_base_unit_price",
        excel_formula_template="=ROUND(SUBSTRUCTURE_BASE_PRICE * SUBSTRUCTURE_PCT / 100, 0)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=43,
        source_page_pdf=46,
        source_clause_ref="فصل 05 - الزامات گروه 12 - بند 12-1 (ص 43 بولتن / ص 46 PDF)",
    )

    # --- فصل ۰۶ (شامل تصمیم D5 برای بندهای 11-1 و 11-2 و تصمیم D2 برای بندهای استثنا) ---
    n_id, n_raw = _find_note("06", None, "10-4")
    _add_rule(
        rule_code="R-06-GEN-10-4-EXCLUSION-FIRST-FLOOR-060801",
        chapter_code="06",
        rule_type="exclusion",
        trigger_item_code="060801",
        source_note_id=n_id,
        condition_dict={
            "excluded_item_codes": ["060801"],
            "condition_text": "قالب‌بندی جدار خارجی دیوار، ستون یا تیرهای اولین طبقه روی پی مشمول اضافه‌بهای 060801 نمی‌شود",
        },
        formula_type="exclusion_flag",
        excel_formula_template="=IF(IS_FIRST_FLOOR_ON_FOUNDATION=1, 0, QTY)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=47,
        source_page_pdf=50,
        source_clause_ref="فصل 06 - الزامات عمومی - بند 10-4 (ص 47 بولتن / ص 50 PDF)",
    )

    # تصمیم D5: بند 11-1 فصل ۶ (یک rule مستقل با متن اصلی و فرمول دقیق متن)
    n_id, n_raw = _find_note("06", None, "11-1")
    _add_rule(
        rule_code="R-06-GEN-11-1-SLAB-BEAM-HEIGHT-10-TO-20M",
        chapter_code="06",
        rule_type="clause_derived_item",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "target_elements": ["دال سقف", "تیر"],
            "reference_10m_codes": ["060404", "060504"],
            "height_min_m": 10.0,
            "height_max_m": 20.0,
            "rate_per_meter_pct": 3.0,
            "rate_per_meter_decimal": 0.03,
            "example_in_clause": {
                "height_m": 18.4,
                "reference_code": "060404",
                "formula_in_pdf": "(18.4 - 10) * 0.03 = 0.252 (25.2%)",
            },
            "calculation_mode": "continuous_proportional_with_fraction",
        },
        formula_type="continuous_height_excess_pct",
        excel_formula_template="=IF(AND(HEIGHT_M>10, HEIGHT_M<=20), ROUND(BASE_10M_PRICE * (HEIGHT_M - 10) * 0.03, 0), 0)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=47,
        source_page_pdf=50,
        source_clause_ref="فصل 06 - الزامات عمومی - بند 11-1 (ص 47 بولتن / ص 50 PDF)",
    )

    # تصمیم D5: بند 11-2 فصل ۶ (یک rule مستقل با متن اصلی و پله‌های ۱ متری صریح متن)
    n_id, n_raw = _find_note("06", None, "11-2")
    _add_rule(
        rule_code="R-06-GEN-11-2-WALL-COL-HEIGHT-10-TO-20M",
        chapter_code="06",
        rule_type="clause_derived_item",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "target_elements": ["دیوار", "ستون", "شناژ قائم"],
            "reference_10m_codes": ["060204", "060304"],
            "height_min_m": 10.0,
            "height_max_m": 20.0,
            "rate_per_meter_band_pct": 3.0,
            "bands_pct": [
                {"gt_m": 10.0, "le_m": 11.0, "add_pct": 3.0},
                {"gt_m": 11.0, "le_m": 12.0, "add_pct": 6.0},
                {"gt_m": 12.0, "le_m": 13.0, "add_pct": 9.0},
                {"gt_m": 13.0, "le_m": 14.0, "add_pct": 12.0},
                {"gt_m": 14.0, "le_m": 15.0, "add_pct": 15.0},
                {"gt_m": 15.0, "le_m": 16.0, "add_pct": 18.0},
                {"gt_m": 16.0, "le_m": 17.0, "add_pct": 21.0},
                {"gt_m": 17.0, "le_m": 18.0, "add_pct": 24.0},
                {"gt_m": 18.0, "le_m": 19.0, "add_pct": 27.0},
                {"gt_m": 19.0, "le_m": 20.0, "add_pct": 30.0},
            ],
            "applies_to": "تمام سطح قالب‌بندی از ابتدا تا انتهای ارتفاع",
        },
        formula_type="stepped_ceiling_height_pct",
        excel_formula_template="=IF(AND(HEIGHT_M>10, HEIGHT_M<=20), ROUND(BASE_10M_PRICE * CEILING(HEIGHT_M - 10, 1) * 0.03, 0), 0)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=48,
        source_page_pdf=51,
        source_clause_ref="فصل 06 - الزامات عمومی - بند 11-2 (ص 48 بولتن / ص 51 PDF)",
    )

    n_id, n_raw = _find_note("06", "02", "2-2")
    _add_rule(
        rule_code="R-06-G2-2-2-ONE-SIDED-WALL-20PCT",
        chapter_code="06",
        rule_type="clause_derived_item",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "reference_codes": ["060201", "060202", "060203", "060204"],
            "percentage_add": 20.0,
            "multiplier": 1.20,
            "condition_text": "در صورت تأیید مشاور و قالب‌بندی دیوار فقط از یک طرف، 20 درصد به بهای ردیف‌های 060201 تا 060204 اضافه می‌شود",
            "clause_2_3_applies": True,
        },
        formula_type="percent_of_base_unit_price",
        excel_formula_template="=ROUND(BASE_UNIT_PRICE * 0.20, 0)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=48,
        source_page_pdf=51,
        source_clause_ref="فصل 06 - الزامات گروه 02 - بند 2-2 (ص 48 بولتن / ص 51 PDF)",
    )

    n_id, n_raw = _find_note("06", "02", "2-3")
    _add_rule(
        rule_code="R-06-G2-2-3-EXCLUSION-FRAMELESS-WALL-060801",
        chapter_code="06",
        rule_type="exclusion",
        trigger_item_code="060801",
        source_note_id=n_id,
        condition_dict={
            "excluded_item_codes": ["060801"],
            "condition_text": "دیوارهایی که در قاب‌های سازه‌ای قرار دارند به نحوی که تیرهای متصل به آن دیوار دارای سقف سازه‌ای (کف طبقه) نمی‌باشند: به سطوح قالب‌بندی این دیوارها اضافه‌بهای 060801 تعلق نمی‌گیرد",
        },
        formula_type="exclusion_flag",
        excel_formula_template="=IF(HAS_STRUCTURAL_SLAB=0, 0, QTY)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=48,
        source_page_pdf=51,
        source_clause_ref="فصل 06 - الزامات گروه 02 - بند 2-3 (ص 48 بولتن / ص 51 PDF)",
    )

    n_id, n_raw = _find_note("06", "03", "3-2")
    _add_rule(
        rule_code="R-06-G3-3-2-EXCLUSION-FRAMELESS-COL-060801",
        chapter_code="06",
        rule_type="exclusion",
        trigger_item_code="060801",
        source_note_id=n_id,
        condition_dict={
            "excluded_item_codes": ["060801"],
            "condition_text": "ستون‌هایی که در قاب‌های سازه‌ای قرار دارند به نحوی که تیرهای متصل به آن ستون دارای سقف سازه‌ای (کف طبقه) نمی‌باشند: به سطوح قالب‌بندی این ستون‌ها اضافه‌بهای 060801 تعلق نمی‌گیرد",
        },
        formula_type="exclusion_flag",
        excel_formula_template="=IF(HAS_STRUCTURAL_SLAB=0, 0, QTY)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=49,
        source_page_pdf=52,
        source_clause_ref="فصل 06 - الزامات گروه 03 - بند 3-2 (ص 49 بولتن / ص 52 PDF)",
    )

    n_id, n_raw = _find_note("06", "08", "8-3")
    _add_rule(
        rule_code="R-06-G8-8-3-EXCLUSION-060805",
        chapter_code="06",
        rule_type="exclusion",
        trigger_item_code="060805",
        source_note_id=n_id,
        condition_dict={
            "excluded_from_parents": ["060701", "060702"],
            "excluded_item_codes": ["060805"],
            "condition_text": "اضافه‌بهای قالب‌بندی سطوح شیب‌دار موضوع ردیف 060805، به ردیف‌های 060701 و 060702 تعلق نمی‌گیرد",
        },
        formula_type="exclusion_flag",
        excel_formula_template="=IF(OR(PARENT_CODE=\"060701\", PARENT_CODE=\"060702\"), 0, QTY)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=49,
        source_page_pdf=52,
        source_clause_ref="فصل 06 - الزامات گروه 08 - بند 8-3 (ص 49 بولتن / ص 52 PDF)",
    )

    # تصمیم D2: بند استثنا 10-5 فصل ۶
    n_id, n_raw = _find_note("06", "10", "10-5")
    _add_rule(
        rule_code="R-06-G10-10-5-EXCLUSION-060801",
        chapter_code="06",
        rule_type="exclusion",
        trigger_item_code="060801",
        source_note_id=n_id,
        condition_dict={
            "excluded_from_parents": ["061001", "061002", "061003", "061004", "061005", "061006"],
            "excluded_item_codes": ["060801"],
            "condition_text": "به ردیف‌های 061001 تا 061006، اضافه‌بهای 060801 تعلق نمی‌گیرد",
        },
        formula_type="exclusion_flag",
        excel_formula_template="=IF(AND(PARENT_CODE>=\"061001\", PARENT_CODE<=\"061006\"), 0, QTY)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=51,
        source_page_pdf=54,
        source_clause_ref="فصل 06 - الزامات گروه 10 - بند 10-5 (ص 51 بولتن / ص 54 PDF)",
    )

    # --- فصل ۰۷ ---
    n_id, n_raw = _find_note("07", None, "5")
    _add_rule(
        rule_code="R-07-GEN-5-WELDED-SPLICE-200PCT",
        chapter_code="07",
        rule_type="clause_derived_item",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "reference_codes": ["090801", "090802", "090803", "090804", "090805"],
            "multiplier": 2.0,
            "condition_text": "هزینه جوشکاری میلگردها (وصله پوششی جوشی) بر اساس ردیف‌های 090801 تا 090805 فصل کارهای فولادی سنگین با اعمال ضریب 2 منظور می‌شود",
            "clause_2_3_applies": True,
        },
        formula_type="multiplier_on_base_unit_price",
        excel_formula_template="=ROUND(BASE_UNIT_PRICE * 2.0, 0)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=56,
        source_page_pdf=59,
        source_clause_ref="فصل 07 - الزامات عمومی - بند 5 (ص 56 بولتن / ص 59 PDF)",
    )

    # --- فصل ۰۸ ---
    n_id, n_raw = _find_note("08", None, "6")
    _add_rule(
        rule_code="R-08-GEN-6-VOID-DEDUCTION-005M3",
        chapter_code="08",
        rule_type="measurement_constraint",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "max_unsubtracted_void_volume_m3": 0.05,
            "condition_text": "حجم حفره‌های تعبیه شده در بتن، که حجم هر یک از آن‌ها 0.05 مترمکعب یا کمتر باشد، در اندازه‌گیری از حجم بتن کسر نمی‌گردد",
        },
        formula_type="void_volume_threshold",
        excel_formula_template="=IF(SINGLE_VOID_M3<=0.05, 0, -VOID_COUNT*SINGLE_VOID_M3)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=61,
        source_page_pdf=64,
        source_clause_ref="فصل 08 - الزامات عمومی - بند 6 (ص 61 بولتن / ص 64 PDF)",
    )

    n_id, n_raw = _find_note("08", "01", "1-1")
    _add_rule(
        rule_code="R-08-G1-1-1-FC-CEMENT-EQUIV",
        chapter_code="08",
        rule_type="calculation_rule",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "formula_text_simple": "fc = (w / 10) - 9  <=>  w = 10 * (fc + 9)",
            "variables": {
                "fc": "مقاومت فشاری مشخصه بتن بر حسب مگاپاسکال (MPa)",
                "w": "عیار سیمان بر حسب کیلوگرم در مترمکعب بتن",
            },
            "condition_text": "در صورتی که با پیشنهاد مهندس مشاور و تصویب کارفرما در مرحله تهیه برآورد، ملاک تعیین قیمت، عیار سیمان مصرفی باشد، بهای ردیف کارهای بتنی بر اساس مقاومت متناظر با عیار سیمان مصرفی از رابطه تطبیقی fc = (w/10) - 9 محاسبه می‌شود",
        },
        formula_type="cement_to_fc_equivalence",
        excel_formula_template="=(CEMENT_KG_M3 / 10) - 9",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=61,
        source_page_pdf=64,
        source_clause_ref="فصل 08 - الزامات گروه 01 - بند 1-1 (ص 61 بولتن / ص 64 PDF)",
    )

    n_id, n_raw = _find_note("08", "03", "3-2")
    _add_rule(
        rule_code="R-08-G3-3-2-SLOPED-CONCRETE-150",
        chapter_code="08",
        rule_type="clause_derived_item",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "reference_codes": ["080301", "080302"],
            "min_slope_pct": 20.0,
            "multiplier": 1.50,
            "excluded_elements": ["ستون شیب‌دار", "دیوار شیب‌دار"],
            "condition_text": "در صورت بتن‌ریزی تیر، شناژ و سقف با شیب بیش از 20 درصد نسبت به افق، حسب مورد، بهای ردیف‌های 080301 یا 080302 با اعمال ضریب 1.50 منظور می‌شود. بابت بتن‌ریزی ستون و دیوار شیب‌دار، هیچ‌گونه هزینه جداگانه‌ای تعلق نمی‌گیرد",
            "clause_2_3_applies": True,
        },
        formula_type="multiplier_on_base_unit_price",
        excel_formula_template="=IF(SLOPE_PCT>20, ROUND(BASE_UNIT_PRICE * 1.50, 0), BASE_UNIT_PRICE)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=62,
        source_page_pdf=65,
        source_clause_ref="فصل 08 - الزامات گروه 03 - بند 3-2 (ص 62 بولتن / ص 65 PDF)",
    )

    # تصمیم D2: بند استثنا 3-5 فصل ۸
    n_id, n_raw = _find_note("08", "03", "3-5")
    _add_rule(
        rule_code="R-08-G3-3-5-EXCLUSION-JOIST-BLOCK-SLAB",
        chapter_code="08",
        rule_type="exclusion",
        trigger_item_code="080302",
        source_note_id=n_id,
        condition_dict={
            "excluded_item_codes": ["080301", "080302", "080304", "080310", "080311"],
            "excluded_target_topic": "سقف‌های تیرچه بلوک",
            "condition_text": "اضافه‌بهای ردیف‌های 080301، 080302، 080304، 080310 و 080311 به سقف‌های تیرچه بلوک تعلق نمی‌گیرد",
        },
        formula_type="exclusion_flag",
        excel_formula_template="=IF(IS_JOIST_BLOCK_SLAB=1, 0, QTY)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=62,
        source_page_pdf=65,
        source_clause_ref="فصل 08 - الزامات گروه 03 - بند 3-5 (ص 62 بولتن / ص 65 PDF)",
    )

    n_id, n_raw = _find_note("08", "04", "4-1")
    _add_rule(
        rule_code="R-08-G4-4-1-EXCLUSION-EXTRA-CEMENT-IN-FC-MODE",
        chapter_code="08",
        rule_type="exclusion",
        trigger_item_code="080401",
        source_note_id=n_id,
        condition_dict={
            "excluded_item_codes": ["080401"],
            "condition_text": "چنانچه ملاک عمل تعیین هزینه تهیه و اجرای بتن بر مبنای مقاومت فشاری مشخصه بتن باشد، هیچ‌گونه اضافه‌بهایی بابت مصرف سیمان اضافی جهت حصول مقاومت مشخصه طرح، رعایت حداقل مقدار سیمان قید شده در مشخصات فنی و نظایر آن تعلق نمی‌گیرد",
        },
        formula_type="exclusion_flag",
        excel_formula_template="=IF(PRICING_BASIS=\"fc\", 0, EXTRA_CEMENT_KG)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=62,
        source_page_pdf=65,
        source_clause_ref="فصل 08 - الزامات گروه 04 - بند 4-1 (ص 62 بولتن / ص 65 PDF)",
    )

    # --- فصل ۲۸ (حمل و نقل — تصمیم D6) ---
    n_id, n_raw = _find_note("28", None, "4")
    _add_rule(
        rule_code="R-28-GEN-4-DIRT-ROAD-HAUL-130",
        chapter_code="28",
        rule_type="clause_derived_item",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "reference_codes": ["280101", "280102", "280103", "280104"],
            "multiplier": 1.30,
            "condition_text": "بهای ردیف‌های حمل در راه‌های خاکی یا شنی، با اعمال ضریب 1.3 نسبت به ردیف‌های حمل در راه آسفالتی محاسبه می‌شود",
            "clause_2_3_applies": True,
        },
        formula_type="multiplier_on_base_unit_price",
        excel_formula_template="=ROUND(BASE_UNIT_PRICE * 1.30, 0)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=228,
        source_page_pdf=231,
        source_clause_ref="فصل 28 - الزامات عمومی - بند 4 (ص 228 بولتن / ص 231 PDF)",
    )

    n_id, n_raw = _find_note("28", "01", "1-2-2")
    _add_rule(
        rule_code="R-28-G1-1-2-2-BULK-CEMENT-HAUL-115",
        chapter_code="28",
        rule_type="clause_derived_item",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "reference_codes": ["280101", "280102"],
            "multiplier": 1.15,
            "deduct_first_km": 30.0,
            "condition_text": "بهای حمل سیمان فله، با اعمال ضریب 1.15 به بهای ردیف‌های مربوط، برای تمام طول مسیر، پس از کسر 30 کیلومتر منظور می‌شود",
            "clause_2_3_applies": True,
        },
        formula_type="multiplier_on_base_unit_price",
        excel_formula_template="=ROUND(BASE_UNIT_PRICE * 1.15, 0)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=228,
        source_page_pdf=231,
        source_clause_ref="فصل 28 - الزامات گروه 01 - بند 1-2-2 (ص 228 بولتن / ص 231 PDF)",
    )

    n_id, n_raw = _find_note("28", "01", "1-2-3")
    _add_rule(
        rule_code="R-28-G1-1-2-3-CEMENT-WASTE-6PCT",
        chapter_code="28",
        rule_type="calculation_rule",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "waste_allowance_pct": 6.0,
            "multiplier": 1.06,
            "applies_to_clauses": ["1-2-3", "1-2-4", "1-2-5", "1-2-6", "1-2-7", "1-2-8", "1-2-9", "1-2-10"],
            "condition_text": "مقدار سیمان برای تهیه بتن و ملات‌ها بر اساس عیار سیمان به اضافه 6 درصد اتلاف سیمان محاسبه می‌شود",
        },
        formula_type="cement_tonnage_with_6pct_waste",
        excel_formula_template="=(VOL_M3 * CEMENT_KG_M3 * 1.06) / 1000",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=228,
        source_page_pdf=231,
        source_clause_ref="فصل 28 - الزامات گروه 01 - بندهای 1-2-3 تا 1-2-10 (ص 228 و 229 بولتن / ص 231 و 232 PDF)",
    )

    n_id, n_raw = _find_note("28", "01", "1-4-3")
    _add_rule(
        rule_code="R-28-G1-1-4-3-GALVANIZED-COLD-ROLLED-STEEL-120",
        chapter_code="28",
        rule_type="clause_derived_item",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "reference_codes": ["280101", "280102"],
            "multiplier": 1.20,
            "condition_text": "حمل مصالح و قطعات فولادی سرد نورد شده گالوانیزه، حسب مورد از ردیف‌های این فصل با اعمال ضریب 1.20 منظور می‌شود",
            "clause_2_3_applies": True,
        },
        formula_type="multiplier_on_base_unit_price",
        excel_formula_template="=ROUND(BASE_UNIT_PRICE * 1.20, 0)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=230,
        source_page_pdf=233,
        source_clause_ref="فصل 28 - الزامات گروه 01 - بند 1-4-3 (ص 230 بولتن / ص 233 PDF)",
    )

    n_id, n_raw = _find_note("28", "01", "1-4-5")
    _add_rule(
        rule_code="R-28-G1-1-4-5-FACTORY-STEEL-STRUCTURES-200",
        chapter_code="28",
        rule_type="clause_derived_item",
        trigger_item_code=None,
        source_note_id=n_id,
        condition_dict={
            "reference_codes": ["280101", "280102", "280301", "280501"],
            "multiplier": 2.0,
            "deduct_first_30km": False,
            "condition_text": "هزینه بارگیری، حمل و باراندازی قطعات ساخته شده از محل کارخانه، حسب مورد از ردیف مربوط (بدون کسر 30 کیلومتر اول) با اعمال ضریب 2 محاسبه می‌شود",
            "clause_2_3_applies": True,
        },
        formula_type="multiplier_on_base_unit_price",
        excel_formula_template="=ROUND(BASE_UNIT_PRICE * 2.0, 0)",
        is_ambiguous=0,
        status="active",
        source_text_raw=n_raw,
        source_page_bulletin=230,
        source_page_pdf=233,
        source_clause_ref="فصل 28 - الزامات گروه 01 - بند 1-4-5 (ص 230 بولتن / ص 233 PDF)",
    )

    # =========================================================================
    # ج) قوانین مربوط به ۶۵ ردیف additional/deduction در فصول برش (01, 02, 03, 05, 06, 07, 08, 28)
    #    با رعایت کامل D3 و D4
    # =========================================================================
    slice_non_base = [
        it
        for it in items_list
        if it["chapter_code"] in STAGE3_SLICE_CHAPTERS
        and it["item_category"] in ("additional", "deduction")
    ]
    slice_non_base.sort(key=lambda x: x["code_official"])

    for it in slice_non_base:
        code = it["code_official"]
        ch = it["chapter_code"]
        cat = it["item_category"]
        desc = it["description"]
        p_bul = it["source_page_bulletin"]
        p_pdf = it["source_page_pdf"]
        r_type = "additional_item" if cat == "additional" else "deduction_item"
        c_ref = f"ردیف {code} (ص {p_bul} بولتن / ص {p_pdf} PDF)"

        # ۱. تصمیم D3: ردیف 030601 مبهم است (بدون فرمول محاسبه، is_ambiguous=1, status='pending_user_decision')
        if code == "030601":
            _add_rule(
                rule_code=f"R-ITEM-{code}",
                chapter_code=ch,
                rule_type=r_type,
                trigger_item_code=code,
                source_note_id=None,
                condition_dict={
                    "parent_codes": ["030501", "030502", "030503", "030504"],
                    "apparent_condition_in_text": "هرگاه عمق کندن زمین تا 2 متر باشد",
                    "related_clause": "بند 6-1 فصل 3 (ص 27 بولتن) و ردیف 030603 (ص 32 بولتن)",
                    "ambiguity_summary": "ابهام بین تفسیر «کل عمق گود <= 2 متر» و تفسیر «لایه‌ای (0 تا 2 متر در هر گود)»",
                },
                formula_type="pending_user_decision",
                excel_formula_template="",
                is_ambiguous=1,
                status="pending_user_decision",
                source_text_raw=desc,
                source_page_bulletin=p_bul,
                source_page_pdf=p_pdf,
                source_clause_ref=c_ref,
            )
            continue

        # ۲. تصمیم D6: ردیف 031203 دارای واحد «درصد» در این برش است (در بخش د همراه با ۷ ردیف دیگر با ابهام ثبت می‌شود)
        if code == "031203":
            continue

        # ۳. تصمیم D4: شرط پله‌ای عمق/طول (020201، 020304، 030603، 031204) -> فقط داده ساخت‌یافته عیناً از متن در condition_json
        if code == "020201":
            _add_rule(
                rule_code=f"R-ITEM-{code}",
                chapter_code=ch,
                rule_type=r_type,
                trigger_item_code=code,
                source_note_id=None,
                condition_dict={
                    "parameter": "depth_m",
                    "unit": "متر",
                    "parent_codes": ["020102", "020104"],
                    "threshold_min": 2.0,
                    "step_size": 2.0,
                    "cap_max": 10.0,
                    "steps": [
                        {"lower_m": 2.0, "upper_m": 4.0, "unit": "متر", "repeat_multiplier": 1},
                        {"lower_m": 4.0, "upper_m": 6.0, "unit": "متر", "repeat_multiplier": 2},
                        {"lower_m": 6.0, "upper_m": 8.0, "unit": "متر", "repeat_multiplier": 3},
                        {"lower_m": 8.0, "upper_m": 10.0, "unit": "متر", "repeat_multiplier": 4},
                    ],
                },
                formula_type="stepped_depth_data",
                excel_formula_template="STEPPED_DATA_ONLY_STAGE3",
                is_ambiguous=0,
                status="active",
                source_text_raw=desc,
                source_page_bulletin=p_bul,
                source_page_pdf=p_pdf,
                source_clause_ref=c_ref,
            )
            continue

        if code == "020304":
            _add_rule(
                rule_code=f"R-ITEM-{code}",
                chapter_code=ch,
                rule_type=r_type,
                trigger_item_code=code,
                source_note_id=None,
                condition_dict={
                    "parameter": "well_depth_m",
                    "unit": "متر",
                    "parent_codes": ["020301", "020303"],
                    "threshold_min": 20.0,
                    "step_size": 5.0,
                    "cap_max": None,
                    "steps": [
                        {"lower_m": 20.0, "upper_m": 25.0, "unit": "متر", "repeat_multiplier": 1},
                        {"lower_m": 25.0, "upper_m": 30.0, "unit": "متر", "repeat_multiplier": 2},
                        {"lower_m": 30.0, "upper_m": 35.0, "unit": "متر", "repeat_multiplier": 3},
                    ],
                    "continuation_in_text": "و به همین ترتیب برای عمق‌های بیشتر",
                },
                formula_type="stepped_depth_data",
                excel_formula_template="STEPPED_DATA_ONLY_STAGE3",
                is_ambiguous=0,
                status="active",
                source_text_raw=desc,
                source_page_bulletin=p_bul,
                source_page_pdf=p_pdf,
                source_clause_ref=c_ref,
            )
            continue

        if code == "030603":
            _add_rule(
                rule_code=f"R-ITEM-{code}",
                chapter_code=ch,
                rule_type=r_type,
                trigger_item_code=code,
                source_note_id=None,
                condition_dict={
                    "parameter": "depth_m",
                    "unit": "متر",
                    "parent_codes": ["030501", "030502", "030503", "030504"],
                    "threshold_min": 3.0,
                    "step_size": 1.0,
                    "cap_max": None,
                    "steps": [
                        {"lower_m": 3.0, "upper_m": 4.0, "unit": "متر", "repeat_multiplier": 1},
                        {"lower_m": 4.0, "upper_m": 5.0, "unit": "متر", "repeat_multiplier": 2},
                        {"lower_m": 5.0, "upper_m": 6.0, "unit": "متر", "repeat_multiplier": 3},
                    ],
                    "continuation_in_text": "و به همین ترتیب برای عمق های بیشتر",
                },
                formula_type="stepped_depth_data",
                excel_formula_template="STEPPED_DATA_ONLY_STAGE3",
                is_ambiguous=0,
                status="active",
                source_text_raw=desc,
                source_page_bulletin=p_bul,
                source_page_pdf=p_pdf,
                source_clause_ref=c_ref,
            )
            continue

        if code == "031204":
            _add_rule(
                rule_code=f"R-ITEM-{code}",
                chapter_code=ch,
                rule_type=r_type,
                trigger_item_code=code,
                source_note_id=None,
                condition_dict={
                    "parameter": "borehole_length_m",
                    "unit": "متر",
                    "parent_codes": ["031201", "031202"],
                    "threshold_min": 15.0,
                    "step_size": 3.0,
                    "cap_max": 30.0,
                    "steps": [
                        {"lower_m": 15.0, "upper_m": 18.0, "unit": "متر", "repeat_multiplier": 1},
                        {"lower_m": 18.0, "upper_m": 21.0, "unit": "متر", "repeat_multiplier": 2},
                        {"lower_m": 21.0, "upper_m": 24.0, "unit": "متر", "repeat_multiplier": 3},
                        {"lower_m": 24.0, "upper_m": 27.0, "unit": "متر", "repeat_multiplier": 4},
                        {"lower_m": 27.0, "upper_m": 30.0, "unit": "متر", "repeat_multiplier": 5},
                    ],
                },
                formula_type="stepped_depth_data",
                excel_formula_template="STEPPED_DATA_ONLY_STAGE3",
                is_ambiguous=0,
                status="active",
                source_text_raw=desc,
                source_page_bulletin=p_bul,
                source_page_pdf=p_pdf,
                source_clause_ref=c_ref,
            )
            continue

        # ۴. سایر ردیف‌های additional/deduction در فصول برش
        codes_in_desc = re.findall(r"\b\d{6}\b", desc)
        is_unresolved_parent = len(codes_in_desc) == 0

        # تشخیص پارامترهای عددی صریح در شرح ردیف (مانند مازاد بر X تا Y)
        cond_payload: Dict[str, Any] = {
            "explicit_parent_codes": codes_in_desc,
            "parent_resolution": "unresolved_topic" if is_unresolved_parent else "resolved_explicit_code",
            "description_text": desc,
        }
        f_type = "direct_condition_link"
        f_tmpl = "=BASE_QTY"

        m_excess_cap = re.search(
            r"مازاد\s+بر\s*(\d+(?:\.\d+)?)\s*(سانتی‌متر|متر|لیتر|رشته).*?تا\s*(?:حداکثر\s*)?(\d+(?:\.\d+)?)",
            desc,
        )
        m_excess_only = re.search(
            r"(?:مازاد\s+بر|بیش\s+از|نسبت\s+به\s+مازاد)\s*(\d+(?:\.\d+)?)\s*(سانتی‌متر|متر|لیتر)",
            desc,
        )
        m_groove_cap = re.search(r"تا\s+سطح\s+مقطع\s+حداکثر\s*(\d+)", desc)

        if code in ("010209", "010212") or m_groove_cap:
            base_cm2 = 20.0 if code == "010209" else 36.0
            cond_payload.update(
                {
                    "parameter": "cross_section_cm2",
                    "base_threshold_cm2": base_cm2,
                    "max_cap_cm2": 100.0,
                    "unit_step": 1.0,
                }
            )
            f_type = "excess_with_cap_multiplier"
            f_tmpl = f"=IF(AREA_CM2>{base_cm2}, (MIN(AREA_CM2, 100) - {base_cm2}) * BASE_QTY, 0)"
        elif code == "020402":
            cond_payload.update(
                {
                    "parameter": "haul_distance_m",
                    "base_included_m": 20.0,
                    "step_m": 20.0,
                    "max_cap_m": 100.0,
                    "proportional_fraction": True,
                }
            )
            f_type = "excess_step_proportional"
            f_tmpl = "=IF(DIST_M>20, ((MIN(DIST_M, 100) - 20) / 20) * BASE_QTY, 0)"
        elif m_excess_cap:
            th_min = float(m_excess_cap.group(1))
            u_name = m_excess_cap.group(2)
            th_max = float(m_excess_cap.group(3))
            cond_payload.update(
                {
                    "parameter": f"param_{u_name}",
                    "threshold_min": th_min,
                    "cap_max": th_max,
                    "proportional_fraction": "کسر" in desc and "به تناسب" in desc,
                }
            )
            f_type = "excess_with_cap_multiplier"
            f_tmpl = f"=IF(PARAM_VAL>{th_min}, (MIN(PARAM_VAL, {th_max}) - {th_min}) * BASE_QTY, 0)"
        elif m_excess_only:
            th_min = float(m_excess_only.group(1))
            u_name = m_excess_only.group(2)
            cond_payload.update(
                {
                    "parameter": f"param_{u_name}",
                    "threshold_min": th_min,
                    "cap_max": None,
                    "proportional_fraction": "کسر" in desc and "به تناسب" in desc,
                }
            )
            f_type = "excess_unbounded_multiplier"
            f_tmpl = f"=IF(PARAM_VAL>{th_min}, (PARAM_VAL - {th_min}) * BASE_QTY, 0)"

        _add_rule(
            rule_code=f"R-ITEM-{code}",
            chapter_code=ch,
            rule_type=r_type,
            trigger_item_code=code,
            source_note_id=None,
            condition_dict=cond_payload,
            formula_type=f_type,
            excel_formula_template=f_tmpl,
            is_ambiguous=0,
            status="active",
            source_text_raw=desc,
            source_page_bulletin=p_bul,
            source_page_pdf=p_pdf,
            source_clause_ref=c_ref,
        )

    # =========================================================================
    # د) ۸ ردیف دارای واحد «درصد» در جدول items (تصمیم D6: ثبت در ambiguous_rules.md و rules با is_ambiguous=1)
    # =========================================================================
    pct_items = [it for it in items_list if it["is_percent_unit"] == 1]
    pct_items.sort(key=lambda x: x["code_official"])
    for it in pct_items:
        code = it["code_official"]
        ch = it["chapter_code"]
        cat = it["item_category"]
        desc = it["description"]
        p_bul = it["source_page_bulletin"]
        p_pdf = it["source_page_pdf"]
        codes_in_desc = re.findall(r"\b\d{6}\b", desc)
        _add_rule(
            rule_code=f"R-PCT-ITEM-{code}",
            chapter_code=ch,
            rule_type="percent_unit_item",
            trigger_item_code=code,
            source_note_id=None,
            condition_dict={
                "item_code": code,
                "item_category": cat,
                "unit": it["unit"],
                "unit_price_value_in_excel": it["unit_price_rial"],
                "explicit_parent_codes": codes_in_desc,
                "question_for_user": "مبنای محاسبه درصد چیست؟ (آیا درصد روی بهای واحد ردیف‌های پایه اعمال می‌شود یا روی مبلغ کل فصل/گروه و چگونه با مقدار کارکرد ضرب می‌گردد؟)",
            },
            formula_type="pending_user_decision",
            excel_formula_template="",
            is_ambiguous=1,
            status="pending_user_decision",
            source_text_raw=desc,
            source_page_bulletin=p_bul,
            source_page_pdf=p_pdf,
            source_clause_ref=f"ردیف {code} (واحد درصد - ص {p_bul} بولتن / ص {p_pdf} PDF)",
        )

    return rules
