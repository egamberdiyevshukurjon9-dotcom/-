# -*- coding: utf-8 -*-
"""Тестлар учун app/ папкасини import йўлига қўшади."""
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
