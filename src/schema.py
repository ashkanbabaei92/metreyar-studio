#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
تعریف اسکیمای ۸ جدول دیتابیس مرجع SQLite (schema.py)
منطبق با docs/ARCHITECTURE_STAGE1.md و تصمیم‌های تأییدشده کارفرما در مرحله ۲
(شامل افزودن ستون unit_raw به جدول items).
"""

import sqlite3
from pathlib import Path
from typing import Union

SCHEMA_TABLES = [
    "chapters",
    "items",
    "notes",
    "rules",
    "item_dependencies",
    "coefficients",
    "adjustment_indices",
    "material_on_site_factors",
]

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
    # ۳. جدول کلیات، دستورالعمل کاربرد و الزامات فصول و گروه‌ها
    """
    CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        discipline TEXT NOT NULL,
        year INTEGER NOT NULL DEFAULT 1404,
        section_type TEXT NOT NULL,
        chapter_code TEXT NULL,
        group_code TEXT NULL,
        clause_number TEXT NOT NULL,
        clause_text TEXT NOT NULL,
        has_calculation_rule INTEGER NOT NULL DEFAULT 0,
        source_file TEXT NOT NULL,
        source_page_bulletin INTEGER NOT NULL,
        source_page_pdf INTEGER NOT NULL
    );
    """,
    # ۴. جدول قوانین محاسباتی، اضافه‌بها، کسربها و ضوابط متره (پر شدن در مرحله ۳)
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
        source_file TEXT NOT NULL,
        source_page_bulletin INTEGER NOT NULL,
        source_page_pdf INTEGER NOT NULL,
        source_clause_ref TEXT NOT NULL
    );
    """,
    # ۵. جدول نگاشت ارتباط ردیف‌های پایه و ردیف‌های وابسته/اضافه‌بها/کسربها
    """
    CREATE TABLE IF NOT EXISTS item_dependencies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        discipline TEXT NOT NULL,
        parent_code_official TEXT NOT NULL,
        child_code_official TEXT NOT NULL,
        dependency_type TEXT NOT NULL,
        rule_id INTEGER NULL,
        source_page_bulletin INTEGER NOT NULL,
        description_note TEXT NULL,
        UNIQUE(discipline, parent_code_official, child_code_official, rule_id)
    );
    """,
    # ۶. جدول ضرایب پیمان، بالاسری، طبقات، منطقه‌ای و کسورات روکش
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
        source_clause TEXT NULL
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
]


def init_database(db_path: Union[str, Path], reset: bool = True) -> sqlite3.Connection:
    """
    ایجاد فایل دیتابیس SQLite و ساخت هر ۸ جدول استاندارد اسکیما.
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
    conn.commit()
    return conn
