"""pytest 共有設定（market_profile モジュールのテスト基盤）。

MP backend は固有名トップパッケージ ``market_profile_api`` を持ち、共有インフラ
（``adapter.compute`` の dataset/forming_bar/ERROR_STATUS・``framework.server``）は
indicator_ui の ``api/`` を sys.path 経由で参照する。両 sys.path 根＋repo 根（marketdata 用）を
import パスへ追加する（server.py・api_loader.py と同じ結線機構）。

this file: indigators/market_profile/api/tests/conftest.py
  parents[1] = indigators/market_profile/api      （market_profile_api の解決）
  parents[4] = /workspaces/app                     （marketdata の解決）
  parents[4]/indigators/indicator_ui/api           （adapter.* / framework.* の解決）
"""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_MP_API_DIR = str(_HERE.parents[1])
_REPO_ROOT = _HERE.parents[4]
_INDICATOR_UI_API_DIR = str(_REPO_ROOT / "indigators" / "indicator_ui" / "api")

for _p in (_MP_API_DIR, _INDICATOR_UI_API_DIR, str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def pytest_configure(config):
    """カスタムマーカーを登録する（未登録警告の抑止・indicator_ui conftest と同方針）。

    slow: 実データ依存・初回集計が重い統合テスト（環境に実データが無ければ skip）。
    """
    config.addinivalue_line("markers", "slow: 実データ依存で重い統合テスト（skip 可能）")
