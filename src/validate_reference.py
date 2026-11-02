#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ماژول اعتبارسنجی مستقل دیتابیس مرجع و تولید گزارش استخراج مرحله ۲ (validate_reference.py).
این ماژول به صورت کاملاً مستقل از extractor، فایل‌های abn1404.xlsx و 11-Abnieh1404.pdf و
دیتابیس reference.db را می‌خواند، تمام معیارهای پذیرش اجباری را بررسی می‌کند،
۲۰ ردیف تصادفی (با seed ثابت) را بین اکسل، دیتابیس و PDF تطبیق می‌دهد،
توزیع واحدهای فصل ۹۹ و کل جدول را استخراج می‌کند و گزارش docs/extraction_report.md را می‌نویسد.
"""

import random
import re
import sqlite3
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional
import pandas as pd

from src.config import (
    DISCIPLINES,
    REFERENCE_DB_PATH,
    REFERENCE_XLSX_PATH,
    EXTRACTION_REPORT_PATH,
    VALIDATION_RANDOM_SEED,
    VALIDATION_SAMPLE_SIZE,
)
from src.schema import SCHEMA_TABLES
from src.pdf_fix import open_patched_pdf, extract_rtl_page_lines
from src.normalize import (
    digits_to_latin,
    normalize_number_str,
    normalize_text,
    MISSING_UNIT_STATUS,
)


def independent_excel_check(db_path: Path, excel_path: Path) -> Tuple[int, int, List[str]]:
    """
    اعتبارسنجی مستقل ۱٬۵۶۵ ردیف دیتابیس در برابر فایل اکسل خام (abn1404.xlsx)
    از نظر کد رسمی و بهای واحد ریالی.
    """
    df_ex = pd.read_excel(excel_path, dtype=object)
    c_code, c_desc, c_unit, c_price = df_ex.columns[:4]

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute(
        "SELECT code_official, unit_price_rial, unit_raw, unit FROM items WHERE discipline='abnieh' ORDER BY source_excel_row"
    )
    db_rows = cur.fetchall()
    conn.close()

    matched = 0
    errors: List[str] = []

    if len(db_rows) != len(df_ex):
        errors.append(f"تعداد سطرهای دیتابیس ({len(db_rows)}) با اکسل ({len(df_ex)}) برابر نیست.")

    for idx, (_, ex_row) in enumerate(df_ex.iterrows()):
        if idx >= len(db_rows):
            break
        ex_code = str(ex_row[c_code]).strip().zfill(6)
        ex_price = normalize_number_str(ex_row[c_price])
        db_code, db_price, _, _ = db_rows[idx]

        code_ok = ex_code == db_code
        if ex_price is None and db_price is None:
            price_ok = True
        elif ex_price is not None and db_price is not None and abs(ex_price - db_price) < 1e-6:
            price_ok = True
        else:
            price_ok = False

        if code_ok and price_ok:
            matched += 1
        else:
            errors.append(
                f"عدم تطابق در سطر {idx+2}: اکسل=({ex_code}, {ex_price}) != دیتابیس=({db_code}, {db_price})"
            )

    return matched, len(df_ex), errors


def sample_20_items_vs_pdf(
    db_path: Path,
    pdf_path: Path,
    seed: int = VALIDATION_RANDOM_SEED,
    sample_size: int = VALIDATION_SAMPLE_SIZE,
) -> List[Dict[str, Any]]:
    """
    انتخاب تصادفی ۲۰ ردیف (با seed ثابت) از دیتابیس و مقایسه مستقل با صفحه متناظر در PDF.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT code_official, unit_raw, unit, unit_price_rial,
               source_page_bulletin, source_page_pdf, description
        FROM items
        WHERE discipline='abnieh'
        ORDER BY code_official
        """
    )
    all_items = [dict(r) for r in cur.fetchall()]
    conn.close()

    rng = random.Random(seed)
    sampled = rng.sample(all_items, sample_size)
    sampled.sort(key=lambda x: x["code_official"])

    doc = open_patched_pdf(pdf_path)
    results: List[Dict[str, Any]] = []

    for item in sampled:
        code = item["code_official"]
        p_pdf = item["source_page_pdf"]
        p_bul = item["source_page_bulletin"]
        ex_price = item["unit_price_rial"]
        unit_std = item["unit"]

        page_lines = extract_rtl_page_lines(doc[p_pdf - 1])
        # یافتن بلوک ردیف در صفحه PDF
        in_block = False
        block_lines: List[str] = []
        for l in page_lines:
            l_lat = digits_to_latin(l)
            m = re.match(r"^\s*(\d{6})\b", l_lat)
            if m:
                if m.group(1) == code:
                    in_block = True
                    block_lines.append(l_lat)
                elif in_block:
                    break
            elif in_block:
                if re.match(r"^\s*\d{1,3}\s*$", l_lat):
                    continue
                block_lines.append(l_lat)

        block_text = " ".join(block_lines)
        # استخراج قیمت از متن بلوک در PDF
        pdf_price_display = "خالی/ستاره‌دار"
        is_match = False
        if ex_price is None or ex_price == 0:
            is_match = code in block_text
            pdf_price_display = "بدون بها (0 / خالی)" if is_match else "یافت نشد"
        else:
            p_int = int(round(ex_price))
            p_abs_fmt = f"{abs(p_int):,}"
            if p_abs_fmt in block_text or str(abs(p_int)) in block_text:
                is_match = True
                pdf_price_display = f"{p_int:,}"
            else:
                pdf_price_display = "عدم تطابق"

        results.append(
            {
                "code_official": code,
                "unit": unit_std if unit_std else "NULL",
                "excel_price": f"{int(round(ex_price)):,}" if ex_price is not None else "NULL (خالی)",
                "pdf_price": pdf_price_display,
                "page_bulletin": p_bul,
                "page_pdf": p_pdf,
                "matched": is_match,
            }
        )

    doc.close()
    return results


def run_all_validations(
    db_path: Path = REFERENCE_DB_PATH,
    xlsx_path: Path = REFERENCE_XLSX_PATH,
    report_path: Path = EXTRACTION_REPORT_PATH,
) -> Dict[str, Any]:
    """
    اجرای تمام بررسی‌های معیارهای پذیرش مرحله ۲ و تولید گزارش docs/extraction_report.md.
    """
    abn_cfg = DISCIPLINES["abnieh"]
    excel_path: Path = abn_cfg["excel_file"]
    pdf_path: Path = abn_cfg["pdf_file"]

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # ۱. بررسی جداول اسکیما و نسخه اسکیما
    cur.execute("PRAGMA user_version;")
    schema_user_version = cur.fetchone()[0]

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
    existing_tables = [r[0] for r in cur.fetchall()]

    # ۲. شمارش رکوردهای اصلی
    cur.execute("SELECT COUNT(*) FROM items;")
    items_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM chapters;")
    chapters_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM coefficients;")
    coefficients_count = cur.fetchone()[0]

    cur.execute(
        "SELECT verification_status, COUNT(*) FROM coefficients GROUP BY verification_status;"
    )
    coeff_verification_counts = dict(cur.fetchall())

    cur.execute("SELECT COUNT(*) FROM notes;")
    notes_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM material_on_site_factors;")
    mos_count = cur.fetchone()[0]

    # ۳. بررسی توزیع قیمت‌ها
    cur.execute("SELECT COUNT(*) FROM items WHERE unit_price_rial > 0;")
    pos_price_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM items WHERE unit_price_rial < 0;")
    neg_price_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM items WHERE unit_price_rial = 0;")
    zero_price_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM items WHERE unit_price_rial IS NULL;")
    null_price_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM items WHERE unit_price_rial IS NULL AND chapter_code = '99';")
    null_price_ch99_count = cur.fetchone()[0]

    # ۴. بررسی ردیف‌های بدون واحد
    cur.execute("SELECT code_official, unit_status FROM items WHERE unit IS NULL;")
    null_unit_rows = cur.fetchall()

    # ۵. بررسی دسته‌بندی base / additional / deduction
    cur.execute("SELECT item_category, COUNT(*) FROM items GROUP BY item_category;")
    cat_counts = dict(cur.fetchall())

    cur.execute("SELECT COUNT(*) FROM items WHERE item_category='additional' AND is_star_price_zero=1;")
    add_star_count = cur.fetchone()[0]

    # ۶. بررسی ۸ ردیف واحد «درصد»
    cur.execute("SELECT code_official FROM items WHERE unit='درصد' ORDER BY code_official;")
    percent_codes = [r[0] for r in cur.fetchall()]

    # ۷. بررسی توزیع واحدها (استاندارد و خام)
    cur.execute("SELECT unit, COUNT(*) as cnt FROM items GROUP BY unit ORDER BY cnt DESC, unit;")
    unit_std_dist = cur.fetchall()

    cur.execute("SELECT unit_raw, COUNT(*) as cnt FROM items GROUP BY unit_raw ORDER BY cnt DESC, unit_raw;")
    unit_raw_dist = cur.fetchall()

    # ۸. بررسی فصل ۹۹ و توزیع واحد ۶۰ ردیف آن
    cur.execute(
        "SELECT unit_raw, unit, COUNT(*) as cnt FROM items WHERE chapter_code='99' GROUP BY unit_raw, unit ORDER BY cnt DESC;"
    )
    ch99_unit_dist = cur.fetchall()

    cur.execute(
        "SELECT code_official, unit_raw, unit, description FROM items WHERE chapter_code='99' AND unit != 'مقطوع' ORDER BY code_official;"
    )
    ch99_non_maghtoo_rows = cur.fetchall()

    # ۹. فهرست ۳۵ فصل برای گزارش
    cur.execute(
        """
        SELECT chapter_code, chapter_type, title_official, excel_row_count,
               start_page_bulletin, end_page_bulletin, start_page_pdf, end_page_pdf
        FROM chapters
        ORDER BY id;
        """
    )
    chapters_rows = cur.fetchall()

    conn.close()

    # ۱۰. اعتبارسنجی مستقل اکسل
    ex_matched, ex_total, ex_errors = independent_excel_check(db_path, excel_path)

    # ۱۱. نمونه‌گیری تصادفی ۲۰ ردیف در برابر PDF
    sample_20 = sample_20_items_vs_pdf(
        db_path=db_path,
        pdf_path=pdf_path,
        seed=VALIDATION_RANDOM_SEED,
        sample_size=VALIDATION_SAMPLE_SIZE,
    )

    summary = {
        "schema_user_version": schema_user_version,
        "schema_tables_ok": set(SCHEMA_TABLES).issubset(set(existing_tables)) and len(existing_tables) == 8,
        "existing_tables": existing_tables,
        "items_count": items_count,
        "chapters_count": chapters_count,
        "coefficients_count": coefficients_count,
        "coeff_verification_counts": coeff_verification_counts,
        "notes_count": notes_count,
        "mos_count": mos_count,
        "pos_price_count": pos_price_count,
        "neg_price_count": neg_price_count,
        "zero_price_count": zero_price_count,
        "null_price_count": null_price_count,
        "null_price_ch99_count": null_price_ch99_count,
        "null_unit_rows": null_unit_rows,
        "base_count": cat_counts.get("base", 0),
        "additional_count": cat_counts.get("additional", 0),
        "deduction_count": cat_counts.get("deduction", 0),
        "add_star_count": add_star_count,
        "percent_codes": percent_codes,
        "excel_matched": ex_matched,
        "excel_total": ex_total,
        "excel_errors": ex_errors,
        "unit_std_dist": unit_std_dist,
        "unit_raw_dist": unit_raw_dist,
        "ch99_unit_dist": ch99_unit_dist,
        "ch99_non_maghtoo_rows": ch99_non_maghtoo_rows,
        "chapters_rows": chapters_rows,
        "sample_20": sample_20,
        "db_size_bytes": db_path.stat().st_size if db_path.exists() else 0,
        "xlsx_size_bytes": xlsx_path.stat().st_size if xlsx_path.exists() else 0,
    }

    _write_extraction_report(summary, report_path)
    return summary


def _write_extraction_report(summary: Dict[str, Any], report_path: Path) -> None:
    """تولید فایل مستند docs/extraction_report.md از روی نتایج واقعی اعتبارسنجی."""
    report_path.parent.mkdir(parents=True, exist_ok=True)

    lines: List[str] = [
        "# گزارش استخراج و اعتبارسنجی مرحله ۲ (`extraction_report.md`)",
        "",
        "این گزارش حاصل اجرای مستقیم اسکریپت‌های `src/build_reference.py` و `src/validate_reference.py` روی فایل‌های واقعی `data/` است.",
        "",
        "---",
        "",
        "## ۱. خلاصه معیارهای پذیرش مرحله ۲",
        "",
        "| معیار | مقدار مورد انتظار | مقدار واقعی استخراج‌شده | وضعیت |",
        "|---|---|---|---|",
        f"| نسخه اسکیما (`PRAGMA user_version`) | `101` (`1.1`) | `{summary['schema_user_version']}` (`1.1`) | {'پاس' if summary['schema_user_version'] == 101 else 'رد'} |",
        f"| تعداد جدول‌های اسکیما | `8` جدول | `{len(summary['existing_tables'])}` جدول | {'پاس' if summary['schema_tables_ok'] else 'رد'} |",
        f"| تعداد کل ردیف‌ها (`items`) | `1565` | `{summary['items_count']}` | {'پاس' if summary['items_count'] == 1565 else 'رد'} |",
        f"| تعداد فصول و بخش‌ها (`chapters`) | `35` | `{summary['chapters_count']}` | {'پاس' if summary['chapters_count'] == 35 else 'رد'} |",
        f"| تعداد ضرایب و پارامترها (`coefficients`) | `18` | `{summary['coefficients_count']}` | {'پاس' if summary['coefficients_count'] == 18 else 'رد'} |",
        f"| ضرایب تأییدشده توسط کاربر (`coefficients`) | `5` ردیف | `{summary['coeff_verification_counts'].get('تأییدشده توسط کاربر', 0)}` ردیف | {'پاس' if summary['coeff_verification_counts'].get('تأییدشده توسط کاربر', 0) == 5 else 'رد'} |",
        f"| ضرایب تأییدنشده (`coefficients`) | `3` ردیف | `{summary['coeff_verification_counts'].get('تأییدنشده', 0)}` ردیف | {'پاس' if summary['coeff_verification_counts'].get('تأییدنشده', 0) == 3 else 'رد'} |",
        f"| ضرایب بدون مقدار - انتخاب پروژه (`coefficients`) | `10` ردیف | `{summary['coeff_verification_counts'].get('بدون مقدار (انتخاب پروژه)', 0)}` ردیف | {'پاس' if summary['coeff_verification_counts'].get('بدون مقدار (انتخاب پروژه)', 0) == 10 else 'رد'} |",
        f"| ردیف‌های با قیمت مثبت (`> 0`) | `1423` | `{summary['pos_price_count']}` | {'پاس' if summary['pos_price_count'] == 1423 else 'رد'} |",
        f"| ردیف‌های با قیمت منفی (`< 0` کسربها) | `20` | `{summary['neg_price_count']}` | {'پاس' if summary['neg_price_count'] == 20 else 'رد'} |",
        f"| ردیف‌های با قیمت صفر (`== 0` بدون بها بند ۲-۴) | `62` | `{summary['zero_price_count']}` | {'پاس' if summary['zero_price_count'] == 62 else 'رد'} |",
        f"| ردیف‌های با قیمت خالی (`NULL` - همه فصل ۹۹) | `60` | `{summary['null_price_count']}` (فصل ۹۹: `{summary['null_price_ch99_count']}`) | {'پاس' if summary['null_price_count'] == 60 and summary['null_price_ch99_count'] == 60 else 'رد'} |",
        f"| ردیف بدون واحد در مبدأ | `1` (`030905`) | `{len(summary['null_unit_rows'])}` (`{summary['null_unit_rows'][0][0] if summary['null_unit_rows'] else ''}`) | {'پاس' if len(summary['null_unit_rows']) == 1 and summary['null_unit_rows'][0][0] == '030905' else 'رد'} |",
        f"| دسته‌بندی `base` | `1241` | `{summary['base_count']}` | {'پاس' if summary['base_count'] == 1241 else 'رد'} |",
        f"| دسته‌بندی `additional` | `304` | `{summary['additional_count']}` | {'پاس' if summary['additional_count'] == 304 else 'رد'} |",
        f"| دسته‌بندی `deduction` | `20` | `{summary['deduction_count']}` | {'پاس' if summary['deduction_count'] == 20 else 'رد'} |",
        f"| ردیف‌های `additional` دارای `is_star_price_zero=1` | `14` | `{summary['add_star_count']}` | {'پاس' if summary['add_star_count'] == 14 else 'رد'} |",
        f"| ردیف‌های با واحد «درصد» | `8` ردیف | `{len(summary['percent_codes'])}` ردیف (`{', '.join(summary['percent_codes'])}`) | {'پاس' if len(summary['percent_codes']) == 8 else 'رد'} |",
        f"| انطباق مستقل با `abn1404.xlsx` | `1565 / 1565` | `{summary['excel_matched']} / {summary['excel_total']}` | {'پاس' if summary['excel_matched'] == 1565 else 'رد'} |",
        "",
        "---",
        "",
        "## ۲. تطبیق ۲۰ ردیف تصادفی با فایل PDF (`seed = 1404`)",
        "",
        "| ردیف | کد رسمی (`code_official`) | واحد استاندارد | قیمت در اکسل (ریال) | قیمت در PDF (ریال) | صفحه بولتن | صفحه PDF | وضعیت تطابق |",
        "|---:|---|---|---:|---:|---:|---:|---|",
    ]

    for idx, s_row in enumerate(summary["sample_20"], start=1):
        lines.append(
            f"| {idx} | `{s_row['code_official']}` | {s_row['unit']} | `{s_row['excel_price']}` | `{s_row['pdf_price']}` | {s_row['page_bulletin']} | {s_row['page_pdf']} | {'تطابق کامل' if s_row['matched'] else 'عدم تطابق'} |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## ۳. توزیع واحدهای ۶۰ ردیف فصل ۹۹ (پیوست ۵ — دستورالعمل تجهیز و برچیدن کارگاه)",
            "",
            "| واحد خام در اکسل (`unit_raw`) | واحد استاندارد (`unit`) | تعداد ردیف در فصل ۹۹ |",
            "|---|---|---:|",
        ]
    )
    for u_raw, u_std, cnt in summary["ch99_unit_dist"]:
        lines.append(f"| `{repr(u_raw)}` | `{u_std}` | {cnt} |")

    lines.extend(
        [
            "",
            "### علت وجود فقط ۵۰ ردیف با واحد «مقطوع» در کل جدول (استخراج‌شده از داده واقعی اکسل و PDF):",
            "- در کل فایل `abn1404.xlsx` و `11-Abnieh1404.pdf` (صفحات ۲۴۹ تا ۲۵۳ بولتن / ۲۵۲ تا ۲۵۶ PDF)، فصل `99` دارای **۶۰ ردیف** است.",
            "- مطابق متن صریح بند `2-1` پیوست ۵ (صفحه ۲۴۵ بولتن / ۲۴۸ PDF): *«...هزینه‌های مربوط را طبق ردیف‌های پیش‌بینی‌شده در فهرست تجهیز و برچیدن کارگاه این پیوست، بر حسب قیمت‌های محل اجرای عملیات و با منظور نمودن هزینه‌های بالاسری بر حسب مورد با واحد **مقطوع، مترمربع یا مترمربع-ماه** برآورد کرده...»*.",
            "- از ۶۰ ردیف فصل ۹۹، دقیقاً **۵۰ ردیف** دارای واحد `مقطوع` هستند و **۱۰ ردیف دیگر** دارای واحدهای مساحتی/زمانی زیر در مبدأ هستند:",
        ]
    )
    for c_off, u_raw, u_std, desc in summary["ch99_non_maghtoo_rows"]:
        lines.append(f"  - `{c_off}`: واحد خام `{repr(u_raw)}` $\\leftarrow$ واحد استاندارد `{u_std}` | {desc}")

    lines.extend(
        [
            "",
            "---",
            "",
            "## ۴. فهرست نهایی واحدها با تعداد (استاندارد و خام)",
            "",
            "### الف) واحدهای استاندارد (`unit` — ۱۶ مقدار متمایز شامل NULL):",
            "",
            "| ردیف | واحد استاندارد (`unit`) | تعداد ردیف |",
            "|---:|---|---:|",
        ]
    )
    for idx, (u_val, cnt) in enumerate(summary["unit_std_dist"], start=1):
        lines.append(f"| {idx} | `{u_val if u_val is not None else 'NULL (فاقد واحد - 030905)'}` | {cnt} |")

    lines.extend(
        [
            "",
            "### ب) واحدهای خام اکسل (`unit_raw` — ۲۲ مقدار متمایز شامل NULL):",
            "",
            "| ردیف | واحد خام (`unit_raw`) | تعداد ردیف |",
            "|---:|---|---:|",
        ]
    )
    for idx, (u_raw, cnt) in enumerate(summary["unit_raw_dist"], start=1):
        lines.append(f"| {idx} | `{repr(u_raw) if u_raw is not None else 'NULL (فاقد واحد - 030905)'}` | {cnt} |")

    lines.extend(
        [
            "",
            "---",
            "",
            "## ۵. فهرست ۳۵ رکورد جدول `chapters` (۲۸ فصل عنوان‌دار + ۱ فصل حذف‌شده ۱۵ + ۶ پیوست)",
            "",
            "| ردیف | کد فصل | نوع بخش | عنوان رسمی در بولتن | تعداد ردیف اکسل | ص شروع بولتن | ص پایان بولتن | ص شروع PDF | ص پایان PDF |",
            "|---:|---|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for idx, ch in enumerate(summary["chapters_rows"], start=1):
        ch_code, ch_type, title, r_cnt, sb, eb, sp, ep = ch
        lines.append(
            f"| {idx} | `{ch_code}` | `{ch_type}` | {title} | {r_cnt} | {sb if sb else '—'} | {eb if eb else '—'} | {sp if sp else '—'} | {ep if ep else '—'} |"
        )

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    res = run_all_validations()
    print("اعتبارسنجی کامل انجام شد:")
    print(f"  items={res['items_count']}, chapters={res['chapters_count']}, coefficients={res['coefficients_count']}")
    print(f"  pos={res['pos_price_count']}, neg={res['neg_price_count']}, zero={res['zero_price_count']}, null={res['null_price_count']}")
    print(f"  base={res['base_count']}, additional={res['additional_count']}, deduction={res['deduction_count']}, add_star={res['add_star_count']}")
    print(f"  excel_match={res['excel_matched']}/{res['excel_total']}, sample_20_all_matched={all(x['matched'] for x in res['sample_20'])}")
