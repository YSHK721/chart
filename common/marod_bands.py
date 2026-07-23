"""marod_bands — 因果ローリング・バンド系プリミティブ（指標横断の共有・単一実装）。

MAROD 系オシレータ（btlm_trail_marod / ma_marod）が用いる系列汎用の因果統計:
    - :func:`rolling_causal` / :func:`rolling_causal_fast` … 当該バー除外の因果ローリング集約
    - :func:`quantile_bands` … 因果ローリング経験分位バンド
    - :func:`sigma_band` … 因果ローリング σ バンド（平均 ± mult·σ）
    - :func:`outlier_event_quantiles` … 外れ値イベント分位水準（バンド算出＋ event_quantiles 委譲）

出自: btlm_trail_marod/src/core.py の系列汎用関数を無改変で移設（SOLID 是正 🟡-10:
ma_marod が兄弟具象へ依存していた DIP 半成立を、common 抽出で対称化する）。移設に伴う
数値挙動の変更は無い（同一性は各指標のテストが恒久固定する）。

因果境界（全関数共通・ISSUE-141 と同一規約）:
    各バー t の統計は **当該バー t を除く** 直近 window_n 本（v_{t-N}..v_{t-1}）から算出する
    ＝非リペイント・未来非参照。有限本数が :data:`MIN_STAT_OBS` 未満のバーは NaN。

依存: numpy と :mod:`common.event_quantiles` のみ（指標パッケージへ依存しない）。
"""
from __future__ import annotations

import warnings

import numpy as np

from common import event_quantiles as _evq

# σ（ddof=1）・分位に必要な最小有限本数（btlm_trail _MIN_EMP_OBS と同値）。
MIN_STAT_OBS: int = 2


def rolling_causal(values: np.ndarray, window_n: int, reducer) -> np.ndarray:
    """各バー t で **当該バー t を除く** 直近 window_n 本（v_{t-N}..v_{t-1}）に reducer を適用する。

    有限本数が :data:`MIN_STAT_OBS` 未満のバーは NaN。窓 = ``values[max(0, t-window_n): t]``。
    """
    vals = np.asarray(values, dtype=np.float64).ravel()
    n = vals.size
    out = np.full(n, np.nan)
    for t in range(n):
        start = max(0, t - window_n)
        window = vals[start:t]  # 当該バー t を除く（... t-1 まで）。
        finite = window[np.isfinite(window)]
        if finite.size >= MIN_STAT_OBS:
            out[t] = float(reducer(finite))
    return out


def rolling_causal_fast(
    values: np.ndarray, window_n: int, kind: str, q: "float | None" = None
) -> np.ndarray:
    """:func:`rolling_causal` のベクトル化版（quantile/mean/std 限定・出力は完全一致）。

    性能是正（ISSUE-154）: 満杯窓の区間（t >= window_n）を ``sliding_window_view`` ＋
    nan 集約（C 実装）で一括計算し、先頭の部分窓区間（t < window_n・最大 window_n 本）のみ
    ループへ委譲する。因果境界（当該バー除外）・NaN 規約（有限本数 < MIN_STAT_OBS は NaN）は
    :func:`rolling_causal` と同一で、同一性は回帰テスト（ランダム系列・NaN 混在）で恒久固定する。
    """
    vals = np.asarray(values, dtype=np.float64).ravel()
    n = vals.size
    reducer = {
        "quantile": (lambda f: np.quantile(f, q)),
        "mean": (lambda f: f.mean()),
        "std": (lambda f: f.std(ddof=1)),
    }[kind]
    head = min(n, window_n)
    out = np.full(n, np.nan)
    # 先頭の部分窓（t < window_n）は従来ループ（最大 window_n 本＝コスト一定）。
    out[:head] = rolling_causal(vals[:head], window_n, reducer)
    if n <= window_n:
        return out
    # 満杯窓: バー t（t=window_n..n-1）の窓 = vals[t-window_n : t]。
    win = np.lib.stride_tricks.sliding_window_view(vals[:-1], window_n)  # 行 i ↔ t=i+window_n
    finite_cnt = np.sum(np.isfinite(win), axis=1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # 全 NaN 行の nan 集約警告（結果は下の mask で NaN 化）
        if kind == "quantile":
            agg = np.nanquantile(win, q, axis=1)
        elif kind == "mean":
            agg = np.nanmean(win, axis=1)
        else:
            agg = np.nanstd(win, axis=1, ddof=1)
    agg[finite_cnt < MIN_STAT_OBS] = np.nan
    out[window_n:] = agg
    return out


def validate_window_qpair(window_n: int, q_low: float, q_high: float) -> tuple[int, float, float]:
    """window_n（>= MIN_STAT_OBS）と分位ペア（0<q_low<q_high<1）を検証する。違反は ValueError。"""
    n = int(window_n)
    if n < MIN_STAT_OBS:
        raise ValueError(f"window_n は {MIN_STAT_OBS} 以上が必要です: window_n={window_n}")
    ql, qh = float(q_low), float(q_high)
    if not (0.0 < ql < qh < 1.0):
        # 文言は移設元（btlm_trail_marod core）と同一（委譲で例外メッセージを変えない）。
        raise ValueError(
            f"分位ペアは 0 < q_low < q_high < 1 が必要です: q_low={q_low}, q_high={q_high}"
        )
    return n, ql, qh


def quantile_bands(
    series: np.ndarray, *, window_n: int, q_low: float, q_high: float
) -> tuple[np.ndarray, np.ndarray]:
    """系列の因果ローリング経験分位バンド（下側 q_low・上側 q_high）を返す。

    各バー t で当該バーを除く直近 window_n 本の有限値の経験分位 q。因果・非リペイント。

    Returns:
        (band_low, band_high)。各長さ n。有限本数 < MIN_STAT_OBS のバーは NaN。

    Raises:
        ValueError: window_n < MIN_STAT_OBS、または分位ペア不正時。
    """
    n, ql, qh = validate_window_qpair(window_n, q_low, q_high)
    low = rolling_causal_fast(series, n, "quantile", ql)
    high = rolling_causal_fast(series, n, "quantile", qh)
    return low, high


def sigma_band(
    series: np.ndarray, *, window_n: int, mult: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """系列の因果ローリング σ バンド（ローリング平均 ± mult·σ）を返す。

    中心はローリング平均・幅は標本標準偏差（ddof=1）× mult。各バー t で当該バーを除く
    直近 window_n 本の有限値から算出（因果・非リペイント）。

    Returns:
        (band_low, band_high, mean, std)。各長さ n。有限本数 < MIN_STAT_OBS のバーは NaN。

    Raises:
        ValueError: window_n < MIN_STAT_OBS のとき。
    """
    n = int(window_n)
    if n < MIN_STAT_OBS:
        raise ValueError(f"window_n は {MIN_STAT_OBS} 以上が必要です: window_n={window_n}")
    m = float(mult)
    mean = rolling_causal_fast(series, n, "mean")
    std = rolling_causal_fast(series, n, "std")
    low = mean - m * std
    high = mean + m * std
    return low, high, mean, std


def outlier_event_quantiles(
    series: np.ndarray,
    *,
    window_n: int,
    q_low: float,
    q_high: float,
    q_out: "float | None",
    k_events: int,
    event_agg: str,
    bands: "tuple[np.ndarray, np.ndarray] | None" = None,
    include_all: bool = True,
) -> "dict[str, np.ndarray]":
    """外れ値イベント（正常バンド超）の因果分位水準を返す（系列レベル API）。

    正常バンドを :func:`quantile_bands`（当該バー除外の因果窓）で算出し、イベント検出・
    集計（episode/bar）・分位算出は共有プリミティブ :func:`common.event_quantiles.
    outlier_event_quantiles`（指標横断の正実装）へ委譲する。仕様・契約（イベント定義・
    因果境界・戻り値キー・例外）は委譲先のとおり。

    Args:
        series: 対象系列（MAROD 等）。
        window_n: 正常バンドの因果ローリング窓（min 2）。
        q_low/q_high: 正常バンドの分位ペア（イベント判定の境界）。
        q_out: イベントの極端分位（有効条件 max(q_high, 0.5) < q_out < 1・無効は極端線のみオフ）。
        k_events: ローリング側の直近観測件数（min 1。episode ではエピソード数）。
        event_agg: 集計単位（"episode"/"bar"）。
        bands: 呼び出し側が算出済みの正常バンドの再利用（二重計算の回避・ISSUE-154）。
        include_all: False で全履歴（*_all）計算を省略（戻り値の形は不変）。

    Returns:
        dict。キーは med_hi/ext_hi/med_lo/ext_lo（直近 k_events 件）と *_all（全履歴）。

    Raises:
        ValueError: window_n / 分位ペア不正、k_events < 1、または event_agg 不正のとき。
    """
    v = np.asarray(series, dtype=np.float64).ravel()
    band_lo, band_hi = bands if bands is not None else quantile_bands(
        v, window_n=window_n, q_low=q_low, q_high=q_high)
    return _evq.outlier_event_quantiles(
        v, band_lo, band_hi,
        q_high=q_high, q_out=q_out, k_events=k_events, event_agg=event_agg,
        include_all=include_all,
    )
