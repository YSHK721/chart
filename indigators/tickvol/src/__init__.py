"""tickvol — ティックボリューム（1 足あたりの tick 数）の指標パッケージ。

供給側 :mod:`marketdata` の ``volume`` 列は当該期間に到来した tick 数であり（確定足＝
ロールアップ／resample、形成中足＝``adapter.compute.forming_bar`` の ``len(mids)``）、本指標は
その値を加工せず専用ペインのヒストグラムとして描く。窓・平滑・状態を持たない点ごとの写像。

外れ値水準（:mod:`.levels`）は、同じ観測集合（POT＋エピソード宣言クラスタリング）の同じ分位を
経験的分位と GPD の 2 通りで推定して並べる。実装は既存の共有プリミティブ
（``common.marod_bands`` / ``common.event_quantiles`` / ``common.gpd``）を無改変で参照する。

公開 API（core + 水準 + 出力アダプタ）:
    build_tickvol        : OHLCV DataFrame → tick 数の float 系列（純関数）。
    resolve_volume_column: tick 数列の実列名を大小不問で解決する。
    tickvol_levels       : 正常帯（下側/上側分位）と外れ値水準（典型深度・経験的極端分位・GPD 外挿）。
    causal_bands         : 正常帯（因果ローリング分位・下側/上側）。
    levels_latest        : 次バーへ適用する水準（増分計算の入口）。
    step_excess_event    : 1 バーぶんのイベント確定（増分計算の入口）。
    gpd_excess_quantile  : 超過分への GPD 当てはめによる分位。
    causal_threshold     : 正常帯上端（＝POT 閾値）の因果ローリング分位。
    tickvol_trend        : 回帰トレンド・帯・外れ値分位線・β/σ/実績率（btlm_trail 仕様）。
    add_tickvol          : 出力アダプタ（ヒストグラム＋正常帯＋水準線＋トレンド系）。
    TICKVOL_COLUMN       : 出力系列名（front の SeriesDef.seriesName と一致）。
"""

from __future__ import annotations

from .core import TICKVOL_COLUMN, build_tickvol, resolve_volume_column
from .levels import (
    DEFAULT_Q_HIGH,
    DEFAULT_Q_LOW,
    DEFAULT_WINDOW_N,
    LEVEL_KEYS,
    MIN_GPD_EVENTS,
    causal_bands,
    causal_threshold,
    gpd_excess_quantile,
    levels_at,
    levels_latest,
    step_excess_event,
    tickvol_levels,
)
from .trend import (
    BAND_METHODS,
    DEFAULT_BAND_METHOD,
    DEFAULT_EMP_N,
    DEFAULT_MAXBARS,
    DEFAULT_N_COV,
    TREND_KEYS,
    tickvol_trend,
)
from .lwc_chart import add_tickvol

__all__ = [
    "TICKVOL_COLUMN",
    "build_tickvol",
    "resolve_volume_column",
    "add_tickvol",
    "tickvol_levels",
    "levels_at",
    "levels_latest",
    "step_excess_event",
    "gpd_excess_quantile",
    "causal_threshold",
    "causal_bands",
    "DEFAULT_WINDOW_N",
    "DEFAULT_Q_LOW",
    "DEFAULT_Q_HIGH",
    "MIN_GPD_EVENTS",
    "LEVEL_KEYS",
    "tickvol_trend",
    "TREND_KEYS",
    "BAND_METHODS",
    "DEFAULT_BAND_METHOD",
    "DEFAULT_MAXBARS",
    "DEFAULT_EMP_N",
    "DEFAULT_N_COV",
]
