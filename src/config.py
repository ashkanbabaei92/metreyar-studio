#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
تنظیمات مسیرها و فهرست رشته‌های فهرست بها (config.py).
افزودن رشته جدید تنها با اضافه کردن یک مدخل به دیکشنری DISCIPLINES
بدون نیاز به تغییر در کدهای استخراج و پردازش امکان‌پذیر است.
"""

from pathlib import Path
from typing import Dict, Any

# مسیر ریشه پروژه (مستقل از سیستم‌عامل، سازگار با ویندوز و لینوکس)
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

# مسیر پوشه‌های اصلی پروژه
DATA_DIR: Path = PROJECT_ROOT / "data"
DATA_FEHREST_DIR: Path = DATA_DIR / "فهرست‌بها (پایه محاسبات متره و برآورد)"
DATA_PEYMAN_DIR: Path = (
    DATA_DIR
    / "شرایط عمومی پیمان (برای کنترل قرارداد، تاخیرات، صورت‌وضعیت)"
    / "نشریه 4311+ ابلاغ اصلاحیه"
    / "نشریه 4311+ ابلاغ اصلاحیه"
)
DATA_TADIL_DIR: Path = DATA_DIR / "تعدیل آحاد بها"

DATA_PROCESSED_DIR: Path = PROJECT_ROOT / "data_processed"
DOCS_DIR: Path = PROJECT_ROOT / "docs"
TEMPLATES_DIR: Path = PROJECT_ROOT / "templates"
OUTPUT_DIR: Path = PROJECT_ROOT / "output"
TESTS_DIR: Path = PROJECT_ROOT / "tests"

# مسیر فایل‌های خروجی مرحله ۲
REFERENCE_DB_PATH: Path = DATA_PROCESSED_DIR / "reference.db"
REFERENCE_XLSX_PATH: Path = DATA_PROCESSED_DIR / "reference.xlsx"
EXTRACTION_REPORT_PATH: Path = DOCS_DIR / "extraction_report.md"

# سال فهرست بها
DEFAULT_YEAR: int = 1404

# فهرست رشته‌ها (Disciplines) — افزودن رشته جدید بدون تغییر کد
DISCIPLINES: Dict[str, Dict[str, Any]] = {
    "abnieh": {
        "title_fa": "ابنیه",
        "year": 1404,
        "internal_prefix": "11",
        "excel_file": DATA_FEHREST_DIR / "abn1404.xlsx",
        "pdf_file": DATA_FEHREST_DIR / "11-Abnieh1404.pdf",
        "pdf_page_offset": 3,  # شماره صفحه PDF = شماره صفحه بولتن + 3
        "enabled_in_stage2": True,  # طبق تصمیم ۱ کارفرما: فقط ابنیه در دیتابیس مرحله ۲ وارد شود
        "active_phase1_chapters": ["01", "02", "03", "05", "06", "07", "08"],
    },
    "mechanic": {
        "title_fa": "تأسیسات مکانیکی",
        "year": 1404,
        "internal_prefix": "12",
        "excel_file": DATA_FEHREST_DIR / "mec1404.xlsx",
        "pdf_file": DATA_FEHREST_DIR / "12-Mechanic1404.pdf",
        "pdf_page_offset": 3,
        "enabled_in_stage2": False,  # غیرفعال در مرحله ۲ طبق تصمیم ۱ کارفرما
        "active_phase1_chapters": [],
    },
    "electric": {
        "title_fa": "تأسیسات برقی",
        "year": 1404,
        "internal_prefix": "13",
        "excel_file": DATA_FEHREST_DIR / "elec1404.xlsx",
        "pdf_file": DATA_FEHREST_DIR / "13-barghi1404.pdf",
        "pdf_page_offset": 3,
        "enabled_in_stage2": False,  # غیرفعال در مرحله ۲ طبق تصمیم ۱ کارفرما
        "active_phase1_chapters": [],
    },
}

# Seed ثابت برای نمونه‌گیری تصادفی ۲۰ ردیف اعتبارسنجی
VALIDATION_RANDOM_SEED: int = 1404
VALIDATION_SAMPLE_SIZE: int = 20
