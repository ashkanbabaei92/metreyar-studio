# -*- coding: utf-8 -*-
"""تنظیم مسیر ریشه پروژه برای اجرای مستقیم pytest روی پوشه tests/."""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
