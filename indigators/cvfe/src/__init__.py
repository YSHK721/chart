"""CVFE — Conditional Volatility Forecast Engine（公開面）。

正本仕様: ``indigators/cvfe/CVFE_spec_v1.0.md``
内部設計: ``indigators/cvfe/CVFE_internal_design_v1.0.md``

次バーの条件付ボラティリティ ``σ̂_{t+1}`` を、気配品質に応じて決定論的に選択された
測定量から 1 期先予測として出力する（仕様 §1）。

主な入口:
    * :func:`compute_cvfe`   一括計算（仕様 §4 の全段階）
    * :class:`CvfeSequential` 逐次計算（1 バー増分更新）
    * :mod:`.evaluation` / :mod:`.benchmarks`  仕様 §5 の評価手続きと比較対象 M0〜M4

依存: 外部 numpy のみ（仕様 §6）。共有プリミティブ ``common.normal_dist`` /
``common.stats_boot`` を無改変参照する。
"""

from __future__ import annotations

from .benchmarks import (
    forecast_ewma,
    forecast_garch11,
    forecast_har_plain,
    forecast_moving_average,
)
from .dto import BarMeasure, CvfeParams, CvfeResult, CvfeState, QualityReport
from .engine import (
    CvfeSequential,
    compute_cvfe,
    fit_state,
    gap_flags_and_squares,
    measure_all_bars,
    measure_bar,
)
from .evaluation import (
    diebold_mariano,
    model_confidence_set,
    mse_loss,
    newey_west_lag,
    qlike,
)
from .errors import (
    E01_INSUFFICIENT_BARS,
    E02_TICKS_NOT_MONOTONIC,
    E03_NONPOSITIVE_PRICE,
    E04_EDGES_NOT_MONOTONIC,
    E05_PARAM_RANGE,
    E06_EMPTY_BAR,
    E07_QUALITY_FAIL,
    E08_HAR_SINGULAR,
    E09_NONFINITE_SIGMA,
    W01_TSRV_NONPOSITIVE,
    W02_BPV_NONPOSITIVE,
    W03_GAP_INIT_LOOKAHEAD,
    W04_HAR_JUMP_COLUMN_CONSTANT,
    CvfeError,
)
from .gap import GapEwma, initial_gap_variance, is_gap_bar
from .har import har_feature_row, har_features, har_fit, har_predict
from .jumps import bipower_variation, jump_test, tri_power_quarticity
from .logs import JsonlLogger, Logger, NULL_LOGGER
from .measures import parkinson, realized_range, realized_variance, two_scale_rv
from .quality import SAMPLING_GRID, diagnose_quality, select_delta_star
from .sampling import previous_tick_sample, split_bars

__all__ = [
    "BarMeasure", "CvfeParams", "CvfeResult", "CvfeState", "QualityReport",
    "CvfeSequential", "compute_cvfe", "fit_state", "gap_flags_and_squares",
    "measure_all_bars", "measure_bar",
    "CvfeError",
    "E01_INSUFFICIENT_BARS", "E02_TICKS_NOT_MONOTONIC", "E03_NONPOSITIVE_PRICE",
    "E04_EDGES_NOT_MONOTONIC", "E05_PARAM_RANGE", "E06_EMPTY_BAR", "E07_QUALITY_FAIL",
    "E08_HAR_SINGULAR", "E09_NONFINITE_SIGMA",
    "W01_TSRV_NONPOSITIVE", "W02_BPV_NONPOSITIVE", "W03_GAP_INIT_LOOKAHEAD",
    "W04_HAR_JUMP_COLUMN_CONSTANT",
    "forecast_ewma", "forecast_garch11", "forecast_har_plain", "forecast_moving_average",
    "diebold_mariano", "model_confidence_set", "mse_loss", "newey_west_lag", "qlike",
    "GapEwma", "initial_gap_variance", "is_gap_bar",
    "har_feature_row", "har_features", "har_fit", "har_predict",
    "bipower_variation", "jump_test", "tri_power_quarticity",
    "JsonlLogger", "Logger", "NULL_LOGGER",
    "parkinson", "realized_range", "realized_variance", "two_scale_rv",
    "SAMPLING_GRID", "diagnose_quality", "select_delta_star",
    "previous_tick_sample", "split_bars",
]
