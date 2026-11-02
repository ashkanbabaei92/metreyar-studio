#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
تست‌های خودکار مرحله ۲ با pytest (شامل بیش از ۶ تست مرحله ۲ + تست رگرسیون پچ ارقام PDF روی ص ۵ و ۱۲۰).
"""

import sqlite3
from pathlib import Path
import pytest

from src.config import (
    DISCIPLINES,
    REFERENCE_DB_PATH,
    REFERENCE_XLSX_PATH,
    EXTRACTION_REPORT_PATH,
    USER_APPROVALS_PATH,
    SCHEMA_USER_VERSION,
    VALIDATION_RANDOM_SEED,
)
from src.schema import SCHEMA_TABLES, ALLOWED_VERIFICATION_STATUSES
from src.pdf_fix import open_patched_pdf, extract_rtl_page_lines
from src.normalize import (
    digits_to_latin,
    unify_persian_chars,
    normalize_unit,
    normalize_item_code,
    MISSING_UNIT_STATUS,
)
from src.validate_reference import independent_excel_check, sample_20_items_vs_pdf


def test_pdf_tounicode_patch_regression_pages_5_and_120():
    """
    تست رگرسیون پچ ارقام در حافظه (/ToUnicode) روی صفحات ۵ و ۱۲۰ فایل 11-Abnieh1404.pdf
    که باید مقادیر 1.30، 1.20، 1.41، 1.14 در صفحه ۵ و 140714 و 1,900,000 در صفحه ۱۲۰ را بدهد.
    همچنین بررسی اصلاح B Lotus,Bold (الزامات گروه 1 در صفحه ۱۱) و ABCEEE+HM FLotoos (صفحه ۱۴۶).
    """
    pdf_path = DISCIPLINES["abnieh"]["pdf_file"]
    doc = open_patched_pdf(pdf_path)

    # بررسی صفحه ۵ PDF (صفحه ۲ بولتن - ضرایب بالاسری و جدول الف)
    p5_text = "\n".join(extract_rtl_page_lines(doc[4]))
    for expected_val in ["1.30", "1.20", "1.41", "1.14"]:
        assert expected_val in p5_text, f"مقدار {expected_val} در صفحه ۵ یافت نشد!"

    # بررسی صفحه ۱۲۰ PDF (صفحه ۱۱۷ بولتن - ردیف 140714 با قیمت 1,900,000)
    p120_text = "\n".join(extract_rtl_page_lines(doc[119]))
    assert "140714" in p120_text, "کد 140714 در صفحه ۱۲۰ یافت نشد!"
    assert "1,900,000" in p120_text, "بهای 1,900,000 در صفحه ۱۲۰ یافت نشد!"

    # بررسی B Lotus,Bold (صفحه ۱۱ PDF: «الزامات گروه 1» نه «الزامات گروه 4»)
    p11_text = "\n".join(extract_rtl_page_lines(doc[10]))
    assert "الزامات گروه 1" in p11_text

    # بررسی زیرفونت ABCEEE+HM FLotoos (صفحه ۱۴۶ PDF: نسبت ملات 1:4 و ضخامت 2 تا 3)
    p146_text = "\n".join(extract_rtl_page_lines(doc[145]))
    assert "1:4" in p146_text
    assert "1,182,000" in p146_text

    doc.close()


def test_schema_8_tables_and_record_counts():
    """
    بررسی وجود هر ۸ جدول اسکیمای مرحله ۱ در reference.db و شمارش دقیق رکوردها:
    items=1565, chapters=35, coefficients=18.
    """
    assert REFERENCE_DB_PATH.exists(), "فایل reference.db ساخته نشده است!"
    assert REFERENCE_XLSX_PATH.exists(), "فایل reference.xlsx ساخته نشده است!"
    assert EXTRACTION_REPORT_PATH.exists(), "فایل extraction_report.md ساخته نشده است!"

    conn = sqlite3.connect(str(REFERENCE_DB_PATH))
    cur = conn.cursor()

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
    tables = {r[0] for r in cur.fetchall()}
    assert set(SCHEMA_TABLES) == tables

    # بررسی ستون‌های جدول items (از جمله discipline و unit_raw)
    cur.execute("PRAGMA table_info(items);")
    item_cols = {r[1] for r in cur.fetchall()}
    assert "discipline" in item_cols
    assert "unit_raw" in item_cols
    assert "code_official" in item_cols
    assert "code_internal" in item_cols

    # بررسی اینکه فقط رشته ابنیه وارد دیتابیس شده است
    cur.execute("SELECT DISTINCT discipline FROM items;")
    disciplines_in_db = [r[0] for r in cur.fetchall()]
    assert disciplines_in_db == ["abnieh"]

    cur.execute("SELECT COUNT(*) FROM items;")
    assert cur.fetchone()[0] == 1565

    cur.execute("SELECT COUNT(*) FROM chapters;")
    assert cur.fetchone()[0] == 35

    cur.execute("SELECT COUNT(*) FROM coefficients;")
    assert cur.fetchone()[0] == 18

    # بررسی اینکه تمام ضرایب در ستون default_in_template مقدار NULL دارند (تصمیم ۴ کارفرما)
    cur.execute("SELECT COUNT(*) FROM coefficients WHERE default_in_template IS NOT NULL;")
    assert cur.fetchone()[0] == 0

    conn.close()


def test_price_distribution_and_missing_unit():
    """
    بررسی توزیع قیمت‌ها در جدول items:
    ۱۴۲۳ مثبت، ۲۰ منفی، ۶۲ صفر، ۶۰ خالی (همه در فصل ۹۹)، و ۱ ردیف بدون واحد (030905).
    """
    conn = sqlite3.connect(str(REFERENCE_DB_PATH))
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM items WHERE unit_price_rial > 0;")
    assert cur.fetchone()[0] == 1423

    cur.execute("SELECT COUNT(*) FROM items WHERE unit_price_rial < 0;")
    assert cur.fetchone()[0] == 20

    cur.execute("SELECT COUNT(*) FROM items WHERE unit_price_rial = 0;")
    assert cur.fetchone()[0] == 62

    cur.execute("SELECT COUNT(*) FROM items WHERE unit_price_rial IS NULL;")
    assert cur.fetchone()[0] == 60

    cur.execute("SELECT COUNT(*) FROM items WHERE unit_price_rial IS NULL AND chapter_code != '99';")
    assert cur.fetchone()[0] == 0

    cur.execute("SELECT code_official, unit_raw, unit, unit_status FROM items WHERE unit IS NULL;")
    null_unit_rows = cur.fetchall()
    assert len(null_unit_rows) == 1
    assert null_unit_rows[0][0] == "030905"
    assert null_unit_rows[0][1] is None
    assert null_unit_rows[0][2] is None
    assert null_unit_rows[0][3] == MISSING_UNIT_STATUS

    conn.close()


def test_item_categories_and_star_flags():
    """
    بررسی دسته‌بندی ردیف‌ها:
    base=1241, additional=304, deduction=20
    و ۱۴ ردیف additional دارای is_star_price_zero=1
    و ۶۲ ردیف کل دارای is_star_price_zero=1.
    """
    conn = sqlite3.connect(str(REFERENCE_DB_PATH))
    cur = conn.cursor()

    cur.execute("SELECT item_category, COUNT(*) FROM items GROUP BY item_category;")
    cats = dict(cur.fetchall())
    assert cats == {"base": 1241, "additional": 304, "deduction": 20}

    cur.execute("SELECT COUNT(*) FROM items WHERE is_star_price_zero = 1;")
    assert cur.fetchone()[0] == 62

    cur.execute("SELECT COUNT(*) FROM items WHERE item_category = 'additional' AND is_star_price_zero = 1;")
    assert cur.fetchone()[0] == 14

    conn.close()


def test_unit_standardization_and_percent_items():
    """
    بررسی استانداردسازی واحدها طبق تصمیم ۵ کارفرما و ۸ ردیف واحد «درصد» طبق تصمیم ۷ کارفرما.
    """
    # تست مستقیم تابع normalize_unit روی ۶ مورد تصریح‌شده در تصمیم ۵
    cases = [
        ("متر مربع", "مترمربع"),
        ("متر طول", "مترطول"),
        ("کیلو گرم", "کیلوگرم"),
        ("دسی‌متر \u200fمکعب", "دسی‌مترمکعب"),
        ("تن -  کیلومتر ", "تن-کیلومتر"),
        ("مترمکعب -  کیلومتر\u200f", "مترمکعب-کیلومتر"),
    ]
    for raw_u, expected_std in cases:
        u_raw, u_std, status = normalize_unit(raw_u)
        assert u_raw == raw_u
        assert u_std == expected_std
        assert status == "valid"

    conn = sqlite3.connect(str(REFERENCE_DB_PATH))
    cur = conn.cursor()

    cur.execute("SELECT code_official FROM items WHERE unit = 'درصد' ORDER BY code_official;")
    pct_codes = [r[0] for r in cur.fetchall()]
    assert pct_codes == [
        "031203",
        "090805",
        "092801",
        "092802",
        "092803",
        "092804",
        "140717",
        "140718",
    ]

    # بررسی توزیع واحدهای فصل ۹۹ (۵۰ مقطوع، ۹ مترمربع، ۱ مترمربع-ماه)
    cur.execute("SELECT unit, COUNT(*) FROM items WHERE chapter_code='99' GROUP BY unit;")
    ch99_std = dict(cur.fetchall())
    assert ch99_std == {"مقطوع": 50, "مترمربع": 9, "مترمربع-ماه": 1}

    conn.close()


def test_independent_excel_and_pdf_sample_validation():
    """
    تست اعتبارسنجی مستقل ۱۵۶۵ از ۱۵۶۵ ردیف با abn1404.xlsx
    و تطبیق ۲۰ ردیف تصادفی (با seed=1404) با PDF.
    """
    abn_cfg = DISCIPLINES["abnieh"]
    matched, total, errors = independent_excel_check(REFERENCE_DB_PATH, abn_cfg["excel_file"])
    assert total == 1565
    assert matched == 1565
    assert errors == []

    sample_20 = sample_20_items_vs_pdf(
        db_path=REFERENCE_DB_PATH,
        pdf_path=abn_cfg["pdf_file"],
        seed=VALIDATION_RANDOM_SEED,
        sample_size=20,
    )
    assert len(sample_20) == 20
    assert all(r["matched"] for r in sample_20), f"عدم تطابق در نمونه ۲۰ تایی: {sample_20}"


def test_schema_v1_1_and_user_approvals_in_coefficients():
    """
    تست اسکیمای ۱٫۱ و ثبت تأییدهای کاربر در جدول coefficients:
    - نسخه اسکیما در PRAGMA user_version برابر 101 (معادل 1.1)
    - وجود ستون‌های verification_status، verified_by، verified_at، evidence_path
    - دقیقاً ۵ ردیف تأییدشده توسط کاربر با مقادیر مرجع (1.30، 1.20، 1.41، 1.30، 1.14)
    - هیچ ردیف دیگری «تأییدشده توسط کاربر» نیست (۳ ردیف «تأییدنشده» و ۱۰ ردیف «بدون مقدار (انتخاب پروژه)»)
    - تمام ۱۸ ردیف دارای default_in_template IS NULL و مقادیر پروژه (بالاسری، منطقه، طبقات، ضریب پیمان) دارای reference_value IS NULL هستند.
    """
    assert USER_APPROVALS_PATH.exists(), "فایل docs/user_approvals.json وجود ندارد!"

    conn = sqlite3.connect(str(REFERENCE_DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("PRAGMA user_version;")
    assert cur.fetchone()[0] == SCHEMA_USER_VERSION == 101

    cur.execute("PRAGMA table_info(coefficients);")
    coeff_cols = {r[1] for r in cur.fetchall()}
    for col in ("verification_status", "verified_by", "verified_at", "evidence_path"):
        assert col in coeff_cols, f"ستون {col} در جدول coefficients یافت نشد!"

    cur.execute("SELECT * FROM coefficients ORDER BY id;")
    rows = [dict(r) for r in cur.fetchall()]
    assert len(rows) == 18

    expected_approved = {
        "OVERHEAD_CIVIL_TENDER": 1.30,
        "OVERHEAD_CIVIL_NON_TENDER": 1.20,
        "OVERHEAD_NON_CIVIL_TENDER": 1.41,
        "OVERHEAD_NON_CIVIL_NON_TENDER": 1.30,
        "OVERHEAD_PURCHASE_ONLY": 1.14,
    }

    approved_rows = [r for r in rows if r["verification_status"] == "تأییدشده توسط کاربر"]
    unverified_rows = [r for r in rows if r["verification_status"] == "تأییدنشده"]
    project_null_rows = [r for r in rows if r["verification_status"] == "بدون مقدار (انتخاب پروژه)"]

    assert len(approved_rows) == 5
    assert len(unverified_rows) == 3
    assert len(project_null_rows) == 10

    for r in rows:
        assert r["verification_status"] in ALLOWED_VERIFICATION_STATUSES
        assert r["default_in_template"] is None
        ckey = r["coeff_key"]
        if ckey in expected_approved:
            assert r["verification_status"] == "تأییدشده توسط کاربر"
            assert abs(r["reference_value"] - expected_approved[ckey]) < 1e-6
            assert r["verified_by"] is not None and len(r["verified_by"]) > 0
            assert r["verified_at"] == "2026-11-02T16:28:14+00:00"
            assert r["evidence_path"] == "docs/evidence/abnieh_page_5_overhead.png"
        elif r["reference_value"] is not None:
            assert r["verification_status"] == "تأییدنشده"
            assert r["verified_by"] is None
            assert r["verified_at"] is None
            assert r["evidence_path"] is None
        else:
            assert r["verification_status"] == "بدون مقدار (انتخاب پروژه)"
            assert r["verified_by"] is None
            assert r["verified_at"] is None
            assert r["evidence_path"] is None

    # بررسی صریح اینکه ۴ ضریب اصلی پروژه در هر دو ستون reference_value و default_in_template مقدار NULL دارند
    project_main_keys = {"OVERHEAD_COEFF", "REGIONAL_COEFF", "FLOOR_COEFF", "CONTRACT_COEFF"}
    for r in rows:
        if r["coeff_key"] in project_main_keys:
            assert r["reference_value"] is None
            assert r["default_in_template"] is None
            assert r["verification_status"] == "بدون مقدار (انتخاب پروژه)"

    conn.close()

