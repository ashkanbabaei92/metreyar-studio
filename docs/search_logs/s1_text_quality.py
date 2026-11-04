#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import signal
import subprocess
from pathlib import Path

ROOT = Path("/home/user/webapp")
sys.path.insert(0, str(ROOT))

import fitz
from src.pdf_fix import open_patched_pdf, extract_rtl_page_lines

OUT_FILE = ROOT / "docs" / "search_logs" / "s1_text_quality.txt"
OUT_FILE.parent.mkdir(parents=True, exist_ok=True)

CONVERT_DIR = ROOT / "data_processed" / "doc_convert_test"
CONVERT_DIR.mkdir(parents=True, exist_ok=True)


class TimeoutError(Exception):
    pass


def _alarm_handler(signum, frame):
    raise TimeoutError("Signal alarm timeout reached")


signal.signal(signal.SIGALRM, _alarm_handler)

FILES = {
    "abnieh": "data/فهرست‌بها (پایه محاسبات متره و برآورد)/11-Abnieh1404.pdf",
    "mechanic": "data/فهرست‌بها (پایه محاسبات متره و برآورد)/12-Mechanic1404.pdf",
    "barghi": "data/فهرست‌بها (پایه محاسبات متره و برآورد)/13-barghi1404.pdf",
    "tafsir": "data/شرایط عمومی پیمان (برای کنترل قرارداد، تاخیرات، صورت‌وضعیت)/نشریه 4311+ ابلاغ اصلاحیه/نشریه 4311+ ابلاغ اصلاحیه/تفسیر شرایط عمومی و خصوصی پیمان.pdf",
    "1147659": "data/تعدیل آحاد بها/1147659-1404.pdf",
    "941636": "data/تعدیل آحاد بها/941636_Tadil-Movaghat-3-4-1404-14041219.pdf",
    "Arz4": "data/تعدیل آحاد بها/Arz4(DrMostafaNajafi.ir).pdf",
    "eblagh": "data/شرایط عمومی پیمان (برای کنترل قرارداد، تاخیرات، صورت‌وضعیت)/نشریه 4311+ ابلاغ اصلاحیه/نشریه 4311+ ابلاغ اصلاحیه/ابلاغ اصلاحيه موافقتنامه، شرايط عمومي.pdf",
    "doc_4311": "data/شرایط عمومی پیمان (برای کنترل قرارداد، تاخیرات، صورت‌وضعیت)/نشریه 4311+ ابلاغ اصلاحیه/نشریه 4311+ ابلاغ اصلاحیه/4311-word.doc",
}


def percentile(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    n = len(sorted_vals)
    if n == 1:
        return float(sorted_vals[0])
    pos = (p / 100.0) * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return float(sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac)


def stats_summary(vals):
    if not vals:
        return {"mean": 0.0, "min": 0.0, "max": 0.0, "p10": 0.0, "p90": 0.0}
    s = sorted(vals)
    return {
        "mean": sum(s) / len(s),
        "min": float(s[0]),
        "max": float(s[-1]),
        "p10": percentile(s, 10),
        "p90": percentile(s, 90),
    }


def is_persian_arabic(ch):
    cp = ord(ch)
    return 0x0600 <= cp <= 0x06FF


def main():
    out_lines = []

    def log(msg=""):
        out_lines.append(str(msg))

    log("=== S1 TEXT QUALITY & DOC CONVERSION REPORT (1-based page numbers / ۱-مبنا) ===")

    # ---------------------------------------------------------
    # SECTION A
    # ---------------------------------------------------------
    log("\n============================================================")
    log("SECTION A: pdf_fix vs raw comparison (1-based page numbers / ۱-مبنا)")
    log("============================================================")
    sec_a_targets = [
        ("11-Abnieh1404.pdf", FILES["abnieh"], 237),
        ("1147659-1404.pdf", FILES["1147659"], 47),
        ("13-barghi1404.pdf", FILES["barghi"], 188),
    ]
    for label, rel_path, page_1based in sec_a_targets:
        try:
            signal.alarm(300)
            full_path = ROOT / rel_path
            idx0 = page_1based - 1

            doc_raw = fitz.open(full_path)
            raw_txt = doc_raw[idx0].get_text()
            raw_lines = [ln.strip() for ln in raw_txt.splitlines() if ln.strip()]
            raw_nchar = len(raw_txt)
            doc_raw.close()

            doc_fix = open_patched_pdf(full_path)
            fix_lines_list = extract_rtl_page_lines(doc_fix[idx0], y_tolerance=4.5)
            fix_lines = [ln.strip() for ln in fix_lines_list if ln.strip()]
            fix_txt = "\n".join(fix_lines_list)
            fix_nchar = len(fix_txt)
            doc_fix.close()

            raw_set = set(raw_lines)
            fix_set = set(fix_lines)
            only_in_raw = [ln for ln in raw_lines if ln not in fix_set]
            only_in_fix = [ln for ln in fix_lines if ln not in raw_set]

            log(f"\n--- Target: {label} | Page (1-based / ۱-مبنا): {page_1based} ---")
            log(f"raw_nchar: {raw_nchar} | fix_nchar: {fix_nchar}")
            log(f"raw_nonempty_lines_count: {len(raw_lines)} | fix_nonempty_lines_count: {len(fix_lines)}")
            log(f"only_in_raw_total_lines: {len(only_in_raw)} | only_in_fix_total_lines: {len(only_in_fix)}")

            log("First 15 lines RAW (repr):")
            for i, ln in enumerate(raw_lines[:15], 1):
                log(f"  RAW[{i:02d}]: {repr(ln)}")

            log("First 15 lines FIX (repr):")
            for i, ln in enumerate(fix_lines[:15], 1):
                log(f"  FIX[{i:02d}]: {repr(ln)}")

            log("Up to 10 lines ONLY IN RAW (repr):")
            for i, ln in enumerate(only_in_raw[:10], 1):
                log(f"  ONLY_RAW[{i:02d}]: {repr(ln)}")

            log("Up to 10 lines ONLY IN FIX (repr):")
            for i, ln in enumerate(only_in_fix[:10], 1):
                log(f"  ONLY_FIX[{i:02d}]: {repr(ln)}")

            signal.alarm(0)
        except Exception as e:
            signal.alarm(0)
            log(f"ERROR in Section A for {label} page {page_1based}: {repr(e)}")

    # ---------------------------------------------------------
    # SECTION B
    # ---------------------------------------------------------
    log("\n============================================================")
    log("SECTION B: Watermark removal on تفسیر شرایط عمومی و خصوصی پیمان.pdf (1-based / ۱-مبنا)")
    log("============================================================")
    try:
        signal.alarm(300)
        tafsir_path = ROOT / FILES["tafsir"]
        doc_t = fitz.open(tafsir_path)
        num_pages = len(doc_t)
        before_counts = []
        after_counts = []
        lt_50_pages = []

        for p_idx in range(num_pages):
            raw_t = doc_t[p_idx].get_text()
            before_counts.append(len(raw_t))
            lines = raw_t.splitlines()
            kept = [
                ln for ln in lines
                if ("Academyofcivil.com" not in ln and "آکادمی عمران" not in ln)
            ]
            clean_t = "\n".join(kept)
            c_len = len(clean_t)
            after_counts.append(c_len)
            if c_len < 50:
                lt_50_pages.append((p_idx + 1, len(raw_t), c_len))
        doc_t.close()

        st_before = stats_summary(before_counts)
        st_after = stats_summary(after_counts)
        log(f"total_pages (1-based 1..{num_pages}): {num_pages}")
        log(f"BEFORE watermark removal -> mean: {st_before['mean']:.2f}, min: {int(st_before['min'])}, max: {int(st_before['max'])}")
        log(f"AFTER  watermark removal -> mean: {st_after['mean']:.2f}, min: {int(st_after['min'])}, max: {int(st_after['max'])}")
        log(f"pages_with_after_nchar_lt_50_count: {len(lt_50_pages)}")
        log(f"pages_with_after_nchar_lt_50_list (1-based page, before_len, after_len): {lt_50_pages}")
        signal.alarm(0)
    except Exception as e:
        signal.alarm(0)
        log(f"ERROR in Section B: {repr(e)}")

    # ---------------------------------------------------------
    # SECTION C
    # ---------------------------------------------------------
    log("\n============================================================")
    log("SECTION C: Convert 4311-word.doc via soffice")
    log("============================================================")
    try:
        signal.alarm(300)
        doc_src = ROOT / FILES["doc_4311"]
        cmd = [
            "soffice",
            "--headless",
            "-env:UserInstallation=file:///tmp/lo_profile",
            "--convert-to",
            "txt:Text",
            "--outdir",
            str(CONVERT_DIR),
            str(doc_src),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        log(f"soffice_returncode: {res.returncode}")
        stderr_lines = res.stderr.splitlines()[:5]
        log(f"soffice_stderr_first_5_lines: {repr(stderr_lines)}")
        stdout_lines = res.stdout.splitlines()[:5]
        log(f"soffice_stdout_first_5_lines: {repr(stdout_lines)}")

        out_txt_path = CONVERT_DIR / "4311-word.txt"
        if out_txt_path.exists() and out_txt_path.stat().st_size > 0:
            fsize = out_txt_path.stat().st_size
            txt_content = out_txt_path.read_text(encoding="utf-8", errors="replace")
            n_chars = len(txt_content)
            n_lines = len(txt_content.splitlines())
            non_space = [ch for ch in txt_content if not ch.isspace()]
            pa_chars = [ch for ch in non_space if is_persian_arabic(ch)]
            pa_ratio = (len(pa_chars) / len(non_space)) if non_space else 0.0

            log(f"output_file_path: {out_txt_path.relative_to(ROOT)}")
            log(f"output_file_size_bytes: {fsize}")
            log(f"n_chars: {n_chars} | n_lines: {n_lines}")
            log(f"non_space_chars: {len(non_space)} | persian_arabic_chars: {len(pa_chars)} | persian_arabic_ratio: {pa_ratio:.4f}")
            log(f"first_600_chars_repr: {repr(txt_content[:600])}")
            mid_start = max(0, (n_chars // 2) - 300)
            log(f"middle_600_chars_repr (slice {mid_start}:{mid_start+600}): {repr(txt_content[mid_start:mid_start+600])}")
        else:
            log("txt conversion missing or empty; attempting fallback to docx + python-docx...")
            cmd_docx = [
                "soffice",
                "--headless",
                "-env:UserInstallation=file:///tmp/lo_profile",
                "--convert-to",
                "docx",
                "--outdir",
                str(CONVERT_DIR),
                str(doc_src),
            ]
            res2 = subprocess.run(cmd_docx, capture_output=True, text=True, timeout=120)
            out_docx_path = CONVERT_DIR / "4311-word.docx"
            if res2.returncode == 0 and out_docx_path.exists():
                import docx
                d = docx.Document(str(out_docx_path))
                log(f"docx_paragraphs_count: {len(d.paragraphs)}")
                for i, p in enumerate(d.paragraphs[:3], 1):
                    log(f"  PARA[{i}]: {repr(p.text)}")
            else:
                log(f"اجرا نشد | raw_reason: returncode={res2.returncode}, stderr={repr(res2.stderr[:500])}")
        signal.alarm(0)
    except Exception as e:
        signal.alarm(0)
        log(f"ERROR in Section C: {repr(e)}")

    # ---------------------------------------------------------
    # SECTION D
    # ---------------------------------------------------------
    log("\n============================================================")
    log("SECTION D: Text quality metrics across 8 documents (raw, 1-based / ۱-مبنا)")
    log("============================================================")
    sec_d_docs = [
        ("abnieh (11-Abnieh1404.pdf)", FILES["abnieh"], False),
        ("mechanic (12-Mechanic1404.pdf)", FILES["mechanic"], False),
        ("barghi (13-barghi1404.pdf)", FILES["barghi"], False),
        ("tafsir (تفسیر شرایط عمومی و خصوصی پیمان.pdf)", FILES["tafsir"], True),
        ("1147659 (1147659-1404.pdf)", FILES["1147659"], False),
        ("941636 (941636_Tadil-Movaghat-3-4-1404-14041219.pdf)", FILES["941636"], False),
        ("Arz4 (Arz4(DrMostafaNajafi.ir).pdf)", FILES["Arz4"], False),
        ("eblagh (ابلاغ اصلاحيه موافقتنامه، شرايط عمومي.pdf)", FILES["eblagh"], False),
    ]

    summary_rows = []
    for doc_label, rel_path, strip_wm in sec_d_docs:
        try:
            signal.alarm(300)
            full_path = ROOT / rel_path
            doc = fitz.open(full_path)
            n_pages = len(doc)

            persian_ratios = []
            single_ratios = []
            nchars = []

            count_fvalad = 0
            count_folad = 0
            count_beton = 0

            for p_idx in range(n_pages):
                raw_t = doc[p_idx].get_text()
                if strip_wm:
                    lines = raw_t.splitlines()
                    kept = [
                        ln for ln in lines
                        if ("Academyofcivil.com" not in ln and "آکادمی عمران" not in ln)
                    ]
                    raw_t = "\n".join(kept)

                nc = len(raw_t)
                nchars.append(nc)

                non_space = [ch for ch in raw_t if not ch.isspace()]
                if non_space:
                    pa_cnt = sum(1 for ch in non_space if is_persian_arabic(ch))
                    p_ratio = pa_cnt / len(non_space)
                else:
                    p_ratio = 0.0
                persian_ratios.append(p_ratio)

                tokens = raw_t.split()
                if tokens:
                    single_cnt = sum(1 for tok in tokens if len(tok) == 1)
                    s_ratio = single_cnt / len(tokens)
                else:
                    s_ratio = 0.0
                single_ratios.append(s_ratio)

                count_fvalad += raw_t.count("فوالد")
                count_folad += raw_t.count("فولاد")
                count_beton += raw_t.count("بتن")

            doc.close()

            st_pr = stats_summary(persian_ratios)
            st_sr = stats_summary(single_ratios)
            st_nc = stats_summary(nchars)

            log(f"\n--- Document: {doc_label} | pages: {n_pages} | watermark_stripped: {strip_wm} ---")
            log(
                f"  persian_ratio -> mean: {st_pr['mean']:.4f}, min: {st_pr['min']:.4f}, "
                f"max: {st_pr['max']:.4f}, p10: {st_pr['p10']:.4f}, p90: {st_pr['p90']:.4f}"
            )
            log(
                f"  single_ratio  -> mean: {st_sr['mean']:.4f}, min: {st_sr['min']:.4f}, "
                f"max: {st_sr['max']:.4f}, p10: {st_sr['p10']:.4f}, p90: {st_sr['p90']:.4f}"
            )
            log(
                f"  nchar         -> mean: {st_nc['mean']:.2f}, min: {st_nc['min']:.0f}, "
                f"max: {st_nc['max']:.0f}, p10: {st_nc['p10']:.2f}, p90: {st_nc['p90']:.2f}"
            )
            log(
                f"  token_counts  -> 'فوالد': {count_fvalad} | 'فولاد': {count_folad} | 'بتن': {count_beton}"
            )
            summary_rows.append((doc_label, n_pages, st_pr, st_sr, st_nc, count_fvalad, count_folad, count_beton))
            signal.alarm(0)
        except Exception as e:
            signal.alarm(0)
            log(f"ERROR in Section D for {doc_label}: {repr(e)}")

    log("\n--- COMPACT TABLE FOR SECTION D ---")
    log("Doc | Pages | PR_mean | PR_min | PR_p10 | PR_p90 | PR_max | SR_mean | SR_min | SR_p10 | SR_p90 | SR_max | NC_mean | NC_min | NC_p10 | NC_p90 | NC_max | فوالد | فولاد | بتن")
    for r in summary_rows:
        dl, npg, pr, sr, nc, c_fv, c_fo, c_bt = r
        log(
            f"{dl} | {npg} | "
            f"{pr['mean']:.4f} | {pr['min']:.4f} | {pr['p10']:.4f} | {pr['p90']:.4f} | {pr['max']:.4f} | "
            f"{sr['mean']:.4f} | {sr['min']:.4f} | {sr['p10']:.4f} | {sr['p90']:.4f} | {sr['max']:.4f} | "
            f"{nc['mean']:.1f} | {nc['min']:.0f} | {nc['p10']:.1f} | {nc['p90']:.1f} | {nc['max']:.0f} | "
            f"{c_fv} | {c_fo} | {c_bt}"
        )

    log("\n=== END OF S1 REPORT ===")
    OUT_FILE.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(out_lines)} lines to {OUT_FILE}")


if __name__ == "__main__":
    main()
