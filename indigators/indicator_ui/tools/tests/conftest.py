"""pytest 共有設定（tools のテスト基盤）。

tools/ ディレクトリを import パスへ追加し、各テストの ``sys.path.insert`` を排除する。
this file: tools/tests/conftest.py → parents[1] = tools/。

ISSUE-093: 自スライスの api ルート（indicator_ui/api）も結線する。test_rollup_builder が
`from adapter.compute import dataset` を用いるが、汎用名パッケージ `adapter` は .pth に
載せない方針（スライス間衝突・tools/install_dev_paths.py 参照）のため、server.py / bridge と
同様に「自スライスのエントリが自分の root を結線する」規約に従う。これが無いと本スイートは
単独実行で collection error となり、失敗が恒常的に見逃される（ISSUE-093 の実態）。
"""

import sys
from pathlib import Path

_TOOLS_DIR = str(Path(__file__).resolve().parents[1])
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

_API_DIR = str(Path(__file__).resolve().parents[2] / "api")
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)
