#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import re
import sys
import signal
from collections import Counter

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))


def _alarm_handler(signum, frame):
    raise TimeoutError("signal.alarm timeout reached")


signal.signal(signal.SIGALRM, _alarm_handler)


def main():
    out = []

    def log(msg=""):
        out.append(str(msg))

    log("=== S2.1 DIAGNOSTIC REPORT ===")

    try:
        import fitz
        from src.search_build import normalize, remove_digit_tokens
        from src.pdf_fix import open_patched_pdf, extract_rtl_page_lines
        log("IMPORT_STATUS: OK (normalize, remove_digit_tokens from src.search_build; open_patched_pdf, extract_rtl_page_lines from src.pdf_fix)")
    except Exception as e:
        log(f"IMPORT_STATUS: FAIL -> {repr(e)}")
        os.makedirs("docs/search_logs", exist_ok=True)
        with open("docs/search_logs/s21_diag.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        return

    # Find PDF paths via os.walk('data')
    data_files_by_norm = {}
    for root, _dirs, files in os.walk("data"):
        for fn in sorted(files):
            nf = normalize(fn)
            data_files_by_norm.setdefault(nf, []).append((fn, os.path.join(root, fn)))

    # =========================================================================
    # D1) 4311-word.txt
    # =========================================================================
    log("\n--- D1: 4311-word.txt (واحدسازی ماده) ---")
    try:
        signal.alarm(60)
        word_path = "data_processed/doc_convert_test/4311-word.txt"
        with open(word_path, "r", encoding="utf-8") as wf:
            raw_word = wf.read()
        if raw_word.startswith("\ufeff"):
            raw_word = raw_word.lstrip("\ufeff")

        lines = raw_word.splitlines()
        madeh_re = re.compile(r"^\s*ماده\s*(\d+)\b")

        matches = []  # (idx1, num, prev_line, cur_line)
        prev_n = None
        anomalies = []
        for idx0, orig_line in enumerate(lines):
            idx1 = idx0 + 1
            norm_line = normalize(orig_line)
            m = madeh_re.match(norm_line)
            if m:
                n_val = int(m.group(1))
                prev_line = lines[idx0 - 1] if idx0 > 0 else ""
                is_anom = (prev_n is not None and n_val <= prev_n)
                rec = (len(matches) + 1, idx1, n_val, prev_n, prev_line, orig_line, is_anom)
                matches.append(rec)
                if is_anom:
                    anomalies.append(rec)
                prev_n = n_val

        log(f"Total lines in 4311-word.txt: {len(lines)}")
        log(f"Total matched '^\\s*ماده\\s*(\\d+)\\b' lines: {len(matches)}")
        log(f"Total anomalies (N <= prev_N): {len(anomalies)}")

        log("\n[D1-A] Anomalies (N <= prev_N):")
        for seq_i, idx1, n_val, p_n, prev_l, cur_l, _ in anomalies:
            log(f"  unit_seq={seq_i} | line={idx1} | N={n_val} (prev_N={p_n})")
            log(f"    prev_line[:100] = {repr(prev_l[:100])}")
            log(f"    cur_line[:150]  = {repr(cur_l[:150])}")

        log("\n[D1-B] First 54 matched ماده units:")
        for seq_i, idx1, n_val, p_n, prev_l, cur_l, is_anom in matches[:54]:
            flag = " [ANOMALY N<=prev]" if is_anom else ""
            log(f"  #{seq_i:02d} | line={idx1:4d} | ماده {n_val:2d} (prev={p_n}){flag} | prev={repr(prev_l[:100])} | cur={repr(cur_l[:150])}")

        log("\n[D1-C] Units from #55 to end (including points where N returns to 17 or other values):")
        for seq_i, idx1, n_val, p_n, prev_l, cur_l, is_anom in matches[54:]:
            flag = " [ANOMALY N<=prev]" if is_anom else ""
            log(f"  #{seq_i:02d} | line={idx1:4d} | ماده {n_val:2d} (prev={p_n}){flag} | prev={repr(prev_l[:100])} | cur={repr(cur_l[:150])}")

        signal.alarm(0)
    except Exception as e:
        signal.alarm(0)
        log(f"D1 ERROR: {repr(e)}")

    # =========================================================================
    # D2) تفسیر شرایط عمومی و خصوصی پیمان.pdf صفحات ۱۰۰، ۲۰۰، ۳۰۰ (۱-مبنا)
    # =========================================================================
    log("\n--- D2: تفسیر شرایط عمومی و خصوصی پیمان.pdf (صفحات 100, 200, 300 ۱-مبنا) ---")
    try:
        signal.alarm(120)
        tafsir_key = normalize("تفسیر شرایط عمومی و خصوصی پیمان.pdf")
        t_matches = data_files_by_norm.get(tafsir_key, [])
        if len(t_matches) != 1:
            log(f"Tafsir file match error: {t_matches}")
        else:
            _, tafsir_path = t_matches[0]
            doc_raw = fitz.open(tafsir_path)
            doc_fix = open_patched_pdf(tafsir_path)
            for pno in [100, 200, 300]:
                idx0 = pno - 1
                page_raw = doc_raw[idx0]
                page_fix = doc_fix[idx0]
                fix_lines = extract_rtl_page_lines(page_fix, y_tolerance=4.5)
                fix_lines_filtered = [
                    ln for ln in fix_lines
                    if ("Academyofcivil.com" not in ln and "آکادمی عمران" not in ln)
                ]
                blocks = page_raw.get_text("blocks")
                imgs = page_raw.get_images(full=True)
                rect = page_raw.rect
                page_area = rect.width * rect.height if (rect.width and rect.height) else 0.0

                log(f"\n[D2] Page {pno} (1-based) | page_rect=({rect.width:.2f}x{rect.height:.2f}, area={page_area:.2f}):")
                log(f"  len(s_lines_raw_fix)={len(fix_lines)}, len(s_lines_after_watermark_filter)={len(fix_lines_filtered)}")
                log(f"  get_text('blocks') count = {len(blocks)}")
                log(f"  First 15 lines of s (filtered): {[repr(x) for x in fix_lines_filtered[:15]]}")
                log(f"  First 15 lines of s (unfiltered): {[repr(x) for x in fix_lines[:15]]}")
                log(f"  page.get_images(full=True) count = {len(imgs)}")
                for im_i, img_info in enumerate(imgs):
                    xref = img_info[0]
                    try:
                        img_rects = page_raw.get_image_rects(xref)
                        rects_str = []
                        for ir in img_rects:
                            ir_area = ir.width * ir.height
                            ratio = (ir_area / page_area) if page_area > 0 else 0.0
                            rects_str.append(f"rect=({ir.x0:.1f},{ir.y0:.1f},{ir.x1:.1f},{ir.y1:.1f}) area_ratio={ratio:.4f}")
                        if not rects_str:
                            ibbox = page_raw.get_image_bbox( img_info )
                            ib_area = ibbox.width * ibbox.height
                            ratio = (ib_area / page_area) if page_area > 0 else 0.0
                            rects_str.append(f"bbox=({ibbox.x0:.1f},{ibbox.y0:.1f},{ibbox.x1:.1f},{ibbox.y1:.1f}) area_ratio={ratio:.4f}")
                        log(f"    img[{im_i}] tuple={repr(img_info)} -> {'; '.join(rects_str)}")
                    except Exception as ie:
                        log(f"    img[{im_i}] tuple={repr(img_info)} -> bbox_err={repr(ie)}")
            doc_raw.close()
            doc_fix.close()
        signal.alarm(0)
    except Exception as e:
        signal.alarm(0)
        log(f"D2 ERROR: {repr(e)}")

    # =========================================================================
    # D3) 1147659-1404.pdf صفحات ۴۶، ۴۷، ۴۸ (۱-مبنا)
    # =========================================================================
    log("\n--- D3: 1147659-1404.pdf (صفحات 46, 47, 48 ۱-مبنا) ---")
    try:
        signal.alarm(120)
        k114 = normalize("1147659-1404.pdf")
        m114 = data_files_by_norm.get(k114, [])
        if len(m114) != 1:
            log(f"1147659 match error: {m114}")
        else:
            _, p114 = m114[0]
            runs = {}
            for run_no in [1, 2]:
                d_raw = fitz.open(p114)
                d_fix = open_patched_pdf(p114)
                runs[run_no] = {}
                for pno in [46, 47, 48]:
                    idx0 = pno - 1
                    fix_lines = extract_rtl_page_lines(d_fix[idx0], y_tolerance=4.5)
                    raw_txt = d_raw[idx0].get_text()
                    raw_lines = raw_txt.splitlines()
                    s_txt = "\n".join(fix_lines)
                    r_txt = "\n".join(raw_lines)
                    runs[run_no][pno] = {
                        "len_s": len(s_txt),
                        "len_r": len(r_txt),
                        "len_raw_get_text": len(raw_txt),
                        "raw_ends_with_nl": raw_txt.endswith("\n"),
                        "raw_line_count": len(raw_lines),
                    }
                d_raw.close()
                d_fix.close()

            # Read exact lines from probe_pdfs_full.txt and s1_text_quality.txt
            probe_lines = []
            if os.path.exists("docs/step_c4_logs/probe_pdfs_full.txt"):
                with open("docs/step_c4_logs/probe_pdfs_full.txt", "r", encoding="utf-8") as pf:
                    in_pdf2 = False
                    for pl in pf:
                        if "PDF #2:" in pl or "1147659-1404.pdf" in pl:
                            in_pdf2 = True
                        elif "PDF #3:" in pl:
                            in_pdf2 = False
                        if in_pdf2:
                            probe_lines.append(pl.rstrip("\n"))

            s1_lines = []
            if os.path.exists("docs/search_logs/s1_text_quality.txt"):
                with open("docs/search_logs/s1_text_quality.txt", "r", encoding="utf-8") as sf:
                    all_s1 = sf.read().splitlines()
                    for li, sl in enumerate(all_s1):
                        if "Target: 1147659-1404.pdf" in sl or "Document: 1147659" in sl:
                            s1_lines.extend(all_s1[li:li + 28])

            log("Run 1 & Run 2 results (1-based pages 46, 47, 48):")
            for pno in [46, 47, 48]:
                r1 = runs[1][pno]
                r2 = runs[2][pno]
                det = "DETERMINISTIC (run1==run2)" if (r1["len_s"] == r2["len_s"] and r1["len_r"] == r2["len_r"]) else "NON-DETERMINISTIC"
                log(f"  Page {pno} (idx0={pno-1}):")
                log(f"    Run1: len(s)={r1['len_s']}, len(r_splitlines_join)={r1['len_r']}, len(page.get_text())={r1['len_raw_get_text']}, ends_nl={r1['raw_ends_with_nl']}")
                log(f"    Run2: len(s)={r2['len_s']}, len(r_splitlines_join)={r2['len_r']}, len(page.get_text())={r2['len_raw_get_text']}, ends_nl={r2['raw_ends_with_nl']} -> {det}")

            log("\nFrom docs/step_c4_logs/probe_pdfs_full.txt (PDF #2):")
            for pl in probe_lines:
                log("  " + pl)

            log("\nFrom docs/search_logs/s1_text_quality.txt:")
            for sl in s1_lines:
                log("  " + sl)

        signal.alarm(0)
    except Exception as e:
        signal.alarm(0)
        log(f"D3 ERROR: {repr(e)}")

    # =========================================================================
    # D4) خطوط سرصفحه حذف‌شده در همه اسناد >=20 صفحه (به‌ویژه تفسیر و 1147659)
    # =========================================================================
    log("\n--- D4: بررسی خطوط تکراری (>50% صفحات) و تعداد توکن پس از normalize ---")
    try:
        signal.alarm(240)
        pdf_keys = [
            ("11-Abnieh1404.pdf", False),
            ("12-Mechanic1404.pdf", False),
            ("13-barghi1404.pdf", False),
            ("تفسیر شرایط عمومی و خصوصی پیمان.pdf", True),
            ("1147659-1404.pdf", False),
        ]
        for key, is_tafsir in pdf_keys:
            nk = normalize(key)
            matches = data_files_by_norm.get(nk, [])
            if len(matches) != 1:
                continue
            actual_fname, full_path = matches[0]
            doc_raw = fitz.open(full_path)
            doc_fix = open_patched_pdf(full_path)
            num_pages = len(doc_raw)
            line_page_freq = Counter()
            for idx0 in range(num_pages):
                fix_lines = extract_rtl_page_lines(doc_fix[idx0], y_tolerance=4.5)
                raw_lines = doc_raw[idx0].get_text().splitlines()
                if is_tafsir:
                    fix_lines = [ln for ln in fix_lines if ("Academyofcivil.com" not in ln and "آکادمی عمران" not in ln)]
                    raw_lines = [ln for ln in raw_lines if ("Academyofcivil.com" not in ln and "آکادمی عمران" not in ln)]
                s_txt = "\n".join(fix_lines)
                r_txt = "\n".join(raw_lines)
                r_nodig = remove_digit_tokens(r_txt)
                page_unique_norm = set()
                for ln in s_txt.splitlines() + r_nodig.splitlines():
                    nln = normalize(ln)
                    if nln:
                        page_unique_norm.add(nln)
                for nln in page_unique_norm:
                    line_page_freq[nln] += 1
            doc_raw.close()
            doc_fix.close()

            threshold = num_pages * 0.5
            rep_all = [(nln, cnt, len(nln.split())) for nln, cnt in sorted(line_page_freq.items(), key=lambda x: (-x[1], x[0])) if cnt > threshold]
            rep_ge3 = [(nln, cnt, tcnt) for (nln, cnt, tcnt) in rep_all if tcnt >= 3]
            rep_lt3 = [(nln, cnt, tcnt) for (nln, cnt, tcnt) in rep_all if tcnt < 3]

            log(f"\n[D4] {actual_fname} (num_pages={num_pages}, threshold>{threshold}):")
            log(f"  Before (all >50%): {len(rep_all)} lines | After (>=3 tokens): {len(rep_ge3)} lines | Kept (<3 tokens): {len(rep_lt3)} lines")
            for nln, cnt, tcnt in rep_all:
                status = "REMOVE (>=3 tokens)" if tcnt >= 3 else "KEEP (<3 tokens)"
                log(f"    - [{cnt}/{num_pages} pages] tokens={tcnt:2d} | {status} | {repr(nln)}")
        signal.alarm(0)
    except Exception as e:
        signal.alarm(0)
        log(f"D4 ERROR: {repr(e)}")

    # Also read remaining lines of src/search_build.py and templates/search_template.html for exact context
    log("\n--- CONTEXT: src/search_build.py lines 295..end ---")
    with open("src/search_build.py", "r", encoding="utf-8") as sf:
        s_lines = sf.read().splitlines()
    for i in range(294, len(s_lines)):
        log(f"{i+1:4d}: {s_lines[i]}")

    log("\n--- CONTEXT: templates/search_template.html lines 225..530 ---")
    with open("templates/search_template.html", "r", encoding="utf-8") as tf:
        t_lines = tf.read().splitlines()
    for i in range(224, len(t_lines)):
        log(f"{i+1:4d}: {t_lines[i]}")

    os.makedirs("docs/search_logs", exist_ok=True)
    with open("docs/search_logs/s21_diag.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
