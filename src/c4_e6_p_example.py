# -*- coding: utf-8 -*-
"""
گام ج-۴ — نوبت E6: استخراج رشته‌های چاپی مثال ضریب طبقات پیوست ۲ (صفحات ۲۴۲ تا ۲۴۴ PDF / ۲۳۹ تا ۲۴۱ بولتن)
و مقایسه با محاسبهٔ مستقل، ورودی اکسل کاربر، و مقادیر ثبت‌شده در DB/تست.
"""

from __future__ import annotations

import json
import re
import signal
import sqlite3
import sys
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.config import DISCIPLINES, PROJECT_ROOT, REFERENCE_DB_PATH
from src.pdf_fix import extract_rtl_page_lines, open_patched_pdf


def _timeout_handler(signum: int, frame: Any) -> None:
    raise TimeoutError("Execution timed out in c4_e6_p_example.py")


FA_TO_EN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫", "01234567890123456789.")


def to_en_digits(s: str) -> str:
    return s.translate(FA_TO_EN)


def round_half_up(val: Fraction, decimals: int) -> Decimal:
    d = Decimal(val.numerator) / Decimal(val.denominator)
    q = Decimal("10") ** (-decimals)
    return d.quantize(q, rounding=ROUND_HALF_UP)


def trunc_decimals(val: Fraction, decimals: int) -> Decimal:
    d = Decimal(val.numerator) / Decimal(val.denominator)
    q = Decimal("10") ** (-decimals)
    return d.quantize(q, rounding=ROUND_DOWN)


def find_scenario30_lines(test_file: Path) -> List[Tuple[int, str]]:
    out: List[Tuple[int, str]] = []
    if not test_file.exists():
        return out
    lines = test_file.read_text(encoding="utf-8").splitlines()
    for idx, line in enumerate(lines, start=1):
        if any(k in line for k in ("1.0451", "34300", "7600", "APP2_FLOOR_FACTOR_FORMULA")):
            out.append((idx, line.strip()))
    return out


def parse_printed_example(pages_lines: Dict[int, List[str]]) -> Dict[str, Any]:
    """
    جستجو و تجزیهٔ خطوط مربوط به فرمول و مثال ضریب طبقات از توکن‌های چاپی صفحات ۲۴۲ تا ۲۴۴.
    هیچ عدد ثابتی در کد فرض نمی‌شود؛ صرفاً الگوهای ریاضی چاپی (s = ..., p = ..., کسر و جمع) کاوش می‌شوند.
    """
    result: Dict[str, Any] = {
        "candidate_lines": [],
        "s_line": None,
        "s_addends": [],
        "s_printed_total": None,
        "s_computed_total": None,
        "weighted_terms": [],
        "weighted_computed_total": None,
        "printed_numerator": None,
        "printed_denominator": None,
        "printed_intermediate": None,
        "printed_p": None,
        "parse_notes": [],
    }

    for pno, lines in sorted(pages_lines.items()):
        for lno, raw_line in enumerate(lines, start=1):
            en_line = to_en_digits(raw_line)
            has_math_or_num = bool(
                re.search(r"[=+\-*/×÷]|(?:\b\d{3,}\b)|(?:\b[spSP]\b)", en_line)
            )
            if has_math_or_num:
                result["candidate_lines"].append(
                    {
                        "page_pdf": pno,
                        "page_bulletin": pno - 3,
                        "line_no": lno,
                        "text": raw_line,
                        "text_en": en_line,
                        "repr": repr(raw_line),
                    }
                )

            # بررسی خط جمع مساحت‌ها (s = ...)
            if re.search(r"\b[sS]\b\s*=", en_line) or (
                "+" in en_line and len(re.findall(r"\b\d{3,4}\b", en_line)) >= 3
            ):
                nums = [int(x) for x in re.findall(r"\b\d{3,6}\b", en_line)]
                if len(nums) >= 3 and result["s_line"] is None:
                    result["s_line"] = f"p{pno}:L{lno}: {raw_line}"
                    max_n = max(nums)
                    idx_max = nums.index(max_n)
                    others = nums[:idx_max] + nums[idx_max + 1 :]
                    if others and max_n >= max(others) * 2:
                        result["s_printed_total"] = max_n
                        result["s_addends"] = others
                        result["s_computed_total"] = sum(others)
                    else:
                        result["s_addends"] = nums
                        result["s_computed_total"] = sum(nums)

            # بررسی عبارت‌های وزن‌دار k * area یا (area * k) در خطوطی که + و = دارند
            if "+" in en_line:
                mult_pairs = re.findall(r"(\d+)\s*[*×xX]\s*(\d+)", en_line)
                if len(mult_pairs) >= 2:
                    for a_str, b_str in mult_pairs:
                        a_val, b_val = int(a_str), int(b_str)
                        if a_val > 0 and b_val > 0:
                            result["weighted_terms"].append((a_val, b_val, a_val * b_val))

            # بررسی اعداد اعشاری حول ۱.۰... یا ۰.۰...
            decs = re.findall(r"\b[01]\.\d{3,6}\b", en_line)
            for d_str in decs:
                if d_str.startswith("0.0"):
                    result["printed_intermediate"] = d_str
                elif d_str.startswith("1.0"):
                    result["printed_p"] = d_str

            # بررسی اعداد ۵ تا ۷ رقمی که کاندید صورت یا مخرج کسر هستند
            big_ints = [int(x) for x in re.findall(r"\b\d{4,7}\b", en_line)]
            for bi in big_ints:
                if result["s_printed_total"] and bi == result["s_printed_total"]:
                    continue
                if bi >= 100000 and result["printed_denominator"] is None:
                    result["printed_denominator"] = bi
                elif 10000 <= bi < 100000 and result["printed_numerator"] is None:
                    result["printed_numerator"] = bi

    if result["weighted_terms"]:
        result["weighted_computed_total"] = sum(t[2] for t in result["weighted_terms"])

    if result["s_line"] is None:
        result["parse_notes"].append("خط s در توکن‌های چاپی تجزیه نشد")
    if result["printed_numerator"] is None:
        result["parse_notes"].append("صورت کسر از توکن‌های چاپی تجزیه نشد")
    if result["printed_p"] is None:
        result["parse_notes"].append("مقدار چاپی p از توکن‌های چاپی تجزیه نشد")

    return result


def main() -> int:
    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(90)

    logs_dir = PROJECT_ROOT / "docs" / "step_c4_logs"
    evidence_dir = PROJECT_ROOT / "docs" / "evidence"
    logs_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = Path(DISCIPLINES["abnieh"]["pdf_file"])
    db_path = Path(REFERENCE_DB_PATH)

    if not pdf_path.exists():
        print(f"ERROR: PDF not found at {pdf_path}", file=sys.stderr)
        return 1
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        return 1

    # ۱) بررسی تصویر شاهد ص ۲۴۳
    ev_243 = evidence_dir / "abnieh_page_243_app2_floor_example.png"
    rendered_new_243 = False

    doc = open_patched_pdf(pdf_path)
    pages_lines: Dict[int, List[str]] = {}
    pages_words_meta: Dict[int, List[Tuple[float, float, float, float, str]]] = {}

    for pno in (242, 243, 244):
        page_obj = doc[pno - 1]
        if page_obj is None:
            pages_lines[pno] = ["استخراج نشد (page_obj is None)"]
            pages_words_meta[pno] = []
            continue

        # ابتدا هر کاری که به شیء فعلی page نیاز دارد (words با bbox و رندر در صورت نبود فایل)
        raw_words = page_obj.get_text("words")
        pages_words_meta[pno] = [
            (round(w[0], 1), round(w[1], 1), round(w[2], 1), round(w[3], 1), w[4])
            for w in raw_words
        ]

        if pno == 243 and not ev_243.exists():
            pix = page_obj.get_pixmap(dpi=200)
            pix.save(str(ev_243))
            rendered_new_243 = True

        # حذف ارجاع قبلی به page_obj تا شمارندهٔ ارجاع در PyMuPDF به ۱ برسد و reload_page خطا ندهد
        del page_obj
        del raw_words

        # بارگذاری دوبارهٔ صفحه از doc[pno-1] پیش از فراخوانی extract_rtl_page_lines
        try:
            rtl_lines = extract_rtl_page_lines(doc[pno - 1])
            pages_lines[pno] = rtl_lines if rtl_lines is not None else ["استخراج نشد"]
        except Exception as exc:
            pages_lines[pno] = [f"استخراج نشد ({exc})"]

    doc.close()

    # ۲) ذخیرهٔ تمام خطوط صفحات ۲۴۲ تا ۲۴۴
    pages_txt_path = logs_dir / "e6_pages_242_244.txt"
    pages_buf: List[str] = []
    for pno in (242, 243, 244):
        pages_buf.append(f"=== PDF Page {pno} (Bulletin Page {pno - 3}) ===")
        for idx, line in enumerate(pages_lines.get(pno, []), start=1):
            pages_buf.append(f"[p{pno}:L{idx:02d}] {line}")
            pages_buf.append(f"          repr={repr(line)}")
        pages_buf.append(f"--- Raw words count on p{pno}: {len(pages_words_meta.get(pno, []))} ---")
        for w_idx, w_item in enumerate(pages_words_meta.get(pno, []), start=1):
            w_en = to_en_digits(w_item[4])
            if re.search(r"\d|[=+\-*/×÷]|[spSP]", w_en):
                pages_buf.append(
                    f"  word[{w_idx:03d}] bbox=({w_item[0]},{w_item[1]},{w_item[2]},{w_item[3]}) "
                    f"text={w_item[4]} repr={repr(w_item[4])}"
                )
        pages_buf.append("")
    pages_txt_path.write_text("\n".join(pages_buf), encoding="utf-8")

    # ۳) خواندن جدول notes (صفحات ۲۴۲ تا ۲۴۴) و floor_factor_params از DB
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    notes_rows = [
        dict(r)
        for r in conn.execute(
            "SELECT id, discipline, year, section_type, clause_number, clause_ref, "
            "text_raw, text_normalized, clause_text, has_calculation_rule, "
            "source_page_bulletin, source_page_pdf "
            "FROM notes WHERE source_page_pdf IN (242, 243, 244) ORDER BY source_page_pdf, id"
        )
    ]
    ffp_rows = [
        dict(r)
        for r in conn.execute(
            "SELECT id, param_key, discipline, year, title_fa, formula_text_simple, "
            "rounding_decimals, excludes_landscaping, excludes_material_on_site, "
            "selected_project_floor_coeff, source_file, source_page_bulletin, "
            "source_page_pdf, clause_ref, verification_status, evidence_path "
            "FROM floor_factor_params WHERE param_key = 'APP2_FLOOR_FACTOR_FORMULA'"
        )
    ]
    conn.close()

    # ۴) تجزیهٔ توکن‌های چاپی صفحات ۲۴۲–۲۴۴
    parsed_pdf = parse_printed_example(pages_lines)

    # محاسبهٔ مستقل روی مقادیر استخراج‌شده از PDF (در صورت وجود)
    pdf_calc: Dict[str, Any] = {
        "s_used": parsed_pdf["s_printed_total"] or parsed_pdf["s_computed_total"],
        "num_printed": parsed_pdf["printed_numerator"],
        "num_from_weights": parsed_pdf["weighted_computed_total"],
        "denom_printed": parsed_pdf["printed_denominator"],
        "weight_vs_printed_status": "تجزیه نشد",
        "from_printed_num": {},
        "from_weighted_num": {},
    }

    if pdf_calc["num_printed"] is not None and pdf_calc["num_from_weights"] is not None:
        if pdf_calc["num_printed"] == pdf_calc["num_from_weights"]:
            pdf_calc["weight_vs_printed_status"] = "منطبق"
        else:
            pdf_calc["weight_vs_printed_status"] = (
                "ناسازگاری در متن استخراج‌شده یا کتاب؛ نیازمند بازبینی چشمی کاربر"
            )
    elif pdf_calc["num_printed"] is not None or pdf_calc["num_from_weights"] is not None:
        pdf_calc["weight_vs_printed_status"] = (
            "ناسازگاری در متن استخراج‌شده یا کتاب؛ نیازمند بازبینی چشمی کاربر"
        )

    for label_key, num_val in (
        ("from_printed_num", pdf_calc["num_printed"]),
        ("from_weighted_num", pdf_calc["num_from_weights"]),
    ):
        s_val = pdf_calc["s_used"]
        if num_val is not None and s_val:
            denom = 100 * s_val
            frac = Fraction(num_val, denom)
            r4 = round_half_up(frac, 4)
            r5 = round_half_up(frac, 5)
            t5 = trunc_decimals(frac, 5)
            p_r4 = Decimal("1") + r4
            p_t4 = Decimal("1") + trunc_decimals(frac, 4)
            printed_p_str = parsed_pdf["printed_p"]
            match_note = "مقدار چاپی p تجزیه نشد"
            if printed_p_str is not None:
                if str(p_r4) == printed_p_str:
                    match_note = "منطبق با گردکردن ۴ رقم اعشار (ROUND_HALF_UP)"
                elif str(p_t4) == printed_p_str:
                    match_note = "منطبق با برش ۴ رقم اعشار (truncate)"
                else:
                    match_note = "با هیچ‌یک از گردکردن‌ها نمی‌خواند"
            pdf_calc[label_key] = {
                "numerator": num_val,
                "s": s_val,
                "denom_100s": denom,
                "exact_fraction": f"{frac.numerator}/{frac.denominator}",
                "exact_decimal_10": str((Decimal(num_val) / Decimal(denom)).quantize(Decimal("0.0000000001"))),
                "round_4": str(r4),
                "round_5": str(r5),
                "trunc_5": str(t5),
                "p_from_round_4": str(p_r4),
                "p_from_trunc_4": str(p_t4),
                "match_with_printed_p": match_note,
            }

    # ۵) محاسبهٔ مستقل روی ورودی اکسل کاربر (فقط با برچسب مشاهده/محاسبهٔ کاربر، نه تأیید رسمی)
    user_json_path = logs_dir / "e6_user_excel_inputs.json"
    user_data = json.loads(user_json_path.read_text(encoding="utf-8"))
    user_rows = user_data["rows"]
    # فرض قالب کاربر: k = شمارهٔ طبقه برای هر دو نوع B و F، و وزن صفر برای طبقهٔ ۰
    user_s_calc = sum(int(r["area"]) for r in user_rows)
    user_w_b = sum(int(r["floor"]) * int(r["area"]) for r in user_rows if r["type"] == "B" and int(r["floor"]) > 0)
    user_w_f = sum(int(r["floor"]) * int(r["area"]) for r in user_rows if r["type"] == "F" and int(r["floor"]) > 0)
    user_w_calc = user_w_b + user_w_f
    user_denom = 100 * user_s_calc
    user_frac = Fraction(user_w_calc, user_denom)
    user_r4 = round_half_up(user_frac, 4)
    user_r5 = round_half_up(user_frac, 5)
    user_t5 = trunc_decimals(user_frac, 5)
    user_p_r4 = Decimal("1") + user_r4

    # همچنین بررسی فرمول DB روی جدول، و بررسی عبارات استخراج‌شده از formula_text_simple در DB
    db_formula_text = ffp_rows[0]["formula_text_simple"] if ffp_rows else ""
    db_nums_in_formula = re.findall(r"\d+(?:\.\d+)?", to_en_digits(db_formula_text))

    scen30_lines = find_scenario30_lines(PROJECT_ROOT / "tests" / "test_rules_matrix.py")

    # ۶) ساخت گزارش متنی کامل e6_p_example.txt
    detail_lines: List[str] = []
    detail_lines.append("=== گام ج-۴ — نوبت E6: خروجی کامل استخراج و محاسبهٔ مثال ضریب طبقات پیوست ۲ ===")
    detail_lines.append(f"PDF Path: {pdf_path}")
    detail_lines.append(f"DB Path: {db_path}")
    detail_lines.append(f"Evidence 243 exists: {ev_243.exists()} (newly_rendered={rendered_new_243})")
    detail_lines.append("")
    detail_lines.append("--- ۱) خطوط کاندید حاوی اعداد/فرمول در صفحات ۲۴۲ تا ۲۴۴ PDF ---")
    for c in parsed_pdf["candidate_lines"]:
        detail_lines.append(
            f"[PDF p{c['page_pdf']} / Bul p{c['page_bulletin']} L{c['line_no']:02d}] "
            f"{c['text']} | EN: {c['text_en']} | repr: {c['repr']}"
        )
    detail_lines.append("")
    detail_lines.append("--- ۲) نتیجهٔ تجزیهٔ خودکار توکن‌های چاپی PDF ---")
    detail_lines.append(f"s_line: {parsed_pdf['s_line']}")
    detail_lines.append(f"s_addends: {parsed_pdf['s_addends']}")
    detail_lines.append(f"s_printed_total: {parsed_pdf['s_printed_total']}")
    detail_lines.append(f"s_computed_total: {parsed_pdf['s_computed_total']}")
    detail_lines.append(f"weighted_terms (a, b, a*b): {parsed_pdf['weighted_terms']}")
    detail_lines.append(f"weighted_computed_total: {parsed_pdf['weighted_computed_total']}")
    detail_lines.append(f"printed_numerator: {parsed_pdf['printed_numerator']}")
    detail_lines.append(f"printed_denominator: {parsed_pdf['printed_denominator']}")
    detail_lines.append(f"printed_intermediate: {parsed_pdf['printed_intermediate']}")
    detail_lines.append(f"printed_p: {parsed_pdf['printed_p']}")
    detail_lines.append(f"parse_notes: {parsed_pdf['parse_notes']}")
    detail_lines.append(f"pdf_calc: {json.dumps(pdf_calc, ensure_ascii=False, indent=2)}")
    detail_lines.append("")
    detail_lines.append("--- ۳) رکوردهای جدول notes در صفحات ۲۴۲ تا ۲۴۴ ---")
    for nr in notes_rows:
        detail_lines.append(json.dumps(nr, ensure_ascii=False))
    detail_lines.append("")
    detail_lines.append("--- ۴) رکورد APP2_FLOOR_FACTOR_FORMULA در floor_factor_params ---")
    for fr in ffp_rows:
        detail_lines.append(json.dumps(fr, ensure_ascii=False))
    detail_lines.append("")
    detail_lines.append("--- ۵) محاسبهٔ مستقل روی جدول اکسل کاربر (مشاهدهٔ/محاسبهٔ کاربر، نه تأیید رسمی) ---")
    detail_lines.append(f"user_s_calc={user_s_calc}, user_w_b={user_w_b}, user_w_f={user_w_f}, user_w_calc={user_w_calc}")
    detail_lines.append(
        f"user_denom(100*S)={user_denom}, exact={user_frac.numerator}/{user_frac.denominator}, "
        f"round_4={user_r4}, round_5={user_r5}, trunc_5={user_t5}, P_round_4={user_p_r4}"
    )
    detail_lines.append("")
    detail_lines.append("--- ۶) خطوط سناریوی ۳۰ در tests/test_rules_matrix.py ---")
    for lno, ltxt in scen30_lines:
        detail_lines.append(f"Line {lno}: {ltxt}")

    out_txt_path = logs_dir / "e6_p_example.txt"
    out_txt_path.write_text("\n".join(detail_lines) + "\n", encoding="utf-8")

    # ۷) ساخت بخش گزارش Markdown برای الحاق به docs/STEP_C4_REPORT.md
    md: List[str] = []
    md.append("\n\n---\n")
    md.append("## نوبت E6 — استخراج رشته‌های چاپی مثال ضریب طبقات پیوست ۲ (ص ۲۴۲–۲۴۴ PDF / ۲۳۹–۲۴۱ بولتن) و مقایسهٔ مستقل\n")
    md.append("> **وضعیت کل نوبت E6**: **تأییدنشده؛ نیاز به بازبینی چشمی کاربر** (مشاهدهٔ عامل، بدون تأیید کاربر).\n")
    md.append("### ۱. رشته‌های چاپی استخراج‌شده از PDF (`extract_rtl_page_lines` با بارگذاری مجدد صفحه)\n")
    for pno in (242, 243, 244):
        md.append(f"#### صفحهٔ PDF {pno} (بولتن {pno - 3})\n")
        md.append("```text")
        for idx, line in enumerate(pages_lines.get(pno, []), start=1):
            md.append(f"[p{pno}:L{idx:02d}] {line}")
        md.append("```\n")

    md.append("### ۲. خطوط حاوی اعداد و فرمول در صفحات ۲۴۲ تا ۲۴۴ (همراه با `repr`)\n")
    md.append("| صفحه PDF / بولتن | شماره خط | متن استخراج‌شده | معادل ارقام انگلیسی | `repr` |")
    md.append("|---|---|---|---|---|")
    for c in parsed_pdf["candidate_lines"]:
        safe_txt = c["text"].replace("|", "\\|")
        safe_en = c["text_en"].replace("|", "\\|")
        safe_repr = c["repr"].replace("|", "\\|")
        md.append(
            f"| {c['page_pdf']} / {c['page_bulletin']} | {c['line_no']} | `{safe_txt}` | `{safe_en}` | `{safe_repr}` |"
        )
    md.append("")

    md.append("### ۳. نتیجهٔ تجزیهٔ توکن‌های چاپی PDF و محاسبهٔ مستقل روی آن‌ها\n")
    md.append(f"- **خط `s` استخراج‌شده**: `{parsed_pdf['s_line'] or 'تجزیه نشد'}`")
    md.append(f"- **اجزای جمع مساحت (`s_addends`)**: `{parsed_pdf['s_addends']}` $\\rightarrow$ جمع محاسبه‌شده: `{parsed_pdf['s_computed_total']}`، مقدار چاپی `s`: `{parsed_pdf['s_printed_total']}`")
    md.append(f"- **جملات وزن‌دار استخراج‌شده از صورت کسر (`weighted_terms`)**: `{parsed_pdf['weighted_terms']}` $\\rightarrow$ جمع وزنی محاسبه‌شده: `{parsed_pdf['weighted_computed_total']}`")
    md.append(f"- **صورت کسر چاپی**: `{parsed_pdf['printed_numerator']}` | **مخرج چاپی**: `{parsed_pdf['printed_denominator']}` | **عدد میانی چاپی**: `{parsed_pdf['printed_intermediate']}` | **مقدار چاپی `p`**: `{parsed_pdf['printed_p']}`")
    md.append(f"- **وضعیت تطابق جمع وزنی با عدد چاپی صورت**: **{pdf_calc['weight_vs_printed_status']}**")
    if pdf_calc["from_printed_num"]:
        fp = pdf_calc["from_printed_num"]
        md.append(
            f"- **محاسبه با صورت چاپی (`{fp['numerator']}`) و `s={fp['s']}` (`100*s={fp['denom_100s']}`)**: "
            f"کسر دقیق `{fp['exact_fraction']}` = `{fp['exact_decimal_10']}` | گردشده ۴ رقم: `{fp['round_4']}` | "
            f"گردشده ۵ رقم: `{fp['round_5']}` | برش ۵ رقم (truncate): `{fp['trunc_5']}` | "
            f"`1 + round4` = `{fp['p_from_round_4']}` | وضعیت نسبت به چاپی: **{fp['match_with_printed_p']}**"
        )
    if pdf_calc["from_weighted_num"]:
        fw = pdf_calc["from_weighted_num"]
        md.append(
            f"- **محاسبه با جمع وزنی عبارت چاپی (`{fw['numerator']}`) و `s={fw['s']}` (`100*s={fw['denom_100s']}`)**: "
            f"کسر دقیق `{fw['exact_fraction']}` = `{fw['exact_decimal_10']}` | گردشده ۴ رقم: `{fw['round_4']}` | "
            f"گردشده ۵ رقم: `{fw['round_5']}` | برش ۵ رقم (truncate): `{fw['trunc_5']}` | "
            f"`1 + round4` = `{fw['p_from_round_4']}` | وضعیت نسبت به چاپی: **{fw['match_with_printed_p']}**"
        )
    md.append("")

    md.append("### ۴. ورودی و مشاهدهٔ اکسل کاربر («مشاهدهٔ/محاسبهٔ کاربر، نه تأیید رسمی»)\n")
    md.append(f"- **برچسب منبع**: `{user_data['source']}` (ساختمان «{user_data['building_name']}»)")
    md.append(f"- **ردیف‌های مساحت کاربر**: زیرزمین `B3=400, B2=400, B1=400, B0=400`؛ روی زمین `F0=600`، طبقات `F1..F10` هرکدام `500`، و `F11=400`.")
    md.append(f"- **فرض قالب کاربر**: `{user_data['user_formula_note']}` (وزن `k` برابر شمارهٔ طبقه برای هر دو نوع `B` و `F`، و وزن صفر برای طبقهٔ `0`).")
    md.append(f"- **محاسبهٔ مستقل روی جدول کاربر**: `S = {user_s_calc}`، جمع وزنی زیرزمین `W_B = {user_w_b}`، جمع وزنی طبقات روی زمین `W_F = {user_w_f}`، کل صورت کسر `W = {user_w_calc}`، مخرج `100 × S = {user_denom}`.")
    md.append(
        f"- **کسر جدول کاربر**: `{user_frac.numerator}/{user_frac.denominator}` = "
        f"`{(Decimal(user_w_calc) / Decimal(user_denom)).quantize(Decimal('0.0000000001'))}` | "
        f"گردشده ۴ رقم: `{user_r4}` | گردشده ۵ رقم: `{user_r5}` | برش ۵ رقم: `{user_t5}` | "
        f"`P = 1 + {user_r4} = {user_p_r4}` (نمایش‌داده‌شده در اکسل کاربر: `S={user_data['user_displayed']['S']}, W={user_data['user_displayed']['W']}, P={user_data['user_displayed']['P']}`)."
    )
    md.append("- **منبع این مساحت‌ها (توسط کاربر پر می‌شود)**: ☐ از چاپ کتاب ص۲۴۳  ☐ خودم ساخته‌ام تا به عدد برسم  ☐ نامشخص")
    md.append("- **مشاهدهٔ کاربر از صفحه (اختیاری؛ خالی بماند اگر پر نشده)**: `s = ____` ؛ `صورت کسر = ____` ؛ `مخرج = ____` ؛ `عدد میانی = ____` ؛ `p = ____`\n")

    md.append("### ۵. مقایسهٔ سه‌جانبه: چاپ کتاب (PDF ص ۲۴۳) ↔ جدول اکسل کاربر ↔ `floor_factor_params` / سناریوی ۳۰ تست\n")
    fp_num = parsed_pdf["printed_numerator"] if parsed_pdf["printed_numerator"] is not None else "تجزیه نشد"
    fw_num = parsed_pdf["weighted_computed_total"] if parsed_pdf["weighted_computed_total"] is not None else "تجزیه نشد"
    fp_s = pdf_calc["s_used"] if pdf_calc["s_used"] is not None else "تجزیه نشد"
    fp_den = parsed_pdf["printed_denominator"] if parsed_pdf["printed_denominator"] is not None else "تجزیه نشد"
    fp_mid = parsed_pdf["printed_intermediate"] if parsed_pdf["printed_intermediate"] is not None else "تجزیه نشد"
    fp_p = parsed_pdf["printed_p"] if parsed_pdf["printed_p"] is not None else "تجزیه نشد"
    fp_r4 = pdf_calc["from_printed_num"].get("round_4", "—")
    fp_r5 = pdf_calc["from_printed_num"].get("round_5", "—")
    fp_t5 = pdf_calc["from_printed_num"].get("trunc_5", "—")
    fp_pr4 = pdf_calc["from_printed_num"].get("p_from_round_4", "—")

    md.append("| پارامتر | ۱. استخراج از چاپ کتاب (PDF ص ۲۴۳) | ۲. محاسبهٔ مستقل روی اعداد چاپی کتاب | ۳. جدول اکسل کاربر (مشاهده/محاسبهٔ کاربر، نه تأیید رسمی) | ۴. `floor_factor_params` و سناریوی ۳۰ تست (`test_rules_matrix.py`) |")
    md.append("|---|---|---|---|---|")
    md.append(f"| مجموع زیربنا (`s` / `S`) | `{fp_s}` (اجزا: `{parsed_pdf['s_addends']}`) | `{parsed_pdf['s_computed_total']}` | `{user_s_calc}` | `7600` |")
    md.append(f"| صورت کسر (جمع وزنی) | چاپی: `{fp_num}` | جمع عبارت چاپی: `{fw_num}` ({pdf_calc['weight_vs_printed_status']}) | `{user_w_calc}` (`W_B={user_w_b} + W_F={user_w_f}`) | `34300` |")
    md.append(f"| مخرج کسر (`100 × s`) | `{fp_den}` | `{pdf_calc['from_printed_num'].get('denom_100s', '—')}` | `{user_denom}` | `100 * 7600 = 760000` |")
    md.append(f"| عدد میانی (کسر) | چاپی: `{fp_mid}` | گرد ۴ رقم: `{fp_r4}` / گرد ۵ رقم: `{fp_r5}` / برش ۵ رقم: `{fp_t5}` | گرد ۴ رقم: `{user_r4}` / گرد ۵ رقم: `{user_r5}` / برش ۵ رقم: `{user_t5}` | در `sensitive_numbers.md`: `0.04512` (برش ۵ رقمی: `{user_t5}`، گرد ۵ رقمی: `{user_r5}`) |")
    md.append(f"| ضریب نهایی طبقات (`p` / `P`) | چاپی: `{fp_p}` | `1 + round4 = {fp_pr4}` ({pdf_calc['from_printed_num'].get('match_with_printed_p', '—')}) | `{user_p_r4}` | `1.0451` |")
    md.append(f"| وضعیت تأیید | **تأییدنشده؛ نیاز به بازبینی چشمی کاربر** | **تأییدنشده؛ نیاز به بازبینی چشمی کاربر** | **مشاهدهٔ/محاسبهٔ کاربر، نه تأیید رسمی** | **تأییدنشده؛ نیاز به بازبینی چشمی کاربر** |\n")

    md.append("### ۶. محتوای فعلی `floor_factor_params` و خطوط سناریوی ۳۰ در `tests/test_rules_matrix.py`\n")
    md.append("#### الف) ردیف `APP2_FLOOR_FACTOR_FORMULA` در جدول `floor_factor_params`\n")
    md.append("```json")
    md.append(json.dumps(ffp_rows, ensure_ascii=False, indent=2))
    md.append("```\n")
    md.append("#### ب) ردیف‌های جدول `notes` در صفحات ۲۴۲ تا ۲۴۴ PDF\n")
    md.append("```json")
    md.append(json.dumps(notes_rows, ensure_ascii=False, indent=2))
    md.append("```\n")
    md.append("#### پ) شمارهٔ خطوط ثابت‌های سناریوی ۳۰ در `tests/test_rules_matrix.py`\n")
    for lno, ltxt in scen30_lines:
        md.append(f"- خط `{lno}`: `{ltxt}`")
    md.append("")

    report_sec_path = logs_dir / "e6_report_section.md"
    report_sec_path.write_text("\n".join(md) + "\n", encoding="utf-8")

    # چاپ خلاصه در stdout برای بررسی سریع
    print("E6_SCRIPT_STATUS=SUCCESS")
    print(f"PDF_PAGE_242_LINES={len(pages_lines.get(242, []))}")
    print(f"PDF_PAGE_243_LINES={len(pages_lines.get(243, []))}")
    print(f"PDF_PAGE_244_LINES={len(pages_lines.get(244, []))}")
    print(f"PARSED_S_LINE={parsed_pdf['s_line']}")
    print(f"PARSED_S_ADDENDS={parsed_pdf['s_addends']} -> computed={parsed_pdf['s_computed_total']}, printed={parsed_pdf['s_printed_total']}")
    print(f"PARSED_WEIGHTED_TERMS={parsed_pdf['weighted_terms']} -> sum={parsed_pdf['weighted_computed_total']}")
    print(f"PARSED_PRINTED_NUM={parsed_pdf['printed_numerator']}, DENOM={parsed_pdf['printed_denominator']}, MID={parsed_pdf['printed_intermediate']}, P={parsed_pdf['printed_p']}")
    print(f"WEIGHT_VS_PRINTED_STATUS={pdf_calc['weight_vs_printed_status']}")
    print(f"PDF_CALC_FROM_PRINTED_NUM={json.dumps(pdf_calc['from_printed_num'], ensure_ascii=False)}")
    print(f"PDF_CALC_FROM_WEIGHTED_NUM={json.dumps(pdf_calc['from_weighted_num'], ensure_ascii=False)}")
    print(f"USER_EXCEL_CALC: S={user_s_calc}, W_B={user_w_b}, W_F={user_w_f}, W={user_w_calc}, denom={user_denom}, r4={user_r4}, r5={user_r5}, t5={user_t5}, P={user_p_r4}")
    print(f"DB_FORMULA_NUMS={db_nums_in_formula}")
    print(f"SCENARIO30_LINES={[x[0] for x in scen30_lines]}")
    for pno in (242, 243, 244):
        for idx, line in enumerate(pages_lines.get(pno, []), start=1):
            print(f"P{pno}_L{idx:02d}: {line}")

    signal.alarm(0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
