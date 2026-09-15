# -*- coding: utf-8 -*-
"""scraper/ 모듈을 import 할 수 있도록 sys.path 에 추가한다."""
import sys
from pathlib import Path

SCRAPER_DIR = Path(__file__).resolve().parent.parent
if str(SCRAPER_DIR) not in sys.path:
    sys.path.insert(0, str(SCRAPER_DIR))
