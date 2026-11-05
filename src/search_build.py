#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import re
import sys
import json
import signal
import sqlite3
import subprocess
import unicodedata
from collections import Counter

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import fitz
from src.pdf_fix import open_patched_pdf, extract_rtl_page_lines


def _alarm_handler(signum, frame):
    raise TimeoutError("signal.alarm timeout reached")


signal.signal(signal.SIGALRM, _alarm_handler)


# (الف) normalize(s) — دقیقاً با همین ترتیب (۸ قاعده)
_REMOVE_RE = re.compile(
    r"[\uFEFF\u200D\u200E\u200F\u202A-\u202E\u064B-\u065F\u0670\u0640]"
)
_DIGIT_MAP = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫٬",
    "01234567890123456789.,",
)
_CHAR_MAP = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک"})
_LATIN_UPPER_MAP = {ord(c): ord(c.lower()) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}
_WS_RE = re.compile(r"\s+")


def normalize(s: str) -> str:
    if not s:
        return ""
    # 1) NFKC
    s = unicodedata.normalize("NFKC", s)
    # 2) حذف: U+FEFF, U+200D, U+200E, U+200F, U+202A–U+202E, اعراب U+064B–U+065F و U+0670, کشیده U+0640
    s = _REMOVE_RE.sub("", s)
    # 3) U+200C -> فاصله
    s = s.replace("\u200c", " ")
    # 4) ي(U+064A) و ى(U+0649)->ی(U+06CC)؛ ك(U+0643)->ک(U+06A9)
    s = s.translate(_CHAR_MAP)
    # 5) ارقام U+06F0–06F9 و U+0660–0669 -> 0-9؛ ٫(U+066B)->'.'؛ ٬(U+066C)->','
    s = s.translate(_DIGIT_MAP)
    # 6) 'لا' -> 'ال'
    s = s.replace("لا", "ال")
    # 7) حروف لاتین lowercase
    s = s.translate(_LATIN_UPPER_MAP)
    # 8) فاصله‌های متوالی -> یکی و trim
    s = _WS_RE.sub(" ", s).strip()
    return s


SELFTEST_INPUTS = [
    "فولاد",
    "فوالد",
    "۱۴۰۴٫۰۹",
    "كتاب يك",
    "قالب\u200cبندی",
    "تاًييديه",
    " A  b ",
    "\ufeffماده ۱",
    "شـرايط   عمومى",
    "١٬٢٣٤٫٥٦",
    "لايه\u200dبندي\u200f",
    "PDF_Test 1404",
]


def get_selftest_pairs():
    return [[inp, normalize(inp)] for inp in SELFTEST_INPUTS]


def remove_digit_tokens(text: str) -> str:
    # حذف توکن‌هایی که رقم دارند (پس از نرمال‌سازی ارقام یا قبل از آن)
    # برای اطمینان هر توکنی که شامل رقم فارسی/عربی/لاتین باشد حذف می‌شود
    digit_re = re.compile(r"[0-9\u06F0-\u06F9\u0660-\u0669]")
    out_lines = []
    for line in text.splitlines():
        tokens = line.split()
        kept = [t for t in tokens if not digit_re.search(t)]
        out_lines.append(" ".join(kept))
    return "\n".join(out_lines)


def main():
    os.makedirs("docs/search_logs", exist_ok=True)
    os.makedirs("output/search", exist_ok=True)
    report_lines = []

    def log(msg=""):
        report_lines.append(str(msg))

    built_date = subprocess.check_output(["date", "+%F"], text=True).strip()
    log(f"=== S2 BUILD REPORT ({built_date}) ===")

    # (الف) Selftest pairs
    selftest_pairs = get_selftest_pairs()

    # (ب) پیدا کردن فایل‌ها با os.walk('data/') با تطبیق نام نرمال‌شده
    data_files_by_norm = {}
    for root, _dirs, files in os.walk("data"):
        for f in sorted(files):
            nf = normalize(f)
            full_p = os.path.join(root, f)
            data_files_by_norm.setdefault(nf, []).append((f, full_p))

    pdf_specs = [
        {"key": "11-Abnieh1404.pdf", "group": "ابنیه", "status_override": None, "scan": False, "tafsir": False},
        {"key": "12-Mechanic1404.pdf", "group": "مکانیک", "status_override": None, "scan": False, "tafsir": False},
        {"key": "13-barghi1404.pdf", "group": "برق", "status_override": None, "scan": False, "tafsir": False},
        {"key": "تفسیر شرایط عمومی و خصوصی پیمان.pdf", "group": "پیمان", "status_override": None, "scan": False, "tafsir": True},
        {"key": "ابلاغ اصلاحیه موافقتنامه، شرایط عمومی.pdf", "group": "پیمان", "status_override": "متن خراب", "scan": False, "tafsir": False},
        {"key": "1147659-1404.pdf", "group": "تعدیل", "status_override": None, "scan": False, "tafsir": False},
        {"key": "941636_Tadil-Movaghat-3-4-1404-14041219.pdf", "group": "تعدیل", "status_override": None, "scan": False, "tafsir": False},
        {"key": "Arz4(DrMostafaNajafi.ir).pdf", "group": "تعدیل", "status_override": None, "scan": False, "tafsir": False},
        {"key": "101-173073.pdf", "group": "تعدیل", "status_override": "بدون متن (اسکن)", "scan": True, "tafsir": False},
        {"key": "4311-pdf.pdf", "group": "پیمان", "status_override": "بدون متن (اسکن)", "scan": True, "tafsir": False},
    ]

    docs_meta = []
    pages_data = []  # [[doc_id, page_label_or_num, n, s]]
    # نگه داشتن صفحات خام و fix برای تحلیل (ه)
    doc_raw_fix_pages = {}  # doc_id -> list of (page_no, s_text, r_text, n_text)
    abnieh_doc_id = None
    doc_1147659_id = None

    log("\n--- (ب) تطبیق فایل‌ها در data/ ---")
    doc_id_counter = 0
    for spec in pdf_specs:
        nk = normalize(spec["key"])
        matches = data_files_by_norm.get(nk, [])
        if len(matches) != 1:
            log(f"KEY={spec['key']} (norm={nk}) -> نیازمند بررسی (تعداد تطبیق={len(matches)})")
            continue
        actual_fname, full_path = matches[0]
        doc_id = doc_id_counter
        doc_id_counter += 1
        log(f"DOC[{doc_id}] key='{spec['key']}' -> matched='{full_path}' (label='{actual_fname}', group='{spec['group']}')")

        if spec["key"] == "11-Abnieh1404.pdf":
            abnieh_doc_id = doc_id
        if spec["key"] == "1147659-1404.pdf":
            doc_1147659_id = doc_id

        if spec["scan"]:
            # اسکن: بدون ایندکس صفحه، فقط ثبت در جدول اسناد
            try:
                signal.alarm(300)
                d_tmp = fitz.open(full_path)
                total_p = len(d_tmp)
                d_tmp.close()
                signal.alarm(0)
            except Exception as e:
                signal.alarm(0)
                total_p = 0
                log(f"  ERROR opening scan pdf {full_path}: {e}")
            docs_meta.append({
                "id": doc_id,
                "label": actual_fname,
                "group": spec["group"],
                "status": spec["status_override"],
                "pages": total_p,
                "low_text_pages": total_p,
                "file": actual_fname,
                "kind": "scan",
            })
            continue

        # (ج) استخراج هر صفحهٔ هر PDF دارای متن
        try:
            signal.alarm(300)
            doc_raw = fitz.open(full_path)
            doc_fix = open_patched_pdf(full_path)
            num_pages = len(doc_raw)
            raw_fix_list = []
            low_text_count = 0

            for idx0 in range(num_pages):
                pno = idx0 + 1
                fix_lines_list = extract_rtl_page_lines(doc_fix[idx0], y_tolerance=4.5)
                raw_txt = doc_raw[idx0].get_text()
                raw_lines_list = raw_txt.splitlines()

                if spec["tafsir"]:
                    fix_lines_list = [
                        ln for ln in fix_lines_list
                        if ("Academyofcivil.com" not in ln and "آکادمی عمران" not in ln)
                    ]
                    raw_lines_list = [
                        ln for ln in raw_lines_list
                        if ("Academyofcivil.com" not in ln and "آکادمی عمران" not in ln)
                    ]

                s_txt = "\n".join(fix_lines_list)
                r_txt = "\n".join(raw_lines_list)
                if len(s_txt) + len(r_txt) < 50:
                    low_text_count += 1
                raw_fix_list.append((pno, s_txt, r_txt))

            doc_raw.close()
            doc_fix.close()
            signal.alarm(0)
        except Exception as e:
            signal.alarm(0)
            log(f"  ERROR extracting {full_path}: {e}")
            continue

        # (د) حذف خطوط سرصفحه تکراری (>50% صفحات در اسناد با >=20 صفحه) فقط از n
        # اصلاح S2.1 (الف): حذف فقط برای خط‌هایی که پس از normalize حداقل ۳ توکن دارند
        repeated_norm_lines = set()
        removed_lines_report = []
        removed_lines_before_s21 = []
        if num_pages >= 20:
            line_page_freq = Counter()
            for _pno, s_txt, r_txt in raw_fix_list:
                r_nodig = remove_digit_tokens(r_txt)
                combined_lines = s_txt.splitlines() + r_nodig.splitlines()
                page_unique_norm = set()
                for ln in combined_lines:
                    nln = normalize(ln)
                    if nln:
                        page_unique_norm.add(nln)
                for nln in page_unique_norm:
                    line_page_freq[nln] += 1

            threshold = num_pages * 0.5
            for nln, cnt in sorted(line_page_freq.items(), key=lambda x: (-x[1], x[0])):
                if cnt > threshold:
                    tcnt = len(nln.split())
                    removed_lines_before_s21.append((nln, cnt, num_pages, tcnt))
                    if tcnt >= 3:
                        repeated_norm_lines.add(nln)
                        removed_lines_report.append((nln, cnt, num_pages, tcnt))

        doc_page_records = []
        for pno, s_txt, r_txt in raw_fix_list:
            r_nodig = remove_digit_tokens(r_txt)
            s_norm_lines = [normalize(ln) for ln in s_txt.splitlines()]
            r_norm_lines = [normalize(ln) for ln in r_nodig.splitlines()]
            if repeated_norm_lines:
                s_norm_lines = [ln for ln in s_norm_lines if ln and ln not in repeated_norm_lines]
                r_norm_lines = [ln for ln in r_norm_lines if ln and ln not in repeated_norm_lines]
            else:
                s_norm_lines = [ln for ln in s_norm_lines if ln]
                r_norm_lines = [ln for ln in r_norm_lines if ln]

            n_s = " ".join(s_norm_lines)
            n_r = " ".join(r_norm_lines)
            n_txt = normalize(s_txt) + "\n" + normalize(r_nodig)
            if repeated_norm_lines:
                n_txt = n_s + "\n" + n_r

            pages_data.append([doc_id, pno, n_txt, s_txt])
            doc_page_records.append((pno, s_txt, r_txt, n_txt))

        doc_raw_fix_pages[doc_id] = {
            "label": actual_fname,
            "group": spec["group"],
            "num_pages": num_pages,
            "low_text_pages": low_text_count,
            "removed_headers_before": removed_lines_before_s21,
            "removed_headers": removed_lines_report,
            "records": doc_page_records,
        }

        status_str = spec["status_override"] if spec["status_override"] else "متن (fix+raw)"
        docs_meta.append({
            "id": doc_id,
            "label": actual_fname,
            "group": spec["group"],
            "status": status_str,
            "pages": num_pages,
            "low_text_pages": low_text_count,
            "file": actual_fname,
            "kind": "pdf",
        })

    # واحد .doc: data_processed/doc_convert_test/4311-word.txt
    word_txt_path = "data_processed/doc_convert_test/4311-word.txt"
    log("\n--- (ج) پردازش 4311-word.txt (واحد=ماده) ---")
    if not os.path.exists(word_txt_path):
        log(f"{word_txt_path} -> نیازمند بررسی (فایل یافت نشد)")
    else:
        doc_id = doc_id_counter
        doc_id_counter += 1
        with open(word_txt_path, "r", encoding="utf-8") as wf:
            raw_word = wf.read()
        if raw_word.startswith("\ufeff"):
            raw_word = raw_word.lstrip("\ufeff")

        madeh_re = re.compile(r"^\s*ماده\s*(\d+)\b")
        units = []  # (unit_label, madeh_num_or_None, start_line_1based, lines)
        cur_label = "مقدمه"
        cur_num = None
        cur_start_line = 1
        cur_lines = []
        madeh_numbers_seq = []

        for line_idx0, orig_line in enumerate(raw_word.splitlines()):
            line_no_1based = line_idx0 + 1
            norm_line = normalize(orig_line)
            m = madeh_re.match(norm_line)
            if m:
                if cur_lines:
                    units.append((cur_label, cur_num, cur_start_line, cur_lines))
                num_val = int(m.group(1))
                madeh_numbers_seq.append(num_val)
                cur_label = f"ماده {num_val}"
                cur_num = num_val
                cur_start_line = line_no_1based
                cur_lines = [orig_line]
            else:
                cur_lines.append(orig_line)
        if cur_lines:
            units.append((cur_label, cur_num, cur_start_line, cur_lines))

        total_doc_units = len(units)
        low_u_count = 0
        doc_page_records = []
        for unit_idx0, (u_label, u_num, u_start_line, u_lines) in enumerate(units):
            unit_seq_1based = unit_idx0 + 1
            s_txt = "\n".join(u_lines).strip()
            r_txt = ""
            if len(s_txt) < 50:
                low_u_count += 1
            n_txt = normalize(s_txt) + "\n"
            # اصلاح S2.1 (ب): ذخیره شماره ماده N، شماره ترتیبی یکتا (i از T) و خط شروع L در 4311-word.txt
            page_field = u_num if u_num is not None else 0
            unit_meta = {
                "n": page_field,
                "i": unit_seq_1based,
                "t": total_doc_units,
                "l": u_start_line,
            }
            pages_data.append([doc_id, page_field, n_txt, s_txt, unit_meta])
            doc_page_records.append((f"{u_label} (واحد {unit_seq_1based}/{total_doc_units}, خط {u_start_line})", s_txt, r_txt, n_txt))

        doc_raw_fix_pages[doc_id] = {
            "label": "4311-word.txt",
            "group": "پیمان",
            "num_pages": len(units),
            "low_text_pages": low_u_count,
            "removed_headers_before": [],
            "removed_headers": [],
            "records": doc_page_records,
        }

        docs_meta.append({
            "id": doc_id,
            "label": "4311-word.txt",
            "group": "پیمان",
            "status": "واحد=ماده؛ بدون شمارهٔ صفحه؛ از تبدیل .doc با soffice",
            "pages": len(units),
            "low_text_pages": low_u_count,
            "file": "4311-word.txt",
            "kind": "doc_madeh",
        })

        # تحلیل شماره‌ها، کمین/بیشینه، تکراری‌ها، نزولی‌ها، جاافتادگی‌ها
        min_n = min(madeh_numbers_seq) if madeh_numbers_seq else None
        max_n = max(madeh_numbers_seq) if madeh_numbers_seq else None
        freq_n = Counter(madeh_numbers_seq)
        duplicates = {k: v for k, v in freq_n.items() if v > 1}
        descents = []
        for i in range(1, len(madeh_numbers_seq)):
            if madeh_numbers_seq[i] <= madeh_numbers_seq[i - 1]:
                descents.append((i, madeh_numbers_seq[i - 1], madeh_numbers_seq[i]))
        present_set = set(madeh_numbers_seq)
        missing_in_range = (
            [x for x in range(min_n, max_n + 1) if x not in present_set]
            if (min_n is not None and max_n is not None)
            else []
        )
        log(f"4311-word.txt total_units={len(units)} (شامل مقدمه={1 if units and units[0][1] is None else 0} + تعداد ماده={len(madeh_numbers_seq)})")
        log(f"  sequence of N: {madeh_numbers_seq}")
        log(f"  min(N)={min_n}, max(N)={max_n}")
        log(f"  duplicates={duplicates}")
        log(f"  descents (index, prev, curr)={descents}")
        log(f"  missing in [min..max]={missing_in_range}")

    # گزارش بخش (د): خطوط سرصفحه حذف‌شده (قبل و بعد از آستانه >=3 توکن در S2.1) و صفحات کم‌متن
    log("\n--- (د) خطوط سرصفحه حذف‌شده (>50% صفحات در اسناد با >=20 صفحه؛ آستانه S2.1: حداقل ۳ توکن پس از normalize) و صفحات کم‌متن (<50 نویسه در s+r) ---")
    for dm in docs_meta:
        did = dm["id"]
        log(f"DOC[{did}] {dm['label']} | group={dm['group']} | status={dm['status']} | pages/units={dm['pages']} | low_text(<50)={dm['low_text_pages']}")
        if did in doc_raw_fix_pages:
            rem_before = doc_raw_fix_pages[did].get("removed_headers_before", [])
            rem_after = doc_raw_fix_pages[did]["removed_headers"]
            log(f"  Before S2.1 (all >50%): {len(rem_before)} lines | After S2.1 (>=3 tokens): {len(rem_after)} lines")
            if rem_before:
                log("  List BEFORE S2.1:")
                for nln, cnt, tot, tcnt in rem_before:
                    log(f"    - [{cnt}/{tot} pages] (tokens={tcnt}) '{nln}'")
            if rem_after:
                log("  List AFTER S2.1 (actually removed from n):")
                for nln, cnt, tot, tcnt in rem_after:
                    log(f"    - [{cnt}/{tot} pages] (tokens={tcnt}) '{nln}'")
            else:
                log("  List AFTER S2.1 (actually removed from n): NONE")

    # (ه-۱) اعتبارسنجی با reference.db (read-only)
    log("\n--- (ه-۱) اعتبارسنجی کدهای ابنیه با reference.db ---")
    try:
        conn = sqlite3.connect("file:data_processed/reference.db?mode=ro", uri=True)
        cur = conn.cursor()
        pragma_rows = cur.execute("PRAGMA table_info(items)").fetchall()
        log(f"PRAGMA table_info(items): {pragma_rows}")
        col_names = [r[1] for r in pragma_rows]
        if "code_official" in col_names and "source_page_pdf" in col_names and abnieh_doc_id is not None:
            rows = cur.execute("SELECT code_official, source_page_pdf FROM items").fetchall()
            null_page_count = sum(1 for r in rows if r[1] is None or str(r[1]).strip() == "")
            valid_rows = [(str(r[0]), int(r[1])) for r in rows if r[1] is not None and str(r[1]).strip() != ""]

            abnieh_records = {pno: (s_t, r_t, n_t) for (pno, s_t, r_t, n_t) in doc_raw_fix_pages[abnieh_doc_id]["records"]}
            abnieh_fix_tokens = {}
            abnieh_raw_tokens = {}
            abnieh_comb_tokens = {}
            abnieh_fix_norm_str = {}
            abnieh_raw_norm_str = {}
            abnieh_comb_norm_str = {}

            for pno, (s_t, r_t, _n_t) in abnieh_records.items():
                ns = normalize(s_t)
                nr = normalize(r_t)  # برای بررسی حضور کد در raw خام نرمال‌شده
                nc = ns + " " + nr
                abnieh_fix_norm_str[pno] = ns
                abnieh_raw_norm_str[pno] = nr
                abnieh_comb_norm_str[pno] = nc
                # توکن مستقل: جداسازی بر اساس هر کاراکتر غیرحرف/غیررقم
                abnieh_fix_tokens[pno] = set(re.findall(r"[0-9a-zA-Z\u0600-\u06FF]+", ns))
                abnieh_raw_tokens[pno] = set(re.findall(r"[0-9a-zA-Z\u0600-\u06FF]+", nr))
                abnieh_comb_tokens[pno] = abnieh_fix_tokens[pno] | abnieh_raw_tokens[pno]

            in_fix_tok = 0
            in_raw_tok = 0
            in_comb_tok = 0
            only_fix_tok = 0
            only_raw_tok = 0
            only_substring = 0
            not_found_list = []

            for code_raw, pno in valid_rows:
                ncode = normalize(code_raw)
                f_has = ncode in abnieh_fix_tokens.get(pno, set())
                r_has = ncode in abnieh_raw_tokens.get(pno, set())
                c_has = ncode in abnieh_comb_tokens.get(pno, set())

                if f_has:
                    in_fix_tok += 1
                if r_has:
                    in_raw_tok += 1
                if c_has:
                    in_comb_tok += 1
                if f_has and not r_has:
                    only_fix_tok += 1
                if r_has and not f_has:
                    only_raw_tok += 1

                if not c_has:
                    if ncode in abnieh_comb_norm_str.get(pno, ""):
                        only_substring += 1
                    else:
                        not_found_list.append((ncode, pno))

            pm1_found = 0
            samples_20 = []
            for ncode, pno in not_found_list:
                found_adj = []
                for adj in (pno - 1, pno + 1):
                    if ncode in abnieh_comb_tokens.get(adj, set()) or ncode in abnieh_comb_norm_str.get(adj, ""):
                        found_adj.append(adj)
                if found_adj:
                    pm1_found += 1
                if len(samples_20) < 20:
                    samples_20.append((ncode, pno, found_adj))

            log(f"Total rows in items={len(rows)} | rows with source_page_pdf={len(valid_rows)} | empty source_page_pdf={null_page_count}")
            log(f"Token match in target page: fix={in_fix_tok}, raw={in_raw_tok}, combined={in_comb_tok}")
            log(f"Exclusive token match: only_in_fix={only_fix_tok}, only_in_raw={only_raw_tok}")
            log(f"Only as substring (not standalone token) in target page={only_substring}")
            log(f"Not found in target page (neither token nor substring)={len(not_found_list)} | found in page ±1={pm1_found}")
            log(f"20 samples of not-found (code, source_page_pdf, found_in_adj_pages): {samples_20}")
        else:
            log("نیازمند بررسی (ستون‌های code_official یا source_page_pdf یافت نشد)")
        conn.close()
    except Exception as e:
        log(f"ERROR in (ه-۱): {e}")

    # (ه-۲) برای هر سند: توکن‌های یکتا فقط در fix، فقط در raw (normalize، بدون توکن رقم‌دار)، مشترک + ۱۵ پرتکرار
    log("\n--- (ه-۲) مقایسهٔ توکن‌های fix و raw (بدون توکن رقم‌دار) به تفکیک سند ---")
    try:
        for did, dinfo in doc_raw_fix_pages.items():
            fix_counter = Counter()
            raw_counter = Counter()
            for _pno, s_txt, r_txt, _n_txt in dinfo["records"]:
                s_nd = normalize(remove_digit_tokens(s_txt))
                r_nd = normalize(remove_digit_tokens(r_txt))
                for t in s_nd.split():
                    fix_counter[t] += 1
                for t in r_nd.split():
                    raw_counter[t] += 1
            fix_set = set(fix_counter.keys())
            raw_set = set(raw_counter.keys())
            only_f = fix_set - raw_set
            only_r = raw_set - fix_set
            common_fr = fix_set & raw_set
            top15_only_f = sorted(((t, fix_counter[t]) for t in only_f), key=lambda x: (-x[1], x[0]))[:15]
            top15_only_r = sorted(((t, raw_counter[t]) for t in only_r), key=lambda x: (-x[1], x[0]))[:15]
            log(f"DOC[{did}] {dinfo['label']}: unique_only_fix={len(only_f)}, unique_only_raw={len(only_r)}, unique_common={len(common_fr)}")
            log(f"  Top 15 only-fix: {top15_only_f}")
            log(f"  Top 15 only-raw: {top15_only_r}")
    except Exception as e:
        log(f"ERROR in (ه-۲): {e}")

    # (ه-۳) برای 1147659 ص۴۷ (۱-مبنا): طول دقیق s و r در کنار اعداد probe_pdfs_full.txt
    log("\n--- (ه-۳) بررسی 1147659-1404.pdf صفحه ۴۷ (۱-مبنا) ---")
    try:
        probe_grep_lines = []
        if os.path.exists("docs/step_c4_logs/probe_pdfs_full.txt"):
            with open("docs/step_c4_logs/probe_pdfs_full.txt", "r", encoding="utf-8") as pf:
                in_file02 = False
                for ln in pf:
                    if "FILE_02:" in ln:
                        in_file02 = True
                    elif ln.startswith("FILE_") and "FILE_02:" not in ln:
                        in_file02 = False
                    if in_file02 and ("886" in ln or "1302" in ln or "mid_page" in ln or "p47" in ln or "47" in ln):
                        probe_grep_lines.append(ln.strip())

        if doc_1147659_id is not None:
            rec_47 = [r for r in doc_raw_fix_pages[doc_1147659_id]["records"] if r[0] == 47]
            if rec_47:
                _, s_47, r_47, _ = rec_47[0]
                len_s = len(s_47)
                len_r = len(r_47)
                log(f"probe_pdfs_full.txt grep lines for FILE_02: {probe_grep_lines}")
                log(f"1147659 p47 (1-based): len(s/fix)={len_s}, len(r/raw)={len_r} | probe_pdfs_full.txt values: fix=886, raw=1302")
                if len_s != 886 or len_r != 1302:
                    log("وضعیت: ناهمخوان، علت اثبات نشد")
                else:
                    log("وضعیت: همخوان (886 و 1302)")
    except Exception as e:
        log(f"ERROR in (ه-۳): {e}")

    # (ه-۴) شبیه‌سازی جستجو در پایتون و تولید s21_expected_counts.json
    log("\n--- (ه-۴) شبیه‌سازی جستجو در پایتون (عادی و بی‌فاصله) و مقایسه با S2 ---")
    try:
        doc_group_map = {dm["id"]: dm["group"] for dm in docs_meta}
        groups_order = ["ابنیه", "مکانیک", "برق", "پیمان", "تعدیل"]

        def sim_search(query_str: str, ignore_spaces: bool = False):
            nq = normalize(query_str)
            tokens = [t for t in nq.split(" ") if t]
            if ignore_spaces:
                tokens = [t.replace(" ", "") for t in tokens]
            total_hits = 0
            by_group = {g: 0 for g in groups_order}
            for rec in pages_data:
                did = rec[0]
                n_txt = rec[2]
                target = n_txt.replace(" ", "").replace("\n", "") if ignore_spaces else n_txt
                if all(tok in target for tok in tokens):
                    total_hits += 1
                    by_group[doc_group_map[did]] += 1
            return total_hits, by_group

        # شبیه‌ساز با n قبل از تغییر S2.1 (حذف همه خطوط >50% حتی <3 توکن) برای ثبت دقیق اعداد S2 در کنار S2.1
        pages_data_s2_legacy = []
        for dm in docs_meta:
            did = dm["id"]
            if did not in doc_raw_fix_pages:
                continue
            dinfo = doc_raw_fix_pages[did]
            if dm["kind"] == "doc_madeh":
                for _ulab, s_t, _r_t, n_t in dinfo["records"]:
                    pages_data_s2_legacy.append((did, n_t))
            else:
                rep_before_set = {x[0] for x in dinfo.get("removed_headers_before", [])}
                for _pno, s_t, r_t, _n_t in dinfo["records"]:
                    r_nd = remove_digit_tokens(r_t)
                    if rep_before_set:
                        sn = [normalize(ln) for ln in s_t.splitlines()]
                        rn = [normalize(ln) for ln in r_nd.splitlines()]
                        sn = [ln for ln in sn if ln and ln not in rep_before_set]
                        rn = [ln for ln in rn if ln and ln not in rep_before_set]
                        nt_old = " ".join(sn) + "\n" + " ".join(rn)
                    else:
                        nt_old = normalize(s_t) + "\n" + normalize(r_nd)
                    pages_data_s2_legacy.append((did, nt_old))

        def sim_search_s2_legacy(query_str: str, ignore_spaces: bool = False):
            nq = normalize(query_str)
            tokens = [t for t in nq.split(" ") if t]
            if ignore_spaces:
                tokens = [t.replace(" ", "") for t in tokens]
            total_hits = 0
            by_group = {g: 0 for g in groups_order}
            for did, n_txt in pages_data_s2_legacy:
                target = n_txt.replace(" ", "").replace("\n", "") if ignore_spaces else n_txt
                if all(tok in target for tok in tokens):
                    total_hits += 1
                    by_group[doc_group_map[did]] += 1
            return total_hits, by_group

        expected_counts_payload = {
            "groups_order": groups_order,
            "queries": [],
        }

        def record_query_pair(q_str: str):
            tot_norm, grp_norm = sim_search(q_str, False)
            tot_nosp, grp_nosp = sim_search(q_str, True)
            s2_tot_norm, s2_grp_norm = sim_search_s2_legacy(q_str, False)
            s2_tot_nosp, s2_grp_nosp = sim_search_s2_legacy(q_str, True)
            expected_counts_payload["queries"].append({
                "query": q_str,
                "normal": {"total": tot_norm, "by_group": grp_norm, "s2_total": s2_tot_norm, "s2_by_group": s2_grp_norm},
                "no_space": {"total": tot_nosp, "by_group": grp_nosp, "s2_total": s2_tot_nosp, "s2_by_group": s2_grp_nosp},
            })
            return (tot_norm, grp_norm, s2_tot_norm, s2_grp_norm), (tot_nosp, grp_nosp, s2_tot_nosp, s2_grp_nosp)

        (c_folad, g_folad, s2_cf, s2_gf), _ = record_query_pair("فولاد")
        (c_fovalad, g_fovalad, s2_cfv, s2_gfv), _ = record_query_pair("فوالد")
        same_flag = "یکسان (OK)" if (c_folad == c_fovalad and g_folad == g_fovalad) else "ناهمسان!"
        log(f"Query 'فولاد' (عادی): S2.1 total={c_folad} {g_folad} | S2 total={s2_cf} {s2_gf}")
        log(f"Query 'فوالد' (عادی): S2.1 total={c_fovalad} {g_fovalad} | S2 total={s2_cfv} {s2_gfv} -> مقایسه: {same_flag}")

        test_queries = [
            "بتن",
            "قالب بندی",
            "تعدیل",
            "حسن انجام کار",
            "صورت وضعیت",
            "پیش پرداخت",
            "ضریب طبقات",
            "شیشه",
            "پیمانکار",
            "پیمان",
        ]
        for q in test_queries:
            (tot_norm, grp_norm, s2_tn, s2_gn), (tot_nosp, grp_nosp, s2_tns, s2_gns) = record_query_pair(q)
            log(f"Query '{q}':")
            log(f"  عادی     : S2.1 total={tot_norm:4d} {grp_norm} | S2 total={s2_tn:4d} {s2_gn}")
            log(f"  بی‌فاصله : S2.1 total={tot_nosp:4d} {grp_nosp} | S2 total={s2_tns:4d} {s2_gns}")

        expected_json_path = "docs/search_logs/s21_expected_counts.json"
        with open(expected_json_path, "w", encoding="utf-8") as ejf:
            json.dump(expected_counts_payload, ejf, ensure_ascii=False, indent=2)
        log(f"Saved expected counts JSON to {expected_json_path} (queries={len(expected_counts_payload['queries'])})")
    except Exception as e:
        log(f"ERROR in (ه-۴): {e}")

    # (ه-۵) بازبینی selftest جفت‌های (الف)
    log("\n--- (ه-۵) خودآزمایی (Selftest) جفت‌های normalize در پایتون ---")
    all_ok = True
    for inp, expected in selftest_pairs:
        actual = normalize(inp)
        ok = actual == expected
        if not ok:
            all_ok = False
        log(f"  in={repr(inp)} -> out={repr(actual)} | ok={ok}")
    log(f"Python selftest overall: {'OK' if all_ok else 'FAIL'}")

    # (و-۰) S3: لایهٔ ردیف‌های فهرست‌بها از سه فایل xlsx (بدون قیمت)
    log("\n--- (و-۰) S3: پردازش ردیف‌های فهرست‌بها از سه فایل xlsx (بدون قیمت) ---")
    rows_json_data = []
    try:
        from src.search_rows import (
            load_abnieh_db_codes,
            read_catalog,
            map_pages_and_build_rows,
            format_s3_build_report,
        )
        db_code_to_pages, db_all_codes, db_info = load_abnieh_db_codes("data_processed/reference.db")
        xlsx_specs = [
            ("abn1404.xlsx", "فهرست‌بها — ابنیه"),
            ("mec1404.xlsx", "فهرست‌بها — مکانیک"),
            ("elec1404.xlsx", "فهرست‌بها — برق"),
        ]
        # یافتن فایل‌های xlsx با os.walk و تطبیق نام نرمال‌شده
        found_xlsx = {}
        for root_dir, _dirs, files in os.walk("data"):
            for fn in files:
                n_fn = normalize(fn)
                for target_fn, grp_name in xlsx_specs:
                    if n_fn == normalize(target_fn):
                        found_xlsx[grp_name] = (os.path.join(root_dir, fn), fn)

        catalog_by_group = {}
        catalog_reports = {}
        for target_fn, grp_name in xlsx_specs:
            try:
                if grp_name not in found_xlsx:
                    log(f"XLSX for {grp_name} ({target_fn}) -> نیازمند بررسی (یافت نشد)")
                    continue
                x_path, actual_xfn = found_xlsx[grp_name]
                use_db_codes = db_all_codes if grp_name == "فهرست‌بها — ابنیه" else None
                entries, rep = read_catalog(x_path, grp_name, db_codes=use_db_codes)
                catalog_by_group[grp_name] = entries
                catalog_reports[grp_name] = rep
                docs_meta.append({
                    "id": doc_id_counter,
                    "label": actual_xfn,
                    "group": grp_name,
                    "status": "ردیف‌های xlsx (بدون قیمت)",
                    "pages": len(entries),
                    "low_text_pages": len(rep["non_code_rows"]),
                    "file": actual_xfn,
                    "kind": "xlsx",
                })
                doc_id_counter += 1
                log(f"Loaded XLSX {actual_xfn} ({grp_name}): valid_rows={len(entries)}, non_code_rows={len(rep['non_code_rows'])}")
            except Exception as ex_cat:
                log(f"ERROR reading catalog {target_fn}: {ex_cat}")

        pdf_records_by_group = {}
        for _did, dinfo in doc_raw_fix_pages.items():
            gname = dinfo["group"]
            if gname in ("فهرست‌بها — ابنیه", "فهرست‌بها — مکانیک", "فهرست‌بها — برق"):
                pdf_records_by_group[gname] = dinfo["records"]

        try:
            rows_json_data, mapping_report = map_pages_and_build_rows(
                catalog_by_group, pdf_records_by_group, db_code_to_pages
            )
            s3_report_text = format_s3_build_report(catalog_reports, mapping_report, db_info)
            s3_report_path = "docs/search_logs/s3_build_report.txt"
            with open(s3_report_path, "w", encoding="utf-8") as s3rf:
                s3rf.write(s3_report_text)
            log(f"Saved S3 build report to {s3_report_path} (total rows={len(rows_json_data)})")
        except Exception as ex_map:
            log(f"ERROR mapping pages for S3 rows: {ex_map}")
    except Exception as ex_s3:
        log(f"ERROR in S3 block: {ex_s3}")

    # (و) تولید HTML
    log("\n--- (و) تولید فایل HTML تک‌فایلی ---")
    template_path = "templates/search_template.html"
    rows_part_path = "templates/search_rows_part.html"
    output_html_path = "output/search/metreyar_search.html"
    if not os.path.exists(template_path):
        log(f"Template not found at {template_path}")
    else:
        with open(template_path, "r", encoding="utf-8") as tf:
            tpl_html = tf.read()

        if "<!--__ROWS_PART__-->" not in tpl_html:
            raise RuntimeError("Placeholder <!--__ROWS_PART__--> not found in templates/search_template.html")
        if not os.path.exists(rows_part_path):
            raise RuntimeError(f"Rows template part not found at {rows_part_path}")
        with open(rows_part_path, "r", encoding="utf-8") as rpf:
            rows_part_html = rpf.read()

        docs_json_list = [
            {
                "id": dm["id"],
                "label": dm["label"],
                "group": dm["group"],
                "status": dm["status"],
                "pages": dm["pages"],
                "low_text_pages": dm["low_text_pages"],
                "file": dm["file"],
            }
            for dm in docs_meta
        ]
        payload = {
            "built": built_date,
            "docs": docs_json_list,
            "pages": pages_data,
            "rows": rows_json_data,
            "selftest": selftest_pairs,
        }
        json_str = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        final_html = tpl_html.replace("/*__DATA__*/", json_str).replace("<!--__ROWS_PART__-->", rows_part_html)
        with open(output_html_path, "w", encoding="utf-8") as outf:
            outf.write(final_html)
        html_size = os.path.getsize(output_html_path)
        log(f"Generated {output_html_path} | size={html_size} bytes ({html_size / (1024*1024):.2f} MB) | indexed_pages_units={len(pages_data)} | rows={len(rows_json_data)}")

    report_path = "docs/search_logs/s2_build_report.txt"
    with open(report_path, "w", encoding="utf-8") as rf:
        rf.write("\n".join(report_lines) + "\n")


if __name__ == "__main__":
    main()
