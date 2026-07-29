"""出力アダプタ: lightweight-charts への CVFE 系列追加（duck typing）。

層名/責務:
    出力アダプタ。``lightweight_charts`` を import せず ``create_line`` を持つ
    オブジェクト（chart）をダックタイピングで受ける（ma_marod の出力アダプタと同一様式）。
    次バーの条件付ボラティリティ予測 ``σ̂_t`` を別 pane の line オシレータとして供給する。
    NaN（warm-up・未確定）は描画から除外する。

系列名（固定・F3 照合は catalog の SeriesDef 集合と突合）:
    - "cvfe"    : ``σ̂_t``（合成・§4.8）。主系列。
    - "cvfe_oc" : 場中成分 ``σ̂_OC,t``（§4.6）。
    - "cvfe_co" : ギャップ成分 ``σ̂_CO,t``（§4.7）。ギャップ非保有バーは 0。

表示単位:
    ``σ̂`` は対数価格の標準偏差（例 0.012 = 1.2%）である。チャート上の可読性のため
    **100 倍して % で描画する**（値の意味は変えない）。3 系列とも同一の換算を行う。

データ経路（重要）:
    チャート UI の計算経路が渡すのは OHLC の DataFrame であり、仕様 §3.1 が要求する
    ティック列ではない。したがって仕様 §4.1-6 の ``quality_gate = "FAIL"`` 行が定める
    縮退（``measure_id = "PARK"``）で算出する（:mod:`.ohlc` を参照）。
    仕様 §7-6 のとおり、この経路の精度は CEB v1.0 と同等まで低下する。
    ティックを供給できる呼び出し元は :func:`~.engine.compute_cvfe` を用いること。

依存:
    標準: __future__, typing / 外部: numpy, pandas / プロジェクト内: ohlc, common_view
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from common_view.lwc_adapter import emit_line as _emit_line
from common_view.lwc_adapter import resolve_times as _resolve_times

from .ohlc import DEFAULT_OHLC_N_HAR, compute_cvfe_from_ohlc

_SERIES_SIGMA = "cvfe"
_SERIES_OC = "cvfe_oc"
_SERIES_CO = "cvfe_co"

# 色（既存指標と識別できる系統を選ぶ。既定値は catalog の param 既定と一致させる）。
COLOR_SIGMA = "rgba(233, 30, 99, 1)"      # σ̂（ピンク・実線）
COLOR_OC = "rgba(3, 169, 244, 1)"         # 場中成分（水色・点線）
COLOR_CO = "rgba(158, 158, 158, 1)"       # ギャップ成分（灰・点線）

#: σ̂ は対数収益の標準偏差。チャートには % で表示する。
_PERCENT = 100.0


def _column(df: pd.DataFrame, name: str) -> np.ndarray:
    lower = {str(c).lower(): c for c in df.columns}
    if name not in lower:
        raise KeyError(f"列が見つかりません: {name}")
    return pd.to_numeric(df[lower[name]], errors="coerce").to_numpy(dtype=np.float64)


def add_cvfe(
    chart,
    df: pd.DataFrame,
    *,
    n_har: int = DEFAULT_OHLC_N_HAR,
    lam_gap: float = 0.97,
    refit_every: int = 0,
    show_components: bool = True,
    time_column: Optional[str] = None,
    color: str = COLOR_SIGMA,
) -> list:
    """``chart`` へ CVFE の ``σ̂``（＋成分 2 本）を line 系列として追加する。

    Args:
        chart: ``create_line(name, **kwargs)`` を持つオブジェクト。
        df: OHLC の DataFrame（``open``/``high``/``low``/``close`` と時刻列）。
        n_har: HAR の学習本数（仕様 §3.1・下限 500）。
        lam_gap: ギャップ分散 EWMA の減衰係数（仕様 §3.1・0.90 以上 1.0 未満）。
        refit_every: 再学習の周期（仕様 §3.1・0 は係数凍結）。
        show_components: 場中成分・ギャップ成分の 2 本を併せて描くか。
        time_column: 時刻列の明示指定。
        color: ``σ̂`` 線の色。

    Returns:
        生成した系列オブジェクトのリスト。
    """
    times = _resolve_times(df, time_column)
    times_sec = (pd.to_datetime(times).astype("int64") // 1_000_000_000).to_numpy(dtype=np.float64)

    res = compute_cvfe_from_ohlc(
        _column(df, "open"), _column(df, "high"), _column(df, "low"), _column(df, "close"),
        times_sec, n_har=n_har, lam_gap=lam_gap, refit_every=refit_every,
    )

    # 未確定バー（available=False）は描画しない。σ̂ は % 表示へ換算する。
    mask = res.available
    sigma = np.where(mask, res.sigma_hat * _PERCENT, np.nan)
    oc = np.where(mask, res.sigma_oc * _PERCENT, np.nan)
    co = np.where(mask, res.sigma_co * _PERCENT, np.nan)

    created: list = []
    created.append(_emit_line(chart, _SERIES_SIGMA, times, sigma, color, "solid"))
    if show_components:
        created.append(_emit_line(chart, _SERIES_OC, times, oc, COLOR_OC, "dotted"))
        created.append(_emit_line(chart, _SERIES_CO, times, co, COLOR_CO, "dotted"))
    return created
