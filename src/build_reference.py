#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
اسکریپت ساخت دیتابیس مرجع (data_processed/reference.db) و اکسل مرجع (data_processed/reference.xlsx)
برای مرحله ۲ پروژه متره‌یار استودیو (build_reference.py).
طبق تصمیم ۱ کارفرما، فقط رشته ابنیه ۱۴۰۴ (abn1404.xlsx و 11-Abnieh1404.pdf) وارد دیتابیس می‌شود.
"""

import sqlite3
from pathlib import Path
from typing import Dict, Any, Tuple
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
import pandas as pd

from src.config import (
    DISCIPLINES,
    REFERENCE_DB_PATH,
    REFERENCE_XLSX_PATH,
)
from src.schema import init_database, SCHEMA_TABLES
from src.pdf_fix import open_patched_pdf
from src.extractor import (
    extract_items_from_excel_and_pdf,
    extract_chapters_from_pdf,
    extract_coefficients_from_pdf,
    extract_notes_from_pdf,
    extract_material_on_site_factors_from_pdf,
)


def build_reference_artifacts(
    db_path: Path = REFERENCE_DB_PATH,
    xlsx_path: Path = REFERENCE_XLSX_PATH,
) -> Dict[str, Any]:
    """
    اجرای کامل استخراج داده‌های مرجع ابنیه ۱۴۰۴ و ساخت reference.db و reference.xlsx.
    خروجی شامل دیکشنری آمار و خلاصه استخراج است.
    """
    conn = init_database(db_path, reset=True)
    cur = conn.cursor()

    total_stats: Dict[str, Any] = {}

    for disc_key, cfg in DISCIPLINES.items():
        if not cfg.get("enabled_in_stage2", False):
            continue

        excel_path: Path = cfg["excel_file"]
        pdf_path: Path = cfg["pdf_file"]
        year: int = cfg["year"]
        prefix: str = cfg["internal_prefix"]
        offset: int = cfg["pdf_page_offset"]
        active_ch: list[str] = cfg["active_phase1_chapters"]

        doc = open_patched_pdf(pdf_path)

        # ۱. استخراج ۱٬۵۶۵ ردیف از اکسل و تطبیق با صفحات PDF
        items_list, pdf_items_map = extract_items_from_excel_and_pdf(
            excel_path=excel_path,
            doc=doc,
            discipline=disc_key,
            year=year,
            internal_prefix=prefix,
            page_offset=offset,
        )

        # شمارش تعداد ردیف‌های هر فصل در اکسل
        excel_ch_counts: Dict[str, int] = {}
        for it in items_list:
            ch_c = it["chapter_code"]
            excel_ch_counts[ch_c] = excel_ch_counts.get(ch_c, 0) + 1

        # ۲. استخراج ۳۵ فصل/بخش بولتن از فهرست مطالب PDF
        chapters_list = extract_chapters_from_pdf(
            doc=doc,
            excel_chapter_counts=excel_ch_counts,
            active_phase1=active_ch,
            pdf_filename=pdf_path.name,
            discipline=disc_key,
            year=year,
            page_offset=offset,
        )

        # ۳. استخراج ۱۸ ردیف جدول ضرایب و پارامترها (coefficients)
        coeffs_list = extract_coefficients_from_pdf(
            doc=doc,
            pdf_filename=pdf_path.name,
        )

        # ۴. استخراج بندهای دستورالعمل کاربرد، کلیات و الزامات فصول (notes)
        notes_list = extract_notes_from_pdf(
            doc=doc,
            chapters_list=chapters_list,
            pdf_filename=pdf_path.name,
            discipline=disc_key,
            year=year,
            page_offset=offset,
        )

        # ۵. استخراج ضرایب متوسط مصالح پای کار جدول ۱ پیوست ۱ (material_on_site_factors)
        mos_list = extract_material_on_site_factors_from_pdf(
            doc=doc,
            pdf_filename=pdf_path.name,
            discipline=disc_key,
            year=year,
            page_offset=offset,
        )

        doc.close()

        # درج در جدول chapters
        cur.executemany(
            """
            INSERT INTO chapters (
                discipline, year, chapter_code, chapter_type, title_official,
                is_active_phase1, excel_row_count, start_page_bulletin, end_page_bulletin,
                start_page_pdf, end_page_pdf, source_file, notes_text
            ) VALUES (
                :discipline, :year, :chapter_code, :chapter_type, :title_official,
                :is_active_phase1, :excel_row_count, :start_page_bulletin, :end_page_bulletin,
                :start_page_pdf, :end_page_pdf, :source_file, :notes_text
            );
            """,
            chapters_list,
        )

        # درج در جدول items
        cur.executemany(
            """
            INSERT INTO items (
                discipline, year, code_official, code_internal, chapter_code,
                group_code, item_seq, description, unit_raw, unit, unit_status,
                unit_price_rial, item_category, is_star_price_zero, is_percent_unit,
                source_excel_file, source_excel_row, source_pdf_file,
                source_page_bulletin, source_page_pdf
            ) VALUES (
                :discipline, :year, :code_official, :code_internal, :chapter_code,
                :group_code, :item_seq, :description, :unit_raw, :unit, :unit_status,
                :unit_price_rial, :item_category, :is_star_price_zero, :is_percent_unit,
                :source_excel_file, :source_excel_row, :source_pdf_file,
                :source_page_bulletin, :source_page_pdf
            );
            """,
            items_list,
        )

        # درج در جدول coefficients
        cur.executemany(
            """
            INSERT INTO coefficients (
                coeff_key, category, title_fa, reference_value, default_in_template,
                is_enabled_default, formula_description, source_file,
                source_page_bulletin, source_clause
            ) VALUES (
                :coeff_key, :category, :title_fa, :reference_value, :default_in_template,
                :is_enabled_default, :formula_description, :source_file,
                :source_page_bulletin, :source_clause
            );
            """,
            coeffs_list,
        )

        # درج در جدول notes
        cur.executemany(
            """
            INSERT INTO notes (
                discipline, year, section_type, chapter_code, group_code,
                clause_number, clause_text, has_calculation_rule,
                source_file, source_page_bulletin, source_page_pdf
            ) VALUES (
                :discipline, :year, :section_type, :chapter_code, :group_code,
                :clause_number, :clause_text, :has_calculation_rule,
                :source_file, :source_page_bulletin, :source_page_pdf
            );
            """,
            notes_list,
        )

        # درج در جدول material_on_site_factors
        cur.executemany(
            """
            INSERT INTO material_on_site_factors (
                discipline, year, chapter_code, material_description,
                factor_percent, source_file, source_page_bulletin, source_page_pdf
            ) VALUES (
                :discipline, :year, :chapter_code, :material_description,
                :factor_percent, :source_file, :source_page_bulletin, :source_page_pdf
            );
            """,
            mos_list,
        )

        conn.commit()
        total_stats[disc_key] = {
            "items_count": len(items_list),
            "chapters_count": len(chapters_list),
            "coefficients_count": len(coeffs_list),
            "notes_count": len(notes_list),
            "mos_factors_count": len(mos_list),
        }

    # ساخت فایل اکسل مرجع (reference.xlsx) از روی جداول دیتابیس با فرمت‌بندی راست‌به‌چپ
    _export_db_to_reference_excel(conn, xlsx_path)
    conn.close()
    return total_stats


def _export_db_to_reference_excel(conn: sqlite3.Connection, xlsx_path: Path) -> None:
    """
    صدور جداول دیتابیس مرجع به فایل اکسل راست‌به‌چپ (reference.xlsx) با حفظ نوع متنی کدهای ۶ رقمی.
    """
    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    # حذف شیت پیش‌فرض اولیه
    default_ws = wb.active

    header_font = Font(name="Tahoma", size=10, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    cell_font = Font(name="Tahoma", size=9)

    for tbl_name in SCHEMA_TABLES:
        df = pd.read_sql_query(f"SELECT * FROM {tbl_name}", conn)
        ws = wb.create_sheet(title=tbl_name)
        ws.views.sheetView[0].rightToLeft = True

        # نوشتن سطر هدر
        cols = list(df.columns)
        ws.append(cols)
        for col_idx in range(1, len(cols) + 1):
            c = ws.cell(row=1, column=col_idx)
            c.font = header_font
            c.fill = header_fill
            c.alignment = Alignment(horizontal="center", vertical="center")

        # نوشتن سطرهای داده با حفظ فرمت متن ('@') برای ستون‌های کد
        text_cols = {
            "code_official",
            "code_internal",
            "chapter_code",
            "group_code",
            "item_seq",
            "parent_code_official",
            "child_code_official",
            "trigger_item_code",
        }
        for r_idx, row in df.iterrows():
            excel_r = int(r_idx) + 2
            for c_idx, col_name in enumerate(cols, start=1):
                val = row[col_name]
                if pd.isna(val):
                    val = None
                cell = ws.cell(row=excel_r, column=c_idx, value=val)
                cell.font = cell_font
                if col_name in text_cols and val is not None:
                    cell.value = str(val)
                    cell.number_format = "@"

    wb.remove(default_ws)
    wb.save(str(xlsx_path))


if __name__ == "__main__":
    stats = build_reference_artifacts()
    print("ساخت دیتابیس و اکسل مرجع با موفقیت انجام شد:", stats)
