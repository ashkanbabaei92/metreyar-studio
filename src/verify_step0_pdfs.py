#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
اسکریپت راستی‌آزمایی مرحله ۰ - بخش الف-۲:
بررسی تمام فایل‌های PDF در پوشه data/ (تعداد صفحات، متنی یا اسکن بودن، نمونه متن) و فایل Word 4311
"""

import os
import glob
import pymupdf as fitz

pdf_files = sorted(glob.glob("data/**/*.pdf", recursive=True))
print(f"تعداد کل فایل‌های PDF یافت‌شده: {len(pdf_files)}\n")

for p in pdf_files:
    doc = fitz.open(p)
    num_pages = len(doc)
    pages_with_text = 0
    total_chars = 0
    total_images = 0
    for page in doc:
        txt = page.get_text("text").strip()
        if len(txt) > 20:
            pages_with_text += 1
        total_chars += len(txt)
        total_images += len(page.get_images())
    
    status = "متنی (Text-based)" if pages_with_text > (num_pages * 0.5) else (
        "اسکن کامل (Scanned - بدون لایه متنی)" if pages_with_text == 0 else f"ترکیبی ({pages_with_text}/{num_pages} صفحه متنی)"
    )
    print(f"فایل: {os.path.basename(p)}")
    print(f"  مسیر: {p}")
    print(f"  حجم: {os.path.getsize(p):,} بایت")
    print(f"  تعداد صفحات: {num_pages}")
    print(f"  صفحات دارای متن (>20 کاراکتر): {pages_with_text} از {num_pages}")
    print(f"  مجموع کاراکترهای لایه متنی: {total_chars:,} | مجموع تصاویر داخل صفحات: {total_images}")
    print(f"  وضعیت: {status}")
    print("-" * 70)
    doc.close()

# بررسی فایل 4311-word.doc
doc_files = sorted(glob.glob("data/**/*.doc*", recursive=True))
for d in doc_files:
    print(f"فایل Word: {os.path.basename(d)} | مسیر: {d} | حجم: {os.path.getsize(d):,} بایت")
    # بررسی فرمت واقعی فایل با خواندن هدر باینری
    with open(d, "rb") as f:
        header = f.read(16)
    print(f"  هدر باینری فایل: {header[:8].hex()} (OLE2/CFB = d0cf11e0a1b11ae1)")
