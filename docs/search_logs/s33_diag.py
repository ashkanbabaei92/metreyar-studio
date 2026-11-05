#!/usr/bin/env python3
import sys
sys.dont_write_bytecode = True

import os
import re
import json
import signal
import subprocess
import traceback
import importlib.util
from pathlib import Path
from collections import Counter

ROOT = "/home/user/webapp"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.search_rows import WORD_CHAR_CLASS, _get_normalize, count_occurrences_py, search_rows_py

QUERIES = [
    "بتن", "فولاد", "آرماتور", "کابل", "کلید", "تخریب",
    "راه", "راه آهن", "باند", "010101", "12010101"
]
GROUPS = ["ALL", "ابنیه", "مکانیک", "برق", "پیمان", "تعدیل"]
MODES = [
    ("normal", "عادی", False, False),
    ("word_start", "شروع", False, True),
    ("whole_word", "کامل", True, False),
]


def build_word_start_regex(token: str) -> re.Pattern:
    return re.compile(rf"(?<![{WORD_CHAR_CLASS}]){re.escape(token)}")


def build_whole_word_regex(token: str) -> re.Pattern:
    return re.compile(rf"(?<![{WORD_CHAR_CLASS}]){re.escape(token)}(?![{WORD_CHAR_CLASS}])")


def independent_re_word_start_rows(rows_data, query_str, selected_groups=None):
    normalize = _get_normalize()
    norm_q = normalize(query_str)
    tokens = [t for t in norm_q.split(" ") if t]
    if not tokens:
        return {"total": 0, "by_group": {}, "codes_set": set(), "occ_map": {}, "top_codes": []}

    patterns = [build_word_start_regex(tok) for tok in tokens]
    allowed = set(selected_groups) if selected_groups is not None else None
    by_group = Counter()
    matched = []
    codes_set = set()
    occ_map = {}

    for idx, r in enumerate(rows_data):
        grp, code, desc, unit, excel_row, pages_list, n_txt = r
        if allowed is not None and grp not in allowed:
            continue
        ok = True
        total_occ = 0
        for pat in patterns:
            c = len(pat.findall(n_txt))
            if c == 0:
                ok = False
                break
            total_occ += c
        if not ok:
            continue
        norm_code = normalize(code)
        exact_code = 1 if norm_code in tokens else 0
        by_group[grp] += 1
        codes_set.add(code)
        occ_map[idx] = total_occ
        matched.append((-exact_code, -total_occ, idx, code))

    matched.sort(key=lambda x: (x[0], x[1], x[2]))
    return {
        "total": len(matched),
        "by_group": dict(by_group),
        "codes_set": codes_set,
        "occ_map": occ_map,
        "top_codes": [m[3] for m in matched[:10]],
    }


def search_pages_py(data_obj, query_str, selected_groups=None, whole_word=False, word_start=False):
    normalize = _get_normalize()
    norm_q = normalize(query_str)
    tokens = [t for t in norm_q.split(" ") if t]
    if not tokens:
        return {"total": 0, "by_doc": {}, "by_group": {}, "keys_set": set(), "occ_map": {}, "top_keys": []}

    doc_map = {d["id"]: d for d in data_obj["docs"]}
    allowed = set(selected_groups) if selected_groups is not None else None
    by_doc = Counter()
    by_group = Counter()
    matched = []
    keys_set = set()
    occ_map = {}

    for idx, rec in enumerate(data_obj["pages"]):
        doc_id = rec[0]
        page_num = rec[1]
        n_txt = rec[2]
        dinfo = doc_map.get(doc_id)
        if not dinfo:
            continue
        grp = dinfo["group"]
        if allowed is not None and grp not in allowed:
            continue
        ok = True
        score = 0
        for tok in tokens:
            c = count_occurrences_py(n_txt, tok, whole_word=whole_word, word_start=word_start)
            if c == 0:
                ok = False
                break
            score += c
        if not ok:
            continue
        by_doc[str(doc_id)] += 1
        by_group[grp] += 1
        k = f"{doc_id}:{page_num}"
        keys_set.add(k)
        occ_map[k] = score
        matched.append((-score, doc_id, page_num, k))

    matched.sort(key=lambda x: (x[0], x[1], x[2]))
    return {
        "total": len(matched),
        "by_doc": dict(by_doc),
        "by_group": dict(by_group),
        "keys_set": keys_set,
        "occ_map": occ_map,
        "top_keys": [m[3] for m in matched[:10]],
    }


def independent_re_pages(data_obj, query_str, selected_groups=None, mode="word_start"):
    normalize = _get_normalize()
    norm_q = normalize(query_str)
    tokens = [t for t in norm_q.split(" ") if t]
    if not tokens:
        return {"total": 0, "by_doc": {}, "by_group": {}, "keys_set": set(), "occ_map": {}, "top_keys": []}

    if mode == "word_start":
        patterns = [build_word_start_regex(tok) for tok in tokens]
    elif mode == "whole_word":
        patterns = [build_whole_word_regex(tok) for tok in tokens]
    else:
        patterns = [re.compile(re.escape(tok)) for tok in tokens]

    doc_map = {d["id"]: d for d in data_obj["docs"]}
    allowed = set(selected_groups) if selected_groups is not None else None
    by_doc = Counter()
    by_group = Counter()
    matched = []
    keys_set = set()
    occ_map = {}

    for idx, rec in enumerate(data_obj["pages"]):
        doc_id = rec[0]
        page_num = rec[1]
        n_txt = rec[2]
        dinfo = doc_map.get(doc_id)
        if not dinfo:
            continue
        grp = dinfo["group"]
        if allowed is not None and grp not in allowed:
            continue
        ok = True
        score = 0
        for pat in patterns:
            c = len(pat.findall(n_txt))
            if c == 0:
                ok = False
                break
            score += c
        if not ok:
            continue
        by_doc[str(doc_id)] += 1
        by_group[grp] += 1
        k = f"{doc_id}:{page_num}"
        keys_set.add(k)
        occ_map[k] = score
        matched.append((-score, doc_id, page_num, k))

    matched.sort(key=lambda x: (x[0], x[1], x[2]))
    return {
        "total": len(matched),
        "by_doc": dict(by_doc),
        "by_group": dict(by_group),
        "keys_set": keys_set,
        "occ_map": occ_map,
        "top_keys": [m[3] for m in matched[:10]],
    }


def main():
    signal.alarm(90)
    diag_py_path = Path(ROOT) / "docs/search_logs/s32_diag.py"
    html_path = Path(ROOT) / "output/search/metreyar_search.html"

    spec = importlib.util.spec_from_file_location("s32_diag_mod", str(diag_py_path))
    s32_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(s32_mod)

    data = s32_mod.load_data_from_html(html_path)
    rows_data = data["rows"]

    proc = subprocess.run(
        ["git", "show", "HEAD:docs/search_logs/s32_expected.json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    head_s32_exp = json.loads(proc.stdout)

    diag_lines = []
    summary_lines = []
    expected_ws = {
        "checkbox_groups": GROUPS[1:],
        "queries": QUERIES,
        "cases": {},
    }

    diag_lines.append("پرسش         | گروه     | حالت        | صفحات      | ردیف‌ها")
    diag_lines.append("-" * 66)

    subset_failures = []
    regex_failures = []
    invariance_failures = []

    for q in QUERIES:
        expected_ws["cases"][q] = {}
        all_counts_for_summary = {}

        for g in GROUPS:
            sel_g = None if g == "ALL" else [g]
            mode_results = {}

            for m_key, m_label, ww_flag, ws_flag in MODES:
                r_res = search_rows_py(
                    rows_data, q, no_space=False, selected_groups=sel_g,
                    max_results=100000, whole_word=ww_flag, word_start=ws_flag
                )
                p_res = search_pages_py(
                    data, q, selected_groups=sel_g, whole_word=ww_flag, word_start=ws_flag
                )
                r_codes_set = {item[3][1] for item in r_res["results"]}
                r_occ_map = {item[2]: item[5] for item in r_res["results"]}
                r_top_codes = [item[3][1] for item in r_res["results"][:10]]

                mode_results[m_key] = {
                    "rows": r_res,
                    "rows_codes_set": r_codes_set,
                    "rows_occ_map": r_occ_map,
                    "rows_top_codes": r_top_codes,
                    "pages": p_res,
                }

                diag_lines.append(
                    f"{q:<12} | {g:<8} | {m_label:<11} | {p_res['total']:<10} | {r_res['total']}"
                )

                if g == "ALL":
                    all_counts_for_summary[m_key] = (p_res["total"], r_res["total"])

            ws_r = mode_results["word_start"]
            expected_ws["cases"][q][g] = {
                "word_start": {
                    "rows": {
                        "total": ws_r["rows"]["total"],
                        "by_group": ws_r["rows"]["by_group"],
                        "top_codes": ws_r["rows_top_codes"],
                    },
                    "pages": {
                        "total": ws_r["pages"]["total"],
                        "by_doc": ws_r["pages"]["by_doc"],
                        "by_group": ws_r["pages"]["by_group"],
                        "top_keys": ws_r["pages"]["top_keys"],
                    },
                }
            }

            # (1) Subset chain: whole_word <= word_start <= normal
            ww_rc = mode_results["whole_word"]["rows_codes_set"]
            ws_rc = mode_results["word_start"]["rows_codes_set"]
            nm_rc = mode_results["normal"]["rows_codes_set"]
            if not (ww_rc <= ws_rc <= nm_rc):
                subset_failures.append(f"Row subset fail q={q} g={g}: ww-ws={ww_rc-ws_rc}, ws-nm={ws_rc-nm_rc}")

            ww_pk = mode_results["whole_word"]["pages"]["keys_set"]
            ws_pk = mode_results["word_start"]["pages"]["keys_set"]
            nm_pk = mode_results["normal"]["pages"]["keys_set"]
            if not (ww_pk <= ws_pk <= nm_pk):
                subset_failures.append(f"Page subset fail q={q} g={g}: ww-ws={ww_pk-ws_pk}, ws-nm={ws_pk-nm_pk}")

            # (2) Independent regex reference for word_start
            ind_r = independent_re_word_start_rows(rows_data, q, sel_g)
            if (
                ind_r["total"] != ws_r["rows"]["total"]
                or ind_r["by_group"] != ws_r["rows"]["by_group"]
                or ind_r["codes_set"] != ws_r["rows_codes_set"]
                or ind_r["occ_map"] != ws_r["rows_occ_map"]
                or ind_r["top_codes"] != ws_r["rows_top_codes"]
            ):
                regex_failures.append(
                    f"Rows regex mismatch q={q} g={g}: sim=({ws_r['rows']['total']},{ws_r['rows']['by_group']},{ws_r['rows_top_codes']}) "
                    f"vs ind=({ind_r['total']},{ind_r['by_group']},{ind_r['top_codes']})"
                )

            ind_p = independent_re_pages(data, q, sel_g, mode="word_start")
            if (
                ind_p["total"] != ws_r["pages"]["total"]
                or ind_p["by_group"] != ws_r["pages"]["by_group"]
                or ind_p["keys_set"] != ws_r["pages"]["keys_set"]
                or ind_p["occ_map"] != ws_r["pages"]["occ_map"]
                or ind_p["top_keys"] != ws_r["pages"]["top_keys"]
            ):
                regex_failures.append(
                    f"Pages regex mismatch q={q} g={g}: sim=({ws_r['pages']['total']},{ws_r['pages']['by_group']},{ws_r['pages']['top_keys']}) "
                    f"vs ind=({ind_p['total']},{ind_p['by_group']},{ind_p['top_keys']})"
                )

            # (3) Invariance against HEAD:docs/search_logs/s32_expected.json for normal and whole_word
            head_case = head_s32_exp.get("cases", {}).get(q, {}).get(g, {})
            for inv_mode in ("normal", "whole_word"):
                exp_m = head_case.get(inv_mode)
                if not exp_m:
                    invariance_failures.append(f"Missing in HEAD s32_expected: q={q} g={g} mode={inv_mode}")
                    continue
                cur_m = mode_results[inv_mode]
                if (
                    cur_m["rows"]["total"] != exp_m["rows"]["total"]
                    or cur_m["rows"]["by_group"] != exp_m["rows"]["by_group"]
                    or cur_m["rows_top_codes"] != exp_m["rows"]["top_codes"]
                ):
                    invariance_failures.append(
                        f"Rows invariance fail q={q} g={g} mode={inv_mode}: cur=({cur_m['rows']['total']},{cur_m['rows']['by_group']},{cur_m['rows_top_codes']}) "
                        f"vs exp=({exp_m['rows']['total']},{exp_m['rows']['by_group']},{exp_m['rows']['top_codes']})"
                    )
                if (
                    cur_m["pages"]["total"] != exp_m["pages"]["total"]
                    or cur_m["pages"]["by_doc"] != exp_m["pages"]["by_doc"]
                    or cur_m["pages"]["by_group"] != exp_m["pages"]["by_group"]
                    or cur_m["pages"]["top_keys"] != exp_m["pages"]["top_keys"]
                ):
                    invariance_failures.append(
                        f"Pages invariance fail q={q} g={g} mode={inv_mode}: cur=({cur_m['pages']['total']},{cur_m['pages']['by_group']},{cur_m['pages']['top_keys']}) "
                        f"vs exp=({exp_m['pages']['total']},{exp_m['pages']['by_group']},{exp_m['pages']['top_keys']})"
                    )

        p_nm, r_nm = all_counts_for_summary["normal"]
        p_ws, r_ws = all_counts_for_summary["word_start"]
        p_ww, r_ww = all_counts_for_summary["whole_word"]
        summary_lines.append(
            f"{q}: صفحه {p_nm}/{p_ws}/{p_ww} | ردیف {r_nm}/{r_ws}/{r_ww}"
        )

    diag_lines.append("-" * 66)
    diag_lines.append(f"CHECK 1 (Subset chain کامل ⊆ شروع ⊆ عادی): {'OK' if not subset_failures else f'FAIL ({len(subset_failures)})'}")
    for f in subset_failures[:10]:
        diag_lines.append("  " + f)

    diag_lines.append(f"CHECK 2 (Independent regex lookbehind for word_start): {'OK' if not regex_failures else f'FAIL ({len(regex_failures)})'}")
    for f in regex_failures[:10]:
        diag_lines.append("  " + f)

    diag_lines.append(f"CHECK 3 (Invariance normal & whole_word vs HEAD s32_expected.json): {'OK' if not invariance_failures else f'FAIL ({len(invariance_failures)})'}")
    for f in invariance_failures[:10]:
        diag_lines.append("  " + f)

    all_ok = not (subset_failures or regex_failures or invariance_failures)
    diag_lines.append(f"DIAG: {'OK' if all_ok else 'FAIL'}")

    (Path(ROOT) / "docs/search_logs/s33_diag.txt").write_text("\n".join(diag_lines) + "\n", encoding="utf-8")
    (Path(ROOT) / "docs/search_logs/s33_summary.txt").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    (Path(ROOT) / "docs/search_logs/s33_expected.json").write_text(
        json.dumps(expected_ws, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if not all_ok:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
