"""Stage B 検証: profit_rsi を Latest 増分計算フレームワークへ分類＋一致検証する。

分類（仕様 §4-0）:
  profit_rsi の出力（add_rsi）は次の 3 系列群:
    - rsi 線（PF_LINE 'rsi'）            : iRSI（Wilder RSI）。core.compute_rsi は
                                          seed（i==period）→ Wilder 平滑
                                          pos[i]=(pos[i-1]*(period-1)+up)/period の **再帰**
                                          （buf[i] が buf[i-1] に依存）。
    - rsi_ma 線（PF_LINE 'rsi_ma'）      : iRSI の EMA 平滑（exponential_ma_on_buffer。
                                          先頭シードからの再帰＝buf[i] が buf[i-1] に依存）。
    - σ 水準線 7 本（PF_HLINE 'profit_rsi'）: **生 RSI 系列** 全体の avg±1/2/3σ＋中央 50。
                                          全系列（先頭からの累積）に依存する大域統計。
  rsi（Wilder）も rsi_ma（EMA）も先頭シードからの再帰であり、σ 水準も全系列依存のため、
  指標全体としては full 必須＝archetype は **recurrence**（安全既定と一致）。

系列 kind → frontend routing:
  catalog def.series の kind 群 = {line, horizontal_line}。horizontal_line を含むため
  frontend routing は **full**。

不変条件:
  latest_meta は profit_rsi を明示登録していないため安全既定
  LatestMeta("recurrence", None, 1) で解決される（min_window=None＝full）。
  latest_dispatch._trail は line/histogram のみ末尾K切りし、horizontal_line は不変で
  素通しする。full フォールバック（tail せず全件計算）のため:
    - rsi / rsi_ma の line 系列は data[-K:] が full の data[-K:] と float 完全一致。
    - σ 水準 horizontal_line は latest でも全件返る（切られない）。
  → K=1 で line は末尾1点・horizontal_line は全件一致。

import 規約: conftest.py が api/ を sys.path へ追加済み。既存 src は read-only。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from adapter.compute import IndicatorComputeAdapter
from adapter.compute.latest_meta import latest_meta
from adapter.compute.latest_dispatch import full_compute, latest_compute

# 本指標の compute_id / variant（catalog def＝単一 variant）。
_COMPUTE_ID = "profit_rsi"
_VARIANTS = ("default",)

# trail 対象 kind（latest_dispatch._TRIMMABLE_KINDS と同義）。
_TRIMMABLE_KINDS = ("line", "histogram")


def _ohlcv(n: int = 100) -> pd.DataFrame:
    """昇順 OHLCV（date 含む）。N>=100>rsi_period(6)・ma_period(5) を満たす合成波形。

    既存 test_latest_compute._ohlcv と同流儀。add_rsi は open/high/low/close と
    time/date 列を要求する（volume は不要だが既存流儀に合わせ含める）。
    """
    base = 10.0 + np.sin(np.linspace(0.0, 6.0 * np.pi, max(n, 1))) * 3.0
    sign = np.where(np.arange(max(n, 1)) % 2 == 0, 1.0, -1.0)
    open_ = base
    close = base + sign * 0.5
    high = np.maximum(open_, close) + 1.0
    low = np.minimum(open_, close) - 1.0
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="h"),
            "open": open_[:n],
            "high": high[:n],
            "low": low[:n],
            "close": close[:n],
            "volume": np.full(n, 1000.0) + np.arange(n, dtype=float),
        }
    )


def _params() -> dict:
    """catalog 既定（PF_INT rsi_period=6, apply=5, ma_period=5）。core 既定とも整合。"""
    return {"rsi_period": 6, "apply": 5, "ma_period": 5}


# =========================================================================== #
# 分類: archetype（recurrence・安全既定）と frontend routing（full）
# =========================================================================== #
def test_series_kinds_are_line_and_horizontal_line():
    # 出力 kind 群 = {line, horizontal_line}（rsi/rsi_ma 線 ＋ σ 水準）。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(100)
    for variant in _VARIANTS:
        series = full_compute(adapter, _COMPUTE_ID, variant, df, _params())
        kinds = {s["kind"] for s in series}
        assert kinds == {"line", "horizontal_line"}, f"{variant}: {kinds}"
        # 線は data を持ち、水平線は lines を持ち data を持たない（trail 対象外）。
        for s in series:
            if s["kind"] == "line":
                assert "data" in s
            else:
                assert "lines" in s and "data" not in s


def test_meta_resolves_to_safe_default_recurrence_full_k1():
    # 未登録 → 安全既定 recurrence/full/K=1（Wilder RSI＋EMA 平滑＋大域σで full 必須・正しい）。
    meta = latest_meta(_COMPUTE_ID, "default", _params())
    assert meta.archetype == "recurrence"  # 安全既定（明示登録なし・full フォールバック）
    assert meta.min_window is None
    assert meta.trailing_k == 1


# =========================================================================== #
# 不変条件: latest 末尾K == full 末尾K（line は float 完全一致・水平線は全件）
# =========================================================================== #
def test_latest_line_tail_equals_full_tail_exact():
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(100)
    for variant in _VARIANTS:
        params = _params()
        k = latest_meta(_COMPUTE_ID, variant, params).trailing_k
        assert k == 1

        full = full_compute(adapter, _COMPUTE_ID, variant, df, dict(params))
        latest = latest_compute(adapter, _COMPUTE_ID, variant, df, dict(params))

        # latest 経路がエラーなく走り、非空であること。
        assert latest, f"{variant}: latest series should not be empty"

        full_by_name = {s["name"]: s for s in full}
        for s in latest:
            f = full_by_name[s["name"]]
            if s["kind"] in _TRIMMABLE_KINDS:
                # 末尾 K 点に切られ、full の末尾 K 点と float 完全一致（time/value 完全一致）。
                assert len(s["data"]) <= k
                assert s["data"] == f["data"][-k:], f"{variant}/{s['name']}"
            else:
                # horizontal_line は latest でも全件（切られない）。
                assert s["kind"] == "horizontal_line"
                assert len(s["lines"]) == len(f["lines"])


def test_latest_lines_trimmed_horizontal_full():
    # full フォールバック（min_window=None）かつ K=1。line は末尾1点に切られ、
    # horizontal_line は全件素通しで full と一致する構造を確認する。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(100)
    full = full_compute(adapter, _COMPUTE_ID, "default", df, _params())
    latest = latest_compute(adapter, _COMPUTE_ID, "default", df, _params())

    full_by_name = {s["name"]: s for s in full}
    for s in latest:
        f = full_by_name[s["name"]]
        if s["kind"] == "line":
            # full は全件、latest は末尾 1 点。
            assert len(s["data"]) == 1
            assert len(f["data"]) == len(df)
            assert s["data"][-1] == f["data"][-1]
        else:
            assert s == f  # horizontal_line は完全一致


def test_horizontal_line_levels_are_seven():
    # σ 水準（p1/p2/p3/m1/m2/m3/mid50）の 7 本が latest でも全件返ること。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(100)
    latest = latest_compute(adapter, _COMPUTE_ID, "default", df, _params())
    hl = next(s for s in latest if s["kind"] == "horizontal_line")
    assert len(hl["lines"]) == 7
    texts = {ln["text"] for ln in hl["lines"]}
    assert texts == {"p1", "p2", "p3", "m1", "m2", "m3", "mid50"}
