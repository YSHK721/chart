"""MT5 fixture ローダ（backtest.tests.fixtures.mt5.load_case）の単体テスト。

warmup CSV 対応の回帰固定:
    input/ に「取引期間 CSV」と「ウォームアップ込み CSV（フル期間レンジ命名）」が
    併存する場合に、ローダが両者を決定論的に弁別することを固定する。
    - input_csv  = 取引期間 CSV（warmup を含まない正準データ・既存契約を不変に保つ）
    - warmup_csv = ウォームアップ込み CSV（指標 seed 収束用・無ければ None）

弁別基準（決定論）:
    ファイル名末尾が `_<12桁>_<12桁>.csv`（開始/終了の完全タイムスタンプ範囲命名）の
    ものを warmup CSV とみなす。それ以外を取引期間 CSV とする。
"""
from __future__ import annotations

from backtest.tests.fixtures.mt5 import load_case

_CASE = "ma_slope_jp225_202501"


def test_input_csv_is_trading_period_not_warmup():
    # Act
    case = load_case(_CASE)
    # Assert: input_csv は取引期間 CSV（フル期間レンジ命名でない）。
    assert case.input_csv.name == "JP225_M1_202501.csv"


def test_warmup_csv_is_full_range_named_csv():
    # Act
    case = load_case(_CASE)
    # Assert: warmup_csv はウォームアップ込み CSV（`_<12桁>_<12桁>.csv` 命名）。
    assert case.warmup_csv is not None
    assert case.warmup_csv.name == "JP225_M1_202412230100_202501302359.csv"
