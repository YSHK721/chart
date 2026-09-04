"""make_regression_fixture — **本体へ移設済み**（ISSUE-479 Wave2 フェーズ 1-D）。

移設先: `simulator/tools/regenerate_account_engine_fixtures.py`

なぜ移したか:
    回帰ゲート（`simulator/tests/unit/test_account_engine_regression.py`）が読む固定値の
    生成器が試作の中に居ると、レビューも回帰ゲートも通らないコードが**ゲートの期待値
    そのもの**を作ることになる。加えて本ファイルは
    `simulator/tests/fixtures/account_engine/` を直接上書きしており、試作の実行が
    共有資産の破壊になり得た（`tools/tests/test_prototype_write_isolation.py` が
    構文木で禁じている X-3a）。

    ゲートシナリオの定義はリポジトリ内で 1 箇所だけに存在する。
    ここに複写を残すと、fixture の由来と検定が別々の定義を持つことになる。

再生成の手順:
    MARKETDATA_DATA_DIR=... <venv python> simulator/tools/regenerate_account_engine_fixtures.py

本ファイルは試作の記録として残す（削除は承認事項）。実行しても何もしない。
"""

from __future__ import annotations

MOVED_TO = "simulator/tools/regenerate_account_engine_fixtures.py"

if __name__ == "__main__":
    raise SystemExit(f"本体へ移設済みです。{MOVED_TO} を実行してください。")
