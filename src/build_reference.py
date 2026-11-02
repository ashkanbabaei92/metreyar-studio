#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
اسکریپت ساخت دیتابیس مرجع (data_processed/reference.db) و اکسل مرجع (data_processed/reference.xlsx)
برای مرحله ۲ و ۳ پروژه متره‌یار استودیو (build_reference.py).
طبق تصمیم ۱ کارفرما، فقط رشته ابنیه ۱۴۰۴ (abn1404.xlsx و 11-Abnieh1404.pdf) وارد دیتابیس می‌شود.
در گام ج (مرحله ۳ — اسکیمای ۱٫۲):
- جدول notes فقط برای دامنه برش D1 (دستورالعمل کاربرد، کلیات، فصول 01, 02, 03, 05, 06, 07, 08, 28 و پیوست‌های 2, 3, 4) پر می‌شود.
- جدول item_dependencies برای تمام ۳۲۴ ردیف additional/deduction همه فصول پر می‌شود (و docs/unresolved_dependencies.md ساخته می‌شود).
- جدول rules برای دامنه برش D1 و ۸ ردیف واحد «درصد» پر می‌شود.
- ۴ جدول مرجع جدید (mortar_table، overhead_table، floor_factor_params، region_factors) طبق تصمیم D7 پر می‌شوند.
"""

import sqlite3
from pathlib import Path
from typing import Dict, Any
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
import pandas as pd

from src.config import (
    DISCIPLINES,
    REFERENCE_DB_PATH,
    REFERENCE_XLSX_PATH,
    UNRESOLVED_DEPS_PATH,
)
from src.schema import init_database, SCHEMA_TABLES
from src.pdf_fix import open_patched_pdf
from src.extractor import (
    extract_items_from_excel_and_pdf,
    extract_chapters_from_pdf,
    extract_coefficients_from_pdf,
    extract_material_on_site_factors_from_pdf,
)
from src.stage3_extractor import (
    extract_notes_stage3_slice,
    extract_group_titles_from_pdf,
    extract_all_item_dependencies,
    extract_reference_tables_d7,
    extract_rules_stage3_slice,
)


def build_reference_artifacts(
    db_path: Path = REFERENCE_DB_PATH,
    xlsx_path: Path = REFERENCE_XLSX_PATH,
) -> Dict[str, Any]:
    """
    اجرای کامل استخراج داده‌های مرجع ابنیه ۱۴۰۴ (مرحله ۲ و مرحله ۳ — اسکیمای ۱٫۲)
    و ساخت reference.db و reference.xlsx.
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

        # ۳. استخراج ۱۹ ردیف جدول ضرایب و پارامترها (coefficients — شامل سقف ۴٪ تجهیز کارگاه طبق D7)
        coeffs_list = extract_coefficients_from_pdf(
            doc=doc,
            pdf_filename=pdf_path.name,
        )

        # ۴. استخراج بندهای notes در دامنه برش D1 (با text_raw، text_normalized و بندهای خالی)
        notes_list = extract_notes_stage3_slice(
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

        # ۶. استخراج جدول عنوان گروه‌ها از PDF و استخراج وابستگی‌های تمام ۳۲۴ ردیف غیرپایه (تصمیم D1 و D2)
        group_titles = extract_group_titles_from_pdf(doc, chapters_list)
        deps_list, dep_stats = extract_all_item_dependencies(
            items_list=items_list,
            group_titles=group_titles,
            unresolved_doc_path=UNRESOLVED_DEPS_PATH,
        )

        # ۷. استخراج قوانین جدول rules در دامنه برش D1 + ۸ ردیف درصد (تصمیم‌های D1 تا D6)
        rules_list = extract_rules_stage3_slice(
            doc=doc,
            items_list=items_list,
            notes_list=notes_list,
            pdf_filename=pdf_path.name,
            discipline=disc_key,
        )

        # ۸. استخراج ۴ جدول مرجع جدید تصمیم D7
        ref_tables_d7 = extract_reference_tables_d7(
            doc=doc,
            pdf_filename=pdf_path.name,
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
                source_page_bulletin, source_clause,
                verification_status, verified_by, verified_at, evidence_path
            ) VALUES (
                :coeff_key, :category, :title_fa, :reference_value, :default_in_template,
                :is_enabled_default, :formula_description, :source_file,
                :source_page_bulletin, :source_clause,
                :verification_status, :verified_by, :verified_at, :evidence_path
            );
            """,
            coeffs_list,
        )

        # درج در جدول notes (اسکیمای ۱٫۲)
        cur.executemany(
            """
            INSERT INTO notes (
                discipline, year, section_type, chapter_code, group_code,
                clause_number, clause_ref, text_raw, text_normalized, clause_text,
                status, has_calculation_rule, source_file, source_page_bulletin, source_page_pdf
            ) VALUES (
                :discipline, :year, :section_type, :chapter_code, :group_code,
                :clause_number, :clause_ref, :text_raw, :text_normalized, :clause_text,
                :status, :has_calculation_rule, :source_file, :source_page_bulletin, :source_page_pdf
            );
            """,
            notes_list,
        )

        # درج در جدول rules (اسکیمای ۱٫۲)
        cur.executemany(
            """
            INSERT INTO rules (
                rule_code, discipline, chapter_code, rule_type, trigger_item_code,
                source_note_id, condition_json, formula_type, excel_formula_template,
                is_ambiguous, status, source_text_raw, source_file,
                source_page_bulletin, source_page_pdf, source_clause_ref, clause_ref
            ) VALUES (
                :rule_code, :discipline, :chapter_code, :rule_type, :trigger_item_code,
                :source_note_id, :condition_json, :formula_type, :excel_formula_template,
                :is_ambiguous, :status, :source_text_raw, :source_file,
                :source_page_bulletin, :source_page_pdf, :source_clause_ref, :clause_ref
            );
            """,
            rules_list,
        )

        # اتصال rule_id در item_dependencies به قوانین متناظر در جدول rules
        cur.execute("SELECT id, trigger_item_code FROM rules WHERE trigger_item_code IS NOT NULL;")
        rule_id_by_trigger = {r[1]: r[0] for r in cur.fetchall()}
        for d_row in deps_list:
            c_code = d_row["child_code_official"]
            if c_code in rule_id_by_trigger:
                d_row["rule_id"] = rule_id_by_trigger[c_code]

        # درج در جدول item_dependencies (اسکیمای ۱٫۲)
        cur.executemany(
            """
            INSERT INTO item_dependencies (
                discipline, parent_code_official, child_code_official, dependency_type,
                target_kind, target_text, resolution_status, rule_id,
                source_file, source_page_bulletin, source_page_pdf,
                source_clause_ref, clause_ref, description_note
            ) VALUES (
                :discipline, :parent_code_official, :child_code_official, :dependency_type,
                :target_kind, :target_text, :resolution_status, :rule_id,
                :source_file, :source_page_bulletin, :source_page_pdf,
                :source_clause_ref, :clause_ref, :description_note
            );
            """,
            deps_list,
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

        # درج در ۴ جدول مرجع تصمیم D7
        cur.executemany(
            """
            INSERT INTO mortar_table (
                mortar_code, discipline, year, block_no, col_no, mortar_title,
                mix_ratio_normalized, mix_ratio_raw_pdf, cement_kg_per_m3,
                source_file, source_page_bulletin, source_page_pdf,
                clause_ref, verification_status, evidence_path
            ) VALUES (
                :mortar_code, :discipline, :year, :block_no, :col_no, :mortar_title,
                :mix_ratio_normalized, :mix_ratio_raw_pdf, :cement_kg_per_m3,
                :source_file, :source_page_bulletin, :source_page_pdf,
                :clause_ref, :verification_status, :evidence_path
            );
            """,
            ref_tables_d7["mortar_table"],
        )

        cur.executemany(
            """
            INSERT INTO overhead_table (
                row_key, sector_fa, discipline_fa, equip_cap_pct,
                star_cap_public_pct, star_cap_limited_pct, star_cap_non_tender_pct,
                overhead_civil_tender, overhead_civil_non_tender,
                overhead_non_civil_tender, overhead_non_civil_non_tender,
                overhead_purchase_only, purchase_clause_note, selected_project_overhead,
                source_file, source_page_bulletin, source_page_pdf, clause_ref,
                equip_cap_verification_status, overhead_verification_status, evidence_path
            ) VALUES (
                :row_key, :sector_fa, :discipline_fa, :equip_cap_pct,
                :star_cap_public_pct, :star_cap_limited_pct, :star_cap_non_tender_pct,
                :overhead_civil_tender, :overhead_civil_non_tender,
                :overhead_non_civil_tender, :overhead_non_civil_non_tender,
                :overhead_purchase_only, :purchase_clause_note, :selected_project_overhead,
                :source_file, :source_page_bulletin, :source_page_pdf, :clause_ref,
                :equip_cap_verification_status, :overhead_verification_status, :evidence_path
            );
            """,
            ref_tables_d7["overhead_table"],
        )

        cur.executemany(
            """
            INSERT INTO floor_factor_params (
                param_key, discipline, year, title_fa, formula_text_simple,
                rounding_decimals, excludes_landscaping, excludes_material_on_site,
                selected_project_floor_coeff, source_file, source_page_bulletin,
                source_page_pdf, clause_ref, verification_status, evidence_path
            ) VALUES (
                :param_key, :discipline, :year, :title_fa, :formula_text_simple,
                :rounding_decimals, :excludes_landscaping, :excludes_material_on_site,
                :selected_project_floor_coeff, :source_file, :source_page_bulletin,
                :source_page_pdf, :clause_ref, :verification_status, :evidence_path
            );
            """,
            ref_tables_d7["floor_factor_params"],
        )

        cur.executemany(
            """
            INSERT INTO region_factors (
                region_key, discipline, year, circular_ref, circular_date,
                weighted_formula_simple, selected_project_region_coeff,
                source_file, source_page_bulletin, source_page_pdf,
                clause_ref, verification_status, evidence_path
            ) VALUES (
                :region_key, :discipline, :year, :circular_ref, :circular_date,
                :weighted_formula_simple, :selected_project_region_coeff,
                :source_file, :source_page_bulletin, :source_page_pdf,
                :clause_ref, :verification_status, :evidence_path
            );
            """,
            ref_tables_d7["region_factors"],
        )

        conn.commit()
        total_stats[disc_key] = {
            "items_count": len(items_list),
            "chapters_count": len(chapters_list),
            "coefficients_count": len(coeffs_list),
            "notes_count": len(notes_list),
            "rules_count": len(rules_list),
            "dependencies_rows_count": len(deps_list),
            "dep_stats": dep_stats,
            "mos_factors_count": len(mos_list),
            "mortar_table_count": len(ref_tables_d7["mortar_table"]),
            "overhead_table_count": len(ref_tables_d7["overhead_table"]),
            "floor_factor_params_count": len(ref_tables_d7["floor_factor_params"]),
            "region_factors_count": len(ref_tables_d7["region_factors"]),
        }

    # ساخت فایل اکسل مرجع (reference.xlsx) از روی تمام ۱۲ جدول دیتابیس با فرمت‌بندی راست‌به‌چپ
    _export_db_to_reference_excel(conn, xlsx_path)
    conn.close()
    return total_stats


def _export_db_to_reference_excel(conn: sqlite3.Connection, xlsx_path: Path) -> None:
    """
    صدور جداول دیتابیس مرجع به فایل اکسل راست‌به‌چپ (reference.xlsx) با حفظ نوع متنی کدهای ۶ رقمی.
    """
    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    default_ws = wb.active

    header_font = Font(name="Tahoma", size=10, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    cell_font = Font(name="Tahoma", size=9)

    for tbl_name in SCHEMA_TABLES:
        df = pd.read_sql_query(f"SELECT * FROM {tbl_name}", conn)
        ws = wb.create_sheet(title=tbl_name)
        ws.views.sheetView[0].rightToLeft = True

        cols = list(df.columns)
        ws.append(cols)
        for col_idx in range(1, len(cols) + 1):
            c = ws.cell(row=1, column=col_idx)
            c.font = header_font
            c.fill = header_fill
            c.alignment = Alignment(horizontal="center", vertical="center")

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
    print("ساخت دیتابیس و اکسل مرجع (اسکیمای ۱٫۲) با موفقیت انجام شد:", stats)
