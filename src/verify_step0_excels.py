#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
اسکریپت راستی‌آزمایی مرحله ۰ - بخش الف-۱:
بررسی دقیق ۳ فایل اکسل فهرست بها (abn1404.xlsx, mec1404.xlsx, elec1404.xlsx)
"""

import os
import openpyxl
import pandas as pd

EXCEL_FILES = [
    ("ابنیه ۱۴۰۴", "data/فهرست‌بها (پایه محاسبات متره و برآورد)/abn1404.xlsx"),
    ("مکانیک ۱۴۰۴", "data/فهرست‌بها (پایه محاسبات متره و برآورد)/mec1404.xlsx"),
    ("برق ۱۴۰۴", "data/فهرست‌بها (پایه محاسبات متره و برآورد)/elec1404.xlsx"),
]

for label, path in EXCEL_FILES:
    print("=" * 80)
    print(f"بررسی فایل: {label} | مسیر: {path}")
    print(f"حجم فایل: {os.path.getsize(path):,} بایت")
    
    wb = openpyxl.load_workbook(path, data_only=True)
    print(f"نام شیت‌ها ({len(wb.sheetnames)}): {wb.sheetnames}")
    
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        print(f"\n--- شیت: {sheet_name} (max_row={ws.max_row}, max_column={ws.max_column}) ---")
        
        # خواندن با pandas برای تحلیل ستون‌ها
        df = pd.read_excel(path, sheet_name=sheet_name, dtype=object)
        print(f"ستون‌های دیتافریم ({len(df.columns)}): {list(df.columns)}")
        
        # بررسی ستون‌های Unnamed یا اضافی
        for col in df.columns:
            non_null = df[col].dropna()
            print(f"  - ستون '{col}': تعداد مقادیر غیرخالی = {len(non_null)}")
            if "Unnamed" in str(col) and len(non_null) > 0:
                print(f"    مقادیر غیرخالی در '{col}':")
                for idx, val in non_null.items():
                    print(f"      سطر اکسل {idx+2}: {repr(val)}")
        
        # فرض اینکه ۴ ستون اول: ردیف، شرح، واحد، بهای واحد هستند
        cols = list(df.columns[:4])
        c_code, c_desc, c_unit, c_price = cols[0], cols[1], cols[2], cols[3]
        
        # بررسی انواع داده در ستون کد ردیف
        codes_raw = df[c_code]
        total_rows = len(df)
        non_empty_code_rows = df[df[c_code].notna()]
        print(f"\nتعداد کل سطرهای دیتافریم (بدون هدر): {total_rows}")
        print(f"تعداد سطرهای دارای کد ردیف: {len(non_empty_code_rows)}")
        
        # بررسی نوع داده کدها و طول آن‌ها
        code_types = non_empty_code_rows[c_code].apply(lambda x: type(x).__name__).value_counts().to_dict()
        code_lengths = non_empty_code_rows[c_code].apply(lambda x: len(str(x).strip())).value_counts().to_dict()
        print(f"نوع داده سلول‌های کد ردیف در اکسل: {code_types}")
        print(f"طول رشته‌ای کدهای ردیف (قبل از نرمال‌سازی): {code_lengths}")
        
        # بررسی کدهای تکراری
        code_strs = non_empty_code_rows[c_code].apply(lambda x: str(x).strip())
        dup_codes = code_strs[code_strs.duplicated(keep=False)]
        print(f"تعداد کدهای تکراری: {len(dup_codes)}")
        if len(dup_codes) > 0:
            print(f"کدهای تکراری: {dup_codes.to_dict()}")
            
        # بررسی قیمت‌ها
        def parse_price(val):
            if pd.isna(val):
                return None
            s = str(val).strip().replace(",", "").replace("،", "")
            if s == "" or s == "-" or s == "*":
                return 0
            try:
                return float(s)
            except ValueError:
                return f"NON_NUMERIC:{s}"
                
        prices_parsed = non_empty_code_rows[c_price].apply(parse_price)
        priced_count = prices_parsed.apply(lambda x: isinstance(x, (int, float)) and x > 0).sum()
        zero_or_empty_price_mask = prices_parsed.apply(lambda x: x is None or x == 0 or (isinstance(x, str) and x.startswith("NON_NUMERIC")))
        zero_or_empty_rows = non_empty_code_rows[zero_or_empty_price_mask]
        
        print(f"تعداد ردیف‌های دارای قیمت (>0): {priced_count}")
        print(f"تعداد ردیف‌های قیمت‌صفر / بدون قیمت / ستاره‌دار / غیرعددی: {len(zero_or_empty_rows)}")
        if len(zero_or_empty_rows) > 0:
            print("فهرست کامل ردیف‌های بدون قیمت عددی مثبت:")
            for idx, r in zero_or_empty_rows.iterrows():
                print(f"  سطر اکسل {idx+2}: کد={repr(r[c_code])} | واحد={repr(r[c_unit])} | قیمت={repr(r[c_price])} | شرح={str(r[c_desc])[:70]}...")
                
        # بررسی ردیف‌های بدون واحد
        missing_unit_mask = non_empty_code_rows[c_unit].isna() | (non_empty_code_rows[c_unit].astype(str).str.strip() == "")
        missing_unit_rows = non_empty_code_rows[missing_unit_mask]
        print(f"تعداد ردیف‌های بدون واحد: {len(missing_unit_rows)}")
        if len(missing_unit_rows) > 0:
            print("فهرست کامل ردیف‌های بدون واحد:")
            for idx, r in missing_unit_rows.iterrows():
                print(f"  سطر اکسل {idx+2}: کد={repr(r[c_code])} | واحد={repr(r[c_unit])} | قیمت={repr(r[c_price])} | شرح={str(r[c_desc])[:80]}")
                
        # بررسی ردیف‌های ستاره‌دار در شرح یا کد یا قیمت
        star_mask = (
            non_empty_code_rows[c_code].astype(str).str.contains(r"\*", na=False) |
            non_empty_code_rows[c_desc].astype(str).str.contains(r"\*", na=False) |
            non_empty_code_rows[c_price].astype(str).str.contains(r"\*", na=False)
        )
        star_rows = non_empty_code_rows[star_mask]
        print(f"تعداد ردیف‌های حاوی کاراکتر '*' در کد/شرح/قیمت: {len(star_rows)}")
        for idx, r in star_rows.iterrows():
            print(f"  سطر اکسل {idx+2}: کد={repr(r[c_code])} | واحد={repr(r[c_unit])} | قیمت={repr(r[c_price])} | شرح={str(r[c_desc])[:80]}")

        print("\n۱۰ ردیف اول:")
        for idx, r in non_empty_code_rows.head(10).iterrows():
            print(f"  [{idx+2}] کد={repr(r[c_code])} | واحد={repr(r[c_unit])} | قیمت={repr(r[c_price])} | شرح={str(r[c_desc])[:65]}")
            
        print("\n۱۰ ردیف آخر:")
        for idx, r in non_empty_code_rows.tail(10).iterrows():
            print(f"  [{idx+2}] کد={repr(r[c_code])} | واحد={repr(r[c_unit])} | قیمت={repr(r[c_price])} | شرح={str(r[c_desc])[:65]}")
