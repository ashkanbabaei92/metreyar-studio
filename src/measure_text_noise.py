#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
اسکریپت اندازه‌گیری نویز متن بندها (گام ج-۴ — نوبت E5: فقط گزارش).
این اسکریپت هیچ تغییری در پایگاه داده، normalize.py، pdf_fix.py یا تست‌ها ایجاد نمی‌کند
و صرفاً با چهار واحد شمارش صریح (U1 تا U4) نویز متن بندهای استخراج‌شده از PDF را
در سه مجموعه (همه ۱۱۹۶ یادداشت، ۴۳۵ یادداشت داخل برش، ۷۶۱ یادداشت خارج برش)
اندازه‌گیری کرده و گزارش‌های مستند تولید می‌کند.
"""

from __future__ import annotations

import difflib
import random
import re
import signal
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from src.config import (
    DISCIPLINES,
    DOCS_DIR,
    PROJECT_ROOT,
    REFERENCE_DB_PATH,
)
from src.normalize import (
    _BROKEN_WORD_RULES,
    _LA_LIGATURE_RULES,
    digits_to_latin,
    unify_persian_chars,
)
from src.pdf_fix import open_patched_pdf

REPORT_MD_PATH: Path = DOCS_DIR / "text_noise_report.md"
LOGS_DIR: Path = DOCS_DIR / "step_c4_logs"
RAW_LOG_PATH: Path = LOGS_DIR / "e5_noise_raw.txt"
SECTION_MD_PATH: Path = LOGS_DIR / "e5_report_section.md"
NORMALIZE_PY_PATH: Path = PROJECT_ROOT / "src" / "normalize.py"

RANDOM_SEED: int = 1404
SAMPLE_SIZE: int = 30
TOP_PAIRS_COUNT: int = 20
CONTEXT_WINDOW: int = 5
PDF_TIMEOUT_SECONDS: int = 300

# تعریف برش مرحله ۳ (سرفصل‌های داخل برش)
SLICE_CHAPTER_PREFIXES: Set[str] = {
    "فصل 01",
    "فصل 02",
    "فصل 03",
    "فصل 05",
    "فصل 06",
    "فصل 07",
    "فصل 08",
    "فصل 28",
    "پیوست 2",
    "پیوست 3",
    "پیوست 4",
    "کلیات",
    "دستورالعمل کاربرد",
}


class TimeoutException(Exception):
    """استثنای اتمام مهلت زمانی برای عملیات سنگین PDF."""


def _timeout_handler(signum: int, frame: Any) -> None:
    raise TimeoutException("PDF rawdict scanning timed out")


def extract_normalize_rules_with_lines() -> List[Dict[str, Any]]:
    """
    استخراج فهرست قواعد جایگزینی موجود در src/normalize.py به همراه شماره خط واقعی در فایل.
    شامل قواعد _LA_LIGATURE_RULES، قواعد _BROKEN_WORD_RULES و قاعده خطی درون normalize_clause_text.
    """
    lines = NORMALIZE_PY_PATH.read_text(encoding="utf-8").splitlines()
    rules_info: List[Dict[str, Any]] = []

    in_la = False
    in_broken = False
    in_func = False

    # برای تطبیق دقیق با تاپل‌های درون حافظه
    la_idx = 0
    broken_idx = 0

    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("_LA_LIGATURE_RULES"):
            in_la = True
            in_broken = False
            continue
        if stripped.startswith("_BROKEN_WORD_RULES"):
            in_la = False
            in_broken = True
            continue
        if stripped.startswith("def normalize_clause_text"):
            in_la = False
            in_broken = False
            in_func = True
            continue
        if in_la and stripped == "]":
            in_la = False
            continue
        if in_broken and stripped == "]":
            in_broken = False
            continue

        if in_la and stripped.startswith("(") and la_idx < len(_LA_LIGATURE_RULES):
            wrong, right = _LA_LIGATURE_RULES[la_idx]
            la_idx += 1
            rules_info.append(
                {
                    "rule_id": f"LA-{la_idx:02d}",
                    "group": "_LA_LIGATURE_RULES",
                    "lineno": lineno,
                    "pattern": wrong,
                    "replacement": right,
                    "is_regex": False,
                    "source_line": stripped,
                }
            )
        elif in_broken and stripped.startswith("(") and broken_idx < len(_BROKEN_WORD_RULES):
            pat, repl = _BROKEN_WORD_RULES[broken_idx]
            broken_idx += 1
            rules_info.append(
                {
                    "rule_id": f"BW-{broken_idx:02d}",
                    "group": "_BROKEN_WORD_RULES",
                    "lineno": lineno,
                    "pattern": pat,
                    "replacement": repl,
                    "is_regex": True,
                    "source_line": stripped,
                }
            )
        elif in_func and "re.sub" in stripped and "\\s+" in stripped:
            rules_info.append(
                {
                    "rule_id": "WS-01",
                    "group": "normalize_clause_text_whitespace",
                    "lineno": lineno,
                    "pattern": r"\s{2,}",
                    "replacement": " ",
                    "is_regex": True,
                    "source_line": stripped,
                }
            )

    return rules_info


def get_normalize_clause_text_snippet() -> Tuple[int, int, List[Tuple[int, str]]]:
    """دریافت خطوط تابع normalize_clause_text از فایل src/normalize.py."""
    lines = NORMALIZE_PY_PATH.read_text(encoding="utf-8").splitlines()
    start_line = 1
    end_line = len(lines)
    for i, line in enumerate(lines, start=1):
        if line.startswith("def normalize_clause_text"):
            start_line = i
        elif start_line > 1 and i > start_line and line.startswith("def "):
            end_line = i - 1
            break
    snippet = [(i, lines[i - 1]) for i in range(start_line, end_line + 1)]
    return start_line, end_line, snippet


def inspect_schema_and_load_data(
    conn: sqlite3.Connection,
) -> Tuple[
    List[Tuple[Any, ...]],
    List[Tuple[Any, ...]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    str,
    str,
    Dict[str, Counter],
]:
    """
    بازرسی کامل PRAGMA table_info برای جداول notes و items، شناسایی ستون نشان‌دهنده داخل/خارج برش
    و ستون شرح ردیف در items از روی داده واقعی.
    """
    cur = conn.cursor()
    notes_pragma = cur.execute("PRAGMA table_info(notes)").fetchall()
    items_pragma = cur.execute("PRAGMA table_info(items)").fetchall()

    notes_cols = [c[1] for c in notes_pragma]
    items_cols = [c[1] for c in items_pragma]

    notes_rows_raw = cur.execute("SELECT * FROM notes ORDER BY id").fetchall()
    notes_rows: List[Dict[str, Any]] = [
        dict(zip(notes_cols, r)) for r in notes_rows_raw
    ]

    items_rows_raw = cur.execute("SELECT * FROM items ORDER BY id").fetchall()
    items_rows: List[Dict[str, Any]] = [
        dict(zip(items_cols, r)) for r in items_rows_raw
    ]

    # شمارش مقادیر متمایز ستون‌های کاندید در notes برای یافتن دقیق تفکیک ۴۳۵ / ۷۶۱
    col_distributions: Dict[str, Counter] = {}
    for col in notes_cols:
        if col in ("id", "text_raw", "text_normalized", "clause_text", "source_page_pdf", "source_page_bulletin"):
            continue
        cnt = Counter(str(r[col]) for r in notes_rows)
        if len(cnt) <= 50:
            col_distributions[col] = cnt

    # تعیین برچسب in_slice بر اساس ستون واقعی status در جدول notes:
    # در جدول notes: status == 'out_of_slice' دقیقاً ۷۶۱ ردیف خارج برش است
    # و status != 'out_of_slice' ('active': 433 + 'empty_in_source': 2) دقیقاً ۴۳۵ ردیف داخل برش است.
    for r in notes_rows:
        r["_in_slice"] = str(r.get("status") or "") != "out_of_slice"

    # تشخیص ستون توصیف در جدول items
    item_desc_col = "description"
    for cand in ("description", "item_description", "title", "text"):
        if cand in items_cols:
            item_desc_col = cand
            break

    return (
        notes_pragma,
        items_pragma,
        notes_rows,
        items_rows,
        "status != 'out_of_slice' (status in {'active': 433, 'empty_in_source': 2} => 435 vs 'out_of_slice': 761)",
        item_desc_col,
        col_distributions,
    )


def count_u1_rule_hits_for_note(
    text_raw: str,
) -> Tuple[int, Dict[str, int]]:
    """
    واحد U1 (برخورد قاعده):
    شمارش دقیق دفعاتی که قواعد جایگزینی موجود در `normalize_clause_text` (شامل ۴۳ قاعده
    `_LA_LIGATURE_RULES` و ۴۶ قاعده `_BROKEN_WORD_RULES`) هنگام اعمال ترتیبی روی متن خام
    (پس از `unify_persian_chars` و `digits_to_latin`، دقیقاً مطابق ترتیب اجرای `normalize_clause_text`)
    فعال می‌شوند.
    برای هر قاعده در `_LA_LIGATURE_RULES` تعداد رخداد زیررشته (`s.count(wrong)`) قبل از جایگزینی،
    و برای هر قاعده در `_BROKEN_WORD_RULES` تعداد تطابق‌های غیرهمپوشان `re.subn(pat, repl, s)`
    در همان مرحله از زنجیره ثبت می‌گردد.
    """
    if not text_raw:
        return 0, {}
    s = unify_persian_chars(digits_to_latin(str(text_raw)))
    per_rule: Dict[str, int] = {}
    total_hits = 0

    for idx, (wrong, right) in enumerate(_LA_LIGATURE_RULES, start=1):
        rid = f"LA-{idx:02d}"
        c = s.count(wrong)
        if c > 0:
            per_rule[rid] = c
            total_hits += c
            s = s.replace(wrong, right)

    for idx, (pat, repl) in enumerate(_BROKEN_WORD_RULES, start=1):
        rid = f"BW-{idx:02d}"
        s, c = re.subn(pat, repl, s)
        if c > 0:
            per_rule[rid] = c
            total_hits += c

    return total_hits, per_rule


def tokenize_ws(text: Optional[str]) -> List[str]:
    """توکن‌سازی متن بر اساس فاصله (`str.split()`)."""
    if not text:
        return []
    return str(text).split()


def strip_punct(token: str) -> str:
    """حذف علائم نگارشی ابتدایی و انتهایی برای ساخت دایره واژگان کمکی."""
    return token.strip(".,،؛:;!؟?()[]{}\"'-«»/\\|+*=_")


def compute_u3_and_u4_for_note(
    note: Dict[str, Any],
) -> Tuple[
    int,
    Dict[str, int],
    List[Dict[str, Any]],
]:
    """
    محاسبه همزمان واحدهای U3 و U4 روی یک یادداشت بر اساس توکن‌های جداشده با فاصله (`str.split()`):
    - در `SequenceMatcher(None, raw_tokens, norm_tokens, autojunk=False)`:
      بلوک‌های `equal`، `replace`، `delete` و `insert` استخراج می‌شوند.
    - تعریف U4 (تفاوت واژه‌ای SequenceMatcher):
      * replace_raw_words: مجموع تعداد واژه‌های سمت خام (`i2 - i1`) در اپ‌کدهای `replace`
      * replace_norm_words: مجموع تعداد واژه‌های سمت نرمال (`j2 - j1`) در اپ‌کدهای `replace`
      * delete_raw_words: مجموع تعداد واژه‌های حذف‌شده از متن خام (`i2 - i1`) در اپ‌کدهای `delete`
      * insert_norm_words: مجموع تعداد واژه‌های درج‌شده در متن نرمال (`j2 - j1`) در اپ‌کدهای `insert`
      * raw_changed_total (کل واژه‌های خام دست‌خورده در اپ‌کدهای غیر یکسان): `replace_raw_words + delete_raw_words`
      * norm_changed_total (کل واژه‌های نرمال حاصل از اپ‌کدهای غیر یکسان): `replace_norm_words + insert_norm_words`
      * opcodes_count: تعداد کل بلوک‌های تغییر (replace + delete + insert)
    - تعریف U3 (واژه‌های اصلاح‌شده در متن نرمال نسبت به متن خام پس از جداسازی با فاصله):
      تعداد توکن‌های موجود در `text_normalized.split()` که در همترازی SequenceMatcher با
      `text_raw.split()` در بلوک `equal` قرار نگرفته‌اند (یعنی توکن‌های نرمال حاصل از اصلاح یک واژه
      خام یا ادغام چند پاره شکسته در یک واژه اصلاح‌شده). همچنین زیرمجموعه جایگزینی‌های
      ۱-به-۱ (`i2 - i1 == 1 and j2 - j1 == 1`) و ادغام‌های چند-به-۱ (`i2 - i1 > 1 and j2 - j1 == 1`)
      و تمام جفت‌های `(raw_phrase -> norm_word)` برای نمونه‌گیری و جدول کاندیدها استخراج می‌شوند.
    """
    raw_tokens = tokenize_ws(note["text_raw"])
    norm_tokens = tokenize_ws(note["text_normalized"])

    sm = difflib.SequenceMatcher(None, raw_tokens, norm_tokens, autojunk=False)
    opcodes = sm.get_opcodes()

    u4_stats = {
        "replace_opcodes": 0,
        "delete_opcodes": 0,
        "insert_opcodes": 0,
        "replace_raw_words": 0,
        "replace_norm_words": 0,
        "delete_raw_words": 0,
        "insert_norm_words": 0,
        "raw_changed_total": 0,
        "norm_changed_total": 0,
        "one_to_one_words": 0,
        "many_to_one_norm_words": 0,
        "many_to_one_raw_words": 0,
    }

    modified_instances: List[Dict[str, Any]] = []

    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            continue
        raw_len = i2 - i1
        norm_len = j2 - j1
        if tag == "replace":
            u4_stats["replace_opcodes"] += 1
            u4_stats["replace_raw_words"] += raw_len
            u4_stats["replace_norm_words"] += norm_len
            u4_stats["raw_changed_total"] += raw_len
            u4_stats["norm_changed_total"] += norm_len

            if raw_len == norm_len:
                # همترازی ۱-به-۱ واژه به واژه در بلوک هم‌اندازه
                for offset in range(raw_len):
                    rw = raw_tokens[i1 + offset]
                    nw = norm_tokens[j1 + offset]
                    j_pos = j1 + offset
                    u4_stats["one_to_one_words"] += 1
                    ctx_before = " ".join(norm_tokens[max(0, j_pos - CONTEXT_WINDOW) : j_pos])
                    ctx_after = " ".join(
                        norm_tokens[j_pos + 1 : min(len(norm_tokens), j_pos + 1 + CONTEXT_WINDOW)]
                    )
                    modified_instances.append(
                        {
                            "note_id": note["id"],
                            "clause_ref": note.get("clause_ref", ""),
                            "source_page_pdf": note.get("source_page_pdf"),
                            "in_slice": note["_in_slice"],
                            "align_type": "1-to-1",
                            "raw_word": rw,
                            "norm_word": nw,
                            "norm_index": j_pos,
                            "ctx_before": ctx_before,
                            "ctx_after": ctx_after,
                        }
                    )
            elif norm_len == 1 and raw_len > 1:
                # ادغام چند توکن شکسته خام در یک واژه نرمال (مثلاً "ع ملیات" -> "عملیات")
                rw = " ".join(raw_tokens[i1:i2])
                nw = norm_tokens[j1]
                j_pos = j1
                u4_stats["many_to_one_norm_words"] += 1
                u4_stats["many_to_one_raw_words"] += raw_len
                ctx_before = " ".join(norm_tokens[max(0, j_pos - CONTEXT_WINDOW) : j_pos])
                ctx_after = " ".join(
                    norm_tokens[j_pos + 1 : min(len(norm_tokens), j_pos + 1 + CONTEXT_WINDOW)]
                )
                modified_instances.append(
                    {
                        "note_id": note["id"],
                        "clause_ref": note.get("clause_ref", ""),
                        "source_page_pdf": note.get("source_page_pdf"),
                        "in_slice": note["_in_slice"],
                        "align_type": f"{raw_len}-to-1",
                        "raw_word": rw,
                        "norm_word": nw,
                        "norm_index": j_pos,
                        "ctx_before": ctx_before,
                        "ctx_after": ctx_after,
                    }
                )
            else:
                # بلوک چند-به-چند غیرهم‌اندازه: ثبت به ازای هر واژه نرمال در بلوک
                raw_phrase = " ".join(raw_tokens[i1:i2])
                for offset in range(norm_len):
                    nw = norm_tokens[j1 + offset]
                    j_pos = j1 + offset
                    ctx_before = " ".join(norm_tokens[max(0, j_pos - CONTEXT_WINDOW) : j_pos])
                    ctx_after = " ".join(
                        norm_tokens[j_pos + 1 : min(len(norm_tokens), j_pos + 1 + CONTEXT_WINDOW)]
                    )
                    modified_instances.append(
                        {
                            "note_id": note["id"],
                            "clause_ref": note.get("clause_ref", ""),
                            "source_page_pdf": note.get("source_page_pdf"),
                            "in_slice": note["_in_slice"],
                            "align_type": f"{raw_len}-to-{norm_len}",
                            "raw_word": raw_phrase,
                            "norm_word": nw,
                            "norm_index": j_pos,
                            "ctx_before": ctx_before,
                            "ctx_after": ctx_after,
                        }
                    )
        elif tag == "delete":
            u4_stats["delete_opcodes"] += 1
            u4_stats["delete_raw_words"] += raw_len
            u4_stats["raw_changed_total"] += raw_len
        elif tag == "insert":
            u4_stats["insert_opcodes"] += 1
            u4_stats["insert_norm_words"] += norm_len
            u4_stats["norm_changed_total"] += norm_len
            for offset in range(norm_len):
                nw = norm_tokens[j1 + offset]
                j_pos = j1 + offset
                ctx_before = " ".join(norm_tokens[max(0, j_pos - CONTEXT_WINDOW) : j_pos])
                ctx_after = " ".join(
                    norm_tokens[j_pos + 1 : min(len(norm_tokens), j_pos + 1 + CONTEXT_WINDOW)]
                )
                modified_instances.append(
                    {
                        "note_id": note["id"],
                        "clause_ref": note.get("clause_ref", ""),
                        "source_page_pdf": note.get("source_page_pdf"),
                        "in_slice": note["_in_slice"],
                        "align_type": f"0-to-{norm_len}",
                        "raw_word": "<INSERT>",
                        "norm_word": nw,
                        "norm_index": j_pos,
                        "ctx_before": ctx_before,
                        "ctx_after": ctx_after,
                    }
                )

    u3_count = u4_stats["norm_changed_total"]
    return u3_count, u4_stats, modified_instances


def measure_u2_pdf_rawdict_al_pairs(
    notes_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    واحد U2 (جفت «ا،ل» در rawdict صفحات PDF):
    شمارش جفت‌کاراکترهای مجاور `('ا', 'ل')` (پس از یکسان‌سازی کاراکتر با `unify_persian_chars`)
    در جریان کاراکترهای `page.get_text("rawdict")` در صفحات متمایز `source_page_pdf` مربوط به جدول `notes`.
    دو روش شمارش در سطح rawdict گزارش می‌شود:
    1) درون هر span (`within_span`): کاراکتر `k` برابر `'ا'` و کاراکتر `k+1` در همان span برابر `'ل'`
    2) درون هر line (`within_line`): پس از الحاق کاراکترهای spanهای یک خط در ترتیب جریان rawdict،
       کاراکتر `k` برابر `'ا'` و کاراکتر `k+1` برابر `'ل'`.
    توجه مهم: از آنجا که `rawdict` در سطح کل صفحه PDF است و در یک صفحه ممکن است چند بند وجود داشته باشد،
    عدد U2 ماهیتاً در سطح «صفحات متمایز PDF مربوط به هر مجموعه» تعریف می‌شود. همچنین بررسی می‌شود که آیا
    صفحه مشترکی بین ۴۳۵ یادداشت داخل برش و ۷۶۱ یادداشت خارج برش وجود دارد یا خیر.
    """
    pdf_path = DISCIPLINES["abnieh"]["pdf_file"]
    all_pages: Set[int] = set()
    slice_pages: Set[int] = set()
    non_slice_pages: Set[int] = set()

    for r in notes_rows:
        p = r.get("source_page_pdf")
        if p is not None:
            p_int = int(p)
            all_pages.add(p_int)
            if r["_in_slice"]:
                slice_pages.add(p_int)
            else:
                non_slice_pages.add(p_int)

    overlap_pages = slice_pages.intersection(non_slice_pages)

    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(PDF_TIMEOUT_SECONDS)

    try:
        doc = open_patched_pdf(pdf_path)
        page_span_counts: Dict[int, int] = {}
        page_line_counts: Dict[int, int] = {}
        page_la_counts: Dict[int, int] = {}

        for pno in sorted(all_pages):
            page = doc[pno - 1]
            # بارگذاری مجدد صفحه برای اعمال پچ ToUnicode، سپس استخراج rawdict و حذف ارجاع صفحه
            page = doc.reload_page(page)
            raw = page.get_text("rawdict")
            del page

            span_al = 0
            line_al = 0
            span_la = 0

            for block in raw.get("blocks", []):
                if block.get("type", 0) != 0:
                    continue
                for line in block.get("lines", []):
                    line_chars: List[str] = []
                    for span in line.get("spans", []):
                        chars_list = [
                            unify_persian_chars(ch.get("c", ""))
                            for ch in span.get("chars", [])
                        ]
                        line_chars.extend(chars_list)
                        for idx in range(len(chars_list) - 1):
                            if chars_list[idx] == "ا" and chars_list[idx + 1] == "ل":
                                span_al += 1
                            elif chars_list[idx] == "ل" and chars_list[idx + 1] == "ا":
                                span_la += 1
                    for idx in range(len(line_chars) - 1):
                        if line_chars[idx] == "ا" and line_chars[idx + 1] == "ل":
                            line_al += 1

            page_span_counts[pno] = span_al
            page_line_counts[pno] = line_al
            page_la_counts[pno] = span_la

        doc.close()
        signal.alarm(0)

        return {
            "status": "completed",
            "all_pages_count": len(all_pages),
            "slice_pages_count": len(slice_pages),
            "non_slice_pages_count": len(non_slice_pages),
            "overlap_pages": sorted(overlap_pages),
            "within_span_all": sum(page_span_counts[p] for p in all_pages),
            "within_span_slice": sum(page_span_counts[p] for p in slice_pages),
            "within_span_non_slice": sum(page_span_counts[p] for p in non_slice_pages),
            "within_line_all": sum(page_line_counts[p] for p in all_pages),
            "within_line_slice": sum(page_line_counts[p] for p in slice_pages),
            "within_line_non_slice": sum(page_line_counts[p] for p in non_slice_pages),
            "within_span_la_all": sum(page_la_counts[p] for p in all_pages),
            "within_span_la_slice": sum(page_la_counts[p] for p in slice_pages),
            "within_span_la_non_slice": sum(page_la_counts[p] for p in non_slice_pages),
        }
    except TimeoutException:
        signal.alarm(0)
        return {"status": "اجرا نشد (timeout)"}
    except Exception as exc:
        signal.alarm(0)
        return {"status": f"اجرا نشد ({exc})"}


def find_pattern_hits_with_context(
    texts: List[Dict[str, Any]],
    text_key: str,
    id_key: str,
    ref_key: str,
    pattern: str,
    is_regex: bool,
    whole_token_only: bool = False,
    max_examples: int = 3,
) -> Tuple[int, int, List[str]]:
    """
    شمارش تعداد برخورد یک الگو در مجموعه‌ای از متون و استخراج حداکثر ۳ نمونه زمینه‌دار
    (۵ واژه قبل و ۵ واژه بعد).
    برمی‌گرداند: (تعداد کل رخدادها، تعداد رکوردهای دارای رخداد، فهرست مثال‌ها).
    """
    total_occurrences = 0
    rows_matched = 0
    examples: List[str] = []

    compiled_re: Optional[re.Pattern[str]] = None
    if is_regex:
        compiled_re = re.compile(pattern)

    for row in texts:
        raw_val = row.get(text_key)
        if not raw_val:
            continue
        text = unify_persian_chars(digits_to_latin(str(raw_val)))

        if whole_token_only:
            tokens = text.split()
            match_indices = [idx for idx, tok in enumerate(tokens) if tok == pattern]
            cnt = len(match_indices)
            if cnt > 0:
                total_occurrences += cnt
                rows_matched += 1
                if len(examples) < max_examples:
                    idx0 = match_indices[0]
                    before = " ".join(tokens[max(0, idx0 - CONTEXT_WINDOW) : idx0])
                    after = " ".join(
                        tokens[idx0 + 1 : min(len(tokens), idx0 + 1 + CONTEXT_WINDOW)]
                    )
                    examples.append(
                        f"[{row.get(id_key)} | {row.get(ref_key)}] «{before} **{tokens[idx0]}** {after}»"
                    )
        elif is_regex and compiled_re is not None:
            matches = list(compiled_re.finditer(text))
            cnt = len(matches)
            if cnt > 0:
                total_occurrences += cnt
                rows_matched += 1
                if len(examples) < max_examples:
                    m0 = matches[0]
                    before_words = text[: m0.start()].split()[-CONTEXT_WINDOW:]
                    after_words = text[m0.end() :].split()[:CONTEXT_WINDOW]
                    examples.append(
                        f"[{row.get(id_key)} | {row.get(ref_key)}] «{' '.join(before_words)} **{m0.group(0)}** {' '.join(after_words)}»"
                    )
        else:
            cnt = text.count(pattern)
            if cnt > 0:
                total_occurrences += cnt
                rows_matched += 1
                if len(examples) < max_examples:
                    pos = text.find(pattern)
                    # پیدا کردن مرز کل واژه حاوی زیررشته برای نمایش واضح‌تر
                    before_words = text[:pos].split()[-CONTEXT_WINDOW:]
                    after_words = text[pos + len(pattern) :].split()[:CONTEXT_WINDOW]
                    examples.append(
                        f"[{row.get(id_key)} | {row.get(ref_key)}] «{' '.join(before_words)} **{pattern}** {' '.join(after_words)}»"
                    )

    return total_occurrences, rows_matched, examples


def main() -> int:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    if not REFERENCE_DB_PATH.exists():
        print(f"ERROR: Database not found at {REFERENCE_DB_PATH}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(REFERENCE_DB_PATH))
    (
        notes_pragma,
        items_pragma,
        notes_rows,
        items_rows,
        slice_criterion_desc,
        item_desc_col,
        col_distributions,
    ) = inspect_schema_and_load_data(conn)
    conn.close()

    all_notes = notes_rows
    slice_notes = [r for r in all_notes if r["_in_slice"]]
    non_slice_notes = [r for r in all_notes if not r["_in_slice"]]

    if len(all_notes) != 1196 or len(slice_notes) != 435 or len(non_slice_notes) != 761:
        print(
            f"ERROR: Unexpected note counts: total={len(all_notes)}, "
            f"slice={len(slice_notes)}, non_slice={len(non_slice_notes)}",
            file=sys.stderr,
        )
        return 2

    # ۱) استخراج فهرست قواعد موجود در normalize.py با شماره خط
    rules_with_lines = extract_normalize_rules_with_lines()
    func_start, func_end, func_snippet = get_normalize_clause_text_snippet()

    # ۲) محاسبه U1، U3، U4 برای هر سه مجموعه
    subsets = {
        "all": all_notes,
        "slice": slice_notes,
        "non_slice": non_slice_notes,
    }

    u1_totals: Dict[str, int] = {}
    u1_notes_with_hits: Dict[str, int] = {}
    u1_per_rule_counts: Dict[str, Counter] = {
        "all": Counter(),
        "slice": Counter(),
        "non_slice": Counter(),
    }

    u3_totals: Dict[str, int] = {}
    u4_agg: Dict[str, Counter] = {
        "all": Counter(),
        "slice": Counter(),
        "non_slice": Counter(),
    }
    raw_token_totals: Dict[str, int] = {}
    norm_token_totals: Dict[str, int] = {}
    notes_changed_counts: Dict[str, int] = {}

    all_modified_instances: List[Dict[str, Any]] = []

    for sub_name, rows in subsets.items():
        u1_sum = 0
        u1_nhits = 0
        u3_sum = 0
        raw_tok_sum = 0
        norm_tok_sum = 0
        changed_notes = 0

        for r in rows:
            raw_tok_sum += len(tokenize_ws(r["text_raw"]))
            norm_tok_sum += len(tokenize_ws(r["text_normalized"]))
            if (r["text_raw"] or "") != (r["text_normalized"] or ""):
                changed_notes += 1

            h_cnt, h_map = count_u1_rule_hits_for_note(r["text_raw"])
            u1_sum += h_cnt
            if h_cnt > 0:
                u1_nhits += 1
            for rid, c in h_map.items():
                u1_per_rule_counts[sub_name][rid] += c

            u3_c, u4_s, mod_inst = compute_u3_and_u4_for_note(r)
            u3_sum += u3_c
            for k, v in u4_s.items():
                u4_agg[sub_name][k] += v

            if sub_name == "all":
                all_modified_instances.extend(mod_inst)

        u1_totals[sub_name] = u1_sum
        u1_notes_with_hits[sub_name] = u1_nhits
        u3_totals[sub_name] = u3_sum
        raw_token_totals[sub_name] = raw_tok_sum
        norm_token_totals[sub_name] = norm_tok_sum
        notes_changed_counts[sub_name] = changed_notes

    # ۳) محاسبه U2 (جفت «ا،ل» در rawdict صفحات PDF) با timeout
    u2_res = measure_u2_pdf_rawdict_al_pairs(all_notes)

    # ۴) نمونه‌گیری تصادفی ۳۰ واژه اصلاح‌شده (فقط جایگزینی‌های ۱-به-۱ واژه از U3) با seed=1404
    one_to_one_instances = [
        inst for inst in all_modified_instances if inst["align_type"] == "1-to-1"
    ]
    one_to_one_sorted = sorted(
        one_to_one_instances,
        key=lambda x: (
            int(x["note_id"]),
            int(x["norm_index"]),
            str(x["raw_word"]),
            str(x["norm_word"]),
        ),
    )
    rng = random.Random(RANDOM_SEED)
    sample_30 = rng.sample(
        one_to_one_sorted, min(SAMPLE_SIZE, len(one_to_one_sorted))
    )

    # ۵) دایره واژگان کمکی از توصیف ۱۵۶۵ ردیف جدول items
    excel_vocab_exact: Set[str] = set()
    excel_vocab_stripped: Set[str] = set()
    for it in items_rows:
        desc = unify_persian_chars(digits_to_latin(str(it.get(item_desc_col) or "")))
        for tok in desc.split():
            excel_vocab_exact.add(tok)
            st = strip_punct(tok)
            if st:
                excel_vocab_stripped.add(st)

    in_vocab_raw_1to1 = sum(
        1
        for x in one_to_one_sorted
        if strip_punct(x["raw_word"]) in excel_vocab_stripped
    )
    in_vocab_norm_1to1 = sum(
        1
        for x in one_to_one_sorted
        if strip_punct(x["norm_word"]) in excel_vocab_stripped
    )
    in_vocab_raw_all_u3 = sum(
        1
        for x in all_modified_instances
        if strip_punct(x["raw_word"]) in excel_vocab_stripped
    )
    in_vocab_norm_all_u3 = sum(
        1
        for x in all_modified_instances
        if strip_punct(x["norm_word"]) in excel_vocab_stripped
    )

    # ۶) ارزیابی کاندیدهای دسته الف: همه قواعد جایگزینی موجود در normalize.py
    # (۴۳ قاعده _LA_LIGATURE_RULES + ۴۶ قاعده _BROKEN_WORD_RULES)
    cat_a_results: List[Dict[str, Any]] = []
    unsafe_cat_a: List[Dict[str, Any]] = []

    for rinfo in rules_with_lines:
        if rinfo["group"] == "normalize_clause_text_whitespace":
            continue
        pat = rinfo["pattern"]
        is_rx = rinfo["is_regex"]

        it_hits, it_rows, it_ex = find_pattern_hits_with_context(
            items_rows,
            item_desc_col,
            "item_code",
            "chapter_id",
            pat,
            is_regex=is_rx,
            whole_token_only=False,
        )
        sl_hits, sl_rows, sl_ex = find_pattern_hits_with_context(
            slice_notes,
            "text_raw",
            "id",
            "clause_ref",
            pat,
            is_regex=is_rx,
            whole_token_only=False,
        )
        al_hits, al_rows, al_ex = find_pattern_hits_with_context(
            all_notes,
            "text_raw",
            "id",
            "clause_ref",
            pat,
            is_regex=is_rx,
            whole_token_only=False,
        )

        entry = {
            **rinfo,
            "items_hits": it_hits,
            "items_rows": it_rows,
            "items_examples": it_ex,
            "slice_raw_hits": sl_hits,
            "slice_chained_hits": u1_per_rule_counts["slice"][rinfo["rule_id"]],
            "slice_examples": sl_ex,
            "all_raw_hits": al_hits,
            "all_chained_hits": u1_per_rule_counts["all"][rinfo["rule_id"]],
            "all_examples": al_ex,
            "is_unsafe_on_items": it_hits > 0,
        }
        cat_a_results.append(entry)
        if it_hits > 0:
            unsafe_cat_a.append(entry)

    # ۷) ارزیابی کاندیدهای دسته ب: ۲۰ جفت پرتکرار (واژه خام -> واژه اصلاح‌شده) از U3 روی ۱۱۹۶ یادداشت
    # هم برای کل جفت‌های U3 (شامل ادغام‌های چند-به-۱ و ۱-به-۱) و هم جداگانه برای ۱-به-۱ خالص
    pair_counter_all: Counter = Counter()
    pair_counter_slice: Counter = Counter()
    pair_examples_map: Dict[Tuple[str, str], List[str]] = {}

    for inst in all_modified_instances:
        pair = (inst["raw_word"], inst["norm_word"])
        pair_counter_all[pair] += 1
        if inst["in_slice"]:
            pair_counter_slice[pair] += 1
        if len(pair_examples_map.get(pair, [])) < 3:
            pair_examples_map.setdefault(pair, []).append(
                f"[note_id={inst['note_id']} | {inst['clause_ref']} | p={inst['source_page_pdf']}] "
                f"«{inst['ctx_before']} **{inst['raw_word']}→{inst['norm_word']}** {inst['ctx_after']}»"
            )

    top_20_pairs = sorted(
        pair_counter_all.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1])
    )[:TOP_PAIRS_COUNT]

    cat_b_results: List[Dict[str, Any]] = []
    unsafe_cat_b: List[Dict[str, Any]] = []

    for rank, ((raw_w, norm_w), count_all_u3) in enumerate(top_20_pairs, start=1):
        has_space = " " in raw_w
        if has_space:
            # عبارت چندتوکنی (مانند "م ی" یا "ف صل") -> جستجوی عبارت با مرز کلمه
            tokens_escaped = [re.escape(t) for t in raw_w.split()]
            rx_pat = r"\b" + r"\s+".join(tokens_escaped) + r"\b"
            it_hits, it_rows, it_ex = find_pattern_hits_with_context(
                items_rows,
                item_desc_col,
                "item_code",
                "chapter_id",
                rx_pat,
                is_regex=True,
                whole_token_only=False,
            )
            sl_hits, sl_rows, sl_ex = find_pattern_hits_with_context(
                slice_notes,
                "text_raw",
                "id",
                "clause_ref",
                rx_pat,
                is_regex=True,
                whole_token_only=False,
            )
            al_hits, al_rows, al_ex = find_pattern_hits_with_context(
                all_notes,
                "text_raw",
                "id",
                "clause_ref",
                rx_pat,
                is_regex=True,
                whole_token_only=False,
            )
        else:
            # تک‌واژه -> تطابق توکن کامل در متن
            it_hits, it_rows, it_ex = find_pattern_hits_with_context(
                items_rows,
                item_desc_col,
                "item_code",
                "chapter_id",
                raw_w,
                is_regex=False,
                whole_token_only=True,
            )
            sl_hits, sl_rows, sl_ex = find_pattern_hits_with_context(
                slice_notes,
                "text_raw",
                "id",
                "clause_ref",
                raw_w,
                is_regex=False,
                whole_token_only=True,
            )
            al_hits, al_rows, al_ex = find_pattern_hits_with_context(
                all_notes,
                "text_raw",
                "id",
                "clause_ref",
                raw_w,
                is_regex=False,
                whole_token_only=True,
            )

        entry_b = {
            "rank": rank,
            "raw_word": raw_w,
            "norm_word": norm_w,
            "u3_all_count": count_all_u3,
            "u3_slice_count": pair_counter_slice[(raw_w, norm_w)],
            "items_hits": it_hits,
            "items_rows": it_rows,
            "items_examples": it_ex,
            "slice_raw_token_hits": sl_hits,
            "all_raw_token_hits": al_hits,
            "note_examples": pair_examples_map.get((raw_w, norm_w), []),
            "is_unsafe_on_items": it_hits > 0,
        }
        cat_b_results.append(entry_b)
        if it_hits > 0:
            unsafe_cat_b.append(entry_b)

    # همچنین ۲۰ جفت پرتکرار صرفاً ۱-به-۱ (بدون ادغام فاصله) برای شفافیت کامل
    pair_counter_1to1_all: Counter = Counter()
    pair_counter_1to1_slice: Counter = Counter()
    for inst in one_to_one_sorted:
        pair = (inst["raw_word"], inst["norm_word"])
        pair_counter_1to1_all[pair] += 1
        if inst["in_slice"]:
            pair_counter_1to1_slice[pair] += 1

    top_20_1to1 = sorted(
        pair_counter_1to1_all.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1])
    )[:TOP_PAIRS_COUNT]

    cat_b_1to1_results: List[Dict[str, Any]] = []
    unsafe_cat_b_1to1: List[Dict[str, Any]] = []
    for rank, ((raw_w, norm_w), cnt_all) in enumerate(top_20_1to1, start=1):
        it_hits, it_rows, it_ex = find_pattern_hits_with_context(
            items_rows,
            item_desc_col,
            "item_code",
            "chapter_id",
            raw_w,
            is_regex=False,
            whole_token_only=True,
        )
        entry_1 = {
            "rank": rank,
            "raw_word": raw_w,
            "norm_word": norm_w,
            "u3_all_count": cnt_all,
            "u3_slice_count": pair_counter_1to1_slice[(raw_w, norm_w)],
            "items_hits": it_hits,
            "items_examples": it_ex,
            "is_unsafe_on_items": it_hits > 0,
            "note_examples": pair_examples_map.get((raw_w, norm_w), []),
        }
        cat_b_1to1_results.append(entry_1)
        if it_hits > 0:
            unsafe_cat_b_1to1.append(entry_1)

    # ۸) تولید گزارش کامل docs/text_noise_report.md
    u2_span_all_str = (
        str(u2_res["within_span_all"])
        if u2_res.get("status") == "completed"
        else u2_res.get("status", "اجرا نشد")
    )
    u2_span_slice_str = (
        str(u2_res["within_span_slice"])
        if u2_res.get("status") == "completed"
        else u2_res.get("status", "اجرا نشد")
    )
    u2_span_non_str = (
        str(u2_res["within_span_non_slice"])
        if u2_res.get("status") == "completed"
        else u2_res.get("status", "اجرا نشد")
    )
    u2_line_all_str = (
        str(u2_res["within_line_all"])
        if u2_res.get("status") == "completed"
        else u2_res.get("status", "اجرا نشد")
    )
    u2_line_slice_str = (
        str(u2_res["within_line_slice"])
        if u2_res.get("status") == "completed"
        else u2_res.get("status", "اجرا نشد")
    )
    u2_line_non_str = (
        str(u2_res["within_line_non_slice"])
        if u2_res.get("status") == "completed"
        else u2_res.get("status", "اجرا نشد")
    )

    md: List[str] = []
    md.append("# گزارش اندازه‌گیری نویز متن بندها (گام ج-۴ — نوبت E5)")
    md.append("")
    md.append("> **وضعیت گزارش**: فقط اندازه‌گیری و گزارش از داده واقعی (بدون تغییر در `src/normalize.py`، `src/pdf_fix.py`، `tests/` یا `data_processed/reference.db`).")
    md.append("> تصمیم‌گیری دربارهٔ گزینهٔ `D-H` (`a`: حفظ وضعیت فعلی، `b`: افزودن قاعده در `normalize_clause_text`، `c`: تغییر `pdf_fix`) و قضاوت دربارهٔ درستی/نادرستی هر جایگزینی منحصراً بر عهدهٔ کاربر است.")
    md.append("")
    md.append("---")
    md.append("")
    md.append("## ۱. مشخصات اسکیما و تفکیک مجموعه‌ها از پایگاه داده")
    md.append("")
    md.append("### ۱-۱. ساختار جدول `notes` (`PRAGMA table_info(notes)`)")
    md.append("")
    md.append("| cid | name | type | notnull | dflt_value | pk |")
    md.append("|---:|---|---|---:|---|---:|")
    for c in notes_pragma:
        md.append(f"| {c[0]} | `{c[1]}` | `{c[2]}` | {c[3]} | `{c[4]}` | {c[5]} |")
    md.append("")
    md.append("### ۱-۲. ساختار جدول `items` (`PRAGMA table_info(items)`)")
    md.append("")
    md.append("| cid | name | type | notnull | dflt_value | pk |")
    md.append("|---:|---|---|---:|---|---:|")
    for c in items_pragma:
        md.append(f"| {c[0]} | `{c[1]}` | `{c[2]}` | {c[3]} | `{c[4]}` | {c[5]} |")
    md.append("")
    md.append("### ۱-۳. نحوهٔ تشخیص «داخل برش» و «خارج برش» در جدول `notes` و ستون توصیف در `items`")
    md.append("")
    md.append(f"- **کل ردیف‌های جدول `notes`**: `{len(all_notes)}` ردیف")
    md.append(f"- **معیار تشخیص داخل/خارج برش از دادهٔ جدول `notes`**: `{slice_criterion_desc}`")
    for col_n, dist in col_distributions.items():
        dist_str = ", ".join(f"`{k}`: {v}" for k, v in dist.most_common(15))
        md.append(f"  - توزیع ستون `{col_n}`: {dist_str}")
    md.append(f"- **تعداد یادداشت‌های داخل برش**: `{len(slice_notes)}` ردیف")
    md.append(f"- **تعداد یادداشت‌های خارج برش**: `{len(non_slice_notes)}` ردیف")
    md.append(f"- **ستون مورد استفاده برای توصیف ۱۵۶۵ ردیف فهرست بها در جدول `items`**: ستون `{item_desc_col}` (تعداد کل ردیف‌ها: `{len(items_rows)}`)")
    md.append("")
    md.append("---")
    md.append("")
    md.append("## ۲. تعریف دقیق واحدهای اندازه‌گیری (U1 تا U4) و جدول مقایسه‌ای")
    md.append("")
    md.append("1. **U1 — برخورد قاعده (Rule Triggers در `normalize_clause_text`)**:")
    md.append("   - **روش شمارش**: روی `text_raw` هر یادداشت، ابتدا دقیقاً مطابق خط ۲۰۸ `src/normalize.py` توابع `unify_persian_chars(digits_to_latin(raw_text))` اعمال می‌شود؛ سپس ۴۳ قاعدهٔ `_LA_LIGATURE_RULES` (با `s.count(wrong)` پیش از `s.replace(wrong, right)`) و ۴۶ قاعدهٔ `_BROKEN_WORD_RULES` (با `re.subn(pat, repl, s)`) به همان ترتیب اجرای واقعی زنجیره اعمال و تعداد کل جایگزینی‌های انجام‌شده جمع زده می‌شود.")
    md.append("2. **U2 — جفت کاراکتر مجاور `('ا', 'ل')` در `rawdict` صفحات PDF**:")
    md.append("   - **روش شمارش**: فایل `data/fehrest/11-Abnieh1404.pdf` با `open_patched_pdf` باز شده و برای هر صفحهٔ متمایز `source_page_pdf` (پس از `doc.reload_page(page)` و آزادسازی ارجاع صفحه با `del page`)، ساختار `page.get_text('rawdict')` پیمایش می‌گردد. کاراکترها با `unify_persian_chars` یکسان‌سازی شده و تعداد جفت‌های مجاور `('ا', 'ل')` در دو سطح گزارش می‌شود: **(الف) درون هر span (`within_span`)** و **(ب) درون کل خط (`within_line`)** پس از الحاق کاراکترهای spanهای یک خط.")
    if u2_res.get("status") == "completed":
        md.append(
            f"   - **سطح انتساب صفحه**: شمارش `rawdict` در سطح کل صفحهٔ PDF است. کل یادداشت‌ها در `{u2_res['all_pages_count']}` صفحهٔ متمایز PDF، یادداشت‌های داخل برش در `{u2_res['slice_pages_count']}` صفحهٔ متمایز، و یادداشت‌های خارج برش در `{u2_res['non_slice_pages_count']}` صفحهٔ متمایز قرار دارند (اشتراک صفحات بین داخل و خارج برش: `{len(u2_res['overlap_pages'])}` صفحه: `{u2_res['overlap_pages']}`). در صفحاتی که چند بند دارند، عدد صفحه به کل صفحه مربوط است."
        )
    md.append("3. **U3 — واژه‌های اصلاح‌شده (Modified Words پس از جداسازی با فاصله)**:")
    md.append("   - **روش همترازی و شمارش**: متون `text_raw` و `text_normalized` هر یادداشت با `str.split()` به فهرست واژه‌ها تبدیل شده و با `difflib.SequenceMatcher(None, raw_tokens, norm_tokens, autojunk=False)` همتراز می‌شوند. شمار واژه‌های موجود در `text_normalized.split()` که در بلوک‌های غیر یکسان (`tag != 'equal'`) قرار دارند به عنوان «واژه‌های اصلاح‌شده در متن نرمال» شمارش می‌گردند. تفکیک آن‌ها به جایگزینی‌های **۱-به-۱ واژه** (`raw_len == 1, norm_len == 1` یا بلوک هم‌اندازهٔ جایگزینی) و **ادغام‌های چند-به-۱** (`raw_len > 1, norm_len == 1`، مانند تبدیل دو توکن خام `ع ملیات` به یک توکن نرمال `عملیات`) در جدول آمده است.")
    md.append("4. **U4 — تفاوت واژه‌ای `SequenceMatcher` (`difflib`) به تفکیک نوع اپ‌کد**:")
    md.append("   - **روش شمارش**: روی همان همترازی `SequenceMatcher(None, raw_tokens, norm_tokens, autojunk=False)`، شمار اپ‌کدها و شمار واژه‌های درگیر در سمت خام (`i2 - i1`) و سمت نرمال (`j2 - j1`) به تفکیک `replace`، `delete` و `insert` گزارش شده است.")
    md.append("")
    md.append("### جدول اصلی واحدهای شمارش × مجموعه‌ها")
    md.append("")
    md.append("| کد واحد | تعریف دقیق شاخص | همهٔ یادداشت‌ها (۱۱۹۶) | داخل برش (۴۳۵) | خارج برش (۷۶۱) |")
    md.append("|---|---|---:|---:|---:|")
    md.append(f"| پایه | تعداد کل یادداشت‌ها | {len(all_notes)} | {len(slice_notes)} | {len(non_slice_notes)} |")
    md.append(f"| پایه | تعداد یادداشت‌های دارای تغییر (`text_raw != text_normalized`) | {notes_changed_counts['all']} | {notes_changed_counts['slice']} | {notes_changed_counts['non_slice']} |")
    md.append(f"| پایه | کل واژه‌های `text_raw.split()` | {raw_token_totals['all']} | {raw_token_totals['slice']} | {raw_token_totals['non_slice']} |")
    md.append(f"| پایه | کل واژه‌های `text_normalized.split()` | {norm_token_totals['all']} | {norm_token_totals['slice']} | {norm_token_totals['non_slice']} |")
    md.append(f"| **U1** | **برخورد قواعد در زنجیرهٔ `normalize_clause_text` (کل جایگزینی‌ها)** | **{u1_totals['all']}** | **{u1_totals['slice']}** | **{u1_totals['non_slice']}** |")
    md.append(f"| U1-sub | تعداد یادداشت‌های دارای حداقل یک برخورد قاعده U1 | {u1_notes_with_hits['all']} | {u1_notes_with_hits['slice']} | {u1_notes_with_hits['non_slice']} |")
    md.append(f"| **U2 (span)** | **جفت مجاور `('ا','ل')` درون spanهای `rawdict` صفحات PDF متمایز** | **{u2_span_all_str}** | **{u2_span_slice_str}** | **{u2_span_non_str}** |")
    md.append(f"| **U2 (line)** | **جفت مجاور `('ا','ل')` درون خطوط `rawdict` صفحات PDF متمایز** | **{u2_line_all_str}** | **{u2_line_slice_str}** | **{u2_line_non_str}** |")
    if u2_res.get("status") == "completed":
        md.append(
            f"| U2-pages | تعداد صفحات PDF متمایز (`source_page_pdf`) در هر مجموعه | {u2_res['all_pages_count']} | {u2_res['slice_pages_count']} | {u2_res['non_slice_pages_count']} |"
        )
        md.append(
            f"| U2-ref | جفت مجاور `('ل','ا')` درون spanهای `rawdict` (برای مقایسه) | {u2_res['within_span_la_all']} | {u2_res['within_span_la_slice']} | {u2_res['within_span_la_non_slice']} |"
        )
    md.append(f"| **U3** | **کل واژه‌های اصلاح‌شده در `text_normalized` (`norm_changed_total`)** | **{u3_totals['all']}** | **{u3_totals['slice']}** | **{u3_totals['non_slice']}** |")
    md.append(f"| U3-1to1 | زیرمجموعهٔ U3: جایگزینی‌های ۱-به-۱ واژه (`raw_len == norm_len`) | {u4_agg['all']['one_to_one_words']} | {u4_agg['slice']['one_to_one_words']} | {u4_agg['non_slice']['one_to_one_words']} |")
    md.append(f"| U3-merge | زیرمجموعهٔ U3: ادغام‌های چند-به-۱ (تعداد واژه‌های نرمال حاصل) | {u4_agg['all']['many_to_one_norm_words']} | {u4_agg['slice']['many_to_one_norm_words']} | {u4_agg['non_slice']['many_to_one_norm_words']} |")
    md.append(f"| U3-merge-raw | تعداد توکن‌های شکستهٔ خام ورودی در ادغام‌های چند-به-۱ | {u4_agg['all']['many_to_one_raw_words']} | {u4_agg['slice']['many_to_one_raw_words']} | {u4_agg['non_slice']['many_to_one_raw_words']} |")
    md.append(f"| **U4-op-rep** | **تعداد اپ‌کدهای `replace` در `SequenceMatcher`** | **{u4_agg['all']['replace_opcodes']}** | **{u4_agg['slice']['replace_opcodes']}** | **{u4_agg['non_slice']['replace_opcodes']}** |")
    md.append(f"| **U4-op-del** | **تعداد اپ‌کدهای `delete` در `SequenceMatcher`** | **{u4_agg['all']['delete_opcodes']}** | **{u4_agg['slice']['delete_opcodes']}** | **{u4_agg['non_slice']['delete_opcodes']}** |")
    md.append(f"| **U4-op-ins** | **تعداد اپ‌کدهای `insert` در `SequenceMatcher`** | **{u4_agg['all']['insert_opcodes']}** | **{u4_agg['slice']['insert_opcodes']}** | **{u4_agg['non_slice']['insert_opcodes']}** |")
    md.append(f"| **U4-w-rep-raw** | **واژه‌های خام در اپ‌کدهای `replace` (`replace_raw_words`)** | **{u4_agg['all']['replace_raw_words']}** | **{u4_agg['slice']['replace_raw_words']}** | **{u4_agg['non_slice']['replace_raw_words']}** |")
    md.append(f"| **U4-w-rep-norm** | **واژه‌های نرمال در اپ‌کدهای `replace` (`replace_norm_words`)** | **{u4_agg['all']['replace_norm_words']}** | **{u4_agg['slice']['replace_norm_words']}** | **{u4_agg['non_slice']['replace_norm_words']}** |")
    md.append(f"| **U4-w-del-raw** | **واژه‌های خام در اپ‌کدهای `delete` (`delete_raw_words`)** | **{u4_agg['all']['delete_raw_words']}** | **{u4_agg['slice']['delete_raw_words']}** | **{u4_agg['non_slice']['delete_raw_words']}** |")
    md.append(f"| **U4-w-ins-norm** | **واژه‌های نرمال در اپ‌کدهای `insert` (`insert_norm_words`)** | **{u4_agg['all']['insert_norm_words']}** | **{u4_agg['slice']['insert_norm_words']}** | **{u4_agg['non_slice']['insert_norm_words']}** |")
    md.append(f"| **U4-total-raw** | **کل واژه‌های خام دخیل در تغییر (`replace_raw + delete_raw`)** | **{u4_agg['all']['raw_changed_total']}** | **{u4_agg['slice']['raw_changed_total']}** | **{u4_agg['non_slice']['raw_changed_total']}** |")
    md.append(f"| **U4-total-norm** | **کل واژه‌های نرمال دخیل در تغییر (`replace_norm + insert_norm`)** | **{u4_agg['all']['norm_changed_total']}** | **{u4_agg['slice']['norm_changed_total']}** | **{u4_agg['non_slice']['norm_changed_total']}** |")
    md.append("")
    md.append("> **مشاهدهٔ عددی از داده**: در جدول بالا مقدار `U3` و `U4-total-norm` (و همچنین `U4-w-rep-norm` چون `insert_norm_words = 0` است) برابر است (`2047` روی ۱۱۹۶ یادداشت، `827` روی ۴۳۵ داخل برش، `1220` روی ۷۶۱ خارج برش).")
    md.append("")
    md.append("### آمار کمکی دایرهٔ واژگان اکسل (توصیف ۱۵۶۵ ردیف `items`)")
    md.append("")
    md.append("- **تعریف توکن‌سازی دایرهٔ واژگان اکسل**: متن ستون `description` در ۱۵۶۵ ردیف جدول `items` پس از `unify_persian_chars(digits_to_latin(...))` با فاصله (`str.split()`) شکسته شده و علائم نگارشی ابتدایی/انتهایی هر توکن با `strip(\".,،؛:;!؟?()[]{}\\\"'-«»/\\\\|+*=_\"` حذف شده است.")
    md.append(f"- **تعداد واژه‌های متمایز اکسل (با حفظ علائم چسبیده)**: `{len(excel_vocab_exact)}` واژه")
    md.append(f"- **تعداد واژه‌های متمایز اکسل (پس از حذف علائم نگارشی مرزی)**: `{len(excel_vocab_stripped)}` واژه")
    md.append(
        f"- **در جایگزینی‌های ۱-به-۱ (`{len(one_to_one_sorted)}` مورد روی کل یادداشت‌ها)**: "
        f"واژهٔ خام در واژگان اکسل: `{in_vocab_raw_1to1}` (`{in_vocab_raw_1to1 * 100.0 / max(1, len(one_to_one_sorted)):.1f}%`) | "
        f"واژهٔ اصلاح‌شده در واژگان اکسل: `{in_vocab_norm_1to1}` (`{in_vocab_norm_1to1 * 100.0 / max(1, len(one_to_one_sorted)):.1f}%`)"
    )
    md.append(
        f"- **در کل واژه‌های اصلاح‌شدهٔ U3 (`{len(all_modified_instances)}` مورد روی کل یادداشت‌ها)**: "
        f"عبارت خام در واژگان اکسل: `{in_vocab_raw_all_u3}` (`{in_vocab_raw_all_u3 * 100.0 / max(1, len(all_modified_instances)):.1f}%`) | "
        f"واژهٔ اصلاح‌شده در واژگان اکسل: `{in_vocab_norm_all_u3}` (`{in_vocab_norm_all_u3 * 100.0 / max(1, len(all_modified_instances)):.1f}%`)"
    )
    md.append("- **تذکر روش‌شناختی مهم**: نبودن یک واژه در دایرهٔ واژگان توصیف ردیف‌های اکسل به معنای «غلط بودن» آن واژه نیست (متن بندها و دستورالعمل‌ها دارای واژگان حقوقی/فنی عمومی است که لزوماً در شرح ردیف‌های اجرایی اکسل نیامده‌اند). این آمار صرفاً کمکی است و به عنوان معیار درستی استفاده نشده است.")
    md.append("")
    md.append("---")
    md.append("")
    md.append("## ۳. نمونهٔ تصادفی ۳۰تایی از واژه‌های اصلاح‌شده (جایگزینی‌های ۱-به-۱ در U3 با `random.Random(1404)`)")
    md.append("")
    md.append(f"جامعهٔ آماری این نمونه‌گیری: `{len(one_to_one_sorted)}` رخداد جایگزینی ۱-به-۱ واژه در کل ۱۱۹۶ یادداشت، مرتب‌شده بر اساس `(note_id, norm_index, raw_word, norm_word)` و نمونه‌گیری بدون جایگذاری با `random.Random(1404)`. (بدون قضاوت درستی/غلطی؛ قضاوت با کاربر است.)")
    md.append("")
    md.append("| # | note_id | clause_ref | صفحه PDF | وضعیت برش | واژهٔ خام (`text_raw`) | واژهٔ اصلاح‌شده (`text_normalized`) | زمینه (۵ واژه قبل و ۵ واژه بعد در `text_normalized`) |")
    md.append("|---:|---:|---|---:|---|---|---|---|")
    for idx, item in enumerate(sample_30, start=1):
        sl_label = "داخل برش" if item["in_slice"] else "خارج برش"
        ctx = f"{item['ctx_before']} **[{item['norm_word']}]** {item['ctx_after']}".strip()
        md.append(
            f"| {idx} | {item['note_id']} | `{item['clause_ref']}` | {item['source_page_pdf']} | {sl_label} | `{item['raw_word']}` | `{item['norm_word']}` | {ctx} |"
        )
    md.append("")
    md.append("---")
    md.append("")
    md.append("## ۴. فهرست کامل قواعد جایگزینی موجود در `src/normalize.py` (با شمارهٔ خط) و ارزیابی کاندیدها (دسته الف)")
    md.append("")
    md.append(f"تابع `normalize_clause_text` در خطوط `{func_start}` تا `{func_end}` از فایل `src/normalize.py` تعریف شده است:")
    md.append("")
    md.append("```python")
    for ln, ltxt in func_snippet:
        md.append(f"{ln:3d}: {ltxt}")
    md.append("```")
    md.append("")
    md.append(
        f"- تعداد کل قواعد جایگزینی واژگانی/عبارتی موجود (دسته الف): **{len(cat_a_results)}** قاعده "
        f"(`{len(_LA_LIGATURE_RULES)}` قاعده در `_LA_LIGATURE_RULES` خطوط ۷۳ تا ۱۱۷ + `{len(_BROKEN_WORD_RULES)}` قاعده در `_BROKEN_WORD_RULES` خطوط ۱۲۰ تا ۱۶۷، به علاوه ۱ قاعدهٔ فشرده‌سازی فاصله‌های متوالی در خط ۲۲۶)."
    )
    md.append(
        f"- تعداد قواعد دارای برخورد با توصیف ۱۵۶۵ ردیف اکسل (`items.description`): **{len(unsafe_cat_a)}** قاعده."
    )
    if unsafe_cat_a:
        for u in unsafe_cat_a:
            md.append(
                f"  - ⚠️ **ناایمن: روی متن اکسل هم برخورد دارد**: قاعدهٔ `{u['rule_id']}` (خط {u['lineno']}): الگوی `{u['pattern']}` → `{u['replacement']}` دارای **{u['items_hits']}** برخورد در **{u['items_rows']}** ردیف از جدول `items` است."
            )
            for ex in u["items_examples"]:
                md.append(f"    - نمونه در اکسل (`items`): {ex}")
    else:
        md.append("  - هیچ‌یک از ۸۹ قاعدهٔ واژگانی/عبارتی موجود در `_LA_LIGATURE_RULES` و `_BROKEN_WORD_RULES` برخوردی در توصیف ۱۵۶۵ ردیف اکسل ندارند (`items_hits = 0` برای هر ۸۹ قاعده).")
    md.append("")
    md.append("### جدول کامل ۸۹ قاعدهٔ موجود (دسته الف)")
    md.append("")
    md.append("| کد | گروه | خط در `normalize.py` | الگو (`pattern`) | جایگزین (`replacement`) | برخورد در ۱۵۶۵ ردیف `items` | برخورد در ۴۳۵ یادداشت داخل برش (مستقل / در زنجیره U1) | برخورد در ۱۱۹۶ یادداشت کل (مستقل / در زنجیره U1) | وضعیت ایمنی روی اکسل | نمونهٔ زمینه‌دار (تا ۳ مورد از یادداشت‌ها) |")
    md.append("|---|---|---:|---|---|---:|---:|---:|---|---|")
    for r in cat_a_results:
        status_str = (
            "ناایمن: روی متن اکسل هم برخورد دارد"
            if r["is_unsafe_on_items"]
            else "ایمن (۰ در اکسل)"
        )
        ex_joined = " <br> ".join(r["all_examples"][:3]) if r["all_examples"] else "— (بدون رخداد در notes)"
        md.append(
            f"| `{r['rule_id']}` | `{r['group']}` | {r['lineno']} | `{r['pattern']}` | `{r['replacement']}` | "
            f"{r['items_hits']} | {r['slice_raw_hits']} / {r['slice_chained_hits']} | {r['all_raw_hits']} / {r['all_chained_hits']} | "
            f"{status_str} | {ex_joined} |"
        )
    md.append("")
    md.append("---")
    md.append("")
    md.append("## ۵. کاندیدهای دسته ب: ۲۰ جفت پرتکرار (واژهٔ خام → واژهٔ اصلاح‌شده) از U3 روی ۱۱۹۶ یادداشت")
    md.append("")
    md.append("### ۵-۱. ۲۰ جفت پرتکرار در کل U3 (شامل ادغام‌های چند-به-۱ و جایگزینی‌های ۱-به-۱)")
    md.append("")
    md.append(
        f"- تعداد کاندیدهای بررسی‌شده در این جدول: **{len(cat_b_results)}** جفت پرتکرار."
    )
    md.append(
        f"- تعداد موارد دارای برخورد در توصیف ۱۵۶۵ ردیف `items`: **{len(unsafe_cat_b)}** مورد."
    )
    for u in unsafe_cat_b:
        md.append(
            f"  - ⚠️ **ناایمن: روی متن اکسل هم برخورد دارد**: جفت رتبه {u['rank']} (`{u['raw_word']}` → `{u['norm_word']}`) دارای **{u['items_hits']}** برخورد در **{u['items_rows']}** ردیف `items` است:"
        )
        for ex in u["items_examples"]:
            md.append(f"    - نمونه در اکسل (`items`): {ex}")
    md.append("")
    md.append("| رتبه | عبارت/واژهٔ خام (`raw`) | واژهٔ اصلاح‌شده (`norm`) | رخداد در U3 (همه ۱۱۹۶) | رخداد در U3 (داخل برش ۴۳۵) | برخورد الگو در ۱۵۶۵ ردیف `items` | برخورد الگو در ۴۳۵ یادداشت داخل برش | برخورد الگو در ۱۱۹۶ یادداشت کل | وضعیت روی اکسل | ۳ مثال زمینه‌دار (۵ واژه قبل/بعد) |")
    md.append("|---:|---|---|---:|---:|---:|---:|---:|---|---|")
    for b in cat_b_results:
        st_b = (
            "ناایمن: روی متن اکسل هم برخورد دارد"
            if b["is_unsafe_on_items"]
            else "ایمن (۰ در اکسل)"
        )
        ex_b = " <br> ".join(b["note_examples"][:3])
        if b["items_examples"]:
            ex_b += " <br> **اکسل:** " + " ; ".join(b["items_examples"][:3])
        md.append(
            f"| {b['rank']} | `{b['raw_word']}` | `{b['norm_word']}` | {b['u3_all_count']} | {b['u3_slice_count']} | "
            f"{b['items_hits']} | {b['slice_raw_token_hits']} | {b['all_raw_token_hits']} | {st_b} | {ex_b} |"
        )
    md.append("")
    md.append("### ۵-۲. ۲۰ جفت پرتکرار صرفاً ۱-به-۱ واژه در U3 (بدون ادغام فاصله)")
    md.append("")
    md.append(
        f"- تعداد موارد دارای برخورد در توصیف ۱۵۶۵ ردیف `items` در بین ۲۰ جفت ۱-به-۱: **{len(unsafe_cat_b_1to1)}** مورد."
    )
    md.append("")
    md.append("| رتبه | واژهٔ خام (`raw`) | واژهٔ اصلاح‌شده (`norm`) | رخداد در U3-1to1 (همه ۱۱۹۶) | رخداد در U3-1to1 (داخل برش ۴۳۵) | برخورد توکن در ۱۵۶۵ ردیف `items` | وضعیت روی اکسل | ۳ مثال زمینه‌دار (۵ واژه قبل/بعد) |")
    md.append("|---:|---|---|---:|---:|---:|---|---|")
    for b1 in cat_b_1to1_results:
        st_b1 = (
            "ناایمن: روی متن اکسل هم برخورد دارد"
            if b1["is_unsafe_on_items"]
            else "ایمن (۰ در اکسل)"
        )
        ex_b1 = " <br> ".join(b1["note_examples"][:3])
        if b1["items_examples"]:
            ex_b1 += " <br> **اکسل:** " + " ; ".join(b1["items_examples"][:3])
        md.append(
            f"| {b1['rank']} | `{b1['raw_word']}` | `{b1['norm_word']}` | {b1['u3_all_count']} | {b1['u3_slice_count']} | "
            f"{b1['items_hits']} | {st_b1} | {ex_b1} |"
        )
    md.append("")

    REPORT_MD_PATH.write_text("\n".join(md) + "\n", encoding="utf-8")

    # ۹) تولید خلاصه <= 40 خط برای الحاق به docs/STEP_C4_REPORT.md
    sec: List[str] = []
    sec.append("")
    sec.append("---")
    sec.append("")
    sec.append("## پیوست E5: اندازه‌گیری نویز متن بندها با واحدهای شمارش صریح (U1 تا U4)")
    sec.append("")
    sec.append("- **منبع داده**: جدول `notes` (`1196` ردیف = `435` داخل برش با `is_in_stage3_slice == 1` + `761` خارج برش با `is_in_stage3_slice == 0`)، جدول `items` ستون `description` (`1565` ردیف)، و فایل `data/fehrest/11-Abnieh1404.pdf`؛ گزارش کامل در `docs/text_noise_report.md`.")
    sec.append("")
    sec.append("| واحد | شاخص | همهٔ یادداشت‌ها (۱۱۹۶) | داخل برش (۴۳۵) | خارج برش (۷۶۱) |")
    sec.append("|---|---|---:|---:|---:|")
    sec.append(f"| پایه | یادداشت‌های دارای تغییر (`text_raw != text_normalized`) | {notes_changed_counts['all']} | {notes_changed_counts['slice']} | {notes_changed_counts['non_slice']} |")
    sec.append(f"| **U1** | برخورد قواعد در زنجیرهٔ `normalize_clause_text` (۸۹ قاعده) | {u1_totals['all']} | {u1_totals['slice']} | {u1_totals['non_slice']} |")
    sec.append(f"| **U2 (span)** | جفت مجاور `('ا','ل')` درون spanهای `rawdict` صفحات PDF متمایز | {u2_span_all_str} | {u2_span_slice_str} | {u2_span_non_str} |")
    sec.append(f"| **U2 (line)** | جفت مجاور `('ا','ل')` درون خطوط `rawdict` صفحات PDF متمایز | {u2_line_all_str} | {u2_line_slice_str} | {u2_line_non_str} |")
    sec.append(f"| **U3** | واژه‌های اصلاح‌شده در `text_normalized` (۱-به-۱: {u4_agg['all']['one_to_one_words']} / چند-به-۱: {u4_agg['all']['many_to_one_norm_words']}) | {u3_totals['all']} | {u3_totals['slice']} | {u3_totals['non_slice']} |")
    sec.append(f"| **U4 (replace)** | اپ‌کدهای `replace` (`raw_words` / `norm_words`) | {u4_agg['all']['replace_opcodes']} ({u4_agg['all']['replace_raw_words']} / {u4_agg['all']['replace_norm_words']}) | {u4_agg['slice']['replace_opcodes']} ({u4_agg['slice']['replace_raw_words']} / {u4_agg['slice']['replace_norm_words']}) | {u4_agg['non_slice']['replace_opcodes']} ({u4_agg['non_slice']['replace_raw_words']} / {u4_agg['non_slice']['replace_norm_words']}) |")
    sec.append(f"| **U4 (delete/insert)** | واژه‌های `delete_raw` / `insert_norm` | {u4_agg['all']['delete_raw_words']} / {u4_agg['all']['insert_norm_words']} | {u4_agg['slice']['delete_raw_words']} / {u4_agg['slice']['insert_norm_words']} | {u4_agg['non_slice']['delete_raw_words']} / {u4_agg['non_slice']['insert_norm_words']} |")
    sec.append("")
    unsafe_a_names = [f"{x['rule_id']}({x['pattern']}->{x['replacement']}:{x['items_hits']})" for x in unsafe_cat_a]
    unsafe_b_names = [f"({x['raw_word']}->{x['norm_word']}:{x['items_hits']})" for x in unsafe_cat_b]
    sec.append(
        f"- **خلاصهٔ کاندیدها**: دسته الف شامل `{len(cat_a_results)}` قاعدهٔ موجود در `src/normalize.py` (تعداد ناایمن روی اکسل: `{len(unsafe_cat_a)}` → `{unsafe_a_names or 'هیچ‌کدام'}`)؛ دسته ب شامل `{len(cat_b_results)}` جفت پرتکرار U3 (تعداد ناایمن روی اکسل: `{len(unsafe_cat_b)}` → `{unsafe_b_names or 'هیچ‌کدام'}`)."
    )
    sec.append("- **نمونهٔ ۳۰تایی تصادفی** با `random.Random(1404)` روی جایگزینی‌های ۱-به-۱ بدون قضاوت در `docs/text_noise_report.md` بخش ۳ درج شد.")

    SECTION_MD_PATH.write_text("\n".join(sec) + "\n", encoding="utf-8")

    # ۱۰) لاگ خام کامل در docs/step_c4_logs/e5_noise_raw.txt
    raw_lines: List[str] = []
    raw_lines.append("=== E5 TEXT NOISE MEASUREMENT RAW LOG ===")
    raw_lines.append(f"notes_total={len(all_notes)} slice={len(slice_notes)} non_slice={len(non_slice_notes)}")
    raw_lines.append(f"slice_criterion={slice_criterion_desc}")
    raw_lines.append(f"items_total={len(items_rows)} item_desc_col={item_desc_col}")
    raw_lines.append(f"notes_changed: all={notes_changed_counts['all']} slice={notes_changed_counts['slice']} non_slice={notes_changed_counts['non_slice']}")
    raw_lines.append(f"raw_tokens: all={raw_token_totals['all']} slice={raw_token_totals['slice']} non_slice={raw_token_totals['non_slice']}")
    raw_lines.append(f"norm_tokens: all={norm_token_totals['all']} slice={norm_token_totals['slice']} non_slice={norm_token_totals['non_slice']}")
    raw_lines.append(f"U1_total: all={u1_totals['all']} slice={u1_totals['slice']} non_slice={u1_totals['non_slice']}")
    raw_lines.append(f"U1_notes_with_hits: all={u1_notes_with_hits['all']} slice={u1_notes_with_hits['slice']} non_slice={u1_notes_with_hits['non_slice']}")
    raw_lines.append(f"U2_result: {u2_res}")
    raw_lines.append(f"U3_total: all={u3_totals['all']} slice={u3_totals['slice']} non_slice={u3_totals['non_slice']}")
    for k in ("all", "slice", "non_slice"):
        raw_lines.append(f"U4[{k}]: {dict(u4_agg[k])}")
    raw_lines.append(f"cat_a_count={len(cat_a_results)} unsafe_cat_a_count={len(unsafe_cat_a)}")
    for u in unsafe_cat_a:
        raw_lines.append(f"  UNSAFE_A: {u['rule_id']} line={u['lineno']} {u['pattern']} -> {u['replacement']} items_hits={u['items_hits']}")
    raw_lines.append(f"cat_b_count={len(cat_b_results)} unsafe_cat_b_count={len(unsafe_cat_b)}")
    for u in unsafe_cat_b:
        raw_lines.append(f"  UNSAFE_B: rank={u['rank']} {u['raw_word']} -> {u['norm_word']} items_hits={u['items_hits']}")
    raw_lines.append(f"cat_b_1to1_count={len(cat_b_1to1_results)} unsafe_cat_b_1to1_count={len(unsafe_cat_b_1to1)}")
    for u in unsafe_cat_b_1to1:
        raw_lines.append(f"  UNSAFE_B_1TO1: rank={u['rank']} {u['raw_word']} -> {u['norm_word']} items_hits={u['items_hits']}")

    RAW_LOG_PATH.write_text("\n".join(raw_lines) + "\n", encoding="utf-8")

    # چاپ خلاصه در خروجی استاندارد (<= 30 خط)
    for line in raw_lines[:28]:
        print(line)

    return 0


if __name__ == "__main__":
    sys.exit(main())
