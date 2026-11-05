#!/usr/bin/env python3
"""
S3.2 Diagnostic & Independent Reference Script (docs/search_logs/s32_diag.py)
- Evaluates rows search via src/search_rows.py:search_rows_py (whole_word=False and True).
- Note on pages simulator: no standalone reusable pages simulator function exists in src/*.py
  (sim_search in src/search_build.py is an inner function inside build() and search_pages_sim
  is inside docs/search_logs/s3_diag.py); we also extract DATA.pages & DATA.docs from the built HTML
  to report exact page counts alongside row counts and verify independent regex lookaround on both.
- Checks:
  (1) Table for queries ['بتن','فولاد','آرماتور','کابل','کلید','تخریب','راه','راه آهن','باند','010101','12010101']
      in normal and whole_word modes, across ALL groups and each single group.
  (2) Independent whole-word reference using Python `re` with negative lookbehind/lookahead
      `(?<![WORD_CHARS])token(?![WORD_CHARS])` (completely independent of indexOf/find loop)
      compared against `search_rows_py(..., whole_word=True)`.
  (3) Default-mode invariance: compares current `search_rows_py(..., whole_word=False)` against
      committed `git show HEAD:docs/search_logs/s3_expected_rows.json`.
  (4) Subset property: for every query and group filter, whole_word matched set ⊆ normal matched set.
  (5) Writes `docs/search_logs/s32_expected.json`.
Any exception or check failure logs details and exits with code 1.
"""
import json
import re
import signal
import subprocess
import sys
import traceback
from collections import Counter
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from src.search_rows import WORD_CHAR_CLASS, _get_normalize, search_rows_py

normalize = _get_normalize()


def _timeout_handler(signum, frame):
    raise TimeoutError("s32_diag timed out via signal.alarm")


def load_data_from_html(html_path: Path):
    text = html_path.read_text(encoding="utf-8")
    prefix = "const DATA = "
    start = text.find(prefix)
    if start == -1:
        raise RuntimeError("Could not find 'const DATA = ' in HTML")
    start_json = start + len(prefix)
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(text, start_json)
    return obj


def extract_checkbox_groups_from_html(html_path: Path):
    text = html_path.read_text(encoding="utf-8")
    return re.findall(r'<input[^>]*class="grp-cb"[^>]*value="([^"]+)"', text)


def independent_re_whole_word_rows(rows_data, query_str, selected_groups=None):
    """
    مرجع مستقل کلمهٔ کامل با re و lookaround (بدون استفاده از حلقهٔ find/indexOf).
    """
    norm_q = normalize(query_str)
    tokens = [t for t in norm_q.split(" ") if t]
    if not tokens:
        return {"total": 0, "by_group": {}, "codes_set": set(), "top_codes": []}

    patterns = [
        re.compile(rf"(?<![{WORD_CHAR_CLASS}]){re.escape(tok)}(?![{WORD_CHAR_CLASS}])")
        for tok in tokens
    ]
    allowed = set(selected_groups) if selected_groups is not None else None
    matched = []
    by_group = Counter()

    for idx, r in enumerate(rows_data):
        grp, code, desc, unit, excel_row, pages_list, n_txt = r
        if allowed is not None and grp not in allowed:
            continue
        ok = True
        total_occ = 0
        for pat in patterns:
            found_all = pat.findall(n_txt)
            if not found_all:
                ok = False
                break
            total_occ += len(found_all)
        if not ok:
            continue
        norm_code = normalize(code)
        exact_code = 1 if norm_code in tokens else 0
        by_group[grp] += 1
        matched.append((-exact_code, -total_occ, idx, code))

    matched.sort(key=lambda x: (x[0], x[1], x[2]))
    return {
        "total": len(matched),
        "by_group": dict(by_group),
        "codes_set": {(m[2], m[3]) for m in matched},
        "top_codes": [m[3] for m in matched[:10]],
    }


def search_pages_indexof(pages_data, doc_by_id, query_str, selected_groups=None, whole_word=False):
    """
    شبیه‌ساز صفحه‌ها با الگوریتم indexOf/find (مشابه JS در search_template.html) برای گزارش جدول و parity.
    """
    from src.search_rows import count_occurrences_py

    norm_q = normalize(query_str)
    tokens = [t for t in norm_q.split(" ") if t]
    if not tokens:
        return {"total": 0, "by_doc": {}, "by_group": {}, "page_keys": set(), "top_keys": []}

    allowed = set(selected_groups) if selected_groups is not None else None
    by_doc = Counter()
    by_group = Counter()
    matched = []

    for idx, rec in enumerate(pages_data):
        doc_id = rec[0]
        pno = rec[1]
        n_txt = rec[2]
        dinfo = doc_by_id.get(doc_id)
        if not dinfo:
            continue
        grp = dinfo["group"]
        if allowed is not None and grp not in allowed:
            continue
        ok = True
        score = 0
        for tok in tokens:
            occ = count_occurrences_py(n_txt, tok, whole_word=whole_word)
            if occ == 0:
                ok = False
                break
            score += occ
        if ok:
            by_doc[str(doc_id)] += 1
            by_group[grp] += 1
            matched.append((-score, doc_id, pno))

    matched.sort(key=lambda x: (x[0], x[1], x[2]))
    return {
        "total": len(matched),
        "by_doc": dict(by_doc),
        "by_group": dict(by_group),
        "page_keys": {(m[1], m[2]) for m in matched},
        "top_keys": [f"{m[1]}:{m[2]}" for m in matched[:10]],
    }


def independent_re_whole_word_pages(pages_data, doc_by_id, query_str, selected_groups=None):
    """
    مرجع مستقل کلمهٔ کامل با re و lookaround روی صفحه‌ها.
    """
    norm_q = normalize(query_str)
    tokens = [t for t in norm_q.split(" ") if t]
    if not tokens:
        return {"total": 0, "by_group": {}, "page_keys": set()}
    patterns = [
        re.compile(rf"(?<![{WORD_CHAR_CLASS}]){re.escape(tok)}(?![{WORD_CHAR_CLASS}])")
        for tok in tokens
    ]
    allowed = set(selected_groups) if selected_groups is not None else None
    by_group = Counter()
    page_keys = set()
    for rec in pages_data:
        doc_id, pno, n_txt = rec[0], rec[1], rec[2]
        dinfo = doc_by_id.get(doc_id)
        if not dinfo:
            continue
        grp = dinfo["group"]
        if allowed is not None and grp not in allowed:
            continue
        if all(pat.search(n_txt) is not None for pat in patterns):
            by_group[grp] += 1
            page_keys.add((doc_id, pno))
    return {"total": len(page_keys), "by_group": dict(by_group), "page_keys": page_keys}


def main():
    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(90)
    try:
        html_path = ROOT_DIR / "output" / "search" / "metreyar_search.html"
        data = load_data_from_html(html_path)
        rows_data = data["rows"]
        pages_data = data["pages"]
        docs_meta = data["docs"]
        doc_by_id = {d["id"]: d for d in docs_meta}
        html_groups = extract_checkbox_groups_from_html(html_path)

        queries = [
            "بتن",
            "فولاد",
            "آرماتور",
            "کابل",
            "کلید",
            "تخریب",
            "راه",
            "راه آهن",
            "باند",
            "010101",
            "12010101",
        ]

        group_filters = [("ALL", html_groups)] + [(g, [g]) for g in html_groups]

        diffs = []
        expected_out = {
            "checkbox_groups": html_groups,
            "queries": queries,
            "cases": {},
        }

        print("=== S3.2 Diagnostic Report (s32_diag.txt) ===")
        print(
            "توجه شبیه‌ساز صفحه‌ها: تابع مستقل شبیه‌ساز صفحه‌ها در ماژول‌های src/ وجود ندارد "
            "(sim_search تابع داخلی build در src/search_build.py است و تغییر فایل‌های دیگر ممنوع است: نیازمند بررسی برای واردکردن مستقیم از src/). "
            "در این اسکریپت علاوه‌بر search_rows_py از src/search_rows.py، شمار صفحه‌ها نیز از روی DATA.pages فایل HTML نهایی محاسبه و با مرجع مستقل re سنجیده شده است."
        )
        print(f"HTML Checkbox Groups: {html_groups}")
        print(f"Total Rows: {len(rows_data)} | Total Pages: {len(pages_data)}")
        print("")
        print(f"{'پرسش':<12} | {'گروه':<8} | {'حالت':<11} | {'شمار صفحه':<10} | {'شمار ردیف':<10}")
        print("-" * 65)

        indep_mismatches = 0
        subset_failures = 0

        for q in queries:
            expected_out["cases"][q] = {}
            for g_key, g_list in group_filters:
                # Normal mode (whole_word=False)
                res_r_norm = search_rows_py(
                    rows_data, q, no_space=False, selected_groups=g_list, max_results=None, whole_word=False
                )
                res_p_norm = search_pages_indexof(
                    pages_data, doc_by_id, q, selected_groups=g_list, whole_word=False
                )

                # Whole-word mode (whole_word=True)
                res_r_ww = search_rows_py(
                    rows_data, q, no_space=False, selected_groups=g_list, max_results=None, whole_word=True
                )
                res_p_ww = search_pages_indexof(
                    pages_data, doc_by_id, q, selected_groups=g_list, whole_word=True
                )

                # Independent regex lookaround reference check
                ref_r_ww = independent_re_whole_word_rows(rows_data, q, selected_groups=g_list)
                ref_p_ww = independent_re_whole_word_pages(pages_data, doc_by_id, q, selected_groups=g_list)

                sim_r_codes_set = {(m[2], m[3][1]) for m in res_r_ww["results"]}
                sim_r_top_codes = [m[3][1] for m in res_r_ww["results"][:10]]
                norm_r_codes_set = {(m[2], m[3][1]) for m in res_r_norm["results"]}
                norm_r_top_codes = [m[3][1] for m in res_r_norm["results"][:10]]

                if (
                    res_r_ww["total"] != ref_r_ww["total"]
                    or res_r_ww["by_group"] != ref_r_ww["by_group"]
                    or sim_r_codes_set != ref_r_ww["codes_set"]
                    or sim_r_top_codes != ref_r_ww["top_codes"]
                ):
                    indep_mismatches += 1
                    diffs.append(
                        f"INDEP_ROWS_MISMATCH q={q} grp={g_key}: sim_total={res_r_ww['total']} ref_total={ref_r_ww['total']}"
                    )

                if (
                    res_p_ww["total"] != ref_p_ww["total"]
                    or res_p_ww["by_group"] != ref_p_ww["by_group"]
                    or res_p_ww["page_keys"] != ref_p_ww["page_keys"]
                ):
                    indep_mismatches += 1
                    diffs.append(
                        f"INDEP_PAGES_MISMATCH q={q} grp={g_key}: sim_total={res_p_ww['total']} ref_total={ref_p_ww['total']}"
                    )

                # Check subset property: whole_word ⊆ normal
                if not sim_r_codes_set.issubset(norm_r_codes_set):
                    subset_failures += 1
                    extra = list(sim_r_codes_set - norm_r_codes_set)[:5]
                    diffs.append(f"SUBSET_ROWS_FAIL q={q} grp={g_key}: extra={extra}")

                if not res_p_ww["page_keys"].issubset(res_p_norm["page_keys"]):
                    subset_failures += 1
                    extra_p = list(res_p_ww["page_keys"] - res_p_norm["page_keys"])[:5]
                    diffs.append(f"SUBSET_PAGES_FAIL q={q} grp={g_key}: extra={extra_p}")

                print(f"{q:<12} | {g_key:<8} | {'عادی':<11} | {res_p_norm['total']:<10} | {res_r_norm['total']:<10}")
                print(f"{q:<12} | {g_key:<8} | {'کلمهٔ کامل':<11} | {res_p_ww['total']:<10} | {res_r_ww['total']:<10}")

                expected_out["cases"][q][g_key] = {
                    "normal": {
                        "rows": {
                            "total": res_r_norm["total"],
                            "by_group": res_r_norm["by_group"],
                            "top_codes": norm_r_top_codes,
                        },
                        "pages": {
                            "total": res_p_norm["total"],
                            "by_doc": res_p_norm["by_doc"],
                            "by_group": res_p_norm["by_group"],
                            "top_keys": res_p_norm["top_keys"],
                        },
                    },
                    "whole_word": {
                        "rows": {
                            "total": res_r_ww["total"],
                            "by_group": res_r_ww["by_group"],
                            "top_codes": sim_r_top_codes,
                        },
                        "pages": {
                            "total": res_p_ww["total"],
                            "by_doc": res_p_ww["by_doc"],
                            "by_group": res_p_ww["by_group"],
                            "top_keys": res_p_ww["top_keys"],
                        },
                    },
                }

        # (3) Default-mode invariance against git show HEAD:docs/search_logs/s3_expected_rows.json
        raw_head_json = subprocess.check_output(
            ["git", "show", "HEAD:docs/search_logs/s3_expected_rows.json"],
            cwd=str(ROOT_DIR),
            text=True,
        )
        s3_expected = json.loads(raw_head_json)
        invariance_failures = 0
        for q_s3, grp_dict in s3_expected.get("queries", {}).items():
            for mode_name, no_sp_flag in [("normal", False), ("no_space", True)]:
                if mode_name not in grp_dict:
                    continue
                exp_entry = grp_dict[mode_name]
                cur_res = search_rows_py(
                    rows_data,
                    q_s3,
                    no_space=no_sp_flag,
                    selected_groups=html_groups,
                    max_results=100,
                    whole_word=False,
                )
                cur_top = [m[3][1] for m in cur_res["results"][:10]]
                if (
                    cur_res["total"] != exp_entry["total"]
                    or cur_res["by_group"] != exp_entry["by_group"]
                    or cur_top != exp_entry["top_codes"]
                ):
                    invariance_failures += 1
                    diffs.append(
                        f"INVARIANCE_FAIL_ALL q={q_s3} mode={mode_name}: "
                        f"cur={cur_res['total']} exp={exp_entry['total']}"
                    )
                per_grp_exp = exp_entry.get("per_group", {})
                for g_single, exp_single in per_grp_exp.items():
                    cur_single = search_rows_py(
                        rows_data,
                        q_s3,
                        no_space=no_sp_flag,
                        selected_groups=[g_single],
                        max_results=100,
                        whole_word=False,
                    )
                    cur_single_top = [m[3][1] for m in cur_single["results"][:10]]
                    if (
                        cur_single["total"] != exp_single["total"]
                        or cur_single["by_group"] != exp_single["by_group"]
                        or cur_single_top != exp_single["top_codes"]
                    ):
                        invariance_failures += 1
                        diffs.append(
                            f"INVARIANCE_FAIL_GRP q={q_s3} grp={g_single} mode={mode_name}: "
                            f"cur={cur_single['total']} exp={exp_single['total']}"
                        )

        print("")
        print("=== خلاصهٔ راستی‌آزمایی‌های S3.2 ===")
        print(f"1. تطابق با مرجع مستقل (re lookaround): {'OK (0 اختلاف)' if indep_mismatches == 0 else f'FAIL ({indep_mismatches} اختلاف)'}")
        print(f"2. خاصیت زیرمجموعه (کلمهٔ کامل ⊆ عادی): {'OK (0 نقض)' if subset_failures == 0 else f'FAIL ({subset_failures} نقض)'}")
        print(f"3. ناتغییری حالت پیش‌فرض نسبت به HEAD:docs/search_logs/s3_expected_rows.json: {'OK (0 اختلاف)' if invariance_failures == 0 else f'FAIL ({invariance_failures} اختلاف)'}")

        exp_path = ROOT_DIR / "docs" / "search_logs" / "s32_expected.json"
        exp_path.write_text(json.dumps(expected_out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"4. فایل مرجع نوشته شد: {exp_path.relative_to(ROOT_DIR)}")

        if diffs:
            diff_path = ROOT_DIR / "docs" / "search_logs" / "s32_diff.txt"
            diff_path.write_text("\n".join(diffs[:10]) + "\n", encoding="utf-8")
            print(f"FAIL: {len(diffs)} مورد خطا ثبت شد در {diff_path.relative_to(ROOT_DIR)}")
            sys.exit(1)

        signal.alarm(0)
    except Exception as exc:
        print(f"ERROR in s32_diag.py: {exc}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
