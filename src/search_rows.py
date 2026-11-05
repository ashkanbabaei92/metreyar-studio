#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S3 — لایهٔ ردیف‌های فهرست‌بها از سه فایل xlsx (ابنیه، مکانیک، برق)
فقط کد، شرح، واحد (ستون‌های ۱ تا ۳). قیمت و ستون‌های ۴ به بعد هرگز در خروجی ذخیره نمی‌شوند.
"""

import re
import sqlite3
from collections import Counter, defaultdict
import openpyxl


def _get_normalize():
    from src.search_build import normalize
    return normalize


def load_abnieh_db_codes(db_path="data_processed/reference.db"):
    """
    خواندن فقط‌خواندنی از جدول items در reference.db
    برمی‌گرداند:
      code_to_pages: dict[code_official_str -> sorted list of source_page_pdf]
      all_codes: set of code_official_str
      db_info: dict for reporting
    """
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(items)")
    cols = [(r[1], r[2]) for r in cur.fetchall()]
    cur.execute("SELECT code_official, source_page_pdf FROM items")
    code_to_pages = defaultdict(set)
    all_codes = set()
    total_db_rows = 0
    for code_off, spage in cur.fetchall():
        total_db_rows += 1
        if code_off is not None:
            c_str = str(code_off).strip()
            all_codes.add(c_str)
            if spage is not None:
                try:
                    code_to_pages[c_str].add(int(spage))
                except (ValueError, TypeError):
                    pass
    conn.close()
    sorted_map = {k: sorted(v) for k, v in code_to_pages.items()}
    return sorted_map, all_codes, {"columns": cols, "total_rows": total_db_rows, "unique_codes": len(all_codes)}


def read_catalog(path, group_id, db_codes=None):
    """
    الف + ب:
    openpyxl read_only=True, data_only=True.
    فقط ستون‌های ۱ تا ۳ (کد، شرح، واحد) برای ساخت کاتالوگ خوانده می‌شوند.
    ستون‌های پنجم به بعد (E–G) فقط شمارش و تا ۵ نمونهٔ repr برای گزارش متنی برداشته می‌شود
    و در هیچ ساختار خروجی HTML/JSON ذخیره نمی‌گردد. ستون چهارم (قیمت) اصلاً ذخیره یا گزارش نمی‌شود.
    """
    normalize = _get_normalize()
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheet_names = wb.sheetnames
    ws = wb[sheet_names[0]]

    first_three_rows_repr = []
    header_row_cols1to3 = None
    max_col_seen = 0

    type_counter = Counter()
    non_code_rows = []
    lead_zero_fixes = []
    lead_zero_unmatched_numeric = []

    extra_cols_nonempty_count = Counter()
    extra_cols_samples = defaultdict(list)
    extra_cols_header_type = {}

    catalog_entries = []
    code_freq = Counter()

    code_alnum_re = re.compile(r"^[0-9a-zA-Z\u0600-\u06FF\-_/\.]{4,16}$")

    for row_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
        row_tuple = tuple(row) if row is not None else ()
        if len(row_tuple) > max_col_seen:
            max_col_seen = len(row_tuple)

        c1 = row_tuple[0] if len(row_tuple) >= 1 else None
        c2 = row_tuple[1] if len(row_tuple) >= 2 else None
        c3 = row_tuple[2] if len(row_tuple) >= 3 else None

        if row_idx <= 3:
            first_three_rows_repr.append((row_idx, repr(c1), repr(c2), repr(c3), f"len={len(row_tuple)}"))

        for col_1based in range(5, len(row_tuple) + 1):
            val_e = row_tuple[col_1based - 1]
            if row_idx == 1:
                extra_cols_header_type[col_1based] = type(val_e).__name__
            else:
                if val_e is not None and str(val_e).strip() != "":
                    extra_cols_nonempty_count[col_1based] += 1
                    if len(extra_cols_samples[col_1based]) < 5:
                        extra_cols_samples[col_1based].append((row_idx, repr(val_e)))

        if row_idx == 1:
            header_row_cols1to3 = (repr(c1), repr(c2), repr(c3))
            continue

        tname = type(c1).__name__
        type_counter[tname] += 1

        if c1 is None or str(c1).strip() == "":
            non_code_rows.append((row_idx, repr(c1), repr(c2)[:60], repr(c3), "empty_code"))
            continue

        raw_c1 = c1
        if isinstance(raw_c1, float):
            if raw_c1.is_integer():
                code_str = str(int(raw_c1))
            else:
                code_str = str(raw_c1).strip()
        elif isinstance(raw_c1, int):
            code_str = str(raw_c1)
        else:
            code_str = str(raw_c1).strip()

        code_norm_digits = normalize(code_str)
        digits_in_code = sum(1 for ch in code_norm_digits if ch.isdigit())
        if digits_in_code < 4 or " " in code_norm_digits or not code_alnum_re.match(code_norm_digits):
            non_code_rows.append((row_idx, repr(c1), repr(c2)[:60], repr(c3), "non_code_pattern"))
            continue

        final_code = code_str.strip()
        if isinstance(raw_c1, (int, float)) or (len(final_code) < 6 and final_code.isdigit()):
            if db_codes is not None:
                if final_code not in db_codes:
                    padded6 = final_code.zfill(6)
                    if padded6 in db_codes:
                        lead_zero_fixes.append((row_idx, repr(raw_c1), padded6))
                        final_code = padded6
                    else:
                        lead_zero_unmatched_numeric.append((row_idx, repr(raw_c1), final_code))
            else:
                if len(final_code) < 6 and final_code.isdigit():
                    lead_zero_unmatched_numeric.append((row_idx, repr(raw_c1), final_code))

        desc_str = "" if c2 is None else str(c2).strip()
        unit_str = "" if c3 is None else str(c3).strip()

        code_freq[final_code] += 1
        catalog_entries.append({
            "group_id": group_id,
            "code": final_code,
            "desc_original": desc_str,
            "unit": unit_str,
            "excel_row": row_idx,
        })

    wb.close()

    duplicates = [(k, cnt) for k, cnt in sorted(code_freq.items()) if cnt > 1]

    report_info = {
        "path": path,
        "group_id": group_id,
        "sheets": sheet_names,
        "max_col_seen": max_col_seen,
        "header_cols1to3": header_row_cols1to3,
        "first_three_rows_repr": first_three_rows_repr,
        "valid_rows_count": len(catalog_entries),
        "type_counter": dict(type_counter),
        "non_code_rows": non_code_rows,
        "lead_zero_fixes": lead_zero_fixes,
        "lead_zero_unmatched_numeric": lead_zero_unmatched_numeric,
        "extra_cols_header_type": extra_cols_header_type,
        "extra_cols_nonempty_count": dict(extra_cols_nonempty_count),
        "extra_cols_samples": {k: v for k, v in extra_cols_samples.items()},
        "duplicates": duplicates,
    }
    return catalog_entries, report_info


def build_fix_token_index(pdf_records):
    """
    ساخت نمایهٔ معکوس از توکن‌های مستقل در متن fix (s_txt) هر صفحهٔ PDF.
    در PDFهای فهرست‌بها ممکن است کد در اکسل دارای پیشوند رشته (مثلاً '12' برای مکانیک یا '13' برای برق)
    باشد ولی در متن PDF به‌صورت ۶ رقمی یا با/بی پیشوند آمده باشد؛ ابتدا توکن‌های دقیق استخراج می‌شوند.
    pdf_records: list of (pno, s_txt, r_txt, n_txt)
    خروجی: dict[norm_token -> sorted list of pno]
    """
    normalize = _get_normalize()
    token_to_pages = defaultdict(list)
    split_re = re.compile(r"[^\w]+", flags=re.UNICODE)
    for pno, s_txt, _r_txt, _n_txt in pdf_records:
        norm_s = normalize(s_txt)
        tokens = set(t for t in split_re.split(norm_s) if t)
        for tok in tokens:
            token_to_pages[tok].append(pno)
    return {k: sorted(set(v)) for k, v in token_to_pages.items()}


def map_pages_and_build_rows(catalog_by_group, pdf_records_by_group, db_code_to_pages):
    """
    ج + د:
    نگاشت صفحه برای هر گروه و ساخت آرایهٔ ۷ فیلدی:
    [group_id, code, desc_original, unit, excel_row, pages_list, n]
    """
    normalize = _get_normalize()
    all_rows_json = []
    mapping_report = {}

    for group_id in ["ابنیه", "مکانیک", "برق"]:
        entries = catalog_by_group.get(group_id, [])
        pdf_recs = pdf_records_by_group.get(group_id, [])
        fix_index = build_fix_token_index(pdf_recs)

        # بررسی نمونهٔ توکن‌های عددی موجود در fix_index برای گزارش دقیق
        digit_tokens_sample = [k for k in sorted(fix_index.keys()) if k.isdigit() and len(k) >= 5][:15]

        text_0_pages = 0
        text_1_page = 0
        text_multi_pages = 0
        text_samples_not_found = []
        text_samples_multi = []
        stripped_prefix_matches = 0

        abn_db_found = 0
        abn_db_not_found = 0
        abn_agree = 0
        abn_disagree = 0
        abn_text_not_found = 0
        abn_disagree_samples = []
        abn_db_missing_samples = []

        for item in entries:
            code = item["code"]
            desc = item["desc_original"]
            unit = item["unit"]
            excel_row = item["excel_row"]
            norm_code = normalize(code)

            text_pages_all = fix_index.get(norm_code, [])
            # اگر کد ۸ رقمی با پیشوند 12 یا 13 در اکسل است و توکن ۸ رقمی در متن نبود، بررسی ۶ رقم پایانی
            if not text_pages_all and len(norm_code) == 8 and norm_code[:2] in ("12", "13"):
                alt_code6 = norm_code[2:]
                alt_pages = fix_index.get(alt_code6, [])
                if alt_pages:
                    text_pages_all = alt_pages
                    stripped_prefix_matches += 1

            if len(text_pages_all) == 0:
                text_0_pages += 1
                if len(text_samples_not_found) < 10:
                    text_samples_not_found.append((code, excel_row))
            elif len(text_pages_all) == 1:
                text_1_page += 1
            else:
                text_multi_pages += 1
                if len(text_samples_multi) < 10:
                    text_samples_multi.append((code, excel_row, text_pages_all[:6]))

            if group_id == "ابنیه":
                db_pages = db_code_to_pages.get(code, [])
                if not db_pages and norm_code != code:
                    db_pages = db_code_to_pages.get(norm_code, [])
                if db_pages:
                    abn_db_found += 1
                else:
                    abn_db_not_found += 1
                    if len(abn_db_missing_samples) < 10:
                        abn_db_missing_samples.append((code, excel_row))

                if not text_pages_all:
                    abn_text_not_found += 1
                else:
                    if db_pages and (set(db_pages) & set(text_pages_all)):
                        abn_agree += 1
                    else:
                        abn_disagree += 1
                        if len(abn_disagree_samples) < 15:
                            abn_disagree_samples.append((code, excel_row, db_pages, text_pages_all[:5]))

                pages_list = list(db_pages[:3])
            else:
                pages_list = list(text_pages_all[:3])

            n_val = normalize(code + " " + desc)
            row_arr = [group_id, code, desc, unit, excel_row, pages_list, n_val]
            assert len(row_arr) == 7, f"Row must have exactly 7 fields, got {len(row_arr)}"
            assert isinstance(row_arr[0], str)
            assert isinstance(row_arr[1], str)
            assert isinstance(row_arr[2], str)
            assert isinstance(row_arr[3], str)
            assert isinstance(row_arr[4], int)
            assert isinstance(row_arr[5], list)
            assert isinstance(row_arr[6], str)
            all_rows_json.append(row_arr)

        mapping_report[group_id] = {
            "total": len(entries),
            "pdf_pages_loaded": len(pdf_recs),
            "digit_tokens_sample": digit_tokens_sample,
            "stripped_prefix_matches": stripped_prefix_matches,
            "text_0_pages": text_0_pages,
            "text_1_page": text_1_page,
            "text_multi_pages": text_multi_pages,
            "text_samples_not_found": text_samples_not_found,
            "text_samples_multi": text_samples_multi,
            "abn_db_found": abn_db_found,
            "abn_db_not_found": abn_db_not_found,
            "abn_agree": abn_agree,
            "abn_disagree": abn_disagree,
            "abn_text_not_found": abn_text_not_found,
            "abn_disagree_samples": abn_disagree_samples,
            "abn_db_missing_samples": abn_db_missing_samples,
        }

    return all_rows_json, mapping_report


# تعریف نویسهٔ کلمه (یکتا در پایتون): حروف فارسی/عربی U+0621-U+063A, U+0641-U+064A, U+067E, U+0686, U+0698, U+06A9, U+06AF, U+06CC، لاتین a-z، ارقام 0-9
WORD_CHAR_CLASS = r"\u0621-\u063A\u0641-\u064A\u067E\u0686\u0698\u06A9\u06AF\u06CCa-z0-9"
WORD_CHAR_RE = re.compile(f"[{WORD_CHAR_CLASS}]")


def is_word_char(ch: str) -> bool:
    return bool(ch) and (WORD_CHAR_RE.match(ch) is not None)


def count_occurrences_py(haystack: str, needle: str, whole_word: bool = False) -> int:
    if not needle:
        return 0
    count = 0
    pos = 0
    tlen = len(needle)
    hlen = len(haystack)
    while True:
        found = haystack.find(needle, pos)
        if found == -1:
            break
        if whole_word:
            before_ch = haystack[found - 1] if found > 0 else ""
            after_ch = haystack[found + tlen] if (found + tlen) < hlen else ""
            if is_word_char(before_ch) or is_word_char(after_ch):
                pos = found + 1
                continue
        count += 1
        pos = found + tlen
    return count


def search_rows_py(rows_data, query_str, no_space=False, selected_groups=None, max_results=100, whole_word=False):
    """
    شبیه‌ساز پایتونی جستجوی ردیف‌ها (دقیقاً مطابق منطق JS در search_rows_part.html)
    """
    normalize = _get_normalize()
    norm_q = normalize(query_str)
    raw_tokens = [t for t in norm_q.split(" ") if t]
    if not raw_tokens:
        return {"total": 0, "by_group": {}, "results": []}

    eff_no_space = bool(no_space)
    eff_whole_word = (not eff_no_space) and bool(whole_word)

    if eff_no_space:
        tokens = [t.replace(" ", "") for t in raw_tokens if t.replace(" ", "")]
    else:
        tokens = raw_tokens

    if not tokens:
        return {"total": 0, "by_group": {}, "results": []}

    allowed_groups = set(selected_groups) if selected_groups is not None else None
    matched = []
    by_group = Counter()

    for idx, r in enumerate(rows_data):
        grp, code, desc, unit, excel_row, pages_list, n_txt = r
        if allowed_groups is not None and grp not in allowed_groups:
            continue

        hay = n_txt.replace(" ", "") if eff_no_space else n_txt
        ok = True
        total_occ = 0
        for tok in tokens:
            c = count_occurrences_py(hay, tok, whole_word=eff_whole_word)
            if c == 0:
                ok = False
                break
            total_occ += c

        if not ok:
            continue

        norm_code = normalize(code)
        if eff_no_space:
            norm_code = norm_code.replace(" ", "")
        exact_code = 1 if norm_code in tokens else 0

        by_group[grp] += 1
        matched.append((
            -exact_code,
            -total_occ,
            idx,
            r,
            exact_code,
            total_occ,
        ))

    matched.sort(key=lambda x: (x[0], x[1], x[2]))
    top_results = matched if max_results is None else matched[:max_results]
    return {
        "total": len(matched),
        "by_group": dict(by_group),
        "results": top_results,
    }


def format_s3_build_report(catalog_reports, mapping_report, db_info):
    """ساخت متن گزارش کامل docs/search_logs/s3_build_report.txt"""
    lines = []
    lines.append("=== S3 Build Report: لایهٔ ردیف‌های فهرست‌بها (xlsx بدون قیمت) ===")
    lines.append(f"DB Info (mode=ro): total_rows={db_info['total_rows']}, unique_codes={db_info['unique_codes']}")
    lines.append(f"DB Columns: {db_info['columns']}")
    lines.append("")

    for grp in ["ابنیه", "مکانیک", "برق"]:
        rep = catalog_reports.get(grp)
        if not rep:
            lines.append(f"--- گروه {grp}: نیازمند بررسی (گزارش موجود نیست) ---")
            continue
        lines.append(f"=== گروه: {grp} | فایل: {rep['path']} ===")
        lines.append(f"Sheets: {rep['sheets']} | max_col_seen: {rep['max_col_seen']}")
        lines.append(f"Header (cols 1..3): {rep['header_cols1to3']}")
        lines.append("First 3 rows (cols 1..3 repr):")
        for r_idx, c1r, c2r, c3r, linfo in rep["first_three_rows_repr"]:
            lines.append(f"  row {r_idx} ({linfo}): col1={c1r} | col2={c2r} | col3={c3r}")
        lines.append(f"Valid catalog rows count: {rep['valid_rows_count']}")
        lines.append(f"Code cell data types (rows 2..N): {rep['type_counter']}")
        lines.append(f"Lead-zero fixes via DB comparison: count={len(rep['lead_zero_fixes'])}, samples={rep['lead_zero_fixes'][:10]}")
        lines.append(f"Short numeric codes (<6 digits): count={len(rep['lead_zero_unmatched_numeric'])}, samples={rep['lead_zero_unmatched_numeric'][:10]}")
        lines.append(f"Non-code / skipped rows count: {len(rep['non_code_rows'])}")
        for nc in rep["non_code_rows"]:
            lines.append(f"  skipped excel_row={nc[0]} ({nc[4]}): col1={nc[1]} | col2={nc[2]} | col3={nc[3]}")
        lines.append(f"Duplicate codes in file: count={len(rep['duplicates'])}, items={rep['duplicates']}")
        lines.append("Extra columns (col 5 onwards, E=5, F=6, G=7) [NOT stored in JSON/HTML]:")
        lines.append(f"  Row 1 cell types in extra cols: {rep['extra_cols_header_type']}")
        for col_idx in sorted(rep["extra_cols_header_type"].keys()):
            col_letter = chr(ord('A') + col_idx - 1) if col_idx <= 26 else f"Col{col_idx}"
            cnt = rep["extra_cols_nonempty_count"].get(col_idx, 0)
            samps = rep["extra_cols_samples"].get(col_idx, [])
            lines.append(f"  Col {col_idx} ({col_letter}): non_empty_rows(2..N)={cnt} | 5 samples (excel_row, repr)={samps}")
        lines.append("")

    lines.append("=== بخش (ج): نگاشت صفحه (Page Mapping) ===")
    for grp in ["ابنیه", "مکانیک", "برق"]:
        mp = mapping_report.get(grp, {})
        lines.append(f"--- {grp} (total={mp.get('total', 0)}, pdf_pages_loaded={mp.get('pdf_pages_loaded', 0)}) ---")
        lines.append(f"  Sample >=5-digit tokens in PDF fix text: {mp.get('digit_tokens_sample')}")
        lines.append(f"  Stripped 2-digit prefix matches (8->6 digits): {mp.get('stripped_prefix_matches', 0)}")
        lines.append(
            f"  Text search on fix pages: not_found(0 pages)={mp.get('text_0_pages')}, "
            f"one_page(1)={mp.get('text_1_page')}, multi_pages(>=2)={mp.get('text_multi_pages')}"
        )
        lines.append(f"  Text not_found samples (code, excel_row): {mp.get('text_samples_not_found')}")
        lines.append(f"  Text multi_page samples (code, excel_row, pages): {mp.get('text_samples_multi')}")
        if grp == "ابنیه":
            lines.append(
                f"  Abnieh DB vs Text comparison: db_found={mp.get('abn_db_found')}, "
                f"db_not_found={mp.get('abn_db_not_found')}, "
                f"agree(DB page in text pages)={mp.get('abn_agree')}, "
                f"disagree(text found but differs from DB)={mp.get('abn_disagree')}, "
                f"text_not_found={mp.get('abn_text_not_found')}"
            )
            lines.append(f"  Abnieh DB missing samples: {mp.get('abn_db_missing_samples')}")
            lines.append("  Abnieh disagree samples (code, excel_row, db_pages, text_pages):")
            for ds in mp.get("abn_disagree_samples", []):
                lines.append(f"    code={ds[0]} (row={ds[1]}): DB={ds[2]} vs TextFix={ds[3]}")
        lines.append("")

    return "\n".join(lines) + "\n"
