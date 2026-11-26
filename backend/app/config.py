from __future__ import annotations
import os
from pathlib import Path

PROJECT_NAME = "发版闸"
ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get("SHIPGATE_DB", str(ROOT / "data" / "shipgate.db")))
