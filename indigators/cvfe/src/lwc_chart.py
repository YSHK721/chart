"""出力アダプタ: lightweight-charts への CVFE バンド追加（duck typing）。

層名/責務:
    出力アダプタ。``lightweight_charts`` を import せず ``create_line`` を持つ
    オブジェクト（chart）をダックタイピングで受ける（btlm_trail / ma_marod と同一様式）。
    次バーの条件付ボラティリティ予測 ``σ̂_t`` を**価格スケール上のバンド**として供給する。
    NaN（warm-up・未確定）は描画から除外する。

バンドの定義（本アダプタの表示仕様）:
    ``σ̂_t`` は「バー ``t`` の対数収益の標準偏差」の予測であり、**バー ``t`` が開く前に
    確定している**（仕様 §4 柱書の因果規約）。したがってバンドの中心は当該バーの始値や
    終値ではなく **1 本前の確定終値 ``close_{t−1}``** に置く。これによりバンドは
    「次の 1 本がどこまで動きうるか」を**バーが動く前に**示す帯になる。

        中心   mid_t = close_{t−1}
        上側   mid_t · exp( + k · σ̂_t )
        下側   mid_t · exp( − k · σ̂_t )

    ``k`` は内側（既定 1.0）と外側（既定 2.0）の 2 段。対数収益の標準偏差であるため
    価格への写像は指数（比率）で行う（加減算ではない）。

外れ値水準（共有プリミティブの無改変参照）:
    正規仮定の ``k = 2`` が 95% に対応するのは標準化残差が正規分布のときだけで、実際の
    収益は裾が厚く被覆不足になる。そこで**実際に外れた履歴から**水準を測る。

    標準化残差 ``z_t = ln(close_t/close_{t−1}) / σ̂_t``（＝予測で規格化した実現値）に対し、
    共有プリミティブ ``common.marod_bands.quantile_bands`` で因果ローリング正常バンドを引き、
    そこを超えた「外れ値イベント」の水準を ``common.event_quantiles.outlier_event_quantiles``
    で求める（連続超過は episode declustering で 1 回に畳む）。得られる水準は
    ``z`` の単位なので ``mid · exp(± evq · σ̂_t)`` で価格へ写す。

    表示規約（色・線種・系列名サフィックス）も ``common.event_quantiles`` の
    ``emit_event_quantile_lines`` に委譲する＝``ma_marod`` / ``btlm_trail_marod`` と同一。
    新規の裾推定は実装しない（既存の外れ値水準機構を参照する・ユーザー裁定 2026-07-30）。

系列名（固定・F3 照合は catalog の SeriesDef 集合と突合）:
    - "cvfe_mid" : バンド中心（``close_{t−1}``）
    - "cvfe_u1" / "cvfe_l1" : 内側バンド（``± sigma_inner × σ̂``）
    - "cvfe_u2" / "cvfe_l2" : 外側バンド（``± sigma_outer × σ̂``）
    - "cvfe_evq_{med|ext}_{hi|lo}" : 外れ値水準（共有プリミティブ・典型深度と極端深度）

仕様との関係（重要）:
    正本仕様 ``CVFE_spec_v1.0.md`` §1 は「区間バンドの構築」を**スコープ外（CEB v1.1 の
    責務）**と明記している。本アダプタのバンドは**表示のための派生量**であり、CEB が定める
    条件付被覆の保証（CEB §5.2 の LR_ind 等）を持たない。依頼者指示による仕様変更として
    ISSUE-223 に記録した。σ̂ 自体の算出は仕様どおりで一切変更していない。

データ経路:
    チャート UI の計算経路が渡すのは OHLC の DataFrame でありティック列ではない。
    よって仕様 §4.1-6 の ``quality_gate = "FAIL"`` 行が定める縮退（``measure_id = "PARK"``）
    で算出する（:mod:`.ohlc` を参照）。精度は仕様 §7-6 のとおり低下する（ISSUE-218）。

依存:
    標準: __future__, typing / 外部: numpy, pandas / プロジェクト内: ohlc, common_view
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from common_view.lwc_adapter import emit_line as _emit_line
from common_view.lwc_adapter import resolve_times as _resolve_times

from common.event_quantiles import (
    DEFAULT_EVENT_AGG,
    DEFAULT_K_EVENTS,
    DEFAULT_Q_OUT,
    emit_event_quantile_lines,
    outlier_event_quantiles,
)
from common.marod_bands import quantile_bands

from .ohlc import DEFAULT_OHLC_N_HAR, compute_cvfe_from_ohlc

_SERIES_MID = "cvfe_mid"
_SERIES_U1 = "cvfe_u1"
_SERIES_L1 = "cvfe_l1"
_SERIES_U2 = "cvfe_u2"
_SERIES_L2 = "cvfe_l2"

#: 既定のバンド倍率（内側 1σ・外側 2σ）。正規近似なら概ね 68% / 95% に対応する。
DEFAULT_SIGMA_INNER: float = 1.0
DEFAULT_SIGMA_OUTER: float = 2.0

#: 外れ値水準の既定（ma_marod / btlm_trail_marod と同値＝共有プリミティブの既定）。
DEFAULT_Q_LOW: float = 0.05
DEFAULT_Q_HIGH: float = 0.95
DEFAULT_WINDOW_N: int = 500

# 色（catalog の param 既定と一致させる）。中心は淡く、外側ほど薄い。
COLOR_BAND = "rgba(233, 30, 99, 1)"          # 内側バンド（ピンク・実線）
COLOR_OUTER = "rgba(233, 30, 99, 0.55)"      # 外側バンド（同系・破線）
COLOR_MID = "rgba(158, 158, 158, 0.8)"       # 中心（灰・点線）


def _column(df: pd.DataFrame, name: str) -> np.ndarray:
    lower = {str(c).lower(): c for c in df.columns}
    if name not in lower:
        raise KeyError(f"列が見つかりません: {name}")
    return pd.to_numeric(df[lower[name]], errors="coerce").to_numpy(dtype=np.float64)


def sigma_band(mid: np.ndarray, sigma_hat: np.ndarray, valid: np.ndarray, k: float):
    """``mid · exp(± k σ̂)`` の上下 2 本を返す（``k`` が非有限なら両方 ``nan``）。"""
    if not np.isfinite(k) or k <= 0.0:
        nan = np.full(mid.size, np.nan, dtype=np.float64)
        return nan, nan.copy()
    width = np.where(valid, np.exp(k * np.asarray(sigma_hat, dtype=np.float64)), np.nan)
    return mid * width, mid / width


def standardized_residuals(close: np.ndarray, sigma_hat: np.ndarray,
                           available: np.ndarray) -> np.ndarray:
    """``z_t = ln(close_t / close_{t−1}) / σ̂_t`` を返す（無効バーは ``nan``）。

    ``σ̂_t`` はバー ``t`` の対数収益の標準偏差の予測であり、``ln(close_t/close_{t−1})``
    がその実現値。両者の比が「予測で規格化した残差」で、外れ値水準の入力になる。
    規格化してあるため、ボラティリティ水準によらず同一尺度で外れ具合を測れる。
    """
    c = np.asarray(close, dtype=np.float64)
    s = np.asarray(sigma_hat, dtype=np.float64)
    ok = np.asarray(available, dtype=bool)

    z = np.full(c.size, np.nan, dtype=np.float64)
    if c.size < 2:
        return z
    r = np.full(c.size, np.nan, dtype=np.float64)
    good = (c[:-1] > 0.0) & (c[1:] > 0.0)
    r[1:][good] = np.log(c[1:][good] / c[:-1][good])
    valid = ok & np.isfinite(r) & np.isfinite(s) & (s > 0.0)
    z[valid] = r[valid] / s[valid]
    return z


def outlier_levels_in_price(close: np.ndarray, sigma_hat: np.ndarray,
                            available: np.ndarray, mid: np.ndarray, *,
                            window_n: int = DEFAULT_WINDOW_N,
                            q_low: float = DEFAULT_Q_LOW,
                            q_high: float = DEFAULT_Q_HIGH,
                            q_out: "float | None" = DEFAULT_Q_OUT,
                            k_events: int = DEFAULT_K_EVENTS,
                            event_agg: str = DEFAULT_EVENT_AGG) -> dict:
    """外れ値水準を価格スケールで返す（キーは共有プリミティブと同一）。

    標準化残差 ``z`` の因果ローリング正常バンド超をイベントとし、その典型深度
    （中央値）と極端深度（``q_out`` 分位）を :func:`~common.event_quantiles.
    outlier_event_quantiles` で求めてから ``mid · exp(evq · σ̂)`` で価格へ写す。

    ``evq`` は上側が正・下側が負の ``z`` 値なので、写像は上下で同一の式でよい。
    """
    z = standardized_residuals(close, sigma_hat, available)
    band_lo, band_hi = quantile_bands(z, window_n=window_n, q_low=q_low, q_high=q_high)
    evq = outlier_event_quantiles(
        z, band_lo, band_hi, q_high=q_high, q_out=q_out,
        k_events=k_events, event_agg=event_agg, include_all=False,
    )
    s = np.asarray(sigma_hat, dtype=np.float64)
    return {k: mid * np.exp(v * s) for k, v in evq.items()}


def cvfe_bands(close: np.ndarray, sigma_hat: np.ndarray, available: np.ndarray, *,
               sigma_inner: float = DEFAULT_SIGMA_INNER,
               sigma_outer: float = DEFAULT_SIGMA_OUTER):
    """``(mid, u1, l1, u2, l2)`` を価格スケールで返す（純関数・描画非依存）。

    ``mid_t = close_{t−1}``、``u_t = mid_t · exp(+k σ̂_t)``、``l_t = mid_t · exp(−k σ̂_t)``。
    ``available`` が ``False`` のバー、および直前終値が非有限なバーは全系列 ``nan``。

    バンドは 1 本前の終値を中心に置くため、当該バーの値動きでは動かない
    （非リペイント。σ̂ 自体も確定済み）。

    Returns:
        ``(mid, u1, l1, u2, l2)``
    """
    c = np.asarray(close, dtype=np.float64)
    s = np.asarray(sigma_hat, dtype=np.float64)
    ok = np.asarray(available, dtype=bool)

    mid = np.full(c.size, np.nan, dtype=np.float64)
    if c.size >= 2:
        mid[1:] = c[:-1]                      # 中心 = 直前確定終値（因果）
    valid = ok & np.isfinite(mid) & np.isfinite(s) & (s > 0.0) & (mid > 0.0)

    mid = np.where(valid, mid, np.nan)
    u1, l1 = sigma_band(mid, s, valid, float(sigma_inner))
    u2, l2 = sigma_band(mid, s, valid, float(sigma_outer))
    return mid, u1, l1, u2, l2


def add_cvfe(
    chart,
    df: pd.DataFrame,
    *,
    n_har: int = DEFAULT_OHLC_N_HAR,
    lam_gap: float = 0.97,
    refit_every: int = 0,
    sigma_inner: float = DEFAULT_SIGMA_INNER,
    sigma_outer: float = DEFAULT_SIGMA_OUTER,
    show_outer: bool = True,
    show_mid: bool = False,
    show_outliers: bool = True,
    q_low: float = DEFAULT_Q_LOW,
    q_high: float = DEFAULT_Q_HIGH,
    q_out: "float | None" = DEFAULT_Q_OUT,
    k_events: int = DEFAULT_K_EVENTS,
    event_agg: str = DEFAULT_EVENT_AGG,
    window_n: int = DEFAULT_WINDOW_N,
    time_column: Optional[str] = None,
    color: str = COLOR_BAND,
) -> list:
    """``chart`` へ CVFE の予測バンドを価格スケール上の line 系列として追加する。

    Args:
        chart: ``create_line(name, **kwargs)`` を持つオブジェクト。
        df: OHLC の DataFrame（``open``/``high``/``low``/``close`` と時刻列）。
        n_har: HAR の学習本数（仕様 §3.1・下限 500）。
        lam_gap: ギャップ分散 EWMA の減衰係数（仕様 §3.1・0.90 以上 1.0 未満）。
        refit_every: 再学習の周期（仕様 §3.1・0 は係数凍結）。
        sigma_inner: 内側バンドの σ 倍率。
        sigma_outer: 外側バンドの σ 倍率。
        show_outer: 外側バンドを描くか。
        show_mid: 中心線（直前終値）を描くか。
        show_outliers: 外れ値水準（典型深度・極端深度の 4 本）を描くか。
        q_low / q_high: 標準化残差の正常バンドの下側/上側分位。
        q_out: 外れ値イベントの極端分位（無効なら極端線のみオフ）。
        k_events: 外れ値水準を測る直近イベント件数。
        event_agg: 外れ値の集計単位（``episode`` / ``bar``）。
        window_n: 正常バンドの因果ローリング窓（本数）。
        time_column: 時刻列の明示指定。
        color: 内側バンドの色。

    Returns:
        生成した系列オブジェクトのリスト。
    """
    times = _resolve_times(df, time_column)
    times_sec = (pd.to_datetime(times).astype("int64") // 1_000_000_000).to_numpy(dtype=np.float64)

    close = _column(df, "close")
    res = compute_cvfe_from_ohlc(
        _column(df, "open"), _column(df, "high"), _column(df, "low"), close,
        times_sec, n_har=n_har, lam_gap=lam_gap, refit_every=refit_every,
    )
    mid, u1, l1, u2, l2 = cvfe_bands(
        close, res.sigma_hat, res.available,
        sigma_inner=sigma_inner, sigma_outer=sigma_outer,
    )

    created: list = []
    if show_mid:
        created.append(_emit_line(chart, _SERIES_MID, times, mid, COLOR_MID, "dotted"))
    created.append(_emit_line(chart, _SERIES_U1, times, u1, color, "solid"))
    created.append(_emit_line(chart, _SERIES_L1, times, l1, color, "solid"))
    if show_outer:
        created.append(_emit_line(chart, _SERIES_U2, times, u2, COLOR_OUTER, "dashed"))
        created.append(_emit_line(chart, _SERIES_L2, times, l2, COLOR_OUTER, "dashed"))
    if show_outliers:
        # 外れ値水準（4 本）。系列名・色・線種は共有プリミティブが単一情報源
        #   （ma_marod / btlm_trail_marod と同一規約）。emit 実体のみ注入する。
        evq_price = outlier_levels_in_price(
            close, res.sigma_hat, res.available, mid,
            window_n=window_n, q_low=q_low, q_high=q_high,
            q_out=q_out, k_events=k_events, event_agg=event_agg,
        )
        created.extend(emit_event_quantile_lines(
            "cvfe", times, evq_price,
            lambda name, ts, vals, c, style: _emit_line(chart, name, ts, vals, c, style),
        ))
    return created
