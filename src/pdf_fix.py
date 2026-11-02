#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ماژول اصلاح در حافظه جداول /ToUnicode فونت‌های معیوب PDF و استخراج راست‌به‌چپ (pdf_fix.py).
منتقل و تکمیل‌شده از verify_step0_full.py برای استفاده مشترک در مرحله ۲ (extractor) و مرحله ۳.

فونت‌های معیوب شناسایی‌شده در 11-Abnieh1404.pdf:
1. B Lotus (XREF 329 -> ToUnicode 47171): گلیف 0x000E (صفر) به <0033> ('3') نگاشت شده بود.
2. B Lotus,Bold (XREF 337 -> ToUnicode 47176): گلیف‌های 0x000E تا 0x0017 (ارقام 0 تا 9) به
   [<0031> <0034> <0032> <0033> <0031> <0035>...] نگاشت شده بودند (جای ۱ و ۴ عوض شده بود).
3. ABCDEE+HM FLotoos (XREF 324 -> ToUnicode 47168): گلیف 0x01D9 (صفر) به <0031> ('1')
   و گلیف 0x01DC (جداکننده هزارگان) به <06F9> ('۹') نگاشت شده بود.
4. ABCEEE+HM FLotoos (XREF 426 -> ToUnicode 47197): زیرفونت جداگانه که گلیف‌های 0x01D0 تا 0x01D7
   را به ارقام فارسی نادرست (<06F4>, <06F3>, <06F1>, <06F8>) نگاشت کرده بود.
5. ABCDEE+HM FLotoos,Bold (XREF 17 -> ToUnicode 47160): گلیف‌های 0x01D0 تا 0x01D9 به ارقام نادرست
   نگاشت شده بودند (مثلاً سال 1404 را 1444 نشان می‌داد).
"""

import re
from pathlib import Path
from typing import List, Union
import pymupdf as fitz


def patch_pdf_tounicode_in_memory(doc: fitz.Document) -> int:
    """
    اصلاح در حافظه (In-Memory) جداول /ToUnicode برای تمام فونت‌های خانواده
    B Lotus (شامل B Lotus و B Lotus,Bold) و HM FLotoos (شامل ABCDEE+HM FLotoos،
    ABCDEE+HM FLotoos,Bold و زیرفونت ABCEEE+HM FLotoos).

    این تابع فایل PDF روی دیسک را به هیچ عنوان تغییر نمی‌دهد.
    تعداد استریم‌های ToUnicode اصلاح‌شده را برمی‌گرداند.
    """
    patched_count = 0
    for xr in range(1, doc.xref_length()):
        if not doc.xref_is_font(xr):
            continue
        obj = doc.xref_object(xr)
        if not any(fn in obj for fn in ["Lotus", "FLotoos"]):
            continue
        m = re.search(r"/ToUnicode\s+(\d+)\s+0\s+R", obj)
        if not m:
            continue
        tu_xr = int(m.group(1))
        st = doc.xref_stream(tu_xr).decode("latin1", errors="replace")

        if "Lotus" in obj:
            # شامل B Lotus و B Lotus,Bold:
            # گلیف‌های 0x000E تا 0x0017 دقیقاً ارقام 0 تا 9 هستند
            # گلیف 0x0018 ممیز '/' و گلیف 0x0019 علامت '=' است
            patch = (
                "\n1 beginbfrange\n<000E> <0017> <0030>\nendbfrange\n"
                "2 beginbfchar\n<0018> <002F>\n<0019> <003D>\nendbfchar\n"
            )
            st = st.replace("endcmap", patch + "endcmap")
            doc.update_stream(tu_xr, st.encode("latin1"))
            patched_count += 1
        elif "FLotoos" in obj:
            # شامل ABCDEE+HM FLotoos، ABCDEE+HM FLotoos,Bold و ABCEEE+HM FLotoos:
            # گلیف‌های 0x01D0 تا 0x01D8 دقیقاً ارقام 1 تا 9 هستند
            # گلیف 0x01D9 رقم 0، گلیف 0x01DB ممیز '/' و گلیف 0x01DC جداکننده هزارگان ',' است
            patch = (
                "\n1 beginbfrange\n<01D0> <01D8> <0031>\nendbfrange\n"
                "3 beginbfchar\n<01D9> <0030>\n<01DB> <002F>\n<01DC> <002C>\nendbfchar\n"
            )
            st = st.replace("endcmap", patch + "endcmap")
            doc.update_stream(tu_xr, st.encode("latin1"))
            patched_count += 1

    return patched_count


def open_patched_pdf(pdf_path: Union[str, Path]) -> fitz.Document:
    """
    باز کردن فایل PDF و اعمال پچ ToUnicode در حافظه به صورت خودکار.
    """
    doc = fitz.open(str(pdf_path))
    patch_pdf_tounicode_in_memory(doc)
    return doc


def extract_rtl_page_lines(page: fitz.Page, y_tolerance: float = 4.5) -> List[str]:
    """
    استخراج خطوط متنی یک صفحه PDF با مرتب‌سازی مکانی از بالا به پایین (محور Y)
    و از راست به چپ (محور X نزولی)، به همراه اصلاح ممیز اعشاری فارسی (مانند 30/1 -> 1.30).
    """
    # بارگذاری مجدد صفحه برای اطمینان از اعمال شدن پچ ToUnicode
    doc = page.parent
    if doc is not None:
        page = doc.reload_page(page)

    words = page.get_text("words")
    lines = []
    for w in sorted(words, key=lambda x: (round(x[1] / 4.0) * 4.0, -x[0])):
        x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4]
        if not lines or abs(lines[-1]["y"] - y0) > y_tolerance:
            lines.append({"y": y0, "words": [(x0, text)]})
        else:
            lines[-1]["words"].append((x0, text))

    out: List[str] = []
    for l in lines:
        w_sorted = [t for _, t in sorted(l["words"], key=lambda z: -z[0])]
        line_str = " ".join(w_sorted)
        # اصلاح الگوی ممیز فارسی که در لایه متنی به شکل 30/1 یا 30 / 1 ذخیره شده است
        line_str = re.sub(
            r"\b(\d+)\s*/\s*(\d+)\b",
            lambda m: f"{m.group(2)}.{m.group(1)}",
            line_str,
        )
        out.append(line_str)
    return out
