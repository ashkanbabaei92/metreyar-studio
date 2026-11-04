#!/usr/bin/env python3
import sqlite3
from pathlib import Path

OUT_PATH = Path("/home/user/webapp/docs/step_c4_logs/inv_titles.txt")
DB_URI = "file:/home/user/webapp/data_processed/reference.db?mode=ro"
XLSX_CANDIDATES = [
    Path("/home/user/webapp/data/abn1404.xlsx"),
    Path("/home/user/webapp/data/فهرست‌بها (پایه محاسبات متره و برآورد)/abn1404.xlsx"),
]

lines = []

def log(s=""):
    lines.append(str(s))

conn = sqlite3.connect(DB_URI, uri=True)
cur = conn.cursor()

log("=== 1) DISTINCT discipline, year ===")
for tbl in ("chapters", "items", "notes", "rules"):
    cur.execute(f"PRAGMA table_info({tbl})")
    cols = [r[1] for r in cur.fetchall()]
    if "discipline" in cols and "year" in cols:
        cur.execute(f"SELECT DISTINCT discipline, year, COUNT(*) FROM {tbl} GROUP BY discipline, year")
        log(f"{tbl}: {cur.fetchall()}")
    elif "discipline" in cols:
        cur.execute(f"SELECT DISTINCT discipline, COUNT(*) FROM {tbl} GROUP BY discipline")
        log(f"{tbl} (no year col, cols={cols}): {cur.fetchall()}")

log("=== 2) sqlite_master & PRAGMA index_list (chapters, items) ===")
for tbl in ("chapters", "items"):
    cur.execute("SELECT type, name, sql FROM sqlite_master WHERE tbl_name=? ORDER BY type, name", (tbl,))
    for row in cur.fetchall():
        sql_oneline = " ".join(row[2].split()) if row[2] else None
        log(f"[{tbl}] {row[0]} {row[1]}: {sql_oneline}")
    cur.execute(f"PRAGMA index_list({tbl})")
    idx_list = cur.fetchall()
    log(f"[{tbl}] index_list: {idx_list}")
    for idx in idx_list:
        idx_name = idx[1]
        cur.execute(f"PRAGMA index_info({idx_name})")
        log(f"[{tbl}] index_info({idx_name}): {cur.fetchall()}")

log("=== 3) chapters title_official repr for 02, 05, 06, 09 ===")
cur.execute("SELECT chapter_code, title_official FROM chapters WHERE chapter_code IN ('02','05','06','09') ORDER BY chapter_code")
for code, title in cur.fetchall():
    log(f"chapter_code={code!r} -> repr={repr(title)}")

log("=== 4) items unit_price_rial NULL vs 0 ===")
cur.execute("""
SELECT COUNT(*),
       SUM(CASE WHEN unit_price_rial IS NULL THEN 1 ELSE 0 END),
       SUM(CASE WHEN unit_price_rial = 0 THEN 1 ELSE 0 END),
       SUM(CASE WHEN unit_price_rial > 0 THEN 1 ELSE 0 END)
FROM items
""")
log(f"ALL items (total, null, zero, pos): {cur.fetchone()}")

cur.execute("""
SELECT COUNT(*),
       SUM(CASE WHEN unit_price_rial IS NULL THEN 1 ELSE 0 END),
       SUM(CASE WHEN unit_price_rial = 0 THEN 1 ELSE 0 END),
       SUM(CASE WHEN unit_price_rial > 0 THEN 1 ELSE 0 END)
FROM items WHERE chapter_code = '99'
""")
log(f"chapter_code='99' (total, null, zero, pos): {cur.fetchone()}")
conn.close()

log("=== 5) abn1404.xlsx read_only inspection ===")
xlsx_path = next((p for p in XLSX_CANDIDATES if p.exists()), None)
log(f"xlsx_path found: {str(xlsx_path) if xlsx_path else None}")
if xlsx_path:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
        log(f"sheetnames: {wb.sheetnames}")
        ws = wb[wb.sheetnames[0]]
        log(f"sheet[0] title={ws.title!r}, dimensions={ws.calculate_dimension()!r}, max_row={ws.max_row}, max_column={ws.max_column}")
        row_iter = ws.iter_rows(values_only=True)
        for i in range(1, 4):
            try:
                log(f"row {i}: {repr(next(row_iter))}")
            except StopIteration:
                break
        fasl_hits = []
        for r_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
            for c_idx, val in enumerate(row, start=1):
                if val is not None and str(val).strip().startswith("فصل"):
                    fasl_hits.append((r_idx, c_idx, repr(val)))
                    if len(fasl_hits) >= 5:
                        break
            if len(fasl_hits) >= 5:
                break
        log(f"cells starting with 'فصل' (up to 5): {fasl_hits}")
        wb.close()
    except Exception as e:
        log(f"openpyxl error: {repr(e)}")

OUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("WROTE", OUT_PATH, "LINES", len(lines))
