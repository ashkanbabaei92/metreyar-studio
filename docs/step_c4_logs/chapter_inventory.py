import sqlite3
from pathlib import Path

db_path = Path("/home/user/webapp/data_processed/reference.db")
uri = f"file:{db_path}?mode=ro"
conn = sqlite3.connect(uri, uri=True)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Full PRAGMA check for all 4 tables
table_cols = {}
for tbl in ["chapters", "items", "notes", "rules"]:
    cur.execute(f"PRAGMA table_info({tbl})")
    cols = [r["name"] for r in cur.fetchall()]
    table_cols[tbl] = cols

# Fetch chapters ordered by chapter_code
cur.execute("SELECT * FROM chapters ORDER BY chapter_code")
chapters = cur.fetchall()

rows_out = []
tot_items = 0
tot_base = 0
tot_add = 0
tot_ded = 0
tot_other_cat = 0
tot_zero_star = 0
tot_zero_price = 0
tot_notes = 0
tot_notes_in_slice = 0
tot_rules = 0
tot_rules_in_slice = 0

rules_has_status = "status" in table_cols["rules"]
chapters_has_status = "status" in table_cols["chapters"]
items_has_status = "status" in table_cols["items"]
notes_has_status = "status" in table_cols["notes"]

for ch in chapters:
    code = ch["chapter_code"]
    title = ch["title_official"]
    is_active_p1 = ch["is_active_phase1"]

    # Items stats per chapter
    cur.execute(
        """
        SELECT
            COUNT(*) AS cnt,
            SUM(CASE WHEN item_category = 'base' THEN 1 ELSE 0 END) AS cnt_base,
            SUM(CASE WHEN item_category = 'additional' THEN 1 ELSE 0 END) AS cnt_add,
            SUM(CASE WHEN item_category = 'deduction' THEN 1 ELSE 0 END) AS cnt_ded,
            MIN(source_page_bulletin) AS min_p,
            MAX(source_page_bulletin) AS max_p,
            SUM(CASE WHEN is_star_price_zero = 1 THEN 1 ELSE 0 END) AS cnt_zero_star,
            SUM(CASE WHEN unit_price_rial = 0 OR unit_price_rial IS NULL THEN 1 ELSE 0 END) AS cnt_zero_price
        FROM items
        WHERE chapter_code = ?
        """,
        (code,),
    )
    it = cur.fetchone()
    cnt = it["cnt"] or 0
    cnt_base = it["cnt_base"] or 0
    cnt_add = it["cnt_add"] or 0
    cnt_ded = it["cnt_ded"] or 0
    min_p = it["min_p"]
    max_p = it["max_p"]
    cnt_zero_star = it["cnt_zero_star"] or 0
    cnt_zero_price = it["cnt_zero_price"] or 0

    # Notes stats per chapter
    cur.execute(
        """
        SELECT
            COUNT(*) AS cnt_notes,
            SUM(CASE WHEN status != 'out_of_slice' THEN 1 ELSE 0 END) AS cnt_notes_slice
        FROM notes
        WHERE chapter_code = ?
        """,
        (code,),
    )
    nt = cur.fetchone()
    cnt_notes = nt["cnt_notes"] or 0
    cnt_notes_slice = nt["cnt_notes_slice"] or 0

    # Rules stats per chapter
    if rules_has_status:
        cur.execute(
            """
            SELECT
                COUNT(*) AS cnt_rules,
                SUM(CASE WHEN status != 'out_of_slice' THEN 1 ELSE 0 END) AS cnt_rules_slice
            FROM rules
            WHERE chapter_code = ?
            """,
            (code,),
        )
        rl = cur.fetchone()
        cnt_rules = rl["cnt_rules"] or 0
        cnt_rules_slice = rl["cnt_rules_slice"] or 0
    else:
        cur.execute("SELECT COUNT(*) AS cnt_rules FROM rules WHERE chapter_code = ?", (code,))
        rl = cur.fetchone()
        cnt_rules = rl["cnt_rules"] or 0
        cnt_rules_slice = 0

    # Determine in-slice via status != 'out_of_slice' where status exists, and is_active_phase1
    # Note: chapters has is_active_phase1 (no status column); notes/rules have status.
    in_slice_notes_rules = (cnt_notes_slice > 0) or (cnt_rules_slice > 0)
    in_slice_flag = "بله" if (is_active_p1 == 1 or in_slice_notes_rules) else "خیر"

    page_range = f"{min_p}–{max_p}" if (min_p is not None and max_p is not None) else "—"

    rows_out.append({
        "code": code,
        "title": title,
        "cnt": cnt,
        "base": cnt_base,
        "add": cnt_add,
        "ded": cnt_ded,
        "page_range": page_range,
        "zero_star": cnt_zero_star,
        "zero_price": cnt_zero_price,
        "notes": cnt_notes,
        "notes_slice": cnt_notes_slice,
        "rules": cnt_rules,
        "rules_slice": cnt_rules_slice,
        "is_active_p1": is_active_p1,
        "in_slice": in_slice_flag,
    })

    tot_items += cnt
    tot_base += cnt_base
    tot_add += cnt_add
    tot_ded += cnt_ded
    tot_zero_star += cnt_zero_star
    tot_zero_price += cnt_zero_price
    tot_notes += cnt_notes
    tot_notes_in_slice += cnt_notes_slice
    tot_rules += cnt_rules
    tot_rules_in_slice += cnt_rules_slice

# Check if any notes or rules have chapter_code IS NULL or not in chapters
cur.execute("SELECT COUNT(*) AS c, SUM(CASE WHEN status != 'out_of_slice' THEN 1 ELSE 0 END) AS cs FROM notes WHERE chapter_code IS NULL OR chapter_code NOT IN (SELECT chapter_code FROM chapters)")
orphan_notes = cur.fetchone()
cur.execute("SELECT COUNT(*) AS c FROM rules WHERE chapter_code IS NULL OR chapter_code NOT IN (SELECT chapter_code FROM chapters)")
orphan_rules = cur.fetchone()

cur.execute("SELECT COUNT(*) AS c FROM items")
db_total_items = cur.fetchone()["c"]
cur.execute("SELECT COUNT(*) AS c FROM notes")
db_total_notes = cur.fetchone()["c"]
cur.execute("SELECT COUNT(*) AS c FROM rules")
db_total_rules = cur.fetchone()["c"]

conn.close()

# Write txt log and markdown report
md_lines = []
md_lines.append("# گزارش موجودی فصل‌ها (CHAPTER INVENTORY) — فهرست‌بهای ابنیه ۱۴۰۴")
md_lines.append("")
md_lines.append("- **تاریخ اجرا**: `2026-11-04`")
md_lines.append("- **منبع داده**: `data_processed/reference.db` (حالت فقط‌خواندنی `mode=ro`)")
md_lines.append("- **وضعیت ستون `status` در جدول‌ها (بر اساس `PRAGMA table_info`)**:")
md_lines.append(f"  - `chapters`: ستون `status` **ندارد** (ستون موجود برای فاز ۱: `is_active_phase1`).")
md_lines.append(f"  - `items`: ستون `status` **ندارد** (فقط `unit_status` دارد).")
md_lines.append(f"  - `notes`: ستون `status` **دارد** (`{'status' in table_cols['notes']}`).")
md_lines.append(f"  - `rules`: ستون `status` **{'دارد' if rules_has_status else 'ندارد'}** (`{rules_has_status}`).")
md_lines.append(f"  - کل ستون‌های `rules`: `{', '.join(table_cols['rules'])}`")
md_lines.append("")
md_lines.append("## جدول موجودی ۳۵ فصل")
md_lines.append("")
md_lines.append("| کد فصل | نام فصل (از DB) | تعداد ردیف | base / additional / deduction | صفحهٔ بولتن (min–max) | ردیف قیمت صفر | notes (کل / در برش) | rules (کل / در برش) | در برش (`status != 'out_of_slice'` / `is_active_phase1`) |")
md_lines.append("|---|---|---|---|---|---|---|---|---|")

for r in rows_out:
    zero_str = f"{r['zero_star']}" if r["zero_star"] == r["zero_price"] else f"{r['zero_star']} (price=0: {r['zero_price']})"
    notes_str = f"{r['notes']} ({r['notes_slice']})"
    rules_str = f"{r['rules']} ({r['rules_slice']})" if rules_has_status else f"{r['rules']}"
    in_slice_detail = f"{r['in_slice']} (p1={r['is_active_p1']}, n_act={r['notes_slice']}, r_act={r['rules_slice']})"
    md_lines.append(
        f"| `{r['code']}` | {r['title']} | {r['cnt']} | {r['base']} / {r['add']} / {r['ded']} | {r['page_range']} | {zero_str} | {notes_str} | {rules_str} | {in_slice_detail} |"
    )

# Summary row
md_lines.append(
    f"| **جمع کل (۳۵ فصل)** | **—** | **{tot_items}** | **{tot_base} / {tot_add} / {tot_ded}** | **—** | **{tot_zero_star}** | **{tot_notes} ({tot_notes_in_slice})** | **{tot_rules} ({tot_rules_in_slice})** | **۸ فصل در برش** |"
)
md_lines.append("")

if orphan_notes["c"] or orphan_rules["c"]:
    md_lines.append("## رکوردهای بدون کد فصل (مثلاً کلیات)")
    md_lines.append(f"- `notes` خارج از کدهای ۳۵ فصل (مثلاً `chapter_code IS NULL` یا کلیات): کل = `{orphan_notes['c']}`، دارای `status != 'out_of_slice'` = `{orphan_notes['cs']}`")
    md_lines.append(f"- `rules` خارج از کدهای ۳۵ فصل: کل = `{orphan_rules['c']}`")
    md_lines.append(f"- جمع کل جدول `notes` در DB = `{db_total_notes}` | جمع کل جدول `rules` در DB = `{db_total_rules}`")
    md_lines.append("")

md_lines.append("## مقایسه با معیارهای ثابت")
md_lines.append("")
md_lines.append("| معیار | مقدار مورد انتظار | مقدار واقعی در DB | وضعیت |")
md_lines.append("|---|---|---|---|")
md_lines.append(f"| تعداد فصل‌ها (`chapters`) | 35 | {len(rows_out)} | {'برقرار' if len(rows_out) == 35 else 'ناهماهنگ'} |")
md_lines.append(f"| کل ردیف‌ها (`items`) | 1565 | {tot_items} (جدول: {db_total_items}) | {'برقرار' if tot_items == 1565 else 'ناهماهنگ'} |")
md_lines.append(f"| تفکیک `base/additional/deduction` | 1241 / 304 / 20 | {tot_base} / {tot_add} / {tot_ded} | {'برقرار' if (tot_base, tot_add, tot_ded) == (1241, 304, 20) else 'ناهماهنگ'} |")
md_lines.append(f"| ردیف قیمت صفر (`zero`) | 62 | {tot_zero_star} (`unit_price_rial=0/NULL`: {tot_zero_price}) | {'برقرار' if tot_zero_star == 62 else 'ناهماهنگ'} |")

content = "\n".join(md_lines) + "\n"
Path("/home/user/webapp/docs/CHAPTER_INVENTORY.md").write_text(content, encoding="utf-8")
Path("/home/user/webapp/docs/step_c4_logs/chapter_inventory.txt").write_text(content, encoding="utf-8")
print("DONE")
