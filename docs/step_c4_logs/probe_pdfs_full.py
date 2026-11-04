#!/usr/bin/env python3
import os
import sys
import signal
import shutil
import subprocess
import sqlite3
from collections import Counter

ROOT_DIR = "/home/user/webapp"
DATA_DIR = os.path.join(ROOT_DIR, "data")
DB_PATH = os.path.join(ROOT_DIR, "data_processed", "reference.db")
OUT_PATH = os.path.join(ROOT_DIR, "docs", "step_c4_logs", "probe_pdfs_full.txt")

sys.path.insert(0, ROOT_DIR)
from src.pdf_fix import open_patched_pdf, extract_rtl_page_lines
import fitz


class TimeoutErrorException(Exception):
    pass


def alarm_handler(signum, frame):
    raise TimeoutErrorException("Timeout (300s) reached for file")


signal.signal(signal.SIGALRM, alarm_handler)


def log_line(fp, msg=""):
    fp.write(msg + "\n")
    fp.flush()


def main():
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    # Parse optional 1-based file indices from argv
    selected_indices = set()
    run_tail_sections = True
    if len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            if arg == "--only-tail":
                selected_indices = set()
            elif arg == "--no-tail":
                run_tail_sections = False
            else:
                for part in arg.split(","):
                    part = part.strip()
                    if part.isdigit():
                        selected_indices.add(int(part))

    mode = "a" if (selected_indices and os.path.exists(OUT_PATH)) else "w"
    with open(OUT_PATH, mode, encoding="utf-8") as fp:
        all_files = []
        for root, dirs, files in os.walk(DATA_DIR):
            dirs.sort()
            files.sort()
            for f in files:
                all_files.append(os.path.join(root, f))

        pdf_files = [p for p in all_files if p.lower().endswith(".pdf")]
        doc_files = [p for p in all_files if p.lower().endswith(".doc")]
        xlsx_files = [p for p in all_files if p.lower().endswith(".xlsx")]

        if mode == "w":
            log_line(fp, "=== [0] RAW FILE LIST FROM os.walk('data/') ===")
            for idx, p in enumerate(all_files, 1):
                rel = os.path.relpath(p, ROOT_DIR)
                log_line(fp, f"FILE_{idx:02d}: {rel} (size={os.path.getsize(p)} bytes)")
            log_line(fp, f"TOTAL_FILES={len(all_files)} | PDFS={len(pdf_files)} | XLSX={len(xlsx_files)} | DOC={len(doc_files)}")
            log_line(fp, "")

        log_line(fp, "=== [A] FULL PDF SCAN ===")
        for idx, pdf_path in enumerate(pdf_files, 1):
            if selected_indices and idx not in selected_indices:
                continue
            rel = os.path.relpath(pdf_path, ROOT_DIR)
            log_line(fp, f"--- PDF #{idx}: {rel} ---")
            signal.alarm(300)
            doc_fix = None
            doc_raw = None
            try:
                doc_fix = open_patched_pdf(pdf_path)
                doc_raw = fitz.open(pdf_path)
                num_pages = len(doc_fix)
                both_zero = 0
                fix_zero_raw_pos = []
                fix_pos_raw_zero = 0
                img_pos_fix_zero = 0
                n_fix_list = []
                page_line_Presence = Counter()
                slash_u066b_lines = 0

                for pno in range(num_pages):
                    page_f = doc_fix[pno]
                    page_r = doc_raw[pno]
                    lines_f = extract_rtl_page_lines(page_f, y_tolerance=4.5)
                    joined_f = "\n".join(lines_f)
                    n_fix = len(joined_f)

                    raw_txt = page_r.get_text()
                    n_raw = len(raw_txt)

                    imgs = page_r.get_images()
                    n_img = len(imgs)

                    n_fix_list.append((n_fix, pno + 1, n_raw, n_img))

                    if n_fix == 0 and n_raw == 0:
                        both_zero += 1
                    if n_fix == 0 and n_raw > 0:
                        fix_zero_raw_pos.append(pno + 1)
                    if n_fix > 0 and n_raw == 0:
                        fix_pos_raw_zero += 1
                    if n_img > 0 and n_fix == 0:
                        img_pos_fix_zero += 1

                    unique_lines_in_page = set(line.strip() for line in lines_f if line.strip())
                    for ul in unique_lines_in_page:
                        page_line_Presence[ul] += 1

                    for line in lines_f:
                        if "\u066b" in line:
                            slash_u066b_lines += 1

                if num_pages > 0:
                    avg_n_fix = sum(x[0] for x in n_fix_list) / num_pages
                    min_n_fix = min(x[0] for x in n_fix_list)
                else:
                    avg_n_fix = 0.0
                    min_n_fix = 0

                sorted_pages = sorted(n_fix_list, key=lambda x: (x[0], x[1]))
                lowest_10 = [(pno1, nf, nr, ni) for (nf, pno1, nr, ni) in sorted_pages[:10]]

                threshold = num_pages * 0.5
                repeated_lines = [
                    (line, cnt)
                    for line, cnt in page_line_Presence.most_common()
                    if cnt > threshold
                ][:5]

                log_line(fp, f"PDF_{idx}_PAGES: {num_pages}")
                log_line(fp, f"PDF_{idx}_BOTH_ZERO (n_fix=0 & n_raw=0): {both_zero}")
                log_line(
                    fp,
                    f"PDF_{idx}_FIX_ZERO_RAW_POS (n_fix=0 & n_raw>0): count={len(fix_zero_raw_pos)} pages_up_to_20={fix_zero_raw_pos[:20]}",
                )
                log_line(fp, f"PDF_{idx}_FIX_POS_RAW_ZERO (n_fix>0 & n_raw=0): {fix_pos_raw_zero}")
                log_line(fp, f"PDF_{idx}_IMG_POS_FIX_ZERO (n_img>0 & n_fix=0): {img_pos_fix_zero}")
                log_line(fp, f"PDF_{idx}_N_FIX_STATS: avg={avg_n_fix:.2f} min={min_n_fix}")
                log_line(fp, f"PDF_{idx}_LOWEST_10 (page_1based, n_fix, n_raw, n_img): {lowest_10}")
                log_line(fp, f"PDF_{idx}_U066B_LINES: {slash_u066b_lines}")
                log_line(fp, f"PDF_{idx}_REPEATED_GT_50PCT (up to 5):")
                if repeated_lines:
                    for rline, rcnt in repeated_lines:
                        log_line(fp, f"  REPEATED: count={rcnt}/{num_pages} text={repr(rline)}")
                else:
                    log_line(fp, "  REPEATED: none")

                if all(x[0] == 0 for x in n_fix_list) or len(fix_zero_raw_pos) > 0 or both_zero == num_pages:
                    mid_pno = num_pages // 2
                    mid_raw = doc_raw[mid_pno].get_text() if num_pages > 0 else ""
                    sample_str = repr(mid_raw[:200]) if mid_raw else "خالی"
                    log_line(
                        fp,
                        f"PDF_{idx}_MID_PAGE_RAW_SAMPLE (page={mid_pno + 1}): {sample_str}",
                    )
                log_line(fp, f"PDF_{idx}_STATUS: OK")
            except Exception as e:
                log_line(fp, f"PDF_{idx}_ERROR: {repr(e)}")
            finally:
                signal.alarm(0)
                try:
                    if doc_fix is not None:
                        doc_fix.close()
                except Exception:
                    pass
                try:
                    if doc_raw is not None:
                        doc_raw.close()
                except Exception:
                    pass
            log_line(fp, "")

        if not run_tail_sections:
            return

        log_line(fp, "=== [B] ENVIRONMENT TOOLS & PYTHON MODULES ===")
        binaries = [
            "antiword",
            "catdoc",
            "soffice",
            "libreoffice",
            "tesseract",
            "pdftotext",
            "pdftoppm",
            "unrtf",
        ]
        for b in binaries:
            w = shutil.which(b)
            log_line(fp, f"WHICH_{b}: {repr(w)}")

        if shutil.which("tesseract"):
            try:
                res = subprocess.run(
                    ["tesseract", "--list-langs"],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                log_line(
                    fp,
                    f"TESSERACT_LANGS: rc={res.returncode} stdout={repr(res.stdout)} stderr={repr(res.stderr)}",
                )
            except Exception as e:
                log_line(fp, f"TESSERACT_LANGS_ERROR: {repr(e)}")
        else:
            log_line(fp, "TESSERACT_LANGS: tesseract_not_found")

        py_mods = ["docx", "pytesseract", "olefile", "fitz"]
        for m in py_mods:
            try:
                mod = __import__(m)
                ver = getattr(mod, "__version__", getattr(mod, "VersionBind", "unknown"))
                log_line(fp, f"IMPORT_{m}: OK version={repr(ver)}")
            except Exception as e:
                log_line(fp, f"IMPORT_{m}: ERROR {repr(e)}")
        log_line(fp, "")

        log_line(fp, "=== [C] 4311-word.doc HEADER & SIZE ===")
        for dp in doc_files:
            rel = os.path.relpath(dp, ROOT_DIR)
            try:
                sz = os.path.getsize(dp)
                with open(dp, "rb") as bf:
                    first8 = bf.read(8)
                log_line(
                    fp,
                    f"DOC_FILE: {rel} | size={sz} | first8_hex={first8.hex()} | first8_repr={repr(first8)}",
                )
            except Exception as e:
                log_line(fp, f"DOC_FILE_ERROR: {rel} -> {repr(e)}")
        log_line(fp, "")

        log_line(fp, "=== [E] FTS5 TRIGRAM TEST ===")
        try:
            conn_mem = sqlite3.connect(":memory:")
            cur = conn_mem.cursor()
            cur.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')")
            cur.execute("INSERT INTO t(x) VALUES (?)", ("بتن مگر و قالب‌بندی فونداسیون",))
            cur.execute("SELECT x FROM t WHERE t MATCH ?", ("قالب",))
            rows_3 = cur.fetchall()
            cur.execute("SELECT x FROM t WHERE t MATCH ?", ("بتن",))
            rows_b = cur.fetchall()
            log_line(
                fp,
                f"FTS5_TRIGRAM: OK | match_قالب={repr(rows_3)} | match_بتن={repr(rows_b)}",
            )
            conn_mem.close()
        except Exception as e:
            log_line(fp, f"FTS5_TRIGRAM: ERROR {repr(e)}")
        log_line(fp, "")

        log_line(fp, "=== [D] XLSX (mec1404.xlsx & elec1404.xlsx) & DB INTERSECTION ===")
        db_codes = set()
        try:
            db_uri = f"file:{DB_PATH}?mode=ro"
            conn_db = sqlite3.connect(db_uri, uri=True)
            cur_db = conn_db.cursor()
            cur_db.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view')")
            tables = cur_db.fetchall()
            log_line(fp, f"DB_TABLES: {repr(tables)}")
            cur_db.execute("PRAGMA table_info(items)")
            cols = cur_db.fetchall()
            log_line(fp, f"DB_ITEMS_PRAGMA: {repr(cols)}")
            cur_db.execute("SELECT code_official FROM items")
            for (c,) in cur_db.fetchall():
                if c is not None:
                    db_codes.add(str(c).strip())
            log_line(fp, f"DB_ITEMS_CODE_OFFICIAL_COUNT: {len(db_codes)} (sample_5={sorted(list(db_codes))[:5]})")
            conn_db.close()
        except Exception as e:
            log_line(fp, f"DB_READ_ERROR: {repr(e)}")

        import openpyxl

        for xp in xlsx_files:
            rel = os.path.relpath(xp, ROOT_DIR)
            bname = os.path.basename(xp).lower()
            if "mec1404" not in bname and "elec1404" not in bname:
                log_line(fp, f"XLSX_SKIP_OTHER: {rel}")
                continue
            log_line(fp, f"--- XLSX: {rel} ---")
            try:
                wb = openpyxl.load_workbook(xp, read_only=True, data_only=True)
                log_line(fp, f"XLSX_SHEETS: {repr(wb.sheetnames)}")
                for sname in wb.sheetnames:
                    ws = wb[sname]
                    dim = ws.calculate_dimension()
                    max_r = ws.max_row
                    max_c = ws.max_column
                    log_line(fp, f"SHEET {repr(sname)}: dimension={repr(dim)} max_row={max_r} max_column={max_c}")

                    rows_iter = ws.iter_rows(values_only=True)
                    first_3 = []
                    header_row = None
                    col0_non_empty = 0
                    codes_list = []
                    price_col_idx = None
                    price_col_name = None
                    price_null_or_empty = 0
                    price_zero = 0
                    total_data_rows = 0

                    for r_idx, row in enumerate(rows_iter, 1):
                        if r_idx <= 3:
                            first_3.append(row)
                        if r_idx == 1 and row:
                            header_row = row
                            for c_i, cell_val in enumerate(row):
                                if cell_val is not None and ("بها" in str(cell_val) or "قیمت" in str(cell_val) or "مبلغ" in str(cell_val)):
                                    if price_col_idx is None:
                                        price_col_idx = c_i
                                        price_col_name = cell_val
                        if not row:
                            continue
                        c0 = row[0] if len(row) > 0 else None
                        if c0 is not None and str(c0).strip() != "":
                            col0_non_empty += 1
                            if r_idx > 1:
                                code_str = str(c0).strip()
                                codes_list.append(code_str)

                        if r_idx > 1:
                            total_data_rows += 1
                            if price_col_idx is not None and price_col_idx < len(row):
                                pval = row[price_col_idx]
                            else:
                                pval = None
                            if pval is None or str(pval).strip() == "" or str(pval).strip() == "-":
                                price_null_or_empty += 1
                            else:
                                try:
                                    if float(str(pval).replace(",", "").strip()) == 0.0:
                                        price_zero += 1
                                except Exception:
                                    pass

                    for i3, r3 in enumerate(first_3, 1):
                        log_line(fp, f"  ROW_{i3}: {repr(r3)}")

                    len_dist = Counter(len(c) for c in codes_list)
                    code_set = set(codes_list)
                    overlap = sorted(list(code_set.intersection(db_codes)))

                    log_line(fp, f"  COL0_NON_EMPTY_ROWS (including header if non-empty): {col0_non_empty}")
                    log_line(fp, f"  DATA_CODES_COUNT (row>1): {len(codes_list)} | UNIQUE_CODES: {len(code_set)}")
                    log_line(fp, f"  SAMPLE_10_CODES: {repr(codes_list[:10])}")
                    log_line(fp, f"  CODE_LENGTH_DIST: {repr(dict(sorted(len_dist.items())))}")
                    log_line(
                        fp,
                        f"  PRICE_COL: idx={price_col_idx} header={repr(price_col_name)} | total_data_rows={total_data_rows} | null_or_empty_or_dash={price_null_or_empty} | zero={price_zero}",
                    )
                    log_line(
                        fp,
                        f"  OVERLAP_WITH_DB_ITEMS_CODE_OFFICIAL: count={len(overlap)} sample_up_to_10={repr(overlap[:10])}",
                    )
                wb.close()
            except Exception as e:
                log_line(fp, f"XLSX_ERROR: {rel} -> {repr(e)}")
            log_line(fp, "")

        log_line(fp, "=== END OF FULL PROBE ===")


if __name__ == "__main__":
    main()
