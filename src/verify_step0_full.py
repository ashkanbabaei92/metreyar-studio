#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
اسکریپت جامع راستی‌آزمایی مرحله ۰ (گام صفر و گام الف)
اجرا روی فایل‌های واقعی پوشه data/ بدون هیچ‌گونه حدس یا داده حافظه‌ای.
"""

import os
import re
import glob
import openpyxl
import pandas as pd
import pymupdf as fitz


def patch_pdf_tounicode_in_memory(doc: fitz.Document) -> int:
    """
    اصلاح قطعی جداول ToUnicode فونت‌های معیوب (B Lotus و HM FLotoos) در حافظه
    بر اساس جدول Glyph ID واقعی فونت‌های توکار PDF فهرست بها ۱۴۰۴.
    """
    patched_count = 0
    for xr in range(1, doc.xref_length()):
        if doc.xref_is_font(xr):
            obj = doc.xref_object(xr)
            if any(fn in obj for fn in ["Lotus", "FLotoos"]):
                m = re.search(r"/ToUnicode\s+(\d+)\s+0\s+R", obj)
                if m:
                    tu_xr = int(m.group(1))
                    st = doc.xref_stream(tu_xr).decode("latin1", errors="replace")
                    if "Lotus" in obj:
                        # در فونت B Lotus: گلیف‌های 0x000E تا 0x0017 متناظر ارقام 0 تا 9 هستند
                        patch = (
                            "\n1 beginbfrange\n<000E> <0017> <0030>\nendbfrange\n"
                            "2 beginbfchar\n<0018> <002F>\n<0019> <003D>\nendbfchar\n"
                        )
                        st = st.replace("endcmap", patch + "endcmap")
                        doc.update_stream(tu_xr, st.encode("latin1"))
                        patched_count += 1
                    elif "FLotoos" in obj:
                        # در فونت HM FLotoos: گلیف‌های 0x01D0 تا 0x01D8 متناظر ارقام 1 تا 9
                        # و گلیف 0x01D9 متناظر رقم 0 و 0x01DC جداکننده هزارگان (،) است
                        patch = (
                            "\n1 beginbfrange\n<01D0> <01D8> <0031>\nendbfrange\n"
                            "3 beginbfchar\n<01D9> <0030>\n<01DB> <002F>\n<01DC> <002C>\nendbfchar\n"
                        )
                        st = st.replace("endcmap", patch + "endcmap")
                        doc.update_stream(tu_xr, st.encode("latin1"))
                        patched_count += 1
    return patched_count


def extract_rtl_page_lines(page: fitz.Page, y_tolerance: float = 4.5) -> list[str]:
    """
    مرتب‌سازی مکانی کلمات صفحه از بالا به پایین (محور Y) و راست به چپ (محور X نزولی)
    و اصلاح اعشار ممیز فارسی که در لایه متنی به شکل 30/1 ذخیره شده‌اند.
    """
    words = page.get_text("words")
    lines = []
    for w in sorted(words, key=lambda x: (round(x[1] / 4.0) * 4.0, -x[0])):
        x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4]
        if not lines or abs(lines[-1]["y"] - y0) > y_tolerance:
            lines.append({"y": y0, "words": [(x0, text)]})
        else:
            lines[-1]["words"].append((x0, text))
    out = []
    for l in lines:
        w_sorted = [t for _, t in sorted(l["words"], key=lambda z: -z[0])]
        line_str = " ".join(w_sorted)
        # اصلاح الگوی ممیز فارسی معکوس در متن (مانند 30 /1 -> 1/30 یا 2/1 -> 1/2)
        line_str = re.sub(r"\b(\d+)\s*/\s*(\d+)\b", lambda m: f"{m.group(2)}.{m.group(1)}", line_str)
        out.append(line_str)
    return out


if __name__ == "__main__":
    print("اجرای کامل اسکریپت راستی‌آزمایی مرحله ۰...")
    doc = fitz.open("data/فهرست‌بها (پایه محاسبات متره و برآورد)/11-Abnieh1404.pdf")
    patched = patch_pdf_tounicode_in_memory(doc)
    print(f"تعداد جداول ToUnicode اصلاح‌شده در حافظه برای 11-Abnieh1404.pdf: {patched}")
    doc.reload_page(doc[4])
    print("\n--- خروجی اصلاح‌شده صفحه ۵ PDF (صفحه ۲ بولتن - ضرایب بالاسری و جدول الف) ---")
    for line in extract_rtl_page_lines(doc[4]):
        print(" ", line)
