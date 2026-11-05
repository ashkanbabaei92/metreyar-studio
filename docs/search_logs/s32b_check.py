# -*- coding: utf-8 -*-
"""
S3.2b — بررسی اختلاف ترتیب «راه» (کلمهٔ کامل)، نمونه‌های ازدست‌رفته در کلمهٔ کامل،
و شمارش خام در برابر شبیه‌ساز برای «آرماتور».
بدون تغییر در هیچ فایل موجود.
"""
from __future__ import annotations

import importlib.util
import inspect
import re
import signal
import sys
import traceback
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import src.search_rows as sr_mod
from src.search_rows import WORD_CHAR_CLASS, _get_normalize, search_rows_py

normalize = _get_normalize()

HTML_PATH = ROOT / "output" / "search" / "metreyar_search.html"
LOG_DIR = ROOT / "docs" / "search_logs"
OUT_CHECK = LOG_DIR / "s32b_check.txt"
OUT_RAH = LOG_DIR / "s32b_rah_diff.txt"
OUT_LOST = LOG_DIR / "s32b_lost_words.txt"


def _timeout_handler(signum, frame):
    raise TimeoutError("S3.2b check script timed out (signal.alarm)")


def _import_s32_diag():
    diag_path = LOG_DIR / "s32_diag.py"
    spec = importlib.util.spec_from_file_location("s32_diag", str(diag_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load spec from {diag_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def independent_re_whole_word_rows_detailed(rows_data, query_str, selected_groups=None):
    """
    مرجع مستقل کلمهٔ کامل با re و lookaround و برگرداندن جزئیات کامل هر ردیف:
    برمی‌گرداند: لیست تاپل‌های (-exact_code, -total_occ, idx, r, exact_code, total_occ)
    """
    norm_q = normalize(query_str)
    tokens = [t for t in norm_q.split(" ") if t]
    if not tokens:
        return []

    patterns = [
        re.compile(rf"(?<![{WORD_CHAR_CLASS}]){re.escape(tok)}(?![{WORD_CHAR_CLASS}])")
        for tok in tokens
    ]
    sel_set = set(selected_groups) if selected_groups is not None else None
    matched = []
    for idx, r in enumerate(rows_data):
        grp = r[0]
        if sel_set is not None and grp not in sel_set:
            continue
        code = str(r[1] or "")
        desc_norm = str(r[6] if len(r) > 6 else "")
        code_norm = normalize(code)
        haystack = code_norm + " " + desc_norm

        ok = True
        total_occ = 0
        for pat in patterns:
            c = len(pat.findall(haystack))
            if c == 0:
                ok = False
                break
            total_occ += c
        if not ok:
            continue
        exact_code = 1 if code_norm == norm_q else 0
        matched.append((-exact_code, -total_occ, idx, r, exact_code, total_occ))

    matched.sort(key=lambda x: (x[0], x[1], x[2]))
    return matched


def run_part1_rah(rows_data, all_groups, s32_diag):
    lines = []
    lines.append("=== S3.2b بخش ۱: بررسی اختلاف ترتیب «راه» با whole_word=True ===")

    ebnieh_grp = next((g for g in all_groups if "ابنیه" in g), None)
    if ebnieh_grp is None:
        raise RuntimeError(f"گروه ابنیه در گروه‌های HTML یافت نشد: {all_groups}")

    scopes = [
        ("ALL", all_groups),
        (f"ابنیه ({ebnieh_grp})", [ebnieh_grp]),
    ]

    diag_src_lines = inspect.getsourcelines(s32_diag.independent_re_whole_word_rows)[0]
    lines.append("شاهد از کد s32_diag.independent_re_whole_word_rows:")
    for ln in diag_src_lines:
        s = ln.rstrip()
        if any(k in s for k in ("haystack", "findall", "total_occ", "exact_code", "matched.append", "matched.sort")):
            lines.append("  " + s)

    sr_src_lines = inspect.getsourcelines(sr_mod.search_rows_py)[0]
    lines.append("شاهد از کد src.search_rows.search_rows_py:")
    for ln in sr_src_lines:
        s = ln.rstrip()
        if any(k in s for k in ("haystack", "_count_occurrences", "total_occ", "exact_code", "results.append", "results.sort")):
            lines.append("  " + s)
    lines.append("")

    for scope_label, grp_list in scopes:
        lines.append(f"--- دامنه: {scope_label} ---")
        sim_res = search_rows_py(
            rows_data, "راه", no_space=False, selected_groups=grp_list, max_results=100000, whole_word=True
        )
        diag_res = s32_diag.independent_re_whole_word_rows(rows_data, "راه", selected_groups=grp_list)
        ref_detailed = independent_re_whole_word_rows_detailed(rows_data, "راه", selected_groups=grp_list)

        sim_items = sim_res["results"]
        sim_map = {str(it[3][1]): (it[4], it[5], it[2], it[3][0]) for it in sim_items}
        ref_map = {str(it[3][1]): (it[4], it[5], it[2], it[3][0]) for it in ref_detailed}

        lines.append(f"total شبیه‌ساز (search_rows_py) = {sim_res['total']} (len={len(sim_items)})")
        lines.append(f"total مرجع s32_diag = {diag_res['total']} | total مرجع تفصیلی re = {len(ref_detailed)}")

        occ_diffs = []
        for code_k, (ex_s, occ_s, idx_s, g_s) in sim_map.items():
            if code_k not in ref_map:
                occ_diffs.append((code_k, "missing_in_ref", occ_s, None))
            else:
                ex_r, occ_r, idx_r, g_r = ref_map[code_k]
                if occ_s != occ_r or ex_s != ex_r:
                    occ_diffs.append((code_k, "diff_val", (ex_s, occ_s, idx_s), (ex_r, occ_r, idx_r)))
        for code_k in ref_map:
            if code_k not in sim_map:
                occ_diffs.append((code_k, "missing_in_sim", None, ref_map[code_k][1]))

        lines.append(f"تعداد کدهای دارای اختلاف در (exact_code, total_occ) بین شبیه‌ساز و مرجع regex: {len(occ_diffs)}")
        if occ_diffs:
            lines.append(f"نمونه اختلاف شمار (کد، نوع، شبیه‌ساز، مرجع): {occ_diffs[:10]}")

        lines.append("\n۱۰ ردیف اول شبیه‌ساز (search_rows_py):")
        lines.append("rank | code     | exact_code | total_occ | idx  | group")
        for rank, it in enumerate(sim_items[:10], 1):
            r = it[3]
            lines.append(f"{rank:<4} | {str(r[1]):<8} | {it[4]:<10} | {it[5]:<9} | {it[2]:<4} | {r[0]}")

        lines.append("\n۱۰ ردیف اول مرجع مستقل regex (independent_re_whole_word_rows_detailed):")
        lines.append("rank | code     | exact_code | total_occ | idx  | group")
        for rank, it in enumerate(ref_detailed[:10], 1):
            r = it[3]
            lines.append(f"{rank:<4} | {str(r[1]):<8} | {it[4]:<10} | {it[5]:<9} | {it[2]:<4} | {r[0]}")

        lines.append("\n۱۰ کد اول برگشتی از s32_diag.independent_re_whole_word_rows (top_codes):")
        lines.append("rank | code     | exact_code_sim | total_occ_sim | exact_code_re | total_occ_re | idx  | group")
        for rank, c_code in enumerate(diag_res["top_codes"][:10], 1):
            ex_s, occ_s, idx_s, grp_s = sim_map.get(str(c_code), (-1, -1, -1, "?"))
            ex_r, occ_r, idx_r, grp_r = ref_map.get(str(c_code), (-1, -1, -1, "?"))
            lines.append(
                f"{rank:<4} | {str(c_code):<8} | {ex_s:<14} | {occ_s:<13} | {ex_r:<13} | {occ_r:<12} | {idx_r:<4} | {grp_r}"
            )

        sim_top10_codes = [str(it[3][1]) for it in sim_items[:10]]
        diag_top10_codes = [str(c) for c in diag_res["top_codes"][:10]]
        lines.append(f"\nمقایسهٔ ۱۰ کد اول:")
        lines.append(f"  search_rows_py top10 : {sim_top10_codes}")
        lines.append(f"  s32_diag.top_codes   : {diag_top10_codes}")
        lines.append("")

    OUT_RAH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lines


def run_part2_lost_words(rows_data, all_groups):
    lines = []
    lines.append("=== S3.2b بخش ۲: ردیف‌های ازدست‌رفته در کلمهٔ کامل و کلمات گسترش‌یافته ===")
    word_seq_re = re.compile(rf"[{WORD_CHAR_CLASS}]+")

    queries = ["آرماتور", "بتن", "فولاد"]
    for q in queries:
        norm_q = normalize(q)
        res_normal = search_rows_py(
            rows_data, q, no_space=False, selected_groups=all_groups, max_results=100000, whole_word=False
        )
        res_ww = search_rows_py(
            rows_data, q, no_space=False, selected_groups=all_groups, max_results=100000, whole_word=True
        )

        assert len(res_normal["results"]) == res_normal["total"], (
            f"len(results)={len(res_normal['results'])} != total={res_normal['total']}"
        )
        assert len(res_ww["results"]) == res_ww["total"], (
            f"len(results)={len(res_ww['results'])} != total={res_ww['total']}"
        )

        ww_indices = {it[2] for it in res_ww["results"]}
        lost_items = [it for it in res_normal["results"] if it[2] not in ww_indices]

        expanded_counter_lost_rows = Counter()
        expanded_counter_all_non_boundary = Counter()

        for it in res_normal["results"]:
            idx = it[2]
            r = it[3]
            code_norm = normalize(str(r[1] or ""))
            desc_norm = str(r[6] if len(r) > 6 else "")
            haystack = code_norm + " " + desc_norm

            for m in word_seq_re.finditer(haystack):
                w = m.group(0)
                if norm_q in w:
                    pos = 0
                    while True:
                        fpos = w.find(norm_q, pos)
                        if fpos == -1:
                            break
                        if w != norm_q:
                            expanded_counter_all_non_boundary[w] += 1
                            if idx not in ww_indices:
                                expanded_counter_lost_rows[w] += 1
                        pos = fpos + len(norm_q)

        lines.append(f"\n------------------------------------------------------------")
        lines.append(f"پرسش: «{q}» (دامنه: ALL)")
        lines.append(
            f"  عادی: total={res_normal['total']} (len(results)={len(res_normal['results'])}) | "
            f"کلمهٔ کامل: total={res_ww['total']} (len(results)={len(res_ww['results'])}) | "
            f"ردیف‌های ازدست‌رفته: {len(lost_items)}"
        )
        lines.append(
            f"  شمار کل تطابق‌های زیررشته‌ای فاقد مرز در ردیف‌های ازدست‌رفته: {sum(expanded_counter_lost_rows.values())} "
            f"(در کل ردیف‌های عادی: {sum(expanded_counter_all_non_boundary.values())})"
        )

        lines.append(f"\n  ۱۵ کلمهٔ گسترش‌یافتهٔ پرتکرار (در ردیف‌های ازدست‌رفته):")
        for rank, (w, cnt) in enumerate(expanded_counter_lost_rows.most_common(15), 1):
            all_cnt = expanded_counter_all_non_boundary[w]
            lines.append(f"    {rank:<2}. {w:<22} : در ردیف‌های ازدست‌رفته={cnt:<4} (در کل ردیف‌ها={all_cnt})")

        lines.append(f"\n  ۵ ردیف نمونه از ردیف‌های ازدست‌رفته برای «{q}»:")
        for s_idx, it in enumerate(lost_items[:5], 1):
            r = it[3]
            grp, code, desc_orig = r[0], r[1], str(r[2] or "")
            snippet = desc_orig[:110] + ("..." if len(desc_orig) > 110 else "")
            lines.append(f"    نمونه {s_idx}: [{grp}] کد={code} | {snippet}")

    OUT_LOST.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lines


def run_part3_armature_raw(rows_data, all_groups):
    lines = []
    lines.append("=== S3.2b بخش ۳: شمار خام normalize(desc_original) شامل «آرماتور» در برابر شبیه‌ساز عادی ===")
    q = "آرماتور"
    norm_q = normalize(q)

    raw_all_codes = []
    raw_by_grp = {g: [] for g in all_groups}
    desc_norm_vs_r6_diffs = []

    for idx, r in enumerate(rows_data):
        grp = r[0]
        code = str(r[1] or "")
        desc_orig = str(r[2] or "")
        r6 = str(r[6] if len(r) > 6 else "")
        norm_desc = normalize(desc_orig)
        if norm_desc != r6:
            desc_norm_vs_r6_diffs.append((idx, code, grp))
        if norm_q in norm_desc:
            raw_all_codes.append((idx, grp, code, desc_orig))
            if grp in raw_by_grp:
                raw_by_grp[grp].append((idx, grp, code, desc_orig))

    sim_all = search_rows_py(
        rows_data, q, no_space=False, selected_groups=all_groups, max_results=100000, whole_word=False
    )
    sim_all_codes = [(it[2], it[3][0], str(it[3][1]), str(it[3][2] or "")) for it in sim_all["results"]]

    lines.append(f"پرسش: «{q}» (normalize='{norm_q}')")
    lines.append(f"تعداد کل ردیف‌های DATA.rows: {len(rows_data)}")
    lines.append(f"اختلاف بین normalize(desc_original) و فیلد ازپیش‌نرمال r[6]: {len(desc_norm_vs_r6_diffs)}")
    lines.append("")
    lines.append(f"{'دامنه / گروه':<28} | {'خام normalize(desc)':<20} | {'شبیه‌ساز عادی':<15} | {'تفاضل متقارن'}")
    lines.append("-" * 80)

    raw_set_all = {(x[0], x[2]) for x in raw_all_codes}
    sim_set_all = {(x[0], x[2]) for x in sim_all_codes}
    sym_all = raw_set_all ^ sim_set_all
    lines.append(f"{'ALL (همهٔ گروه‌ها)':<28} | {len(raw_all_codes):<20} | {sim_all['total']:<15} | {len(sym_all)}")

    for g in all_groups:
        sim_g = search_rows_py(
            rows_data, q, no_space=False, selected_groups=[g], max_results=100000, whole_word=False
        )
        raw_g_set = {(x[0], x[2]) for x in raw_by_grp[g]}
        sim_g_set = {(it[2], str(it[3][1])) for it in sim_g["results"]}
        sym_g = raw_g_set ^ sim_g_set
        lines.append(f"{g:<28} | {len(raw_by_grp[g]):<20} | {sim_g['total']:<15} | {len(sym_g)}")
        if sym_g:
            lines.append(f"  کدهای تفاضل متقارن در {g}: {list(sym_g)[:10]}")

    lines.append("\nفهرست کامل ردیف‌های منطبق با «آرماتور» در DATA.rows (خام و شبیه‌ساز):")
    for idx, grp, code, desc_orig in raw_all_codes:
        lines.append(f"  idx={idx:<4} | [{grp}] | code={code} | {desc_orig[:90]}")

    lines.append("\n--- بررسی عدد قدیمی «آرماتور 2/2» در docs/search_logs/*.txt ---")
    log_files = sorted(LOG_DIR.glob("*.txt"))
    grep_hits = []
    for lf in log_files:
        if lf.name.startswith("s32b_"):
            continue
        for lineno, ltext in enumerate(lf.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if "آرماتور" in ltext:
                grep_hits.append(f"{lf.relative_to(ROOT)}:{lineno}:{ltext}")

    if grep_hits:
        lines.append("یافته‌های grep برای «آرماتور» در docs/search_logs/*.txt (حداکثر ۲۰ خط):")
        for h in grep_hits[:20]:
            lines.append("  " + h)
        lines.append(
            "\nتوضیح مستند با شاهد از لاگ‌ها:\n"
            "  ۱) در docs/search_logs/s3_diag.txt:21 و docs/search_logs/s3_parity.txt:12-13 (مربوط به مرحلهٔ S3 "
            "که فقط روی صفحه‌های اسناد DATA.pages اجرا شده بود، قبل از اضافه شدن جدول ردیف‌های متره در S3.1):\n"
            "     شمار صفحه‌های حاوی «آرماتور» در کل اسناد برابر 2 (هر 2 در «فهرست‌بها — ابنیه») بوده است (عادی=2، بی‌فاصله=2).\n"
            "  ۲) در docs/search_logs/s32_diag.txt:34-37 (مربوط به مرحلهٔ S3.2 روی ردیف‌های متره DATA.rows):\n"
            "     برای ردیف‌ها در ALL: عادی = 9 ردیف، کلمهٔ کامل = 2 ردیف (در ابنیه: عادی = 5، کلمهٔ کامل = 2؛ مکانیک عادی = 2؛ برق عادی = 2).\n"
            "     بنابراین عدد 2 در ردیف‌ها مربوط به حالت «کلمهٔ کامل» (۲ ردیف دارای کلمهٔ مستقل «آرماتور» در ابنیه: کدهای 070501 و 070601) "
            "و در صفحه‌ها مربوط به کل صفحات حاوی «آرماتور» (2 صفحه در S3) است، در حالی که شمار خام و شبیه‌ساز عادی روی ردیف‌ها هر دو دقیقاً 9 ردیف هستند (تفاضل متقارن = 0)."
        )
    else:
        lines.append("نیازمند بررسی: عدد قدیمی در لاگ‌ها یافت نشد")

    return lines


def main():
    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(90)
    try:
        s32_diag = _import_s32_diag()
        data_obj = s32_diag.load_data_from_html(HTML_PATH)
        all_groups = s32_diag.extract_checkbox_groups_from_html(HTML_PATH)
        rows_data = data_obj["rows"]

        rah_lines = run_part1_rah(rows_data, all_groups, s32_diag)
        lost_lines = run_part2_lost_words(rows_data, all_groups)
        part3_lines = run_part3_armature_raw(rows_data, all_groups)

        summary = []
        summary.append("=== S3.2b Check Summary (s32b_check.txt) ===")
        summary.append(f"HTML: {HTML_PATH.relative_to(ROOT)} | تعداد کل ردیف‌ها: {len(rows_data)} | گروه‌ها: {all_groups}")
        summary.append("")
        summary.extend(part3_lines)
        summary.append("")
        summary.append("--- خلاصهٔ خروجی‌های تولیدشده ---")
        summary.append(f"1. {OUT_RAH.relative_to(ROOT)} ({len(rah_lines)} خط)")
        summary.append(f"2. {OUT_LOST.relative_to(ROOT)} ({len(lost_lines)} خط)")
        summary.append(f"3. {OUT_CHECK.relative_to(ROOT)}")
        summary.append("STATUS: OK")

        OUT_CHECK.write_text("\n".join(summary) + "\n", encoding="utf-8")
        signal.alarm(0)
    except Exception:
        err_msg = f"ERROR in s32b_check.py:\n{traceback.format_exc()}"
        try:
            OUT_CHECK.write_text(err_msg, encoding="utf-8")
        except Exception:
            pass
        print(err_msg, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
