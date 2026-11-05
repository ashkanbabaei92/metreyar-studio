#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S3 Diagnostics (docs/search_logs/s3_diag.py)
بخش‌های R1 تا R6 + تولید docs/search_logs/s3_expected_rows.json برای آزمون parity
هر بخش دارای try/except و signal.alarm است.
"""

import json
import os
import random
import re
import signal
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.search_build import normalize
from src.search_rows import search_rows_py


def _alarm_handler(signum, frame):
    raise TimeoutError("Section timed out via signal.alarm")


signal.signal(signal.SIGALRM, _alarm_handler)


def extract_json_payload_from_html(html_path="output/search/metreyar_search.html"):
    with open(html_path, "r", encoding="utf-8") as f:
        html_text = f.read()
    m = re.search(r"const\s+DATA\s*=\s*(\{.*?\});\s*\n", html_text, flags=re.DOTALL)
    if not m:
        raise RuntimeError("Could not locate const DATA = {...}; in HTML")
    raw_json = m.group(1).replace("<\\/", "</")
    return json.loads(raw_json), html_text


def main():
    out_lines = []

    def log(msg=""):
        print(msg)
        out_lines.append(str(msg))

    log("=== S3 Diagnostics Report (docs/search_logs/s3_diag.txt) ===")

    payload = None
    html_text = ""
    try:
        signal.alarm(30)
        payload, html_text = extract_json_payload_from_html("output/search/metreyar_search.html")
        signal.alarm(0)
        log(f"Loaded HTML payload: docs={len(payload.get('docs', []))}, pages={len(payload.get('pages', []))}, rows={len(payload.get('rows', []))}")
    except Exception as e:
        signal.alarm(0)
        log(f"ERROR loading HTML payload: {e}")
        return

    rows_data = payload.get("rows", [])
    pages_data = payload.get("pages", [])
    docs_list = payload.get("docs", [])
    doc_by_id = {d["id"]: d for d in docs_list}

    # ---------------------------------------------------------
    # R1: آزمون بازیابی خودکار روی همهٔ ردیف‌ها
    # ---------------------------------------------------------
    log("\n--- R1: آزمون بازیابی خودکار روی همهٔ ردیف‌ها ---")
    try:
        signal.alarm(60)
        unique_codes_cache = {}
        for r in rows_data:
            c = r[1]
            if c not in unique_codes_cache:
                res_top100 = search_rows_py(rows_data, c, no_space=False, selected_groups=None, max_results=100)
                res_all = search_rows_py(rows_data, c, no_space=False, selected_groups=None, max_results=None)
                top100_excel_grp = set((x[3][0], x[3][4]) for x in res_top100["results"])
                all_excel_grp = set((x[3][0], x[3][4]) for x in res_all["results"])
                unique_codes_cache[c] = (top100_excel_grp, all_excel_grp)

        r1a_ok_top100 = 0
        r1a_fail_top100 = []
        r1a_ok_unbounded = 0
        r1a_fail_unbounded = []
        for r in rows_data:
            grp, code, desc, unit, excel_row, pages_list, n_txt = r
            top100_set, all_set = unique_codes_cache[code]
            key = (grp, excel_row)
            if key in top100_set:
                r1a_ok_top100 += 1
            else:
                r1a_fail_top100.append((grp, code, excel_row))
            if key in all_set:
                r1a_ok_unbounded += 1
            else:
                r1a_fail_unbounded.append((grp, code, excel_row))

        log(f"R1(الف) پرسش = کد ردیف (کل ردیف‌ها={len(rows_data)}):")
        log(f"  در سقف ۱۰۰ نتیجه: موفق={r1a_ok_top100} | ناموفق={len(r1a_fail_top100)} | ۲۰ نمونهٔ ناموفق={r1a_fail_top100[:20]}")
        log(f"  بدون سقف ۱۰۰:     موفق={r1a_ok_unbounded} | ناموفق={len(r1a_fail_unbounded)} | ۲۰ نمونهٔ ناموفق={r1a_fail_unbounded[:20]}")

        rng = random.Random(1404)
        rows_by_grp = defaultdict(list)
        for idx, r in enumerate(rows_data):
            rows_by_grp[r[0]].append((idx, r))

        sampled_300 = []
        for grp_name in ["فهرست‌بها — ابنیه", "فهرست‌بها — مکانیک", "فهرست‌بها — برق"]:
            pool = rows_by_grp[grp_name]
            chosen = rng.sample(pool, min(100, len(pool)))
            sampled_300.extend(chosen)

        r1b_ok = 0
        r1b_fail = []
        r1b_ranks = []
        for orig_idx, r in sampled_300:
            grp, code, desc, unit, excel_row, pages_list, n_txt = r
            norm_desc = normalize(desc)
            words = [w for w in norm_desc.split(" ") if w]
            q_two = " ".join(words[:2])
            res_unbounded = search_rows_py(rows_data, q_two, no_space=False, selected_groups=None, max_results=None)
            found_rank = None
            for rank_1based, item_tuple in enumerate(res_unbounded["results"], start=1):
                if item_tuple[2] == orig_idx:
                    found_rank = rank_1based
                    break
            if found_rank is not None:
                r1b_ok += 1
                r1b_ranks.append(found_rank)
            else:
                r1b_fail.append((grp, code, excel_row, q_two))

        rank_top1 = sum(1 for rk in r1b_ranks if rk == 1)
        rank_le10 = sum(1 for rk in r1b_ranks if rk <= 10)
        rank_le100 = sum(1 for rk in r1b_ranks if rk <= 100)
        rank_gt100 = sum(1 for rk in r1b_ranks if rk > 100)
        max_rank = max(r1b_ranks) if r1b_ranks else 0
        avg_rank = (sum(r1b_ranks) / len(r1b_ranks)) if r1b_ranks else 0.0

        log(f"R1(ب) ۳۰۰ ردیف تصادفی با seed=1404 (۱۰۰ از هر فایل)، پرسش = دو کلمهٔ اول شرح پس از normalize (بدون سقف ۱۰۰):")
        log(f"  موفق={r1b_ok} / {len(sampled_300)} | ناموفق={len(r1b_fail)} | ۲۰ نمونهٔ ناموفق={r1b_fail[:20]}")
        log(f"  توزیع رتبه (rank): رتبه ۱={rank_top1} | رتبه <=10={rank_le10} | رتبه <=100={rank_le100} | رتبه >100={rank_gt100} | میانگین رتبه={avg_rank:.2f} | بیشترین رتبه={max_rank}")

        log("R1(پ) کد تکراری در یک فایل:")
        for grp_name in ["فهرست‌بها — ابنیه", "فهرست‌بها — مکانیک", "فهرست‌بها — برق"]:
            c_map = defaultdict(list)
            for _idx, r in rows_by_grp[grp_name]:
                c_map[r[1]].append(r[4])
            dups = [(c, erows) for c, erows in sorted(c_map.items()) if len(erows) > 1]
            log(f"  {grp_name}: duplicate_codes_count={len(dups)} | samples={dups[:20]}")
        signal.alarm(0)
    except Exception as e:
        signal.alarm(0)
        log(f"ERROR in R1: {e}")

    # ---------------------------------------------------------
    # R2: آزمون مستقل روی ۸ پرسش + ساخت s3_expected_rows.json
    # ---------------------------------------------------------
    log("\n--- R2: آزمون مستقل روی ۸ پرسش (مقایسهٔ تابع ابزار با str.__contains__ خام بدون normalize) ---")
    try:
        signal.alarm(30)
        r2_queries = ["بتن", "فولاد", "شیشه", "آرماتور", "لوله", "کابل", "کلید", "قالب بندی"]
        for q in r2_queries:
            tool_res = search_rows_py(rows_data, q, no_space=False, selected_groups=None, max_results=100)
            tool_total = tool_res["total"]
            tool_by_grp = tool_res["by_group"]

            raw_cnt = 0
            raw_by_grp = Counter()
            for r in rows_data:
                raw_cell_text = f"{r[1]} {r[2]}"
                if str.__contains__(raw_cell_text, q):
                    raw_cnt += 1
                    raw_by_grp[r[0]] += 1

            diff = tool_total - raw_cnt
            log(f"  پرسش '{q}': تابع ابزار (عادی)={tool_total} {tool_by_grp} | شمارش خام str.__contains__={raw_cnt} {dict(raw_by_grp)} | اختلاف (ابزار - خام)={diff:+d}")
            if q == "فولاد":
                raw_fuald_cnt = 0
                raw_fuald_by_grp = Counter()
                for r in rows_data:
                    raw_cell_text = f"{r[1]} {r[2]}"
                    if str.__contains__(raw_cell_text, "فوالد"):
                        raw_fuald_cnt += 1
                        raw_fuald_by_grp[r[0]] += 1
                tool_fuald = search_rows_py(rows_data, "فوالد", no_space=False, selected_groups=None, max_results=100)
                log(f"    [کنار فولاد: 'فوالد']: تابع ابزار={tool_fuald['total']} {tool_fuald['by_group']} | شمارش خام str.__contains__('فوالد')={raw_fuald_cnt} {dict(raw_fuald_by_grp)}")

        abn_rows = [r for r in rows_data if r[0] == "فهرست‌بها — ابنیه" and len(r[1]) == 6 and r[1].isdigit()]
        sample_abn_code = abn_rows[0][1] if abn_rows else "010101"
        parity_queries = [
            "بتن",
            "فولاد",
            "فوالد",
            "شیشه",
            "آرماتور",
            "لوله",
            "کابل",
            "کلید",
            "قالب بندی",
            sample_abn_code,
        ]
        all_groups = [
            "فهرست‌بها — ابنیه",
            "فهرست‌بها — مکانیک",
            "فهرست‌بها — برق",
            "تعدیل",
            "شرایط عمومی پیمان",
        ]
        expected_rows_payload = {
            "sample_abnieh_6digit_code": sample_abn_code,
            "groups": all_groups,
            "queries": {},
        }
        for pq in parity_queries:
            res_norm = search_rows_py(rows_data, pq, no_space=False, selected_groups=all_groups, max_results=100)
            res_nosp = search_rows_py(rows_data, pq, no_space=True, selected_groups=all_groups, max_results=100)
            expected_rows_payload["queries"][pq] = {
                "normal": {
                    "total": res_norm["total"],
                    "by_group": res_norm["by_group"],
                    "top_codes": [x[3][1] for x in res_norm["results"][:10]],
                },
                "no_space": {
                    "total": res_nosp["total"],
                    "by_group": res_nosp["by_group"],
                    "top_codes": [x[3][1] for x in res_nosp["results"][:10]],
                },
            }
        exp_path = "docs/search_logs/s3_expected_rows.json"
        with open(exp_path, "w", encoding="utf-8") as ef:
            json.dump(expected_rows_payload, ef, ensure_ascii=False, indent=2)
        log(f"Saved {exp_path} for 10 parity queries (sample_abnieh_6digit_code={sample_abn_code})")
        signal.alarm(0)
    except Exception as e:
        signal.alarm(0)
        log(f"ERROR in R2: {e}")

    # ---------------------------------------------------------
    # R3: «راه / راه‌آهن / باند»
    # ---------------------------------------------------------
    log("\n--- R3: بررسی «تعدیل»، «راه آهن»، «باند» و «راه» در صفحات و در ردیف‌های سه xlsx ---")
    try:
        signal.alarm(30)

        def search_pages_sim(q_str, allowed_groups_set):
            norm_q = normalize(q_str)
            tokens = [t for t in norm_q.split(" ") if t]
            by_doc = Counter()
            matched_pages = []
            for idx, p in enumerate(pages_data):
                doc_id = p[0]
                pno = p[1]
                n_txt = p[2]
                s_txt = p[3]
                dmeta = doc_by_id.get(doc_id, {})
                grp = dmeta.get("group", "")
                if allowed_groups_set is not None and grp not in allowed_groups_set:
                    continue
                ok = True
                occ = 0
                for tok in tokens:
                    if tok not in n_txt:
                        ok = False
                        break
                    pos = 0
                    while True:
                        fnd = n_txt.find(tok, pos)
                        if fnd == -1:
                            break
                        occ += 1
                        pos = fnd + len(tok)
                if not ok:
                    continue
                by_doc[dmeta.get("label", str(doc_id))] += 1
                matched_pages.append((-occ, idx, dmeta.get("label", str(doc_id)), pno, s_txt, tokens))
            matched_pages.sort(key=lambda x: (x[0], x[1]))
            return len(matched_pages), dict(by_doc), matched_pages[:5]

        r3_specs = [
            ("تعدیل", {"تعدیل"}, "فقط گروه تعدیل"),
            ("راه آهن", None, "همهٔ گروه‌ها"),
            ("باند", None, "همهٔ گروه‌ها"),
            ("راه", None, "همهٔ گروه‌ها"),
        ]
        for q_str, grp_filter, desc_filter in r3_specs:
            tot_p, by_doc_p, top5_p = search_pages_sim(q_str, grp_filter)
            log(f"پرسش صفحات '{q_str}' ({desc_filter}): total={tot_p} | به تفکیک سند={by_doc_p}")
            for rank_i, (_neg_occ, _idx, dlabel, pno, s_txt, toks) in enumerate(top5_p, start=1):
                matching_lines = []
                for ln in (s_txt or "").splitlines():
                    nln = normalize(ln)
                    if any(t in nln for t in toks):
                        matching_lines.append(repr(ln))
                        if len(matching_lines) == 3:
                            break
                log(f"  [{rank_i}] doc={dlabel} | page/unit={repr(pno)} | 3 lines(repr)={matching_lines}")

        for phrase in ["راه آهن", "باند"]:
            res_r3_row = search_rows_py(rows_data, phrase, no_space=False, selected_groups=None, max_results=None)
            res_r3_row_nosp = search_rows_py(rows_data, phrase, no_space=True, selected_groups=None, max_results=None)
            samples_r3 = [(x[3][0], x[3][1], x[3][4], repr(x[3][2][:80])) for x in res_r3_row["results"][:5]]
            log(f"جستجو در ردیف‌های سه xlsx برای عبارت '{phrase}': عادی total={res_r3_row['total']} {res_r3_row['by_group']} | بی‌فاصله total={res_r3_row_nosp['total']} {res_r3_row_nosp['by_group']} | نمونه={samples_r3}")
        signal.alarm(0)
    except Exception as e:
        signal.alarm(0)
        log(f"ERROR in R3: {e}")

    # ---------------------------------------------------------
    # R4: 'بتن' بی‌فاصله از s21_expected_counts.json
    # ---------------------------------------------------------
    log("\n--- R4: 'بتن' بی‌فاصله از docs/search_logs/s21_expected_counts.json ---")
    try:
        signal.alarm(10)
        with open("docs/search_logs/s21_expected_counts.json", "r", encoding="utf-8") as f21:
            s21_data = json.load(f21)
        q_list = s21_data.get("queries", [])
        beton_entry = {}
        if isinstance(q_list, list):
            for item in q_list:
                if isinstance(item, dict) and item.get("query") == "بتن":
                    beton_entry = item
                    break
        elif isinstance(q_list, dict):
            beton_entry = q_list.get("بتن", {})
        log("مقدار خام 'بتن' در s21_expected_counts.json:")
        log(f"  normal  : total={beton_entry.get('normal', {}).get('total')}, s2_total={beton_entry.get('normal', {}).get('s2_total')}, by_group={beton_entry.get('normal', {}).get('by_group')}, s2_by_group={beton_entry.get('normal', {}).get('s2_by_group')}")
        log(f"  no_space: total={beton_entry.get('no_space', {}).get('total')}, s2_total={beton_entry.get('no_space', {}).get('s2_total')}, by_group={beton_entry.get('no_space', {}).get('by_group')}, s2_by_group={beton_entry.get('no_space', {}).get('s2_by_group')}")
        signal.alarm(0)
    except Exception as e:
        signal.alarm(0)
        log(f"ERROR in R4: {e}")

    # ---------------------------------------------------------
    # R5: ناسازگاری S1 (صفحهٔ ۴۷ سند 1147659-1404.pdf در s1_text_quality.txt)
    # ---------------------------------------------------------
    log("\n--- R5: بررسی دقیق عدد 865/853 یا 886/1302 در docs/search_logs/s1_text_quality.txt ---")
    try:
        signal.alarm(10)
        with open("docs/search_logs/s1_text_quality.txt", "r", encoding="utf-8") as f_s1:
            s1_lines = f_s1.readlines()
        matches_r5 = []
        for lno, ltxt in enumerate(s1_lines, start=1):
            if any(k in ltxt for k in ("865", "853", "886", "1302", "1147659")):
                matches_r5.append((lno, ltxt.rstrip("\n")))
        log("خطوط حاوی 865 / 853 / 886 / 1302 / 1147659 در docs/search_logs/s1_text_quality.txt:")
        for lno, ltxt in matches_r5:
            log(f"  line {lno}: {ltxt}")
        log("بافت کامل خطوط 38 تا 45 در docs/search_logs/s1_text_quality.txt:")
        for lno in range(38, min(46, len(s1_lines) + 1)):
            log(f"  {lno}: {s1_lines[lno - 1].rstrip()}")
        signal.alarm(0)
    except Exception as e:
        signal.alarm(0)
        log(f"ERROR in R5: {e}")

    # ---------------------------------------------------------
    # R6: تأیید نبود قیمت در JSON داخل HTML
    # ---------------------------------------------------------
    log("\n--- R6: تأیید نبود قیمت در JSON داخل HTML ---")
    try:
        signal.alarm(20)
        field_lengths = Counter(len(r) for r in rows_data)
        all_len_7 = (set(field_lengths.keys()) == {7})

        forbidden_keys_found = []

        def walk_keys(obj, path="DATA"):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    ks = str(k).lower()
                    if "price" in ks or "قیمت" in ks or "baha" in ks or "mablagh" in ks:
                        forbidden_keys_found.append(f"{path}.{k}")
                    walk_keys(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, item in enumerate(obj[:50]):
                    if isinstance(item, dict):
                        walk_keys(item, f"{path}[{i}]")

        walk_keys(payload)
        log(f"Total rows in DATA.rows: {len(rows_data)}")
        log(f"Row field count distribution: {dict(field_lengths)} | all_rows_have_exactly_7_fields={all_len_7}")
        log(f"Forbidden price/قیمت keys in JSON payload: {forbidden_keys_found} (count={len(forbidden_keys_found)})")
        signal.alarm(0)
    except Exception as e:
        signal.alarm(0)
        log(f"ERROR in R6: {e}")

    out_path = "docs/search_logs/s3_diag.txt"
    with open(out_path, "w", encoding="utf-8") as outf:
        outf.write("\n".join(out_lines) + "\n")
    print(f"\nSaved diagnostics output to {out_path}")


if __name__ == "__main__":
    main()
