#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
تعریف اسکیمای ۱٫۲ دیتابیس مرجع SQLite (schema.py)
منطبق با docs/ARCHITECTURE_STAGE1.md و تصمیم‌های تأییدشده کارفرما در مرحله ۲، گام الف و گام ج (مرحله ۳):
- ثبت نسخه اسکیما 1.2 در PRAGMA user_version (102)
- شامل ستون unit_raw در جدول items
- شامل ستون‌های verification_status، verified_by، verified_at، evidence_path در جدول coefficients (۱۹ ردیف شامل سقف ۴٪ تجهیز کارگاه)
- شامل ستون‌های text_raw، text_normalized، clause_ref، status در جدول notes
- شامل ستون‌های target_kind، target_text، resolution_status، source_file، source_page_pdf، source_clause_ref، clause_ref در جدول item_dependencies
- شامل ستون‌های status، clause_ref، source_text_raw در جدول rules
- شامل ۴ جدول مرجع جدید طبق تصمیم D7 کارفرما:
  mortar_table (جدول ۱-۱ کلیات)، overhead_table (جدول الف)،
  floor_factor_params (پیوست ۲)، region_factors (پیوست ۴)
"""

import sqlite3
from pathlib import Path
from typing import Union

from src.config import SCHEMA_USER_VERSION

SCHEMA_TABLES = [
    "chapters",
    "items",
    "notes",
    "rules",
    "item_dependencies",
    "coefficients",
    "adjustment_indices",
    "material_on_site_factors",
    "mortar_table",
    "overhead_table",
    "floor_factor_params",
    "region_factors",
]

ALLOWED_VERIFICATION_STATUSES = (
    "تأییدشده توسط کاربر",
    "تأییدنشده",
    "تأییدنشده؛ نیاز به بازبینی چشمی کاربر",
    "بدون مقدار (انتخاب پروژه)",
)

DDL_STATEMENTS = [
    # ۱. جدول فصول و بخش‌های فهرست بها
    """
    CREATE TABLE IF NOT EXISTS chapters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        discipline TEXT NOT NULL,
        year INTEGER NOT NULL DEFAULT 1404,
        chapter_code TEXT NOT NULL,
        chapter_type TEXT NOT NULL,
        title_official TEXT NOT NULL,
        is_active_phase1 INTEGER NOT NULL DEFAULT 0,
        excel_row_count INTEGER NOT NULL DEFAULT 0,
        start_page_bulletin INTEGER NULL,
        end_page_bulletin INTEGER NULL,
        start_page_pdf INTEGER NULL,
        end_page_pdf INTEGER NULL,
        source_file TEXT NOT NULL,
        notes_text TEXT NULL,
        UNIQUE(discipline, year, chapter_code)
    );
    """,
    # ۲. جدول ردیف‌های فهرست بها (شامل ستون unit_raw طبق تصمیم ۵ کارفرما)
    """
    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        discipline TEXT NOT NULL,
        year INTEGER NOT NULL DEFAULT 1404,
        code_official TEXT NOT NULL,
        code_internal TEXT NOT NULL,
        chapter_code TEXT NOT NULL,
        group_code TEXT NOT NULL,
        item_seq TEXT NOT NULL,
        description TEXT NOT NULL,
        unit_raw TEXT NULL,
        unit TEXT NULL,
        unit_status TEXT NOT NULL,
        unit_price_rial REAL NULL,
        item_category TEXT NOT NULL,
        is_star_price_zero INTEGER NOT NULL DEFAULT 0,
        is_percent_unit INTEGER NOT NULL DEFAULT 0,
        source_excel_file TEXT NOT NULL,
        source_excel_row INTEGER NOT NULL,
        source_pdf_file TEXT NULL,
        source_page_bulletin INTEGER NULL,
        source_page_pdf INTEGER NULL,
        UNIQUE(discipline, code_official)
    );
    """,
    # ۳. جدول کلیات، دستورالعمل کاربرد و الزامات فصول و گروه‌ها (اسکیمای ۱٫۲ با text_raw و text_normalized و status)
    """
    CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        discipline TEXT NOT NULL,
        year INTEGER NOT NULL DEFAULT 1404,
        section_type TEXT NOT NULL,
        chapter_code TEXT NULL,
        group_code TEXT NULL,
        clause_number TEXT NOT NULL,
        clause_ref TEXT NOT NULL,
        text_raw TEXT NOT NULL,
        text_normalized TEXT NOT NULL,
        clause_text TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        has_calculation_rule INTEGER NOT NULL DEFAULT 0,
        source_file TEXT NOT NULL,
        source_page_bulletin INTEGER NOT NULL,
        source_page_pdf INTEGER NOT NULL
    );
    """,
    # ۴. جدول قوانین محاسباتی، اضافه‌بها، کسربها، استثناها و اقلام مشتق از بندها (اسکیمای ۱٫۲)
    """
    CREATE TABLE IF NOT EXISTS rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_code TEXT NOT NULL UNIQUE,
        discipline TEXT NOT NULL,
        chapter_code TEXT NOT NULL,
        rule_type TEXT NOT NULL,
        trigger_item_code TEXT NULL,
        source_note_id INTEGER NULL,
        condition_json TEXT NOT NULL,
        formula_type TEXT NOT NULL,
        excel_formula_template TEXT NOT NULL,
        is_ambiguous INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'active',
        source_text_raw TEXT NULL,
        source_file TEXT NOT NULL,
        source_page_bulletin INTEGER NOT NULL,
        source_page_pdf INTEGER NOT NULL,
        source_clause_ref TEXT NOT NULL,
        clause_ref TEXT NOT NULL
    );
    """,
    # ۵. جدول نگاشت ارتباط ردیف‌های پایه و ردیف‌های وابسته/اضافه‌بها/کسربها (اسکیمای ۱٫۲ طبق تصمیم D1 و D2)
    """
    CREATE TABLE IF NOT EXISTS item_dependencies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        discipline TEXT NOT NULL,
        parent_code_official TEXT NULL,
        child_code_official TEXT NOT NULL,
        dependency_type TEXT NOT NULL,
        target_kind TEXT NOT NULL CHECK (
            target_kind IN ('item', 'range', 'list', 'text_topic')
        ),
        target_text TEXT NOT NULL,
        resolution_status TEXT NOT NULL CHECK (
            resolution_status IN ('resolved_explicit_code', 'unresolved_topic', 'ambiguous')
        ),
        rule_id INTEGER NULL,
        source_file TEXT NOT NULL,
        source_page_bulletin INTEGER NOT NULL,
        source_page_pdf INTEGER NOT NULL,
        source_clause_ref TEXT NOT NULL,
        clause_ref TEXT NOT NULL,
        description_note TEXT NULL,
        UNIQUE(discipline, parent_code_official, child_code_official, target_kind)
    );
    """,
    # ۶. جدول ضرایب پیمان، بالاسری، طبقات، منطقه‌ای، سقف تجهیز و کسورات روکش (اسکیمای ۱٫۲)
    """
    CREATE TABLE IF NOT EXISTS coefficients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        coeff_key TEXT NOT NULL UNIQUE,
        category TEXT NOT NULL,
        title_fa TEXT NOT NULL,
        reference_value REAL NULL,
        default_in_template REAL NULL,
        is_enabled_default INTEGER NOT NULL DEFAULT 0,
        formula_description TEXT NULL,
        source_file TEXT NULL,
        source_page_bulletin INTEGER NULL,
        source_clause TEXT NULL,
        verification_status TEXT NOT NULL CHECK (
            verification_status IN (
                'تأییدشده توسط کاربر',
                'تأییدنشده',
                'تأییدنشده؛ نیاز به بازبینی چشمی کاربر',
                'بدون مقدار (انتخاب پروژه)'
            )
        ),
        verified_by TEXT NULL,
        verified_at TEXT NULL,
        evidence_path TEXT NULL
    );
    """,
    # ۷. جدول شاخص‌های فصلی تعدیل آحاد بها (رزرو فاز ۲)
    """
    CREATE TABLE IF NOT EXISTS adjustment_indices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        discipline TEXT NOT NULL,
        chapter_code TEXT NOT NULL,
        year INTEGER NOT NULL,
        quarter INTEGER NOT NULL,
        index_value REAL NOT NULL,
        is_provisional INTEGER NOT NULL DEFAULT 0,
        circular_number TEXT NULL,
        circular_date TEXT NULL,
        source_file TEXT NOT NULL,
        source_page_pdf INTEGER NULL
    );
    """,
    # ۸. جدول ضرایب متوسط مصالح پای کار جدول ۱ پیوست ۱ (فاز ۲)
    """
    CREATE TABLE IF NOT EXISTS material_on_site_factors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        discipline TEXT NOT NULL,
        year INTEGER NOT NULL DEFAULT 1404,
        chapter_code TEXT NOT NULL,
        material_description TEXT NOT NULL,
        factor_percent REAL NOT NULL,
        source_file TEXT NOT NULL,
        source_page_bulletin INTEGER NOT NULL,
        source_page_pdf INTEGER NOT NULL
    );
    """,
    # ۹. جدول مرجع ۱-۱ کلیات: مقدار سیمان در ملات‌ها (تصمیم D7)
    """
    CREATE TABLE IF NOT EXISTS mortar_table (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mortar_code TEXT NOT NULL UNIQUE,
        discipline TEXT NOT NULL DEFAULT 'abnieh',
        year INTEGER NOT NULL DEFAULT 1404,
        block_no INTEGER NOT NULL,
        col_no INTEGER NOT NULL,
        mortar_title TEXT NOT NULL,
        mix_ratio_normalized TEXT NULL,
        mix_ratio_raw_pdf TEXT NULL,
        cement_kg_per_m3 REAL NOT NULL,
        source_file TEXT NOT NULL,
        source_page_bulletin INTEGER NOT NULL,
        source_page_pdf INTEGER NOT NULL,
        clause_ref TEXT NOT NULL,
        verification_status TEXT NOT NULL,
        evidence_path TEXT NOT NULL
    );
    """,
    # ۱۰. جدول مرجع الف دستورالعمل کاربرد: ضرایب بالاسری، سقف ستاره‌دار و سقف تجهیز کارگاه (تصمیم D7)
    """
    CREATE TABLE IF NOT EXISTS overhead_table (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        row_key TEXT NOT NULL UNIQUE,
        sector_fa TEXT NOT NULL,
        discipline_fa TEXT NOT NULL,
        equip_cap_pct REAL NOT NULL,
        star_cap_public_pct REAL NOT NULL,
        star_cap_limited_pct REAL NOT NULL,
        star_cap_non_tender_pct REAL NOT NULL,
        overhead_civil_tender REAL NOT NULL,
        overhead_civil_non_tender REAL NOT NULL,
        overhead_non_civil_tender REAL NOT NULL,
        overhead_non_civil_non_tender REAL NOT NULL,
        overhead_purchase_only REAL NULL,
        purchase_clause_note TEXT NOT NULL,
        selected_project_overhead REAL NULL,
        source_file TEXT NOT NULL,
        source_page_bulletin INTEGER NOT NULL,
        source_page_pdf INTEGER NOT NULL,
        clause_ref TEXT NOT NULL,
        equip_cap_verification_status TEXT NOT NULL,
        overhead_verification_status TEXT NOT NULL,
        evidence_path TEXT NOT NULL
    );
    """,
    # ۱۱. جدول مرجع پارامترها و فرمول ضریب طبقات پیوست ۲ (تصمیم D7)
    """
    CREATE TABLE IF NOT EXISTS floor_factor_params (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        param_key TEXT NOT NULL UNIQUE,
        discipline TEXT NOT NULL DEFAULT 'abnieh',
        year INTEGER NOT NULL DEFAULT 1404,
        title_fa TEXT NOT NULL,
        formula_text_simple TEXT NOT NULL,
        rounding_decimals INTEGER NOT NULL DEFAULT 4,
        excludes_landscaping INTEGER NOT NULL DEFAULT 1,
        excludes_material_on_site INTEGER NOT NULL DEFAULT 1,
        selected_project_floor_coeff REAL NULL,
        source_file TEXT NOT NULL,
        source_page_bulletin INTEGER NOT NULL,
        source_page_pdf INTEGER NOT NULL,
        clause_ref TEXT NOT NULL,
        verification_status TEXT NOT NULL,
        evidence_path TEXT NOT NULL
    );
    """,
    # ۱۲. جدول مرجع ضریب منطقه‌ای پیوست ۴ (تصمیم D7)
    """
    CREATE TABLE IF NOT EXISTS region_factors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        region_key TEXT NOT NULL UNIQUE,
        discipline TEXT NOT NULL DEFAULT 'abnieh',
        year INTEGER NOT NULL DEFAULT 1404,
        circular_ref TEXT NOT NULL,
        circular_date TEXT NOT NULL,
        weighted_formula_simple TEXT NOT NULL,
        selected_project_region_coeff REAL NULL,
        source_file TEXT NOT NULL,
        source_page_bulletin INTEGER NOT NULL,
        source_page_pdf INTEGER NOT NULL,
        clause_ref TEXT NOT NULL,
        verification_status TEXT NOT NULL,
        evidence_path TEXT NOT NULL
    );
    """,
]


def init_database(db_path: Union[str, Path], reset: bool = True) -> sqlite3.Connection:
    """
    ایجاد فایل دیتابیس SQLite، ثبت نسخه اسکیمای ۱٫۲ (PRAGMA user_version = 102)
    و ساخت تمام ۱۲ جدول استاندارد اسکیما.
    اگر reset=True باشد، جداول قبلی پاک و از نو ساخته می‌شوند.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    cur = conn.cursor()
    if reset:
        for tbl in reversed(SCHEMA_TABLES):
            cur.execute(f"DROP TABLE IF EXISTS {tbl};")
    for ddl in DDL_STATEMENTS:
        cur.execute(ddl)
    cur.execute(f"PRAGMA user_version = {SCHEMA_USER_VERSION};")
    conn.commit()
    return conn
