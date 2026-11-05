#!/usr/bin/env python3
import sys
sys.dont_write_bytecode = True

import os
import re
import json
import signal
import traceback
import importlib.util
from pathlib import Path

ROOT = "/home/user/webapp"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.search_rows import WORD_CHAR_CLASS, _get_normalize, search_rows_py


def describe_schema(obj, depth=0, max_depth=4, lines=None, prefix=""):
    if lines is None:
        lines = []
    if len(lines) >= 28:
        return lines
    tname = type(obj).__name__
    if depth >= max_depth:
        lines.append(f"{prefix}: {tname}")
        return lines
    if isinstance(obj, dict):
        keys = list(obj.keys())
        sample_keys = keys[:6]
        lines.append(f"{prefix or '<root>'}: dict(len={len(keys)}, keys={sample_keys})")
        for k in keys[:3]:
            if len(lines) >= 28:
                break
            describe_schema(obj[k], depth + 1, max_depth, lines, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, list):
        lines.append(f"{prefix or '<root>'}: list(len={len(obj)})")
        if obj and len(lines) < 28:
            describe_schema(obj[0], depth + 1, max_depth, lines, f"{prefix}[0]")
    else:
        lines.append(f"{prefix}: {tname} = {repr(obj)[:60]}")
    return lines


def find_query_entries(obj, target_q, path="", out=None):
    if out is None:
        out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            new_p = f"{path}.{k}" if path else str(k)
            if k == target_q or (isinstance(v, str) and v == target_q and k in ("query", "q")):
                out.append((new_p, obj if k in ("query", "q") else v))
            else:
                find_query_entries(v, target_q, new_p, out)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            find_query_entries(item, target_q, f"{path}[{i}]", out)
    return out


def main():
    signal.alarm(60)
    out_lines = []

    def log(s=""):
        out_lines.append(str(s))

    expected_path = Path(ROOT) / "docs/search_logs/s32_expected.json"
    diag_txt_path = Path(ROOT) / "docs/search_logs/s32_diag.txt"
    diag_py_path = Path(ROOT) / "docs/search_logs/s32_diag.py"
    html_path = Path(ROOT) / "output/search/metreyar_search.html"

    with open(expected_path, "r", encoding="utf-8") as f:
        s32_exp = json.load(f)

    # A0
    log("=== A0: s32_expected.json structure (up to depth 4) ===")
    schema_lines = describe_schema(s32_exp, 0, 4)
    for ln in schema_lines[:30]:
        log(ln)

    # Load s32_diag.py via importlib
    spec = importlib.util.spec_from_file_location("s32_diag_mod", str(diag_py_path))
    s32_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(s32_mod)

    data = s32_mod.load_data_from_html(html_path)
    rows_data = data["rows"]
    groups = ["ابنیه", "مکانیک", "برق", "راه و باند", "راهداری"]

    # A1
    log("\n=== A1: آرماتور counts (search_rows_py, s32_diag.txt, s32_expected.json) ===")
    q_arm = "آرماتور"
    res_arm_sub_all = search_rows_py(rows_data, q_arm, no_space=False, selected_groups=None, max_results=100000, whole_word=False)
    res_arm_ww_all = search_rows_py(rows_data, q_arm, no_space=False, selected_groups=None, max_results=100000, whole_word=True)
    log(f"ALL normal: total={res_arm_sub_all['total']} len(results)==total: {len(res_arm_sub_all['results']) == res_arm_sub_all['total']} by_group={res_arm_sub_all['by_group']}")
    log(f"ALL whole_word: total={res_arm_ww_all['total']} len(results)==total: {len(res_arm_ww_all['results']) == res_arm_ww_all['total']} by_group={res_arm_ww_all['by_group']}")
    for g in groups:
        r_sub = search_rows_py(rows_data, q_arm, no_space=False, selected_groups=[g], max_results=100000, whole_word=False)
        r_ww = search_rows_py(rows_data, q_arm, no_space=False, selected_groups=[g], max_results=100000, whole_word=True)
        log(f"Group [{g}]: normal_total={r_sub['total']} (len_ok={len(r_sub['results'])==r_sub['total']}), ww_total={r_ww['total']} (len_ok={len(r_ww['results'])==r_ww['total']})")

    log("--- Lines in s32_diag.txt starting with 'آرماتور': ---")
    diag_lines = diag_txt_path.read_text(encoding="utf-8").splitlines()
    for ln in diag_lines:
        if ln.lstrip().startswith("آرماتور"):
            log(ln)

    log("--- Recursive search in s32_expected.json for 'آرماتور': ---")
    arm_entries = find_query_entries(s32_exp, q_arm)
    for p, val in arm_entries:
        log(f"path={p} -> {json.dumps(val, ensure_ascii=False)}")

    ind_arm_ww_all = s32_mod.independent_re_whole_word_rows(rows_data, q_arm, None)
    log(f"Independent regex ww ALL for آرماتور: total={ind_arm_ww_all['total']} by_group={ind_arm_ww_all['by_group']}")

    # A2
    log("\n=== A2: راه with whole_word=True in ALL and ابنیه ===")
    q_rah = "راه"
    res_rah_ww_all = search_rows_py(rows_data, q_rah, no_space=False, selected_groups=None, max_results=100000, whole_word=True)
    res_rah_ww_abn = search_rows_py(rows_data, q_rah, no_space=False, selected_groups=["ابنیه"], max_results=100000, whole_word=True)
    log(f"search_rows_py راه ww ALL: total={res_rah_ww_all['total']} by_group={res_rah_ww_all['by_group']}")
    log(f"search_rows_py راه ww ابنیه: total={res_rah_ww_abn['total']} by_group={res_rah_ww_abn['by_group']}")

    log("--- Lines in s32_diag.txt with 'راه' and (FAIL or top_codes or table rows): ---")
    rah_diag_matches = [ln for ln in diag_lines if ("راه" in ln and ("FAIL" in ln or "top_codes" in ln or ln.lstrip().startswith("راه")))]
    for ln in rah_diag_matches[:20]:
        log(ln)

    ind_rah_ww_all = s32_mod.independent_re_whole_word_rows(rows_data, q_rah, None)
    ind_rah_ww_abn = s32_mod.independent_re_whole_word_rows(rows_data, q_rah, ["ابنیه"])
    sim_all_top10 = [item[3][1] for item in res_rah_ww_all["results"][:10]]
    sim_abn_top10 = [item[3][1] for item in res_rah_ww_abn["results"][:10]]
    log(f"ALL top10 sim_codes: {sim_all_top10}")
    log(f"ALL top10 ind_codes: {ind_rah_ww_all.get('top_codes', [])[:10]}")
    log(f"ابنیه top10 sim_codes: {sim_abn_top10}")
    log(f"ابنیه top10 ind_codes: {ind_rah_ww_abn.get('top_codes', [])[:10]}")

    log("--- Detailed tuple keys for first 5 in ALL (sim): ---")
    for item in res_rah_ww_all["results"][:5]:
        log(f"  sim item: exact_code={item[4]}, total_occ={item[5]}, idx={item[2]}, code={item[3][1]}, len_n={len(item[3][6])}")

    log("--- grep sort|sorted|key= in s32_diag.py (<=10 lines): ---")
    s32_py_lines = diag_py_path.read_text(encoding="utf-8").splitlines()
    sort_hits = []
    for idx_ln, ln in enumerate(s32_py_lines, 1):
        if re.search(r"sort|sorted|key=", ln):
            sort_hits.append(f"{idx_ln}: {ln}")
    for h in sort_hits[:10]:
        log(h)

    rah_entries = find_query_entries(s32_exp, q_rah)
    for p, val in rah_entries:
        log(f"s32_expected راه path={p} -> {json.dumps(val, ensure_ascii=False)}")

    # A3
    log("\n=== A3: Rows where normalize(r[2]) != r[6] ===")
    norm_fn = _get_normalize()
    diff_rows = []
    for idx, r in enumerate(rows_data):
        n2 = norm_fn(r[2])
        r6 = r[6]
        if n2 != r6:
            diff_rows.append((idx, r, n2, r6))
    log(f"Total rows={len(rows_data)}, count(normalize(r[2]) != r[6]) = {len(diff_rows)}")
    for sample_i, (idx, r, n2, r6) in enumerate(diff_rows[:3]):
        first_diff = 0
        min_l = min(len(n2), len(r6))
        while first_diff < min_l and n2[first_diff] == r6[first_diff]:
            first_diff += 1
        log(f"Sample #{sample_i+1} (row idx={idx}, group={r[0]}, code={r[1]}, unit={r[3]}):")
        log(f"  len(normalize(r[2]))={len(n2)}, len(r[6])={len(r6)}, first_diff_at={first_diff}")
        log(f"  repr(normalize(r[2])): {repr(n2)}")
        log(f"  repr(r[6]):            {repr(r6)}")
        log(f"  tail of r[6] from first_diff: {repr(r6[first_diff:])}")

    # A4
    log("\n=== A4: Cause of FAIL 2 in s32b_showmore.txt ===")
    s32b_js_path = Path(ROOT) / "docs/search_logs/s32b_js_showmore.js"
    log("--- Lines in s32b_js_showmore.js containing 'wrapDisplay' (<=20 lines): ---")
    for i, ln in enumerate(s32b_js_path.read_text(encoding="utf-8").splitlines(), 1):
        if "wrapDisplay" in ln:
            log(f"{i}: {ln}")

    log("--- Lines in templates/search_rows_part.html & search_template.html matching ShowMoreWrap (<=20 lines): ---")
    tpl_hits = []
    for tname in ("templates/search_rows_part.html", "templates/search_template.html"):
        tpath = Path(ROOT) / tname
        lines_t = tpath.read_text(encoding="utf-8").splitlines()
        for i, ln in enumerate(lines_t, 1):
            if "ShowMoreWrap" in ln or "showMoreWrap" in ln:
                start = max(0, i - 2)
                end = min(len(lines_t), i + 3)
                for j in range(start, end):
                    tpl_hits.append(f"{tname}:{j+1}: {lines_t[j]}")
    # deduplicate preserving order
    seen_h = set()
    dedup_hits = []
    for h in tpl_hits:
        if h not in seen_h:
            seen_h.add(h)
            dedup_hits.append(h)
    for h in dedup_hits[:20]:
        log(h)

    # A5
    log("\n=== A5: Check __pycache__ directories ===")
    for d in ("src/__pycache__", "tests/__pycache__", "docs/search_logs/__pycache__"):
        full_d = Path(ROOT) / d
        log(f"os.path.isdir('{d}') = {full_d.is_dir()}")

    # GATE verification
    log("\n=== GATE Verification ===")
    gate_ok = True
    gate_reasons = []

    for q_test in (q_arm, q_rah):
        for g_test in (None, ["ابنیه"]):
            sr = search_rows_py(rows_data, q_test, no_space=False, selected_groups=g_test, max_results=100000, whole_word=True)
            ir = s32_mod.independent_re_whole_word_rows(rows_data, q_test, g_test)
            if sr["total"] != ir["total"] or sr["by_group"] != ir["by_group"]:
                gate_ok = False
                gate_reasons.append(f"Mismatch sim vs ind regex for {q_test} groups={g_test}: sim=({sr['total']}, {sr['by_group']}) vs ind=({ir['total']}, {ir['by_group']})")

    def verify_expected_rows(node, path_keys):
        if isinstance(node, dict):
            if "total" in node and "by_group" in node:
                return [(path_keys, node)]
            res = []
            for k, v in node.items():
                res.extend(verify_expected_rows(v, path_keys + [k]))
            return res
        elif isinstance(node, list):
            res = []
            for idx_item, item in enumerate(node):
                res.extend(verify_expected_rows(item, path_keys + [str(idx_item)]))
            return res
        return []

    if "rows" in s32_exp:
        leaves = verify_expected_rows(s32_exp["rows"], [])
        log(f"Found {len(leaves)} leaf entries in s32_expected['rows']; sample path={leaves[0][0] if leaves else None}")
        checked_count = 0
        for pkeys, leaf in leaves:
            mode_val = leaf.get("mode")
            group_val = leaf.get("group")
            query_val = leaf.get("query")
            for pk in pkeys:
                if pk in ("substring", "normal", "whole_word", "ww", "sub"):
                    mode_val = pk
                elif pk in ("ALL", "ابنیه", "مکانیک", "برق", "راه و باند", "راهداری"):
                    group_val = pk
                elif not pk.isdigit() and pk not in ("rows",):
                    query_val = pk
            if query_val is not None and mode_val is not None:
                ww_flag = mode_val in ("whole_word", "ww")
                sel_g = None if group_val in (None, "ALL") else [group_val]
                sr = search_rows_py(rows_data, query_val, no_space=False, selected_groups=sel_g, max_results=100000, whole_word=ww_flag)
                checked_count += 1
                if sr["total"] != leaf["total"] or sr["by_group"] != leaf["by_group"]:
                    gate_ok = False
                    gate_reasons.append(f"Mismatch at {pkeys}: sim=({sr['total']},{sr['by_group']}) vs exp=({leaf['total']},{leaf['by_group']})")
        log(f"Verified {checked_count}/{len(leaves)} row leaves against s32_expected.json; mismatches={len(gate_reasons)}")

    if gate_ok:
        log("GATE: OK")
    else:
        for r in gate_reasons:
            log(f"GATE_REASON: {r}")
        log("GATE: FAIL")

    out_path = Path(ROOT) / "docs/search_logs/s33_recon.txt"
    out_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
