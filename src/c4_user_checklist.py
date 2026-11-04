#!/usr/bin/env python3
import os
import re
import glob
import sqlite3
from pathlib import Path

ROOT = Path("/home/user/webapp")
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
EVIDENCE_DIR = DOCS_DIR / "evidence"
OUT_FILE = DOCS_DIR / "USER_VISUAL_CHECKLIST.md"


def find_db():
    # Locate non-empty sqlite db in data/ or build from data/schema.sql or seed script if empty
    candidates = list(DATA_DIR.glob("*.db")) + list(DATA_DIR.glob("**/*.db")) + list(ROOT.glob("**/*.db"))
    for p in sorted(set(candidates), key=lambda x: x.stat().st_size if x.exists() else 0, reverse=True):
        if p.exists() and p.stat().st_size > 0:
            conn = sqlite3.connect(str(p))
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in cur.fetchall()]
            conn.close()
            if tables:
                return p
    return DATA_DIR / "reference.db"


def get_table_cols(conn, table):
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    return [r[1] for r in cur.fetchall()]


def load_sensitive_numbers():
    sn_path = DOCS_DIR / "sensitive_numbers.md"
    if not sn_path.exists():
        return "", []
    text = sn_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    return text, lines


def load_doc_text(filename):
    p = DOCS_DIR / filename
    if p.exists():
        return p.read_text(encoding="utf-8")
    return ""


def find_evidence_images(pdf_page):
    pattern = f"abnieh_page_{pdf_page}_*.png"
    matches = sorted(EVIDENCE_DIR.glob(pattern))
    if not matches:
        return ["تصویر نیست"]
    return [f"docs/evidence/{m.name}" for m in matches]


def trunc(text, max_len=300):
    if text is None:
        return "—"
    s = " ".join(str(text).split())
    if len(s) > max_len:
        return s[:max_len] + "…"
    return s


def pick_col(row_dict, candidates, default="—"):
    for c in candidates:
        if c in row_dict and row_dict[c] not in (None, ""):
            return str(row_dict[c])
    return default


def main():
    db_path = find_db()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    all_tables = [r[0] for r in cur.fetchall()]

    schema_info = {}
    for t in ["notes", "rules", "items", "item_dependencies", "coefficients"]:
        if t in all_tables:
            schema_info[t] = get_table_cols(conn, t)
        else:
            schema_info[t] = []

    _, sn_lines = load_sensitive_numbers()
    ambig_text = load_doc_text("ambiguous_rules.md")
    c4_dec_text = load_doc_text("STEP_C4_DECISIONS.md")

    # Helper to find sensitive_numbers entries matching page or clause
    def match_sensitive_numbers(pdf_page, clauses=None):
        bulletin_page = pdf_page - 3
        hits = []
        for ln in sn_lines:
            ln_s = ln.strip()
            if not ln_s or ln_s.startswith("#") or set(ln_s) <= {"|", "-", ":", " "}:
                continue
            # Check if line mentions pdf_page or bulletin_page or any clause
            page_hit = (
                re.search(rf"\b{pdf_page}\b", ln_s)
                or re.search(rf"\b{bulletin_page}\b", ln_s)
            )
            clause_hit = False
            if clauses:
                for cl in clauses:
                    if cl and cl != "—" and cl in ln_s:
                        clause_hit = True
                        break
            if page_hit or clause_hit:
                hits.append(trunc(ln_s, 180))
        return hits[:3]

    # Query rows by source_page_pdf across notes, rules, items, coefficients
    def query_by_pdf_page(pdf_page):
        results = []
        for tbl in ["notes", "rules", "items", "coefficients"]:
            cols = schema_info.get(tbl, [])
            if not cols:
                continue
            page_col = None
            for pc in ["source_page_pdf", "pdf_page", "page_pdf"]:
                if pc in cols:
                    page_col = pc
                    break
            if not page_col:
                continue
            cur.execute(f"SELECT * FROM {tbl} WHERE {page_col} = ?", (pdf_page,))
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                clause_val = pick_col(
                    r,
                    ["clause_ref", "source_clause_ref", "item_code", "code", "rule_id", "note_id", "coeff_code", "id"],
                )
                text_val = pick_col(
                    r,
                    ["text_normalized", "description_normalized", "text", "description", "title", "rule_text", "note_text", "name"],
                )
                b_page = pick_col(r, ["source_page_bulletin", "bulletin_page", "page_bulletin"], str(pdf_page - 3))
                # Stored numeric/value columns in DB if any
                val_parts = []
                for vc in [
                    "unit_price",
                    "price",
                    "value",
                    "coeff_value",
                    "rate",
                    "percentage",
                    "amount",
                    "formula",
                    "condition",
                    "unit",
                    "status",
                    "rule_type",
                ]:
                    if vc in r and r[vc] not in (None, ""):
                        val_parts.append(f"{vc}={r[vc]}")
                db_val_str = f"DB({tbl}): " + ", ".join(val_parts) if val_parts else ""
                results.append(
                    {
                        "table": tbl,
                        "clause_ref": clause_val,
                        "text_normalized": trunc(text_val, 300),
                        "source_page_pdf": r.get(page_col, pdf_page),
                        "source_page_bulletin": b_page,
                        "db_val": db_val_str,
                    }
                )
        return results

    visual_cases = [
        (1, [5, 250]),
        (2, [8]),
        (3, [30, 35]),
        (4, [50, 51, 53]),
        (5, [64]),
        (6, [242, 243, 246]),
        (7, [4, 5]),
    ]

    md_lines = []
    md_lines.append("# چک‌لیست بازدید چشمی و تصمیم‌های کاربر (گام ج-۴)")
    md_lines.append("")
    md_lines.append(f"- **پایگاه دادهٔ مرجع**: `{db_path.relative_to(ROOT)}`")
    md_lines.append("- **قاعدهٔ تبدیل صفحه**: `صفحهٔ PDF = صفحهٔ بولتن + ۳`")
    md_lines.append("- **توضیح**: تمام متون، کدها، بندها و مقادیر زیر مستقیماً و بدون دخل‌وتصرف از جداول پایگاه داده (`reference.db`) و مستندات (`docs/`) استخراج شده‌اند.")
    md_lines.append("")
    md_lines.append("---")
    md_lines.append("")
    md_lines.append("## بخش الف) موارد هفت‌گانهٔ بازدید چشمی (۱ تا ۷)")
    md_lines.append("")

    case_stats = {}

    for case_num, pages in visual_cases:
        total_rows_for_case = 0
        has_any_img = False
        page_rows_map = {}
        page_imgs_map = {}
        topic_candidates = []

        for pg in pages:
            imgs = find_evidence_images(pg)
            if imgs != ["تصویر نیست"]:
                has_any_img = True
            page_imgs_map[pg] = imgs
            rows = query_by_pdf_page(pg)
            total_rows_for_case += len(rows)
            page_rows_map[pg] = rows[:5]
            for r in rows[:2]:
                if r["text_normalized"] and r["text_normalized"] != "—":
                    topic_candidates.append(f"[{r['table']}:{r['clause_ref']}] {trunc(r['text_normalized'], 90)}")

        topic_str = " / ".join(topic_candidates[:2]) if topic_candidates else "موضوع نامشخص"
        case_stats[case_num] = (total_rows_for_case, has_any_img)

        pages_str = "، ".join([f"{p} (بولتن {p-3})" for p in pages])
        md_lines.append(f"### مورد {case_num} — صفحات PDF: {pages_str}")
        md_lines.append(f"- **موضوع (از داده‌های ذخیره‌شده)**: {topic_str}")
        md_lines.append(f"- **تعداد کل رکوردهای یافت‌شده در DB برای این صفحات**: {total_rows_for_case} (حداکثر ۵ مورد به ازای هر صفحه نمایش داده شده است)")
        md_lines.append("")
        md_lines.append("| صفحه PDF (بولتن) | مسیر تصویر در `docs/evidence` | جدول منبع | بند / کد (`clause_ref`) | متن ذخیره‌شده (`text_normalized` ≤۳۰۰ نویسه) | مقدار ذخیره‌شده (با نام منبع) |")
        md_lines.append("|---|---|---|---|---|---|")

        for pg in pages:
            imgs_str = "<br>".join(f"`{im}`" if im != "تصویر نیست" else im for im in page_imgs_map[pg])
            rows = page_rows_map[pg]
            if not rows:
                sn_hits = match_sensitive_numbers(pg, [])
                sn_str = "<br>".join([f"`docs/sensitive_numbers.md`: {h}" for h in sn_hits]) if sn_hits else "—"
                md_lines.append(
                    f"| {pg} ({pg-3}) | {imgs_str} | — | — | موردی با `source_page_pdf={pg}` در جداول DB یافت نشد | {sn_str} |"
                )
            else:
                clauses_on_page = [r["clause_ref"] for r in rows]
                sn_hits = match_sensitive_numbers(pg, clauses_on_page)
                sn_str = ("<br>`docs/sensitive_numbers.md`: " + " ؛ ".join(sn_hits)) if sn_hits else ""
                for r in rows:
                    val_combined = r["db_val"]
                    if sn_str:
                        val_combined = (val_combined + sn_str) if val_combined else sn_str.lstrip("<br>")
                    if not val_combined:
                        val_combined = "—"
                    safe_text = r["text_normalized"].replace("|", "\\|")
                    safe_val = val_combined.replace("|", "\\|")
                    md_lines.append(
                        f"| {r['source_page_pdf']} ({r['source_page_bulletin']}) | {imgs_str} | `{r['table']}` | `{r['clause_ref']}` | {safe_text} | {safe_val} |"
                    )

        md_lines.append("")
        md_lines.append("نظر کاربر: ____")
        md_lines.append("")

    # Section B: D-B
    md_lines.append("---")
    md_lines.append("")
    md_lines.append("## بخش ب) تصمیم D-B — بررسی چهار کد `020201`, `020304`, `030603`, `031204`")
    md_lines.append("")

    db_codes = ["020201", "020304", "030603", "031204"]
    db_found_count = 0

    for code in db_codes:
        ch_prefix = code[:2]  # e.g. '02', '03'
        ch_int_str = str(int(ch_prefix))  # '2', '3'

        md_lines.append(f"### کد ردیف `{code}`")

        # 1. items table
        item_cols = schema_info.get("items", [])
        code_col = "item_code" if "item_code" in item_cols else ("code" if "code" in item_cols else item_cols[0])
        cur.execute(f"SELECT * FROM items WHERE {code_col} = ?", (code,))
        item_rows = [dict(r) for r in cur.fetchall()]
        db_found_count += len(item_rows)

        if item_rows:
            for ir in item_rows:
                desc = pick_col(ir, ["text_normalized", "description_normalized", "description", "text", "title"])
                unit = pick_col(ir, ["unit", "unit_normalized"])
                price = pick_col(ir, ["unit_price", "price", "rate"])
                p_pdf = pick_col(ir, ["source_page_pdf", "pdf_page"])
                p_bul = pick_col(ir, ["source_page_bulletin", "bulletin_page"])
                c_ref = pick_col(ir, ["clause_ref", "source_clause_ref", code_col])
                md_lines.append(
                    f"- **مشخصات در جدول `items`**: کد `{code}` | واحد: `{unit}` | بها: `{price}` | بند/مرجع: `{c_ref}` | صفحهٔ PDF: `{p_pdf}` (بولتن: `{p_bul}`)"
                )
                md_lines.append(f"  - **متن ردیف**: {desc}")
        else:
            md_lines.append(f"- **مشخصات در جدول `items`**: یافت نشد.")

        # 2. item_dependencies related to this code
        dep_cols = schema_info.get("item_dependencies", [])
        dep_rows = []
        if dep_cols:
            conds = []
            params = []
            for c in dep_cols:
                if any(k in c.lower() for k in ["item", "code", "clause", "ref", "source", "target", "base", "derived"]):
                    conds.append(f"CAST({c} AS TEXT) LIKE ?")
                    params.append(f"%{code}%")
            if conds:
                cur.execute(f"SELECT * FROM item_dependencies WHERE {' OR '.join(conds)}", params)
                dep_rows = [dict(r) for r in cur.fetchall()]
        db_found_count += len(dep_rows)

        md_lines.append(f"- **وابستگی‌ها در `item_dependencies` مرتبط با `{code}`** ({len(dep_rows)} مورد):")
        if dep_rows:
            for dr in dep_rows:
                dr_str = " | ".join([f"`{k}`={v}" for k, v in dr.items() if v not in (None, "")])
                md_lines.append(f"  - {dr_str}")
        else:
            md_lines.append("  - موردی در `item_dependencies` با ذکر مستقیم این کد یافت نشد.")

        # Collect clause_refs from item_dependencies or items to also search notes & rules
        related_clauses = set()
        for dr in dep_rows:
            for k, v in dr.items():
                if "clause" in k.lower() and v:
                    related_clauses.add(str(v))

        # 3. rules table related to code or related_clauses
        rule_cols = schema_info.get("rules", [])
        rule_rows = []
        if rule_cols:
            conds = []
            params = []
            for c in rule_cols:
                conds.append(f"CAST({c} AS TEXT) LIKE ?")
                params.append(f"%{code}%")
                for rc in related_clauses:
                    conds.append(f"CAST({c} AS TEXT) = ?")
                    params.append(rc)
            cur.execute(f"SELECT * FROM rules WHERE {' OR '.join(conds)}", params)
            rule_rows = [dict(r) for r in cur.fetchall()]
        db_found_count += len(rule_rows)

        md_lines.append(f"- **قواعد در جدول `rules` مرتبط با `{code}`** ({len(rule_rows)} مورد):")
        if rule_rows:
            for rr in rule_rows:
                r_clause = pick_col(rr, ["clause_ref", "source_clause_ref", "rule_id", "id"])
                r_pdf = pick_col(rr, ["source_page_pdf", "pdf_page"])
                r_bul = pick_col(rr, ["source_page_bulletin", "bulletin_page"])
                r_txt = pick_col(rr, ["text_normalized", "text", "description", "rule_text"])
                extra_meta = ", ".join(
                    [
                        f"{k}={rr[k]}"
                        for k in ["rule_type", "status", "target_items", "source_items", "unit", "value"]
                        if k in rr and rr[k] not in (None, "")
                    ]
                )
                md_lines.append(
                    f"  - **بند/شناسه**: `{r_clause}` | **صفحه PDF**: `{r_pdf}` (بولتن: `{r_bul}`) | {extra_meta}"
                )
                md_lines.append(f"    - **متن عینی**: {r_txt}")
        else:
            md_lines.append("  - موردی در `rules` یافت نشد.")

        # 4. notes table related to code or related_clauses
        note_cols = schema_info.get("notes", [])
        note_rows = []
        if note_cols:
            conds = []
            params = []
            for c in note_cols:
                conds.append(f"CAST({c} AS TEXT) LIKE ?")
                params.append(f"%{code}%")
                for rc in related_clauses:
                    conds.append(f"CAST({c} AS TEXT) = ?")
                    params.append(rc)
            cur.execute(f"SELECT * FROM notes WHERE {' OR '.join(conds)}", params)
            note_rows = [dict(r) for r in cur.fetchall()]
        db_found_count += len(note_rows)

        md_lines.append(f"- **بندها در جدول `notes` مرتبط با `{code}`** ({len(note_rows)} مورد):")
        if note_rows:
            for nr in note_rows:
                n_clause = pick_col(nr, ["clause_ref", "source_clause_ref", "note_id", "id"])
                n_pdf = pick_col(nr, ["source_page_pdf", "pdf_page"])
                n_bul = pick_col(nr, ["source_page_bulletin", "bulletin_page"])
                n_txt = pick_col(nr, ["text_normalized", "text", "note_text", "description"])
                md_lines.append(f"  - **بند**: `{n_clause}` | **صفحه PDF**: `{n_pdf}` (بولتن: `{n_bul}`)")
                md_lines.append(f"    - **متن عینی**: {n_txt}")
        else:
            md_lines.append("  - موردی در `notes` یافت نشد.")

        md_lines.append("")

    md_lines.append("نظر کاربر: ____")
    md_lines.append("")

    # Section C: D-C1
    md_lines.append("---")
    md_lines.append("")
    md_lines.append("## بخش پ) تصمیم D-C1 — فهرست همهٔ ردیف‌ها و قواعد با واحد «درصد»")
    md_lines.append("")

    # 1. Items in `items` table where unit contains 'درصد'
    item_cols = schema_info.get("items", [])
    unit_col = "unit" if "unit" in item_cols else ("unit_normalized" if "unit_normalized" in item_cols else None)
    pct_items = []
    if unit_col:
        cur.execute(f"SELECT * FROM items WHERE {unit_col} LIKE '%درصد%' ORDER BY 1")
        pct_items = [dict(r) for r in cur.fetchall()]

    md_lines.append(f"### ۱) ردیف‌های جدول `items` که واحد آن‌ها «درصد» است ({len(pct_items)} ردیف)")
    md_lines.append("")
    md_lines.append("| کد ردیف | واحد | بها (`unit_price`) | بند/فصل (`clause_ref`) | صفحه PDF (بولتن) | متن عینی (`text_normalized`) |")
    md_lines.append("|---|---|---|---|---|---|")
    for ir in pct_items:
        c_code = pick_col(ir, ["item_code", "code", "id"])
        c_unit = pick_col(ir, ["unit", "unit_normalized"])
        c_price = pick_col(ir, ["unit_price", "price", "rate"])
        c_ref = pick_col(ir, ["clause_ref", "source_clause_ref", "chapter_code", "chapter"])
        c_pdf = pick_col(ir, ["source_page_pdf", "pdf_page"])
        c_bul = pick_col(ir, ["source_page_bulletin", "bulletin_page"])
        c_txt = pick_col(ir, ["text_normalized", "description_normalized", "description", "text"]).replace("|", "\\|")
        md_lines.append(f"| `{c_code}` | `{c_unit}` | `{c_price}` | `{c_ref}` | {c_pdf} ({c_bul}) | {c_txt} |")
    md_lines.append("")

    # 2. Rules in `rules` table related to percentage / out_of_slice / unit='درصد'
    rule_cols = schema_info.get("rules", [])
    pct_rules = []
    if rule_cols:
        conds = []
        for c in rule_cols:
            conds.append(f"CAST({c} AS TEXT) LIKE '%درصد%'")
            conds.append(f"CAST({c} AS TEXT) LIKE '%percent%'")
            conds.append(f"CAST({c} AS TEXT) LIKE '%out_of_slice%'")
        cur.execute(f"SELECT * FROM rules WHERE {' OR '.join(conds)} ORDER BY 1")
        all_cand_rules = [dict(r) for r in cur.fetchall()]
        # Keep rules that either mention درصد or are out_of_slice with درصد or link to pct_items
        pct_item_codes = {pick_col(ir, ["item_code", "code", "id"]) for ir in pct_items}
        for rr in all_cand_rules:
            row_str = " ".join(str(v) for v in rr.values() if v is not None)
            if "درصد" in row_str or "percent" in row_str.lower() or any(ic in row_str for ic in pct_item_codes):
                pct_rules.append(rr)

    md_lines.append(f"### ۲) قواعد جدول `rules` مرتبط با «درصد» (شامل قواعد `out_of_slice` دارای درصد) ({len(pct_rules)} قاعده)")
    md_lines.append("")
    md_lines.append("| شناسه / کد | بند (`clause_ref`) | وضعیت / نوع | صفحه PDF (بولتن) | متن عینی (`text_normalized`) |")
    md_lines.append("|---|---|---|---|---|")
    for rr in pct_rules:
        r_id = pick_col(rr, ["rule_id", "item_code", "code", "id"])
        r_clause = pick_col(rr, ["clause_ref", "source_clause_ref"])
        r_type_stat = " / ".join(
            [str(rr[k]) for k in ["status", "slice_status", "scope_status", "rule_type", "category", "unit"] if k in rr and rr[k] not in (None, "")]
        ) or "—"
        r_pdf = pick_col(rr, ["source_page_pdf", "pdf_page"])
        r_bul = pick_col(rr, ["source_page_bulletin", "bulletin_page"])
        r_txt = pick_col(rr, ["text_normalized", "text", "description", "rule_text"]).replace("|", "\\|")
        md_lines.append(f"| `{r_id}` | `{r_clause}` | `{r_type_stat}` | {r_pdf} ({r_bul}) | {r_txt} |")
    md_lines.append("")

    # 3. Also include raw entries from docs/ambiguous_rules.md if present
    if ambig_text.strip():
        md_lines.append("### ۳) متن مستند `docs/ambiguous_rules.md` (جهت تطبیق ردیف‌های درصدی و قواعد خارج از اسلایس)")
        md_lines.append("")
        for ln in ambig_text.splitlines():
            if any(k in ln for k in ["درصد", "out_of_slice", "|", "0", "1", "2", "3", "بند", "صفحه"]):
                md_lines.append(ln)
        md_lines.append("")

    md_lines.append("نظر کاربر: ____")
    md_lines.append("")

    # Section D: D-F
    md_lines.append("---")
    md_lines.append("")
    md_lines.append("## بخش ت) تصمیم D-F — ۳ نمونهٔ واقعی از `item_dependencies` با `clause_derived_item`")
    md_lines.append("")

    dep_cols = schema_info.get("item_dependencies", [])
    df_samples = []
    if dep_cols:
        conds = [f"CAST({c} AS TEXT) = 'clause_derived_item'" for c in dep_cols]
        cur.execute(f"SELECT * FROM item_dependencies WHERE {' OR '.join(conds)} LIMIT 3")
        df_samples = [dict(r) for r in cur.fetchall()]
        if not df_samples:
            conds_like = [f"CAST({c} AS TEXT) LIKE '%clause_derived_item%'" for c in dep_cols]
            cur.execute(f"SELECT * FROM item_dependencies WHERE {' OR '.join(conds_like)} LIMIT 3")
            df_samples = [dict(r) for r in cur.fetchall()]

    if df_samples:
        headers = list(df_samples[0].keys())
        md_lines.append("| " + " | ".join(f"`{h}`" for h in headers) + " |")
        md_lines.append("|" + "|".join(["---"] * len(headers)) + "|")
        for s in df_samples:
            row_vals = [str(s[h]).replace("|", "\\|") if s[h] is not None else "NULL" for h in headers]
            md_lines.append("| " + " | ".join(row_vals) + " |")
    else:
        md_lines.append("نمونه‌ای با مقدار `clause_derived_item` در `item_dependencies` یافت نشد.")

    md_lines.append("")
    md_lines.append("نظر کاربر: ____")
    md_lines.append("")

    OUT_FILE.write_text("\n".join(md_lines), encoding="utf-8")

    # Print summary for stdout
    print(f"DB_PATH={db_path}")
    for c_num, (cnt, has_img) in case_stats.items():
        print(f"CASE_{c_num}: rows={cnt}, image={'بله' if has_img else 'خیر'}")
    print(f"D-B: total_records={db_found_count}")
    print(f"D-C1: pct_items={len(pct_items)}, pct_rules={len(pct_rules)}")
    print(f"D-F: samples={len(df_samples)}")

    conn.close()


if __name__ == "__main__":
    main()
