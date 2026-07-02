"""pytest 共有設定（テスト基盤）。

api/ ディレクトリを import パスへ追加し、各テストの `sys.path.insert` を排除する。
this file: api/tests/conftest.py → parents[1] = api/。
"""

import sys
from pathlib import Path

_API_DIR = str(Path(__file__).resolve().parents[1])
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)


def pytest_configure(config):
    """カスタムマーカーを登録する（未登録警告の抑止）。

    slow: 実データ依存・初回集計が重い統合テスト（環境に実データが無ければ skip）。
    """
    config.addinivalue_line("markers", "slow: 実データ依存で重い統合テスト（skip 可能）")
