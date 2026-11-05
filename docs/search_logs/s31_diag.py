#!/usr/bin/env python3
"""
S3.1 Diagnostic Script (docs/search_logs/s31_diag.py)
Generates docs/search_logs/s31_diag.txt and updates docs/search_logs/s3_expected_rows.json.
"""
import json
import os
import re
import signal
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


class TimeoutError(Exception):
    pass


def _alarm_handler(signum, frame):
    raise TimeoutError("Section timed out via signal.alarm")


signal.signal(signal.SIGALRM, _alarm_handler)


def is_standard_char(ch):
    cp = ord(ch)
    if ch == ' ' or ('0' <= ch <= '9') or ('A' <= ch <= 'Z') or ('a' <= ch <= 'z'):
        return True
    persian_std = set("آابپتثجچحخدذرزژسشصضطظعغفقکگلمنوهیءئؤأإة")
    if ch in persian_std:
        return True
    if 0x06F0 <= cp <= 0x06F9 or 0x0660 <= cp <= 0x0669:
        return True
    return False


def main():
    out_lines = []

    def log(msg=""):
        out_lines.append(str(msg))

    log("=== S3.1 Diagnostic Report (s31_diag.txt) ===")

    html_path = os.path.join(ROOT_DIR, "output", "search", "metreyar_search.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html_text = f.read()

    m_data = re.search(r"const\s+DATA\s*=\s*(\{.*?\});\s*\n", html_text, re.DOTALL)
    if not m_data:
        raise RuntimeError("Could not extract DATA JSON from metreyar_search.html")
    payload = json.loads(m_data.group(1))
    rows_data = payload.get("rows", [])
    docs_data = payload.get("docs", [])
    pages_data = payload.get("pages", [])

    from src.search_build import normalize
    from src.search_rows import (
        load_abnieh_db_codes,
        read_catalog,
        map_pages_and_build_rows,
        search_rows_py,
    )

    # ---------------------------------------------------------
    # Section A: Page Mapping after fix + 10 not_found samples
    # ---------------------------------------------------------
    log("\n--- بخش A: نگاشت صفحه پس از رفع باگ نام گروه ---")
    try:
        signal.alarm(60)
        db_path = os.path.join(ROOT_DIR, "data_processed", "reference.db")
        db_code_to_pages, db_all_codes, db_info = load_abnieh_db_codes(db_path)

        xlsx_specs = [
            ("abn1404.xlsx", "ابنیه"),
            ("mec1404.xlsx", "مکانیک"),
            ("elec1404.xlsx", "برق"),
        ]
        found_xlsx = {}
        for root_d, _dirs, files in os.walk(os.path.join(ROOT_DIR, "data")):
            for fn in files:
                n_fn = normalize(fn)
                for target_fn, grp_name in xlsx_specs:
                    if n_fn == normalize(target_fn):
                        found_xlsx[grp_name] = os.path.join(root_d, fn)

        catalog_by_group = {}
        for target_fn, grp_name in xlsx_specs:
            x_path = found_xlsx[grp_name]
            use_db = db_all_codes if grp_name == "ابنیه" else None
            entries, _rep = read_catalog(x_path, grp_name, db_codes=use_db)
            catalog_by_group[grp_name] = entries

        doc_id_to_group = {d["id"]: d["group"] for d in docs_data if d.get("status") == "متن (fix+raw)"}
        pdf_records_by_group = {"ابنیه": [], "مکانیک": [], "برق": []}
        for p_entry in pages_data:
            did, pno, n_txt, s_txt = p_entry[0], p_entry[1], p_entry[2], p_entry[3]
            grp = doc_id_to_group.get(did)
            if grp in pdf_records_by_group:
                pdf_records_by_group[grp].append((pno, s_txt, "", n_txt))

        _rows_rebuilt, mapping_report = map_pages_and_build_rows(
            catalog_by_group, pdf_records_by_group, db_code_to_pages
        )

        for grp in ["ابنیه", "مکانیک", "برق"]:
            mp = mapping_report.get(grp, {})
            log(
                f"Group '{grp}': total={mp.get('total')}, pdf_pages_loaded={mp.get('pdf_pages_loaded')}, "
                f"not_found(0 pages)={mp.get('text_0_pages')}, one_page(1)={mp.get('text_1_page')}, "
                f"multi_pages(>=2)={mp.get('text_multi_pages')}, stripped_prefix_matches={mp.get('stripped_prefix_matches')}"
            )
            if grp == "ابنیه":
                log(
                    f"  Abnieh vs DB(source_page_pdf): db_found={mp.get('abn_db_found')}, "
                    f"db_not_found={mp.get('abn_db_not_found')}, agree={mp.get('abn_agree')}, "
                    f"disagree={mp.get('abn_disagree')}, text_missing_for_db_code={mp.get('abn_text_missing')}"
                )
                log(f"  Abnieh disagree samples (code, db_pages, text_pages): {mp.get('abn_disagree_samples')}")

            if grp in ("مکانیک", "برق") and mp.get("text_0_pages", 0) > 0:
                not_found_samples = mp.get("text_samples_not_found", [])[:10]
                log(f"  10 not_found samples for '{grp}':")
                entries = catalog_by_group[grp]
                code_to_pages_mapped = {
                    r[1]: r[5] for r in rows_data if r[0] == grp
                }
                pno_to_lines = {
                    pno: s_txt.splitlines()
                    for (pno, s_txt, _r, _n) in pdf_records_by_group[grp]
                }
                code_to_idx = {e["code"]: idx for idx, e in enumerate(entries)}
                for nf_code, nf_excel_row in not_found_samples:
                    idx = code_to_idx.get(nf_code)
                    prev_pages = []
                    next_pages = []
                    if idx is not None:
                        for back in range(idx - 1, max(-1, idx - 5), -1):
                            pp = code_to_pages_mapped.get(entries[back]["code"], [])
                            if pp:
                                prev_pages = pp
                                break
                        for fwd in range(idx + 1, min(len(entries), idx + 5)):
                            np_list = code_to_pages_mapped.get(entries[fwd]["code"], [])
                            if np_list:
                                next_pages = np_list
                                break
                    cand_page = None
                    if prev_pages and next_pages and prev_pages[-1] == next_pages[0]:
                        cand_page = prev_pages[-1]
                    elif len(prev_pages) == 1 and not next_pages:
                        cand_page = prev_pages[0]

                    if cand_page is not None and cand_page in pno_to_lines:
                        lines_on_p = pno_to_lines[cand_page]
                        short_c = nf_code[2:] if len(nf_code) == 8 else nf_code
                        chap_pref = short_c[:4]
                        near_lines = [ln for ln in lines_on_p if chap_pref in normalize(ln)][:3]
                        if not near_lines:
                            near_lines = lines_on_p[:3]
                        log(f"    code={nf_code} (excel_row={nf_excel_row}) | صفحهٔ PDF مورد انتظار (از همسایه‌ها={cand_page}): 3 lines={near_lines!r}")
                    else:
                        log(f"    code={nf_code} (excel_row={nf_excel_row}) | صفحهٔ PDF مورد انتظار: نامشخص (prev_pages={prev_pages}, next_pages={next_pages})")
        signal.alarm(0)
    except Exception as ex_a:
        signal.alarm(0)
        log(f"ERROR in Section A: {ex_a}")
        diag_txt_path = os.path.join(ROOT_DIR, "docs", "search_logs", "s31_diag.txt")
        with open(diag_txt_path, "w", encoding="utf-8") as df:
            df.write("\n".join(out_lines) + "\n")
        sys.exit(1)

    # ---------------------------------------------------------
    # Section B: R2 discrepancy ('کابل', 'کلید', 'قالب بندی')
    # ---------------------------------------------------------
    log("\n--- بخش B: ناهمخوانی R2 (ابزار پیدا کرده ولی str.__contains__ خام روی شرح پیدا نکرده) ---")
    try:
        signal.alarm(30)
        for q in ("کابل", "کلید", "قالب بندی"):
            res = search_rows_py(rows_data, q, no_space=False, selected_groups=None, max_results=5000)
            norm_q = normalize(q)
            tokens = [t for t in norm_q.split(" ") if t]
            diff_samples = []
            total_tool_hits = 0
            total_raw_desc_hits = 0
            for r in rows_data:
                grp, code, desc, unit, excel_row, pages_list, n_val = r
                if q in desc:
                    total_raw_desc_hits += 1
                if all(t in n_val for t in tokens):
                    total_tool_hits += 1
                    if q not in desc and len(diff_samples) < 5:
                        non_std = sorted({
                            f"U+{ord(ch):04X} ({ch!r})"
                            for ch in desc
                            if not is_standard_char(ch)
                        })
                        diff_samples.append((grp, code, repr(desc[:200]), non_std))
            log(f"Query '{q}': tool_hits={total_tool_hits} (search_rows_py.total={res['total']}), raw_desc_contains={total_raw_desc_hits}, diff_samples_shown={len(diff_samples)}")
            for grp, code, d_repr, non_std in diff_samples:
                log(f"  [{grp}] code={code} | desc_repr={d_repr}")
                log(f"    non_standard_codepoints={non_std}")
        signal.alarm(0)
    except Exception as ex_b:
        signal.alarm(0)
        log(f"ERROR in Section B: {ex_b}")
        diag_txt_path = os.path.join(ROOT_DIR, "docs", "search_logs", "s31_diag.txt")
        with open(diag_txt_path, "w", encoding="utf-8") as df:
            df.write("\n".join(out_lines) + "\n")
        sys.exit(1)

    # ---------------------------------------------------------
    # Section C: 'فوالد' vs 'فولاد' raw counts in xlsx descriptions
    # ---------------------------------------------------------
    log("\n--- بخش C: شمارش خام 'فوالد' و 'فولاد' در شرح ردیف‌های xlsx ---")
    try:
        signal.alarm(20)
        raw_favalad = 0
        raw_folad = 0
        by_grp_favalad = {"ابنیه": 0, "مکانیک": 0, "برق": 0}
        by_grp_folad = {"ابنیه": 0, "مکانیک": 0, "برق": 0}
        for r in rows_data:
            grp, _code, desc = r[0], r[1], r[2]
            if "فوالد" in desc:
                raw_favalad += 1
                by_grp_favalad[grp] = by_grp_favalad.get(grp, 0) + 1
            if "فولاد" in desc:
                raw_folad += 1
                by_grp_folad[grp] = by_grp_folad.get(grp, 0) + 1
        log(f"Raw 'فوالد' count in rows descriptions: total={raw_favalad} | by_group={by_grp_favalad}")
        log(f"Raw 'فولاد' count in rows descriptions: total={raw_folad} | by_group={by_grp_folad}")
        signal.alarm(0)
    except Exception as ex_c:
        signal.alarm(0)
        log(f"ERROR in Section C: {ex_c}")
        diag_txt_path = os.path.join(ROOT_DIR, "docs", "search_logs", "s31_diag.txt")
        with open(diag_txt_path, "w", encoding="utf-8") as df:
            df.write("\n".join(out_lines) + "\n")
        sys.exit(1)

    # ---------------------------------------------------------
    # Section D: Consistency invariants
    # ---------------------------------------------------------
    log("\n--- بخش D: ثابت‌های سازگاری (Consistency Invariants) ---")
    try:
        signal.alarm(20)
        cb_values = re.findall(r'<input[^>]*class="grp-cb"[^>]*value="([^"]+)"', html_text)
        cb_set = set(cb_values)
        rows_groups = sorted({r[0] for r in rows_data})
        docs_groups = sorted({d["group"] for d in docs_data})
        rows_missing_in_cb = sorted(set(rows_groups) - cb_set)
        docs_missing_in_cb = sorted(set(docs_groups) - cb_set)
        cb_without_rows = sorted(cb_set - set(rows_groups))

        log(f"1) Checkbox value attributes in HTML (via regex): {cb_values}")
        log(f"   Unique groups in DATA.rows: {rows_groups}")
        log(f"   Unique groups in DATA.docs: {docs_groups}")
        log(f"   Groups in DATA.rows but NOT in checkboxes: {rows_missing_in_cb}")
        log(f"   Groups in DATA.docs but NOT in checkboxes: {docs_missing_in_cb}")
        log(f"   Checkboxes that have PDF docs only and no xlsx rows: {cb_without_rows}")

        from collections import Counter
        grp_counts = Counter(r[0] for r in rows_data)
        log(f"2) Row count per group in DATA.rows: {dict(grp_counts)} (total={len(rows_data)})")

        for idx, r in enumerate(rows_data):
            assert isinstance(r, list) and len(r) == 7, f"Row {idx} does not have exactly 7 fields: len={len(r)}"
        log(f"3) Assert every row in DATA.rows has 7 fields: OK (checked {len(rows_data)} rows)")
        signal.alarm(0)
    except Exception as ex_d:
        signal.alarm(0)
        log(f"ERROR in Section D: {ex_d}")
        diag_txt_path = os.path.join(ROOT_DIR, "docs", "search_logs", "s31_diag.txt")
        with open(diag_txt_path, "w", encoding="utf-8") as df:
            df.write("\n".join(out_lines) + "\n")
        sys.exit(1)

    # ---------------------------------------------------------
    # Section E: Rebuild s3_expected_rows.json
    # ---------------------------------------------------------
    log("\n--- بخش E: بازسازی docs/search_logs/s3_expected_rows.json ---")
    try:
        signal.alarm(30)
        abn_sample_code = next((r[1] for r in rows_data if r[0] == "ابنیه" and len(r[1]) == 6), None)
        mec_sample_code = next((r[1] for r in rows_data if r[0] == "مکانیک" and len(r[1]) == 8), None)
        elec_sample_code = next((r[1] for r in rows_data if r[0] == "برق" and len(r[1]) == 8), None)

        queries = [
            "بتن",
            "فولاد",
            "شیشه",
            "لوله",
            "کابل",
            "قالب بندی",
            abn_sample_code,
            mec_sample_code,
            elec_sample_code,
        ]
        cb_values = re.findall(r'<input[^>]*class="grp-cb"[^>]*value="([^"]+)"', html_text)
        expected_obj = {
            "built": payload.get("built"),
            "generated_by": "docs/search_logs/s31_diag.py from output/search/metreyar_search.html (built by src/search_build.py)",
            "total_rows": len(rows_data),
            "checkbox_groups": cb_values,
            "sample_codes": {
                "ابنیه_6digit": abn_sample_code,
                "مکانیک_8digit": mec_sample_code,
                "برق_8digit": elec_sample_code,
            },
            "queries": {},
        }

        for q in queries:
            res_norm_all = search_rows_py(rows_data, q, no_space=False, selected_groups=None)
            res_nosp_all = search_rows_py(rows_data, q, no_space=True, selected_groups=None)

            per_group_norm = {}
            per_group_nosp = {}
            for g in cb_values:
                pg_n = search_rows_py(rows_data, q, no_space=False, selected_groups=[g])
                pg_s = search_rows_py(rows_data, q, no_space=True, selected_groups=[g])
                per_group_norm[g] = {
                    "total": pg_n["total"],
                    "by_group": pg_n["by_group"],
                    "top_codes": [item[3][1] for item in pg_n["results"][:10]],
                }
                per_group_nosp[g] = {
                    "total": pg_s["total"],
                    "by_group": pg_s["by_group"],
                    "top_codes": [item[3][1] for item in pg_s["results"][:10]],
                }

            expected_obj["queries"][q] = {
                "normal": {
                    "total": res_norm_all["total"],
                    "by_group": res_norm_all["by_group"],
                    "top_codes": [item[3][1] for item in res_norm_all["results"][:10]],
                    "single_group": per_group_norm,
                },
                "no_space": {
                    "total": res_nosp_all["total"],
                    "by_group": res_nosp_all["by_group"],
                    "top_codes": [item[3][1] for item in res_nosp_all["results"][:10]],
                    "single_group": per_group_nosp,
                },
            }
            log(
                f"Query '{q}': normal.total={res_norm_all['total']} (by_group={res_norm_all['by_group']}) | "
                f"no_space.total={res_nosp_all['total']} (by_group={res_nosp_all['by_group']})"
            )

        exp_path = os.path.join(ROOT_DIR, "docs", "search_logs", "s3_expected_rows.json")
        with open(exp_path, "w", encoding="utf-8") as ef:
            json.dump(expected_obj, ef, ensure_ascii=False, indent=2)
        log(f"Saved {exp_path} successfully (built={payload.get('built')}).")

        with open(exp_path, "r", encoding="utf-8") as vf:
            saved_exp = json.load(vf)
        allowed_row_groups = {"ابنیه", "مکانیک", "برق"}
        seen_by_group_keys = set()
        seen_all_group_keys = set(saved_exp.get("groups", []))
        for q_key, q_val in saved_exp.get("queries", {}).items():
            for mode_name in ("normal", "no_space"):
                m_obj = q_val.get(mode_name, {})
                seen_by_group_keys.update(m_obj.get("by_group", {}).keys())
                for sg_key, sg_val in m_obj.get("single_group", {}).items():
                    seen_all_group_keys.add(sg_key)
                    seen_by_group_keys.update(sg_val.get("by_group", {}).keys())
        bad_prefix = [k for k in (seen_all_group_keys | seen_by_group_keys) if k.startswith("فهرست‌بها")]
        assert not bad_prefix, f"Found group keys starting with فهرست‌بها: {bad_prefix}"
        assert seen_by_group_keys.issubset(allowed_row_groups), f"Row by_group keys not subset of {allowed_row_groups}: {seen_by_group_keys}"
        log(f"Verified s3_expected_rows.json: row by_group keys={sorted(seen_by_group_keys)} subset of {sorted(allowed_row_groups)}, no 'فهرست‌بها' prefix anywhere (all group keys={sorted(seen_all_group_keys)}): OK")
        signal.alarm(0)
    except Exception as ex_e:
        signal.alarm(0)
        log(f"ERROR in Section E: {ex_e}")
        diag_txt_path = os.path.join(ROOT_DIR, "docs", "search_logs", "s31_diag.txt")
        with open(diag_txt_path, "w", encoding="utf-8") as df:
            df.write("\n".join(out_lines) + "\n")
        sys.exit(1)

    diag_txt_path = os.path.join(ROOT_DIR, "docs", "search_logs", "s31_diag.txt")
    with open(diag_txt_path, "w", encoding="utf-8") as df:
        df.write("\n".join(out_lines) + "\n")
    print(f"Wrote {diag_txt_path} ({len(out_lines)} lines)")


if __name__ == "__main__":
    main()
