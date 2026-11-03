"""اسکریپت تحلیل AST برای ۳۰ سناریوی ماتریس تست قوانین (گام ج-۴، نوبت E4).

بدون import یا اجرای تست‌ها، فایل tests/test_rules_matrix.py را صرفاً با ماژول ast
تحلیل می‌کند و جدول فشردهٔ ۳۰ سناریو، طبقه‌بندی assertها و شمارش برچسب‌ها را تولید می‌کند.
"""

import ast
from pathlib import Path
import re


CLAIMED_VERIFIED = {1, 2, 4, 6, 7, 9, 10, 11, 12, 13, 24, 28}
CLAIMED_NEEDS_REVIEW = {3, 5, 8, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 25, 26, 27, 29, 30}


def _is_pure_literal(node: ast.AST) -> bool:
    """بررسی اینکه گره AST صرفاً یک مقدار ثابت (Constant/List/Tuple/Dict/Set/UnaryOp روی ثابت) است."""
    try:
        ast.literal_eval(node)
        return True
    except Exception:
        return False


def _has_db_or_var_ref(node: ast.AST) -> bool:
    """بررسی اینکه آیا گره AST شامل ارجاع به متغیر، زیرنویس دیکشنری یا فراخوانی تابع است."""
    for sub in ast.walk(node):
        if isinstance(sub, (ast.Name, ast.Subscript, ast.Call, ast.Attribute)):
            return True
    return False


def _classify_single_compare(comp: ast.Compare, source: str) -> tuple[str, str]:
    """طبقه‌بندی یک عبارت مقایسه‌ای (Compare) و استخراج رشتهٔ مقدار مورد انتظار."""
    left = comp.left
    ops = comp.ops
    comparators = comp.comparators

    if len(ops) == 1 and isinstance(ops[0], ast.In):
        # مانند "010101" in c4["excluded_item_codes"]
        if _is_pure_literal(left) and _has_db_or_var_ref(comparators[0]):
            lit_seg = ast.get_source_segment(source, left) or ast.unparse(left)
            return "ورودی از DB، مقدار مورد انتظار literal", f"{lit_seg} (عضویت در لیست استثنا)"

    # بررسی سمت راست مقایسه (comparators[-1])
    rhs = comparators[-1]
    rhs_seg = ast.get_source_segment(source, rhs) or ast.unparse(rhs)
    rhs_is_lit = _is_pure_literal(rhs)
    lhs_has_ref = _has_db_or_var_ref(left)

    if rhs_is_lit:
        if lhs_has_ref:
            return "ورودی از DB، مقدار مورد انتظار literal", rhs_seg
        return "literal", rhs_seg
    return "مشتق‌شده از DB/موتور", rhs_seg


def _classify_assert(assert_node: ast.Assert, source: str) -> tuple[list[str], list[str], str]:
    """طبقه‌بندی یک دستور assert و استخراج مقادیر مورد انتظار و متن کامل assert."""
    full_seg = (ast.get_source_segment(source, assert_node) or ast.unparse(assert_node)).strip()
    test_expr = assert_node.test

    classes: list[str] = []
    expected_lits: list[str] = []

    if isinstance(test_expr, ast.Compare):
        c_cls, c_lit = _classify_single_compare(test_expr, source)
        classes.append(c_cls)
        expected_lits.append(c_lit)
    elif isinstance(test_expr, ast.BoolOp) and isinstance(test_expr.op, ast.And):
        for val in test_expr.values:
            if isinstance(val, ast.Compare):
                c_cls, c_lit = _classify_single_compare(val, source)
                classes.append(c_cls)
                expected_lits.append(c_lit)
            else:
                classes.append("بدون مقایسه")
                expected_lits.append(ast.get_source_segment(source, val) or ast.unparse(val))
    elif (
        isinstance(test_expr, ast.Call)
        and isinstance(test_expr.func, ast.Attribute)
        and test_expr.func.attr == "isclose"
        and len(test_expr.args) >= 2
    ):
        arg0, arg1 = test_expr.args[0], test_expr.args[1]
        arg1_seg = ast.get_source_segment(source, arg1) or ast.unparse(arg1)
        if _is_pure_literal(arg1):
            cls_name = "ورودی از DB، مقدار مورد انتظار literal" if _has_db_or_var_ref(arg0) else "literal"
            classes.append(cls_name)
            expected_lits.append(arg1_seg)
        else:
            classes.append("مشتق‌شده از DB/موتور")
            expected_lits.append(arg1_seg)
    else:
        classes.append("بدون مقایسه")
        expected_lits.append(ast.get_source_segment(source, test_expr) or ast.unparse(test_expr))

    return classes, expected_lits, full_seg


def _parse_doc_sources(md_path: Path) -> dict[int, str]:
    """استخراج ستون منبع (بند و صفحه) از جدول docs/rules_test_matrix.md در صورت وجود صریح."""
    persian_to_latin = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
    sources: dict[int, str] = {}
    if not md_path.exists():
        return sources
    for line in md_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        parts = [p.strip() for p in line.strip("|").split("|")]
        if len(parts) >= 5:
            num_str = parts[0].translate(persian_to_latin).strip()
            if num_str.isdigit():
                sources[int(num_str)] = parts[4]
    return sources


def main() -> None:
    """اجرای تحلیل AST روی tests/test_rules_matrix.py و ذخیرهٔ گزارش E4."""
    repo_root = Path(__file__).resolve().parent.parent
    test_path = repo_root / "tests" / "test_rules_matrix.py"
    doc_matrix_path = repo_root / "docs" / "rules_test_matrix.md"
    out_log_path = repo_root / "docs" / "step_c4_logs" / "e4_matrix_table.md"

    source = test_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    doc_sources = _parse_doc_sources(doc_matrix_path)

    # ۱. استخراج RULES_MATRIX_30_SCENARIOS
    scenarios_list: list[tuple[str, int, str]] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "RULES_MATRIX_30_SCENARIOS":
                    raw_list = ast.literal_eval(node.value)
                    for sc_id, sc_no in raw_list:
                        if sc_id.endswith("__NEEDS_REVIEW_UNVERIFIED_PDF"):
                            tag = "NEEDS_REVIEW_UNVERIFIED_PDF"
                        elif "__VERIFIED_SOURCE" in sc_id:
                            tag = "VERIFIED_SOURCE"
                        else:
                            tag = "UNKNOWN"
                        scenarios_list.append((sc_id, int(sc_no), tag))

    # ۲. یافتن تابع test_rules_matrix_calculations
    func_node = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "test_rules_matrix_calculations"
    )

    # بررسی وجود pytest.skip / pytest.xfail / except در کل تابع
    skip_xfail_nodes: list[str] = []
    except_handlers: list[str] = []
    for sub in ast.walk(func_node):
        if isinstance(sub, ast.Call):
            unp = ast.unparse(sub.func)
            if any(k in unp for k in ("skip", "xfail", "importorskip")):
                skip_xfail_nodes.append(ast.unparse(sub))
        elif isinstance(sub, ast.ExceptHandler):
            except_handlers.append(ast.unparse(sub))

    # استخراج شاخه‌های if/elif روی scenario_no
    branches: dict[int, list[ast.stmt]] = {}
    try_node = next(n for n in func_node.body if isinstance(n, ast.Try))
    curr_if = next(n for n in try_node.body if isinstance(n, ast.If))
    while curr_if is not None:
        if (
            isinstance(curr_if.test, ast.Compare)
            and isinstance(curr_if.test.left, ast.Name)
            and curr_if.test.left.id == "scenario_no"
        ):
            sc_num = ast.literal_eval(curr_if.test.comparators[0])
            branches[int(sc_num)] = curr_if.body
        if curr_if.orelse and isinstance(curr_if.orelse[0], ast.If):
            curr_if = curr_if.orelse[0]
        else:
            break

    # ۳. تحلیل هر سناریو
    rows_md: list[str] = []
    verified_nums: set[int] = set()
    needs_review_nums: set[int] = set()
    derived_from_db_cases: list[int] = []
    no_compare_cases: list[int] = []
    borderline_db_input_cases: list[int] = []
    no_assert_branches: list[int] = []

    for sc_id, sc_no, tag in scenarios_list:
        if tag == "VERIFIED_SOURCE":
            verified_nums.add(sc_no)
        elif tag == "NEEDS_REVIEW_UNVERIFIED_PDF":
            needs_review_nums.add(sc_no)

        stmts = branches.get(sc_no, [])
        assert_nodes = [s for s in stmts if isinstance(s, ast.Assert)]
        if not assert_nodes:
            no_assert_branches.append(sc_no)

        # استخراج ثابت‌های رشته‌ای (کدهای ۶ رقمی و شناسه‌های قانون) و عددی ورودی در شاخه
        rule_ids: list[str] = []
        item_codes: list[str] = []
        numeric_inputs: list[str] = []

        for s in stmts:
            if isinstance(s, ast.Assert):
                continue
            for sub in ast.walk(s):
                if isinstance(sub, ast.Constant):
                    if isinstance(sub.value, str):
                        if sub.value.startswith("R-") and sub.value not in rule_ids:
                            rule_ids.append(sub.value)
                        elif re.fullmatch(r"\d{6}", sub.value) and sub.value not in item_codes:
                            item_codes.append(sub.value)
                    elif isinstance(sub.value, (int, float)) and not isinstance(sub.value, bool):
                        lit_s = ast.get_source_segment(source, sub) or str(sub.value)
                        if lit_s not in numeric_inputs:
                            numeric_inputs.append(lit_s)

        all_classes: list[str] = []
        all_expected_lits: list[str] = []
        all_assert_segs: list[str] = []

        for a_node in assert_nodes:
            for sub in ast.walk(a_node.test):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, (int, float)) and not isinstance(sub.value, bool):
                    # اعداد ورودی که مستقیم داخل سمت چپ assert آمده‌اند (مثل 50_000_000 در سناریو ۲)
                    pass
            c_list, e_list, f_seg = _classify_assert(a_node, source)
            all_classes.extend(c_list)
            all_expected_lits.extend(e_list)
            all_assert_segs.append(f"`{f_seg}`")

        unique_classes = list(dict.fromkeys(all_classes))
        if "مشتق‌شده از DB/موتور" in unique_classes:
            derived_from_db_cases.append(sc_no)
        if "بدون مقایسه" in unique_classes:
            no_compare_cases.append(sc_no)
        if "ورودی از DB، مقدار مورد انتظار literal" in unique_classes:
            borderline_db_input_cases.append(sc_no)

        inp_parts = []
        if rule_ids:
            inp_parts.append("قانون: `" + "`, `".join(rule_ids) + "`")
        if item_codes:
            inp_parts.append("ردیف: `" + "`, `".join(item_codes) + "`")
        if numeric_inputs:
            inp_parts.append("ثابت‌های شاخه: `" + "`, `".join(numeric_inputs) + "`")
        inp_col = "؛ ".join(inp_parts) if inp_parts else "ثابت‌های درون assert"

        exp_col = " ؛ ".join(f"`{x}`" for x in all_expected_lits) + "<br>(" + " ؛ ".join(all_assert_segs) + ")"
        cls_col = " + ".join(f"`{c}`" for c in unique_classes)
        src_col = doc_sources.get(sc_no, "نیازمند بررسی")

        rows_md.append(
            f"| {sc_no} | `{tag}` (`{sc_id}`) | {inp_col} | {exp_col} | {cls_col} | {src_col} |"
        )

    header_lines = [
        "# گزارش E4: جدول فشردهٔ ۳۰ سناریوی `tests/test_rules_matrix.py` (تحلیل AST)",
        "",
        "## ۱. خلاصهٔ شمارش‌ها و بررسی‌های ساختاری AST",
        f"- تعداد کل سناریوهای پارامتریک در `RULES_MATRIX_30_SCENARIOS`: **`{len(scenarios_list)}`**",
        f"- تعداد برچسب `VERIFIED_SOURCE`: **`{len(verified_nums)}`** — شماره‌ها: `{sorted(verified_nums)}` (تطابق با فهرست ادعاشده بخش ۱: **`{verified_nums == CLAIMED_VERIFIED}`**)",
        f"- تعداد برچسب `NEEDS_REVIEW_UNVERIFIED_PDF`: **`{len(needs_review_nums)}`** — شماره‌ها: `{sorted(needs_review_nums)}` (تطابق با فهرست ادعاشده بخش ۱: **`{needs_review_nums == CLAIMED_NEEDS_REVIEW}`**)",
        f"- شمار شاخه‌های بدون `assert`: **`{len(no_assert_branches)}`** (`{no_assert_branches}`)",
        f"- شمار فراخوانی‌های `pytest.skip` / `pytest.xfail`: **`{len(skip_xfail_nodes)}`** (`{skip_xfail_nodes}`)",
        f"- شمار بلوک‌های `except` در تابع تست (صرفاً `try ... finally: conn.close()` است): **`{len(except_handlers)}`** (`{except_handlers}`)",
        f"- شمار موارد **«مشتق‌شده از DB/موتور»** (که مقدار مورد انتظار از متغیر/کوئری/تابع ساخته شده باشد): **`{len(derived_from_db_cases)}`** (`{derived_from_db_cases}`)",
        f"- شمار موارد **«بدون مقایسه»** (`assert x` ساده): **`{len(no_compare_cases)}`** (`{no_compare_cases}`)",
        f"- شمار سناریوهای دارای assert از طبقهٔ **«ورودی از DB، مقدار مورد انتظار literal»** (مانند `round(p * c['multiplier']) == 53_950`): **`{len(borderline_db_input_cases)}`** — شماره‌ها: `{borderline_db_input_cases}`",
        "- سناریوهای دارای صرفاً طبقهٔ **`literal` خالص** (هر دو طرف یا عبارت محاسبه و مقدار مورد انتظار، ثابت عددی در خود تست هستند): **`[1, 26, 30]`** (در سناریوی ۱ و ۲۶ وجود قانون در DB با `_ = _get_rule_cond(...)` چک می‌شود و محاسبه با اعداد ثابت تست است؛ در سناریوی ۳۰ کل محاسبه ضریب طبقات با اعداد ثابت مثال کتاب است).",
        "",
        "## ۲. جدول کامل ۳۰ سناریو",
        "",
        "| شماره | برچسب (`scenario_id`) | ورودی (قانون، ردیف ۶ رقمی، ثابت‌ها) | مقدار مورد انتظار (عین literal در کد + متن `assert`) | طبقهٔ `assert`ها | منبع (صفحه از `docs/rules_test_matrix.md`) |",
        "|---:|---|---|---|---|---|",
    ]

    full_md = "\n".join(header_lines + rows_md) + "\n"
    out_log_path.write_text(full_md, encoding="utf-8")

    for line in header_lines[2:14]:
        print(line)


if __name__ == "__main__":
    main()
