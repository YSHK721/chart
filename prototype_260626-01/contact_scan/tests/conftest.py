"""pytest 共通設定: contact_scan パッケージと（slow 時のみ）API 根を import 可能にする。"""
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent              # contact_scan/tests/
_PROTO = _HERE.parents[1]                             # prototype_260626-01/（contact_scan の親）
_REPO = _HERE.parents[2]                              # repo 根
_API = _REPO / "indigators" / "indicator_ui" / "api"

for _p in (str(_PROTO), str(_API), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: 実データ（parquet/CSV）を要する重いテスト")
