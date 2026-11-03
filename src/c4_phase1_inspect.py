#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
اسکریپت کمکی بررسی E0 تا E3 برای فاز ۱ گام ج-۴.
"""

import re
import sqlite3
import subprocess
from pathlib import Path
import pymupdf as fitz

from src.config import REFERENCE_DB_PATH, DISCIPLINES
from src.pdf_fix import open_patched_pdf, extract_rtl_page_lines
from src.normalize import normalize_text


def run_phase1_checks() -> None:
    conn = sqlite3.connect(str(REFERENCE_DB_PATH))
    cur = conn.cursor()

    print("=== E0: Database Counts ===")
    cur.execute("PRAGMA user_version;")
    print("user_version:", cur.fetchone()[0])
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;")
    tables = [r[0] for r in cur.fetchall()]
    print("table_count:", len(tables), tables)

    schema_lines = []
    for tname in ("notes", "rules", "item_dependencies", "items"):
        cur.execute(f"PRAGMA table_info({tname});")
        cols = cur.fetchall()
        schema_lines.append(f"=== {tname} ===")
        for c in cols:
            schema_lines.append(str(c))
    Path("docs/step_c4_logs/schema_columns.txt").write_text("\n".join(schema_lines) + "\n", encoding="utf-8")

    cur.execute("SELECT COUNT(*) FROM notes;")
    print("notes_total:", cur.fetchone()[0])
    cur.execute("SELECT status, COUNT(*) FROM notes GROUP BY status;")
    print("notes_by_status:", cur.fetchall())

    cur.execute("SELECT COUNT(*) FROM item_dependencies;")
    print("item_dependencies_total:", cur.fetchone()[0])
    cur.execute("SELECT resolution_status, COUNT(*) FROM item_dependencies GROUP BY resolution_status;")
    print("deps_by_resolution:", cur.fetchall())
    cur.execute(
        """
        SELECT resolution_status,
               CASE WHEN substr(child_code_official,1,2) IN ('01','02','03','05','06','07','08','28') THEN 'in_slice' ELSE 'out_of_slice' END AS slice_grp,
               COUNT(*)
        FROM item_dependencies
        GROUP BY resolution_status, slice_grp;
        """
    )
    print("deps_by_res_and_slice:", cur.fetchall())

    cur.execute("SELECT COUNT(*) FROM rules;")
    print("rules_total:", cur.fetchone()[0])
    cur.execute("SELECT status, COUNT(*) FROM rules GROUP BY status;")
    print("rules_by_status:", cur.fetchall())
    cur.execute(
        """
        SELECT status,
               CASE WHEN chapter_code IN ('01','02','03','05','06','07','08','28','A2','A3','A4','GEN','INS','99') THEN 'in_slice_or_app' ELSE 'out_of_slice' END AS slice_grp,
               COUNT(*)
        FROM rules
        GROUP BY status, slice_grp;
        """
    )
    print("rules_by_status_and_slice:", cur.fetchall())
    cur.execute("SELECT chapter_code, status, COUNT(*) FROM rules GROUP BY chapter_code, status;")
    print("rules_by_chapter_status:", cur.fetchall())

    print("\n=== E1: Evidence PNGs ===")
    ev_dir = Path("docs/evidence")
    ls_files = sorted([p.name for p in ev_dir.glob("*.png")])
    git_ls = subprocess.check_output(["git", "ls-files", "docs/evidence/*.png"], text=True).strip().splitlines()
    git_ls_names = sorted([Path(x).name for x in git_ls])
    print(f"ls count: {len(ls_files)}, git ls-files count: {len(git_ls_names)}")

    doc_files = list(Path("docs").glob("*.md")) + list(Path("docs").glob("*.json"))
    for fname in ls_files:
        commit_out = subprocess.check_output(
            ["git", "log", "--oneline", "-1", "--", f"docs/evidence/{fname}"], text=True
        ).strip()
        refs = []
        for df in doc_files:
            txt = df.read_text(encoding="utf-8", errors="ignore")
            if fname in txt:
                refs.append(df.name)
        print(f"  {fname} | commit: {commit_out} | refs: {refs}")

    print("\n=== E2: 62 Zero-Price Items ===")
    active_ch = set(DISCIPLINES["abnieh"]["active_phase1_chapters"])
    cur.execute(
        """
        SELECT code_official, chapter_code, item_category, unit, unit_price_rial, is_star_price_zero, source_page_bulletin, source_page_pdf, description
        FROM items
        WHERE is_star_price_zero = 1 OR unit_price_rial = 0
        ORDER BY code_official;
        """
    )
    rows_62 = cur.fetchall()
    print("Total rows with is_star_price_zero=1 or unit_price_rial=0:", len(rows_62))
    in_slice = [r for r in rows_62 if r[1] in active_ch]
    out_slice = [r for r in rows_62 if r[1] not in active_ch]
    print(f"In-slice count: {len(in_slice)}, Out-of-slice count: {len(out_slice)}")

    print("--- In-slice breakdown by chapter & category ---")
    cur.execute(
        """
        SELECT chapter_code, item_category, COUNT(*), GROUP_CONCAT(code_official, ', ')
        FROM items
        WHERE is_star_price_zero = 1 AND chapter_code IN ('01','02','03','05','06','07','08','28')
        GROUP BY chapter_code, item_category
        ORDER BY chapter_code, item_category;
        """
    )
    for r in cur.fetchall():
        print("  IN_SLICE:", r)

    print("--- Out-of-slice breakdown by chapter & category ---")
    cur.execute(
        """
        SELECT chapter_code, item_category, COUNT(*), GROUP_CONCAT(code_official, ', ')
        FROM items
        WHERE is_star_price_zero = 1 AND chapter_code NOT IN ('01','02','03','05','06','07','08','28')
        GROUP BY chapter_code, item_category
        ORDER BY chapter_code, item_category;
        """
    )
    for r in cur.fetchall():
        print("  OUT_SLICE:", r)

    print("\n=== E3: Clauses 2-6, 2-8, 2-17-1 in notes table ===")
    cur.execute(
        """
        SELECT id, section_type, chapter_code, group_code, clause_number, clause_ref,
               source_page_bulletin, source_page_pdf, text_raw, text_normalized
        FROM notes
        WHERE clause_number IN ('2-6', '2-8', '2-17-1', '2-7-1', '2-7-2', '2-7-3')
        ORDER BY source_page_pdf, id;
        """
    )
    for r in cur.fetchall():
        print(f"NOTE id={r[0]} | sec={r[1]} | ch={r[2]} | grp={r[3]} | num={r[4]} | ref={r[5]} | bul={r[6]} | pdf={r[7]}")
        print(f"  RAW : {r[8]}")
        print(f"  NORM: {r[9]}")

    conn.close()

    # Check PDF pages 4, 5, 6 and 250 directly from patched PDF
    pdf_path = DISCIPLINES["abnieh"]["pdf_file"]
    doc = open_patched_pdf(pdf_path)
    for p_pdf in [4, 5, 6, 250]:
        print(f"\n--- Patched PDF Page {p_pdf} (Bulletin {p_pdf - 3}) ---")
        lines = extract_rtl_page_lines(doc[p_pdf - 1])
        for idx, l in enumerate(lines, 1):
            print(f"  L{idx:02d}: {l}")

    # Render page 6 if not exists
    p6_png = Path("docs/evidence/abnieh_page_6_instructions_2_8_cont.png")
    if not p6_png.exists():
        page6 = doc[5]
        pix = page6.get_pixmap(dpi=200)
        pix.save(str(p6_png))
        print(f"\nRendered {p6_png} ({p6_png.stat().st_size} bytes)")
    doc.close()


if __name__ == "__main__":
    run_phase1_checks()
