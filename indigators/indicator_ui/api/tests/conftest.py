"""pytest 共有設定（テスト基盤）。

api/ ディレクトリを import パスへ追加し、各テストの `sys.path.insert` を排除する。
this file: api/tests/conftest.py → parents[1] = api/。
"""

import sys
from pathlib import Path

_API_DIR = str(Path(__file__).resolve().parents[1])
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

# ISSUE-183（DIP）: テストセッションの Composition Root。本番の framework/server.py と同様に、
#   usecase の Output Boundary（DatasetPort）へ既定 factory を 1 回登録する。これによりポート側の
#   「未注入なら adapter を pull する」遅延 import（内側 → 外側の逆流）を撤去しても、未注入時の
#   既定合成という従来挙動がテスト経路でも変わらない。
from adapter.gateway.composition import install_default_ports as _install_default_ports  # noqa: E402

_install_default_ports()


def pytest_configure(config):
    """カスタムマーカーを登録する（未登録警告の抑止）。

    slow: 実データ依存・初回集計が重い統合テスト（環境に実データが無ければ skip）。
    """
    config.addinivalue_line("markers", "slow: 実データ依存で重い統合テスト（skip 可能）")
