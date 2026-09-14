"""dataset の読み取り時 外れ値バー補正（per-bar OHLC クランプ）の検証。

背景（実データ不良）: ``jp225_tick`` 1D の 2025-08-26 で intraday の atomic 不良値
(~15,099) が日足の安値を異常に引き下げる（O/H/C は ~42,000 台）。``load_dataframe``
の最終返却前に、各行(バー)の OHLC を読み取り時クランプして補正する（ソースは不変・
返す DataFrame のコピー上でのみ補正）。

補正規約（既存 ``tools/export_jp225_m1.repair_outlier_rows`` の threshold=0.3 に整合）:
  - ``ref_lo=min(open,close)`` / ``ref_hi=max(open,close)``（open/close は外れにくい）。
  - ``low < ref_lo*(1-threshold)`` なら low を ref_lo にクランプ（下ヒゲ外れ）。
  - ``high > ref_hi*(1+threshold)`` なら high を ref_hi にクランプ（上ヒゲ外れ）。
  - threshold 既定 0.3（±30%）。正常バー（±30% 以内のヒゲ）は完全に不変（no-op）。
  - open/close が 0/NaN、low/high が NaN のバーは防御的にスキップする。

補正対象は実市場 ref（jp225 系）に限定する（sample 等の合成 golden を壊さない）。
"""

from __future__ import annotations

import csv as _csv
import os as _os

import numpy as np
import pandas as pd
import pytest

from adapter.compute import dataset


# --------------------------------------------------------------------------- #
# ヘルパ: 合成 OHLC DataFrame（date index）。
# --------------------------------------------------------------------------- #
def _mkdf(rows):
    # rows: [(date, open, high, low, close), ...]
    idx = pd.to_datetime([r[0] for r in rows])
    data = {
        "open": [r[1] for r in rows],
        "high": [r[2] for r in rows],
        "low": [r[3] for r in rows],
        "close": [r[4] for r in rows],
    }
    df = pd.DataFrame(data, index=idx)
    df.index.name = "date"
    return df


_MARKET_REF = "jp225_tick"  # クランプ対象の実市場 ref


# --------------------------------------------------------------------------- #
# 回帰: 下ヒゲ外れ（8/26 相当・low が open/close の -64%）→ low を ref_lo にクランプ。
# --------------------------------------------------------------------------- #
def test_clamp_low_outlier_clamps_low_to_ref_lo_and_leaves_others():
    # Arrange: 2 行目 low=15098 は open/close(~42000) の約 -64%（8/26 相当）。
    df = _mkdf([
        ("2025-08-25", 43076.97, 43199.07, 42527.94, 42650.00),  # 正常（不変）
        ("2025-08-26", 42642.89, 42705.29, 15098.53, 42476.68),  # 下ヒゲ外れ
    ])
    # Act
    out = dataset._clamp_outlier_bars(df, _MARKET_REF)
    # Assert: 外れ行の low は ref_lo=min(open,close)=42476.68 にクランプ。
    assert out["low"].iloc[1] == pytest.approx(42476.68)
    # 正常行は完全不変。
    assert out["low"].iloc[0] == pytest.approx(42527.94)
    # OHLC の他要素（open/high/close）は不変。
    assert out["high"].iloc[1] == pytest.approx(42705.29)
    assert out["open"].iloc[1] == pytest.approx(42642.89)
    assert out["close"].iloc[1] == pytest.approx(42476.68)


def test_clamp_does_not_mutate_source_df():
    # ソース df（＝返す df の元）を破壊しない（.copy() 上でのみ補正）。
    df = _mkdf([
        ("2025-08-26", 42642.89, 42705.29, 15098.53, 42476.68),
    ])
    _ = dataset._clamp_outlier_bars(df, _MARKET_REF)
    # 元 df の low は補正されず生値のまま。
    assert df["low"].iloc[0] == pytest.approx(15098.53)


# --------------------------------------------------------------------------- #
# 回帰: 上ヒゲ外れ → high を ref_hi にクランプ。
# --------------------------------------------------------------------------- #
def test_clamp_high_outlier_clamps_high_to_ref_hi():
    # Arrange: high=90000 は open/close(~42000) の +100% 超（上ヒゲ外れ）。
    df = _mkdf([
        ("2025-08-26", 42642.89, 90000.00, 42400.00, 42476.68),
    ])
    out = dataset._clamp_outlier_bars(df, _MARKET_REF)
    # high は ref_hi=max(open,close)=42642.89 にクランプ。low は正常なので不変。
    assert out["high"].iloc[0] == pytest.approx(42642.89)
    assert out["low"].iloc[0] == pytest.approx(42400.00)


# --------------------------------------------------------------------------- #
# no-op: ±30% 以内のヒゲは完全に不変（正常バーに無影響）。
# --------------------------------------------------------------------------- #
def test_clamp_within_threshold_is_noop_returns_same_object():
    # low=ちょうど -30%（境界内）、high=ちょうど +30%（境界内）は不変。
    # ref_lo=ref_hi=100（open=close=100）→ 下限=70, 上限=130。
    df = _mkdf([
        ("2025-01-01", 100.0, 130.0, 70.0, 100.0),  # 境界ちょうど（不変）
        ("2025-01-02", 100.0, 129.0, 71.0, 100.0),  # 境界内（不変）
    ])
    out = dataset._clamp_outlier_bars(df, _MARKET_REF)
    # 変更が無いとき同一オブジェクトを返す（コピーせず・キャッシュ非破壊）。
    assert out is df
    assert out["low"].iloc[0] == pytest.approx(70.0)
    assert out["high"].iloc[0] == pytest.approx(130.0)


# --------------------------------------------------------------------------- #
# 対象限定: 非市場 ref（sample 等）は外れ値があっても補正しない（golden 非破壊）。
# --------------------------------------------------------------------------- #
def test_clamp_skips_non_market_ref():
    df = _mkdf([
        ("2025-08-26", 42642.89, 90000.00, 15098.53, 42476.68),  # 上下とも外れ
    ])
    out = dataset._clamp_outlier_bars(df, "sample")
    # 非対象 ref は同一オブジェクト・生値のまま。
    assert out is df
    assert out["low"].iloc[0] == pytest.approx(15098.53)
    assert out["high"].iloc[0] == pytest.approx(90000.00)


# --------------------------------------------------------------------------- #
# 防御: open/close が 0/NaN、low/high が NaN の行はスキップ（誤補正しない）。
# --------------------------------------------------------------------------- #
def test_clamp_defensive_skips_zero_open_close():
    # open=close=0 → ref_lo=ref_hi=0。閾値 0 で全比較が不定になるためスキップ。
    df = _mkdf([
        ("2025-01-01", 0.0, 5.0, -3.0, 0.0),
    ])
    out = dataset._clamp_outlier_bars(df, _MARKET_REF)
    assert out is df  # スキップ＝不変（同一オブジェクト）


def test_clamp_defensive_skips_nan_values():
    df = _mkdf([
        ("2025-01-01", np.nan, 5.0, 1.0, 3.0),   # open NaN → スキップ
        ("2025-01-02", 100.0, 130.0, np.nan, 100.0),  # low NaN → スキップ
    ])
    out = dataset._clamp_outlier_bars(df, _MARKET_REF)
    assert out is df
    assert np.isnan(out["low"].iloc[1])


# --------------------------------------------------------------------------- #
# 統合（合成・DATA_DIR 非依存）: load_dataframe の resample 経路で一様に効く。
#   tmp ref を whitelist と clamp 対象集合の双方へ登録し、日足→週足 resample 後の
#   集約バーの外れ安値がクランプされ、正常バーが不変であることを固定する。
# --------------------------------------------------------------------------- #
_CSV_HEADER = ("date", "open", "high", "low", "close")


def _write_csv(path, rows):
    with open(path, "w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(_CSV_HEADER)
        w.writerows(rows)


def test_load_dataframe_resample_path_applies_clamp_for_market_ref(tmp_path, monkeypatch):
    # Arrange: 日足 CSV。1 行だけ low を -64% 外れにする（週内に混入）。
    csv_path = tmp_path / "mkt.csv"
    _write_csv(csv_path, [
        ("2025-08-25", 43076.97, 43199.07, 42527.94, 42650.00),
        ("2025-08-26", 42642.89, 42705.29, 15098.53, 42476.68),  # 外れ安値
        ("2025-08-27", 42481.76, 42626.97, 42268.89, 42343.92),
    ])
    monkeypatch.setitem(dataset.DATASET_WHITELIST, "_tmp_mkt", csv_path)
    monkeypatch.setitem(dataset._OUTLIER_CLAMP_REFS_SET, "_tmp_mkt", True)
    dataset._BASE_CACHE.clear()
    dataset._RESAMPLE_CACHE.clear()
    # Act: 週足へ resample（週内 low の最小=15098 が集約されるはず→クランプ対象）。
    weekly = dataset.load_candles("_tmp_mkt", "1W")
    # Assert: 週足 low は 15098 ではなく、その週の ref_lo 近傍（>= 42000 台）へクランプ。
    assert all(c["low"] > 40000.0 for c in weekly)


def test_load_dataframe_atomic_path_applies_clamp_for_market_ref(tmp_path, monkeypatch):
    # 原子（timeframe=None）経路でも補正が効く。
    csv_path = tmp_path / "mkt_atomic.csv"
    _write_csv(csv_path, [
        ("2025-08-26", 42642.89, 42705.29, 15098.53, 42476.68),
    ])
    monkeypatch.setitem(dataset.DATASET_WHITELIST, "_tmp_atom", csv_path)
    monkeypatch.setitem(dataset._OUTLIER_CLAMP_REFS_SET, "_tmp_atom", True)
    dataset._BASE_CACHE.clear()
    dataset._RESAMPLE_CACHE.clear()
    atomic = dataset.load_candles("_tmp_atom", None)
    assert atomic[0]["low"] == pytest.approx(42476.68)


def test_load_dataframe_non_market_ref_unaffected(tmp_path, monkeypatch):
    # 非市場 ref（clamp 対象外）は load_dataframe 経由でも外れ値が残る（golden 非破壊の担保）。
    csv_path = tmp_path / "syn.csv"
    _write_csv(csv_path, [
        ("2025-08-26", 42642.89, 42705.29, 15098.53, 42476.68),
    ])
    monkeypatch.setitem(dataset.DATASET_WHITELIST, "_tmp_syn", csv_path)
    dataset._BASE_CACHE.clear()
    dataset._RESAMPLE_CACHE.clear()
    atomic = dataset.load_candles("_tmp_syn", None)
    assert atomic[0]["low"] == pytest.approx(15098.53)


# --------------------------------------------------------------------------- #
# 統合（実データ・slow）: jp225_tick 1D 2025-08-26 に配信欠損ファントムが無い（ISSUE-107）。
#   旧前提（M1 素材に ~15,098 が残り serving クランプで low==min(open,close) になる）は、
#   M1 素材化段での日内中央値クリーニング（tick_m1._clean_m1_day）導入により廃止。
#   現在は素材が清浄＝実在の下ヒゲ（~42,135）がそのまま供給される（クランプ痕にならない）。
# --------------------------------------------------------------------------- #
@pytest.mark.slow
def test_jp225_tick_2025_08_26_has_no_phantom_and_neighbor_unchanged():
    ref = "jp225_tick"
    try:
        df = dataset.load_dataframe(ref, "1D")
    except (FileNotFoundError, OSError):
        pytest.skip("jp225_tick 実データが無い環境")
    if df is None or len(df) == 0:
        pytest.skip("jp225_tick 実データが空")
    lm = {str(c).lower(): c for c in df.columns}
    target = pd.Timestamp("2025-08-26")
    neighbor = pd.Timestamp("2025-08-25")
    if target not in df.index or neighbor not in df.index:
        pytest.skip("対象日が実データ範囲外")
    row = df.loc[target]
    # 8/26 の low はファントム（~15,098）ではなく実勢帯（>40,000）にある。
    assert float(row[lm["low"]]) > 40000.0
    # OHLC 整合（low は open/close 以下＝実在の下ヒゲとして供給される・クランプ痕に依存しない）。
    ref_lo = min(float(row[lm["open"]]), float(row[lm["close"]]))
    assert float(row[lm["low"]]) <= ref_lo
    # 8/25（正常バー）は不変（クランプで動かない）。
    n = df.loc[neighbor]
    n_ref_lo = min(float(n[lm["open"]]), float(n[lm["close"]]))
    assert float(n[lm["low"]]) < n_ref_lo  # 正常な下ヒゲが残っている
