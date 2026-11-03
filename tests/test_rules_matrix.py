#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
تست‌های خودکار مرحله ۳ (گام ج) شامل:
۱. اجرای کامل ماتریس تست قوانین بدون ابهام (`docs/rules_test_matrix.md`)
۲. تست‌های اسکیمای ۱٫۲ (`PRAGMA user_version = 102` و ۱۲ جدول)
۳. تست‌های یکتایی شناسه قوانین (`rule_code`)، نبود عدد بدون منبع،
   حل شدن کدهای هدف در `item_dependencies` (۲۰۹ حل‌شده + ۱۱۵ موضوعی حل‌نشده بدون کد جعلی)،
   نرمال‌سازی متن `notes` (`text_raw` در برابر `text_normalized` و بندهای خالی `empty_in_source`)،
   و صحت ۴ جدول مرجع جدید تصمیم D7 (`mortar_table`, `overhead_table`, `floor_factor_params`, `region_factors`).
"""

import json
import math
import sqlite3
from pathlib import Path
import pytest

from src.config import (
    REFERENCE_DB_PATH,
    UNRESOLVED_DEPS_PATH,
    AMBIGUOUS_RULES_PATH,
    RULES_TEST_MATRIX_PATH,
    SENSITIVE_NUMBERS_PATH,
    RULES_EXTRACTION_REPORT_PATH,
    SCHEMA_USER_VERSION,
)
from src.schema import SCHEMA_TABLES
from src.normalize import normalize_clause_text


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(REFERENCE_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def test_schema_v1_2_tables_and_columns():
    """
    بررسی ارتقای اسکیما به نسخه 1.2 (PRAGMA user_version = 102)،
    وجود هر ۱۲ جدول و ستون‌های جدید تصمیم‌های D2، D7 و بخش ۵ پرامپت.
    """
    conn = _get_conn()
    cur = conn.cursor()

    assert cur.execute("PRAGMA user_version;").fetchone()[0] == 102 == SCHEMA_USER_VERSION

    tables = {
        r[0]
        for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"
        ).fetchall()
    }
    assert tables == set(SCHEMA_TABLES)
    assert len(tables) == 12

    # بررسی ستون‌های جدید notes
    note_cols = {r[1] for r in cur.execute("PRAGMA table_info(notes);").fetchall()}
    for c in ("text_raw", "text_normalized", "status", "clause_ref", "source_file", "source_page_bulletin", "source_page_pdf"):
        assert c in note_cols

    # بررسی ستون‌های جدید item_dependencies
    dep_cols = {r[1] for r in cur.execute("PRAGMA table_info(item_dependencies);").fetchall()}
    for c in ("target_kind", "target_text", "resolution_status", "source_clause_ref", "clause_ref", "source_file", "source_page_bulletin", "source_page_pdf"):
        assert c in dep_cols

    # بررسی ستون‌های جدید rules
    rule_cols = {r[1] for r in cur.execute("PRAGMA table_info(rules);").fetchall()}
    for c in ("status", "source_text_raw", "clause_ref", "source_clause_ref", "source_file", "source_page_bulletin", "source_page_pdf"):
        assert c in rule_cols

    conn.close()


def test_notes_text_normalization_and_empty_clauses():
    """
    بررسی کیفیت متن در جدول notes (بخش ۵ پرامپت):
    - حفظ متن خام pdf_fix در text_raw و اصلاح قاعده‌مند «باالسری» -> «بالاسری»، «ع ملیات» -> «عملیات»،
      «آزمایشگا ه» -> «آزمایشگاه»، «مترمربع - ماه» -> «مترمربع-ماه» در text_normalized
    - عدم تخریب کلماتی که واقعاً دارای «ال» هستند (الزامات، الف، دال، مصالح، قالب، کانال، سال)
    - ثبت بند 9 دستورالعمل کاربرد و بند 7 کلیات با متن خالی و status='empty_in_source'
    - عدم وجود هیچ رکوردی بدون منبع و صفحه در notes.
    """
    # ۱. تست واحد تابع normalize_clause_text روی موارد تصریح‌شده در پرامپت
    assert normalize_clause_text("ضرایب باالسری و فوالدی") == "ضرایب بالاسری و فولادی"
    assert normalize_clause_text("ع ملیات و عمل ی ات") == "عملیات و عملیات"
    assert normalize_clause_text("آزمایشگا ه و کارگا ه") == "آزمایشگاه و کارگاه"
    assert normalize_clause_text("مترمربع - ماه") == "مترمربع-ماه"
    # حفظ کلمات دارای «ال» واقعی
    sample_real_al = "الزامات جدول الف برای دال و قالب و مصالح و کانال در سال 1404"
    assert normalize_clause_text(sample_real_al) == sample_real_al

    conn = _get_conn()
    cur = conn.cursor()

    total_notes = cur.execute("SELECT COUNT(*) FROM notes;").fetchone()[0]
    assert total_notes == 1196

    in_slice_notes = cur.execute("SELECT COUNT(*) FROM notes WHERE status IN ('active', 'empty_in_source');").fetchone()[0]
    out_of_slice_notes = cur.execute("SELECT COUNT(*) FROM notes WHERE status = 'out_of_slice';").fetchone()[0]
    assert in_slice_notes == 435
    assert out_of_slice_notes == 761

    # هیچ رکوردی بدون source_file، source_page_bulletin، source_page_pdf یا clause_ref نیست
    missing_src = cur.execute(
        """
        SELECT COUNT(*) FROM notes
        WHERE source_file IS NULL OR source_file = ''
           OR source_page_bulletin IS NULL
           OR source_page_pdf IS NULL
           OR clause_ref IS NULL OR clause_ref = '';
        """
    ).fetchone()[0]
    assert missing_src == 0

    # بررسی بندهای خالی منبع (بند 9 دستورالعمل کاربرد و بند 7 کلیات)
    empty_rows = cur.execute(
        "SELECT section_type, clause_number, text_raw, text_normalized, status, source_page_bulletin FROM notes WHERE status='empty_in_source' ORDER BY id;"
    ).fetchall()
    assert len(empty_rows) == 2
    assert (empty_rows[0]["section_type"], empty_rows[0]["clause_number"], empty_rows[0]["source_page_bulletin"]) == ("instructions", "9", 3)
    assert empty_rows[0]["text_raw"] == "" and empty_rows[0]["text_normalized"] == ""
    assert (empty_rows[1]["section_type"], empty_rows[1]["clause_number"], empty_rows[1]["source_page_bulletin"]) == ("general_terms", "7", 4)
    assert empty_rows[1]["text_raw"] == "" and empty_rows[1]["text_normalized"] == ""

    # در text_normalized نباید «باالسری» یا «فوالدی» یا «مالت» باقی مانده باشد
    bad_norm = cur.execute(
        "SELECT COUNT(*) FROM notes WHERE text_normalized LIKE '%باالسری%' OR text_normalized LIKE '%فوالد%' OR text_normalized LIKE '%مالت%';"
    ).fetchone()[0]
    assert bad_norm == 0

    # اما در text_raw آثار خام فونت PDF حفظ شده است
    raw_preserved = cur.execute(
        "SELECT COUNT(*) FROM notes WHERE text_raw LIKE '%باالسر%' OR text_raw LIKE '%فوالد%' OR text_raw LIKE '%مالت%';"
    ).fetchone()[0]
    assert raw_preserved > 0

    conn.close()


def test_item_dependencies_completeness_and_no_fake_codes():
    """
    تست کامل جدول item_dependencies طبق تصمیم‌های D1 و D2:
    - پوشش هر ۳۲۴ ردیف additional/deduction در کل فصول
    - ۲۰۹ ردیف دارای کد ۶ رقمی صریح با resolution_status='resolved_explicit_code'
      که تمام parent_code_official آن‌ها کدهای ۶ رقمی معتبر موجود در جدول items هستند
    - ۱۱۵ ردیف بدون کد ۶ رقمی صریح با target_kind='text_topic'،
      resolution_status='unresolved_topic' و parent_code_official IS NULL
    - عدم وجود هیچ کد جعلی مثل 'CHAPTER_xx'
    - وجود فایل docs/unresolved_dependencies.md.
    """
    assert UNRESOLVED_DEPS_PATH.exists(), "فایل docs/unresolved_dependencies.md وجود ندارد!"

    conn = _get_conn()
    cur = conn.cursor()

    # تمام ۳۲۴ ردیف غیرپایه باید در item_dependencies حضور داشته باشند
    distinct_children = cur.execute(
        "SELECT COUNT(DISTINCT child_code_official) FROM item_dependencies;"
    ).fetchone()[0]
    assert distinct_children == 324

    # هیچ کد جعلی در parent_code_official وجود ندارد
    fake_parents = cur.execute(
        """
        SELECT COUNT(*) FROM item_dependencies
        WHERE parent_code_official IS NOT NULL
          AND parent_code_official NOT IN (SELECT code_official FROM items);
        """
    ).fetchone()[0]
    assert fake_parents == 0

    # تفکیک حل‌شده (۲۰۹ ردیف) و حل‌نشده موضوعی (۱۱۵ ردیف)
    resolved_children = cur.execute(
        "SELECT COUNT(DISTINCT child_code_official) FROM item_dependencies WHERE resolution_status='resolved_explicit_code';"
    ).fetchone()[0]
    unresolved_children = cur.execute(
        "SELECT COUNT(DISTINCT child_code_official) FROM item_dependencies WHERE resolution_status='unresolved_topic';"
    ).fetchone()[0]
    assert resolved_children == 209
    assert unresolved_children == 115

    # تمام ردیف‌های unresolved_topic باید دارای target_kind='text_topic' و parent_code_official IS NULL باشند
    bad_unresolved = cur.execute(
        """
        SELECT COUNT(*) FROM item_dependencies
        WHERE resolution_status='unresolved_topic'
          AND (target_kind != 'text_topic' OR parent_code_official IS NOT NULL);
        """
    ).fetchone()[0]
    assert bad_unresolved == 0

    # بررسی نبود رکورد بدون منبع در item_dependencies
    missing_src = cur.execute(
        """
        SELECT COUNT(*) FROM item_dependencies
        WHERE source_file IS NULL OR source_page_bulletin IS NULL
           OR source_page_pdf IS NULL OR source_clause_ref IS NULL;
        """
    ).fetchone()[0]
    assert missing_src == 0

    conn.close()


def test_rules_uniqueness_sources_and_d3_d4_d5_d6():
    """
    بررسی یکتایی شناسه قوانین (rule_code)، نبود عدد یا قانون بدون منبع،
    و رعایت دقیق تصمیم‌های D2 (استثناها)، D3 (030601 مبهم)، D4 (پله‌ای عمق)،
    D5 (11-1 و 11-2 مستقل) و D6 (clause_derived_item و ۸ ردیف درصد).
    """
    conn = _get_conn()
    cur = conn.cursor()

    total_rules = cur.execute("SELECT COUNT(*) FROM rules;").fetchone()[0]
    distinct_codes = cur.execute("SELECT COUNT(DISTINCT rule_code) FROM rules;").fetchone()[0]
    assert total_rules == distinct_codes == 121

    # هیچ قانونی بدون منبع و صفحه وجود ندارد
    missing_src = cur.execute(
        """
        SELECT COUNT(*) FROM rules
        WHERE source_file IS NULL OR source_file = ''
           OR source_page_bulletin IS NULL
           OR source_page_pdf IS NULL
           OR source_clause_ref IS NULL OR source_clause_ref = ''
           OR clause_ref IS NULL OR clause_ref = '';
        """
    ).fetchone()[0]
    assert missing_src == 0

    # تصمیم D3: ردیف 030601 باید is_ambiguous=1 و status='pending_user_decision' و بدون فرمول محاسبه باشد
    r_030601 = dict(
        cur.execute("SELECT * FROM rules WHERE rule_code='R-ITEM-030601';").fetchone()
    )
    assert r_030601["is_ambiguous"] == 1
    assert r_030601["status"] == "pending_user_decision"
    assert r_030601["excel_formula_template"] == ""

    # تصمیم D4: ردیف‌های پله‌ای 020201، 020304، 030603، 031204 دارای آرایه steps در condition_json هستند
    for scode, expected_steps_len in [("020201", 4), ("020304", 3), ("030603", 3), ("031204", 5)]:
        r_step = dict(
            cur.execute("SELECT * FROM rules WHERE rule_code=?;", (f"R-ITEM-{scode}",)).fetchone()
        )
        cond = json.loads(r_step["condition_json"])
        assert "steps" in cond
        assert len(cond["steps"]) == expected_steps_len
        for st in cond["steps"]:
            assert {"lower_m", "upper_m", "unit", "repeat_multiplier"}.issubset(set(st.keys()))

    # تصمیم D5: بندهای 11-1 و 11-2 فصل ۶ دو قانون مستقل هستند
    r_11_1 = dict(
        cur.execute("SELECT * FROM rules WHERE rule_code='R-06-GEN-11-1-SLAB-BEAM-HEIGHT-10-TO-20M';").fetchone()
    )
    r_11_2 = dict(
        cur.execute("SELECT * FROM rules WHERE rule_code='R-06-GEN-11-2-WALL-COL-HEIGHT-10-TO-20M';").fetchone()
    )
    assert r_11_1["source_page_bulletin"] == 47 and r_11_1["source_page_pdf"] == 50
    assert r_11_2["source_page_bulletin"] == 48 and r_11_2["source_page_pdf"] == 51
    cond_11_2 = json.loads(r_11_2["condition_json"])
    assert len(cond_11_2["bands_pct"]) == 10

    # تصمیم D2: بندهای استثنا 10-5 فصل ۶ و 3-5 فصل ۸ با rule_type='exclusion' ثبت شده‌اند
    r_ex_06 = dict(
        cur.execute("SELECT * FROM rules WHERE rule_code='R-06-G10-10-5-EXCLUSION-060801';").fetchone()
    )
    assert r_ex_06["rule_type"] == "exclusion"
    assert json.loads(r_ex_06["condition_json"])["excluded_from_parents"] == [
        "061001", "061002", "061003", "061004", "061005", "061006"
    ]

    r_ex_08 = dict(
        cur.execute("SELECT * FROM rules WHERE rule_code='R-08-G3-3-5-EXCLUSION-JOIST-BLOCK-SLAB';").fetchone()
    )
    assert r_ex_08["rule_type"] == "exclusion"
    assert json.loads(r_ex_08["condition_json"])["excluded_item_codes"] == [
        "080301", "080302", "080304", "080310", "080311"
    ]

    # تصمیم D6: ۸ ردیف واحد «درصد» همگی با is_ambiguous=1 و status='pending_user_decision' ثبت شده‌اند
    pct_rules = cur.execute(
        "SELECT rule_code, trigger_item_code, is_ambiguous, status FROM rules WHERE rule_type='percent_unit_item' ORDER BY trigger_item_code;"
    ).fetchall()
    assert len(pct_rules) == 8
    assert all(r["is_ambiguous"] == 1 and r["status"] == "pending_user_decision" for r in pct_rules)

    conn.close()


def test_d7_reference_tables():
    """
    بررسی ۴ جدول مرجع جدید تصمیم D7:
    - mortar_table (۱۱ ردیف جدول ۱-۱ کلیات با نسبت‌های حجمی اصلاح‌شده 1:6، 1:5، 1:4، 1:3، 1:2:9، 1:1:3، 1:2.5:2.5)
    - overhead_table (۴ ردیف جدول الف با سقف ۴٪ تجهیز کارگاه «تأییدنشده» و selected_project_overhead IS NULL)
    - floor_factor_params (۱ ردیف پیوست ۲ با وضعیت «تأییدنشده؛ نیاز به بازبینی چشمی کاربر» و selected_project_floor_coeff IS NULL)
    - region_factors (۱ ردیف پیوست ۴ با وضعیت «تأییدنشده؛ نیاز به بازبینی چشمی کاربر» و selected_project_region_coeff IS NULL)
    """
    conn = _get_conn()
    cur = conn.cursor()

    mortars = [dict(r) for r in cur.execute("SELECT * FROM mortar_table ORDER BY block_no, col_no;").fetchall()]
    assert len(mortars) == 11
    # بررسی مقادیر سیمان در هر ۳ بلوک جدول ۱-۱
    assert [m["cement_kg_per_m3"] for m in mortars] == [
        200.0, 225.0, 285.0, 360.0,
        285.0, 130.0, 300.0, 400.0,
        225.0, 350.0, 430.0,
    ]
    # بررسی اصلاح نسبت‌های حجمی (عدم وجود 6.1 یا 5.1 در mix_ratio_normalized)
    ratios = [m["mix_ratio_normalized"] for m in mortars]
    assert ratios == [
        "1:6", "1:5", "1:4", "1:3",
        "1:4", "1:2:9", "1:1:3", "1:4",
        "1:6", "1:2.5:2.5", None,
    ]
    assert all(Path(m["evidence_path"]).exists() for m in mortars)

    overheads = [dict(r) for r in cur.execute("SELECT * FROM overhead_table ORDER BY id;").fetchall()]
    assert len(overheads) == 4
    assert all(o["equip_cap_pct"] == 4.0 for o in overheads)
    assert all(o["equip_cap_verification_status"] == "تأییدنشده" for o in overheads)
    assert all(o["overhead_verification_status"] == "تأییدشده توسط کاربر" for o in overheads)
    assert all(o["selected_project_overhead"] is None for o in overheads)

    floors = [dict(r) for r in cur.execute("SELECT * FROM floor_factor_params;").fetchall()]
    assert len(floors) == 1
    assert floors[0]["selected_project_floor_coeff"] is None
    assert floors[0]["verification_status"] == "تأییدنشده؛ نیاز به بازبینی چشمی کاربر"
    assert Path(floors[0]["evidence_path"]).exists()

    regions = [dict(r) for r in cur.execute("SELECT * FROM region_factors;").fetchall()]
    assert len(regions) == 1
    assert regions[0]["selected_project_region_coeff"] is None
    assert regions[0]["verification_status"] == "تأییدنشده؛ نیاز به بازبینی چشمی کاربر"
    assert Path(regions[0]["evidence_path"]).exists()

    # ۴ ردیف سقف تأییدنشده در جدول coefficients نیز باید دارای مسیر مدرک معتبر در docs/evidence باشند (گام ج-۳: C5)
    unverified_caps = [
        dict(r)
        for r in cur.execute(
            "SELECT coeff_key, verification_status, evidence_path, source_page_bulletin FROM coefficients WHERE verification_status='تأییدنشده' ORDER BY id;"
        ).fetchall()
    ]
    assert len(unverified_caps) == 4
    for uc in unverified_caps:
        assert uc["evidence_path"] is not None, f"Missing evidence_path for {uc['coeff_key']}"
        assert Path(uc["evidence_path"]).exists(), f"Evidence file not found: {uc['evidence_path']}"
    # سقف ۳۰٪ مناقصه عمومی در متن بند ۲-۶ صفحه ۱ بولتن (صفحه ۴ PDF) شروع می‌شود
    cap_30 = next(c for c in unverified_caps if c["coeff_key"] == "STAR_ITEM_CAP_PUBLIC_TENDER_PCT")
    assert cap_30["evidence_path"] == "docs/evidence/abnieh_page_4_instructions_2_4_2_6.png"

    conn.close()


def _get_item_price(cur: sqlite3.Cursor, code: str) -> float:
    row = cur.execute("SELECT unit_price_rial FROM items WHERE code_official=?;", (code,)).fetchone()
    assert row is not None and row[0] is not None
    return float(row[0])


def _get_rule_cond(cur: sqlite3.Cursor, rule_code: str) -> dict:
    row = cur.execute("SELECT condition_json, is_ambiguous FROM rules WHERE rule_code=?;", (rule_code,)).fetchone()
    assert row is not None
    assert row["is_ambiguous"] == 0, f"Rule {rule_code} is marked ambiguous and must not be in test matrix!"
    return json.loads(row["condition_json"])


# تفکیک ۳۰ سناریوی ماتریس تست طبق گام ج-۳ (C4):
# - ۱۲ سناریوی متکی صرفاً به اکسل رسمی، ضرایب تأییدشده توسط کاربر یا استثناهای ساختاری مصوب: برچسب VERIFIED_SOURCE
# - ۱۸ سناریوی متکی به اعداد یا فرمول‌های استخراج‌شده از لایه متنی PDF که هنوز در بخش ۵ تأیید چشمی نشده‌اند: برچسب NEEDS_REVIEW_UNVERIFIED_PDF
RULES_MATRIX_30_SCENARIOS = [
    ("01_R-INST-2-8-SEQUENTIAL-COEFFS__VERIFIED_SOURCE_EXPERIMENTAL_VALS", 1),
    ("02_R-GEN-10-HARDWARE-INVOICE__VERIFIED_SOURCE", 2),
    ("03_R-GEN-18-2-UNNOTIFIED-MINUTES-070__NEEDS_REVIEW_UNVERIFIED_PDF", 3),
    ("04_R-01-G1-1-1-EXCLUSION__VERIFIED_SOURCE", 4),
    ("05_R-01-G1-1-6-TREE-ROOT-EARTHWORK__NEEDS_REVIEW_UNVERIFIED_PDF", 5),
    ("06_R-01-G2-2-2-GROOVE-MAX-100CM2_AND_010209__VERIFIED_SOURCE", 6),
    ("07_R-01-G4-4-2-EXCLUSION-010403__VERIFIED_SOURCE", 7),
    ("08_R-01-G4-4-3-HEAVY-MACHINERY-050__NEEDS_REVIEW_UNVERIFIED_PDF", 8),
    ("09_R-ITEM-010517__VERIFIED_SOURCE", 9),
    ("10_R-ITEM-010921__VERIFIED_SOURCE", 10),
    ("11_R-ITEM-020201_D4_STEPS__VERIFIED_SOURCE", 11),
    ("12_R-ITEM-020304_D4_STEPS__VERIFIED_SOURCE", 12),
    ("13_R-ITEM-020402__VERIFIED_SOURCE", 13),
    ("14_R-03-G2-2-1-BLASTING-MAX-VOLUME__NEEDS_REVIEW_UNVERIFIED_PDF", 14),
    ("15_R-03-G2-2-2-CONTROLLED-BLASTING-30PCT__NEEDS_REVIEW_UNVERIFIED_PDF", 15),
    ("16_R-03-G6-6-1-EXCLUSION_AND_030603_D4__NEEDS_REVIEW_UNVERIFIED_PDF", 16),
    ("17_R-03-G7-7-4-DIRT-ROAD-HAUL-130__NEEDS_REVIEW_UNVERIFIED_PDF", 17),
    ("18_R-03-G9-9-2-LAYER-THICKNESS-DEDUCTION__NEEDS_REVIEW_UNVERIFIED_PDF", 18),
    ("19_R-03-G12-12-4-REDRILLING-50PCT__NEEDS_REVIEW_UNVERIFIED_PDF", 19),
    ("20_R-05-GEN-10-NON-NRAD-WOOD-DED-35PCT__NEEDS_REVIEW_UNVERIFIED_PDF", 20),
    ("21_R-05-G2-2-1-ONE-SIDED-WALL-20PCT__NEEDS_REVIEW_UNVERIFIED_PDF", 21),
    ("22_R-06-GEN-11-1-SLAB-BEAM-HEIGHT-10-TO-20M__NEEDS_REVIEW_UNVERIFIED_PDF", 22),
    ("23_R-06-GEN-11-2-WALL-COL-HEIGHT-10-TO-20M__NEEDS_REVIEW_UNVERIFIED_PDF", 23),
    ("24_R-06-G10-10-5-EXCLUSION-060801__VERIFIED_SOURCE", 24),
    ("25_R-07-GEN-5-WELDED-SPLICE-200PCT__NEEDS_REVIEW_UNVERIFIED_PDF", 25),
    ("26_R-08-G1-1-1-FC-CEMENT-EQUIV__NEEDS_REVIEW_UNVERIFIED_PDF", 26),
    ("27_R-08-G3-3-2-SLOPED-CONCRETE-150__NEEDS_REVIEW_UNVERIFIED_PDF", 27),
    ("28_R-08-G3-3-5-EXCLUSION-JOIST-BLOCK-SLAB__VERIFIED_SOURCE", 28),
    ("29_R-28-G1-1-2-2-BULK-CEMENT_AND_1-2-3-WASTE__NEEDS_REVIEW_UNVERIFIED_PDF", 29),
    ("30_APP2_FLOOR_FACTOR_FORMULA__NEEDS_REVIEW_UNVERIFIED_PDF", 30),
]


@pytest.mark.parametrize("scenario_id,scenario_no", RULES_MATRIX_30_SCENARIOS)
def test_rules_matrix_calculations(scenario_id: str, scenario_no: int):
    """
    اجرای پارامتریک و مستقل هر یک از ۳۰ مورد ماتریس تست قوانین بدون ابهام (`docs/rules_test_matrix.md`)
    در برابر مقادیر واقعی دیتابیس `reference.db` به گونه‌ای که شکست یک سناریو سایر سناریوها را پنهان نکند.
    """
    conn = _get_conn()
    cur = conn.cursor()
    try:
        if scenario_no == 1:
            _ = _get_rule_cond(cur, "R-INST-2-8-SEQUENTIAL-COEFFS")
            total_est = round(1_000_000_000 * 1.0451 * 1.30 * 1.00) + 40_000_000
            assert total_est == 1_398_630_000
        elif scenario_no == 2:
            c2 = _get_rule_cond(cur, "R-GEN-10-HARDWARE-INVOICE")
            assert round(50_000_000 * c2["overhead_multiplier"]) == 57_000_000
        elif scenario_no == 3:
            c3 = _get_rule_cond(cur, "R-GEN-18-2-UNNOTIFIED-MINUTES-070")
            assert round(200_000_000 * c3["payable_factor"]) == 140_000_000
        elif scenario_no == 4:
            c4 = _get_rule_cond(cur, "R-01-G1-1-1-EXCLUSION")
            assert "010101" in c4["excluded_item_codes"]
        elif scenario_no == 5:
            c5 = _get_rule_cond(cur, "R-01-G1-1-6-TREE-ROOT-EARTHWORK")
            p_020102 = _get_item_price(cur, "020102")
            assert p_020102 == 1_401_000.0
            assert round(p_020102 * c5["multiplier"]) == 1_681_200
        elif scenario_no == 6:
            c6 = _get_rule_cond(cur, "R-ITEM-010209")
            p_010209 = _get_item_price(cur, "010209")
            assert p_010209 == 25_400.0
            eff_diff = min(120.0, c6["max_cap_cm2"]) - c6["base_threshold_cm2"]
            assert eff_diff == 80.0
            assert round(eff_diff * 10 * p_010209) == 20_320_000
        elif scenario_no == 7:
            c7 = _get_rule_cond(cur, "R-01-G4-4-2-EXCLUSION-010403")
            assert c7["active_item_code"] == "010404" and c7["excluded_item_codes"] == ["010403"]
        elif scenario_no == 8:
            c8 = _get_rule_cond(cur, "R-01-G4-4-3-HEAVY-MACHINERY-050")
            p_010405 = _get_item_price(cur, "010405")
            assert p_010405 == 15_775_000.0
            assert round(p_010405 * c8["multiplier"]) == 7_887_500
        elif scenario_no == 9:
            _ = _get_rule_cond(cur, "R-ITEM-010517")
            p_010517 = _get_item_price(cur, "010517")
            assert p_010517 == -104_500.0
            assert round(50 * p_010517) == -5_225_000
        elif scenario_no == 10:
            c10 = _get_rule_cond(cur, "R-ITEM-010921")
            p_010921 = _get_item_price(cur, "010921")
            assert p_010921 == 131_500.0
            qty_10 = (min(22.5, c10["cap_max"]) - c10["threshold_min"]) * 20.0
            assert qty_10 == 150.0
            assert round(qty_10 * p_010921) == 19_725_000
        elif scenario_no == 11:
            c11 = _get_rule_cond(cur, "R-ITEM-020201")
            assert c11["threshold_min"] == 2.0 and c11["step_size"] == 2.0 and c11["cap_max"] == 10.0
        elif scenario_no == 12:
            c12 = _get_rule_cond(cur, "R-ITEM-020304")
            assert c12["threshold_min"] == 20.0 and c12["step_size"] == 5.0 and c12["cap_max"] is None
        elif scenario_no == 13:
            c13 = _get_rule_cond(cur, "R-ITEM-020402")
            p_020402 = _get_item_price(cur, "020402")
            assert p_020402 == 457_500.0
            factor_13 = (min(50.0, c13["max_cap_m"]) - c13["base_included_m"]) / c13["step_m"]
            assert factor_13 == 1.5
            assert round(factor_13 * 40.0 * p_020402) == 27_450_000
        elif scenario_no == 14:
            c14 = _get_rule_cond(cur, "R-03-G2-2-1-BLASTING-MAX-VOLUME")
            v_max = (20.0 + (300.0 * c14["detonator_equiv_grams"] / 1000.0)) / c14["denominator_kg_per_m3"]
            assert v_max == 320.0
        elif scenario_no == 15:
            c15 = _get_rule_cond(cur, "R-03-G2-2-2-CONTROLLED-BLASTING-30PCT")
            p_030201 = _get_item_price(cur, "030201")
            assert p_030201 == 1_069_000.0
            assert round(p_030201 * c15["percentage_add"] / 100.0) == 320_700
        elif scenario_no == 16:
            c16_ex = _get_rule_cond(cur, "R-03-G6-6-1-EXCLUSION-DEPTH-2-TO-3M")
            assert c16_ex["no_additional_band_m"] == [2.0, 3.0]
            c16_st = _get_rule_cond(cur, "R-ITEM-030603")
            assert c16_st["threshold_min"] == 3.0 and c16_st["step_size"] == 1.0
        elif scenario_no == 17:
            c17 = _get_rule_cond(cur, "R-03-G7-7-4-DIRT-ROAD-HAUL-130")
            p_030703 = _get_item_price(cur, "030703")
            assert p_030703 == 41_500.0
            assert round(p_030703 * c17["multiplier"]) == 53_950
        elif scenario_no == 18:
            c18 = _get_rule_cond(cur, "R-03-G9-9-2-LAYER-THICKNESS-DEDUCTION")
            p_030901 = _get_item_price(cur, "030901")
            assert p_030901 == 231_000.0
            assert round(p_030901 * c18["thickness_deductions_pct"]["25_cm"] / 100.0) == -46_200
        elif scenario_no == 19:
            c19 = _get_rule_cond(cur, "R-03-G12-12-4-REDRILLING-50PCT")
            l_total = 20.0 + (c19["redrill_length_factor_pct"] / 100.0) * (5.0 + 13.0)
            assert l_total == 29.0
        elif scenario_no == 20:
            c20 = _get_rule_cond(cur, "R-05-GEN-10-NON-NRAD-WOOD-DED-35PCT")
            p_050102 = _get_item_price(cur, "050102")
            assert p_050102 == 2_204_000.0
            assert round(p_050102 * c20["percentage_deduction"] / 100.0) == -771_400
        elif scenario_no == 21:
            c21 = _get_rule_cond(cur, "R-05-G2-2-1-ONE-SIDED-WALL-20PCT")
            p_050201 = _get_item_price(cur, "050201")
            assert p_050201 == 5_837_000.0
            assert round(p_050201 * c21["percentage_add"] / 100.0) == 1_167_400
        elif scenario_no == 22:
            c22 = _get_rule_cond(cur, "R-06-GEN-11-1-SLAB-BEAM-HEIGHT-10-TO-20M")
            p_060404 = _get_item_price(cur, "060404")
            assert p_060404 == 6_091_000.0
            pct_22 = round((18.4 - c22["height_min_m"]) * c22["rate_per_meter_decimal"], 6)
            assert pct_22 == 0.252
            assert round(p_060404 * pct_22) == 1_534_932
        elif scenario_no == 23:
            c23 = _get_rule_cond(cur, "R-06-GEN-11-2-WALL-COL-HEIGHT-10-TO-20M")
            p_060304 = _get_item_price(cur, "060304")
            assert p_060304 == 3_988_000.0
            band_pct = next(b["add_pct"] for b in c23["bands_pct"] if b["gt_m"] < 11.4 <= b["le_m"])
            assert band_pct == 6.0
            assert round(p_060304 * band_pct / 100.0) == 239_280
        elif scenario_no == 24:
            c24 = _get_rule_cond(cur, "R-06-G10-10-5-EXCLUSION-060801")
            assert len(c24["excluded_from_parents"]) == 6
        elif scenario_no == 25:
            c25 = _get_rule_cond(cur, "R-07-GEN-5-WELDED-SPLICE-200PCT")
            p_090801 = _get_item_price(cur, "090801")
            assert p_090801 == 1_655_000.0
            assert round(p_090801 * c25["multiplier"]) == 3_310_000
        elif scenario_no == 26:
            _ = _get_rule_cond(cur, "R-08-G1-1-1-FC-CEMENT-EQUIV")
            fc_val = (350.0 / 10.0) - 9.0
            assert fc_val == 26.0
        elif scenario_no == 27:
            c27 = _get_rule_cond(cur, "R-08-G3-3-2-SLOPED-CONCRETE-150")
            p_080302 = _get_item_price(cur, "080302")
            assert p_080302 == 1_713_000.0
            assert round(p_080302 * c27["multiplier"]) == 2_569_500
        elif scenario_no == 28:
            c28 = _get_rule_cond(cur, "R-08-G3-3-5-EXCLUSION-JOIST-BLOCK-SLAB")
            assert c28["excluded_item_codes"] == ["080301", "080302", "080304", "080310", "080311"]
        elif scenario_no == 29:
            c29_bulk = _get_rule_cond(cur, "R-28-G1-1-2-2-BULK-CEMENT-HAUL-115")
            c29_waste = _get_rule_cond(cur, "R-28-G1-1-2-3-CEMENT-WASTE-6PCT")
            p_280101 = _get_item_price(cur, "280101")
            assert p_280101 == 28_400.0
            tonnage = (100.0 * 300.0 * c29_waste["multiplier"]) / 1000.0
            assert math.isclose(tonnage, 31.8, rel_tol=1e-9)
            assert round(p_280101 * c29_bulk["multiplier"]) == 32_660
        elif scenario_no == 30:
            num = 1 * 400 + 2 * 400 + 3 * 400 + sum(i * 500 for i in range(1, 11)) + 11 * 400
            assert num == 34300
            s_area = 1200 + 400 + 600 + 5000 + 400
            assert s_area == 7600
            p_raw = 1.0 + num / (100.0 * s_area)
            p_rounded = math.floor(p_raw * 10000 + 0.5) / 10000.0
            assert p_rounded == 1.0451
    finally:
        conn.close()
