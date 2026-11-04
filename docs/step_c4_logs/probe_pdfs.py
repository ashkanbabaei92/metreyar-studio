import os
import sys
import signal
import sqlite3
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from src.pdf_fix import (
    patch_pdf_tounicode_in_memory,
    open_patched_pdf,
    extract_rtl_page_lines,
)


class TimeoutErrorException(Exception):
    pass


def _alarm_handler(signum, frame):
    raise TimeoutErrorException("Timed out after 120 seconds")


signal.signal(signal.SIGALRM, _alarm_handler)


def is_ar_fa_char(ch: str) -> bool:
    cp = ord(ch)
    return (
        (0x0600 <= cp <= 0x06FF)
        or (0x0750 <= cp <= 0x077F)
        or (0x08A0 <= cp <= 0x08FF)
        or (0xFB50 <= cp <= 0xFDFF)
        or (0xFE70 <= cp <= 0xFEFF)
    )


def sample_page_indices(page_count: int, max_samples: int = 8):
    if page_count <= 0:
        return []
    if page_count <= max_samples:
        return list(range(page_count))
    return [int(round(i * (page_count - 1) / (max_samples - 1))) for i in range(max_samples)]


def main():
    out_path = ROOT_DIR / "docs" / "step_c4_logs" / "probe_pdfs.txt"
    data_dir = ROOT_DIR / "data"

    lines_out = []

    def log(msg: str = ""):
        lines_out.append(msg)

    log("=== 1. DATA DIRECTORY WALK (*.pdf, *.xlsx, and all files) ===")
    all_files = []
    pdf_files = []
    xlsx_files = []
    other_files = []

    for root, dirs, files in os.walk(data_dir):
        dirs.sort()
        for f in sorted(files):
            full_p = Path(root) / f
            rel_p = full_p.relative_to(ROOT_DIR)
            size_b = full_p.stat().st_size
            all_files.append((str(rel_p), size_b))
            ext = full_p.suffix.lower()
            if ext == ".pdf":
                pdf_files.append(full_p)
            elif ext == ".xlsx":
                xlsx_files.append(full_p)
            else:
                other_files.append(full_p)

    for rel_str, size_b in all_files:
        log(f"FILE: {rel_str} | SIZE_BYTES: {size_b}")

    log(f"TOTAL_FILES_IN_DATA: {len(all_files)} (compared to expected 14)")
    log(f"PDF_COUNT: {len(pdf_files)} | XLSX_COUNT: {len(xlsx_files)} | OTHER_COUNT: {len(other_files)}")
    log("")

    log("=== 2. SQLITE3 & FTS5 PROBE ===")
    log(f"sqlite3.sqlite_version: {sqlite3.sqlite_version}")
    try:
        conn = sqlite3.connect(":memory:")
        cur = conn.cursor()
        cur.execute("CREATE VIRTUAL TABLE test_fts USING fts5(title, body);")
        cur.execute("INSERT INTO test_fts(title, body) VALUES ('تست', 'فهرست بهای ابنیه فولاد');")
        cur.execute("SELECT title, body FROM test_fts WHERE test_fts MATCH 'فولاد';")
        fts_rows = cur.fetchall()
        conn.close()
        log(f"FTS5_STATUS: OK | SAMPLE_MATCH_ROWS: {repr(fts_rows)}")
    except Exception as e:
        log(f"FTS5_STATUS: ERROR | {repr(e)}")
    log("")

    log("=== 3. PDF PROBE VIA src/pdf_fix.py ===")
    for idx, pdf_path in enumerate(pdf_files, 1):
        rel_str = str(pdf_path.relative_to(ROOT_DIR))
        log(f"--- PDF [{idx}/{len(pdf_files)}]: {rel_str} ---")
        signal.alarm(120)
        doc = None
        try:
            doc = open_patched_pdf(pdf_path)
            page_count = len(doc)
            sample_indices = sample_page_indices(page_count, 8)
            log(f"PAGE_COUNT: {page_count}")
            log(f"SAMPLE_PAGE_INDICES_0BASED: {sample_indices}")
            log(f"SAMPLE_PAGE_NUMBERS_1BASED: {[i + 1 for i in sample_indices]}")

            total_chars = 0
            total_ar_fa = 0
            empty_sample_pages = 0
            foolad_correct = 0
            fovaald_corrupt = 0
            per_page_stats = []
            sample_texts = {}

            for p_idx in sample_indices:
                page = doc[p_idx]
                rtl_lines = extract_rtl_page_lines(page)
                page_text = "\n".join(rtl_lines)
                sample_texts[p_idx] = page_text
                c_len = len(page_text)
                ar_len = sum(1 for ch in page_text if is_ar_fa_char(ch))
                if c_len == 0 or page_text.strip() == "":
                    empty_sample_pages += 1
                total_chars += c_len
                total_ar_fa += ar_len
                c_foolad = page_text.count("فولاد")
                c_fovaald = page_text.count("فوالد")
                foolad_correct += c_foolad
                fovaald_corrupt += c_fovaald
                per_page_stats.append(
                    f"p{p_idx+1}(0-idx={p_idx}): chars={c_len}, ar_fa={ar_len}, lines={len(rtl_lines)}, فولاد={c_foolad}, فوالد={c_fovaald}"
                )

            n_samp = len(sample_indices)
            avg_chars = (total_chars / n_samp) if n_samp > 0 else 0.0
            avg_ar_fa = (total_ar_fa / n_samp) if n_samp > 0 else 0.0

            mid_pos = len(sample_indices) // 2
            mid_page_idx = sample_indices[mid_pos] if sample_indices else None
            if mid_page_idx is not None and not sample_texts.get(mid_page_idx, "").strip():
                for alt_idx in sample_indices:
                    if sample_texts.get(alt_idx, "").strip():
                        mid_page_idx = alt_idx
                        break

            mid_snippet = sample_texts.get(mid_page_idx, "")[:200] if mid_page_idx is not None else ""

            log(f"SAMPLES_COUNT: {n_samp} | EMPTY_SAMPLE_PAGES: {empty_sample_pages}")
            log(f"TOTAL_SAMPLE_CHARS: {total_chars} | AVG_SAMPLE_CHARS: {avg_chars:.1f}")
            log(f"TOTAL_SAMPLE_AR_FA: {total_ar_fa} | AVG_SAMPLE_AR_FA: {avg_ar_fa:.1f}")
            log(f"COUNT_فولاد_IN_SAMPLES: {foolad_correct} | COUNT_فوالد_IN_SAMPLES: {fovaald_corrupt}")
            for st in per_page_stats:
                log(f"  PAGE_STAT: {st}")
            log(f"MID_SAMPLE_PAGE_1BASED: {(mid_page_idx + 1) if mid_page_idx is not None else None}")
            log(f"MID_200_CHARS_REPR: {repr(mid_snippet)}")
            log("STATUS: OK")
        except Exception as e:
            log(f"STATUS: ERROR | {repr(e)}")
        finally:
            signal.alarm(0)
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass
        log("")

    out_path.write_text("\n".join(lines_out) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
