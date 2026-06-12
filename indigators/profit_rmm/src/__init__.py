"""層名: src パッケージ（profit_rmm の core / 成果物層 / アダプタの再公開）。

責務:
    PRO!fitRMM（複合レベルカウント指標）の純粋計算 core 層（``core``）と
    pandas 成果物層（``rmm``）、入力アダプタ（``loader``）・lightweight-charts 出力
    アダプタ（``lwc_chart``）を束ねる薄い再公開層。matplotlib 出力アダプタ（``plot``）は
    重い描画依存を import 時に持ち込まないため再公開対象から除外する（``src.plot`` で
    明示 import する）。

公開 API:
    build_rmm           : OHLCV DataFrame → レベルカウント列（rmm_lc）を持つ DataFrame。
    rmm_levels          : level_count の σ6 水準辞書（up_1s..dn_3s）。
    compute_rmm         : 純粋計算（numpy 配列入出力）→ RmmResult。
    compute_rmm_levels  : level_count の σ6 水準（numpy 入力）。
    load_ohlcv_csv      : CSV → OHLCV DataFrame（volume 必須）。
    add_rmm             : lightweight-charts へヒストグラム + σ6 水準線を追加（duck typing）。
    RmmResult / 定数・列名（DEFAULT_OSC_PERIOD / DEFAULT_MA_PERIOD / LEVEL_COUNT_COLUMN）。

元 MQL 対応:
    ``PRO!fitRMM.mq4``（iRSI / iWPR / iMFI / MAROD を funLevelCount で合算する
    複合レベルカウント指標）を昇順=古→新へ 1:1 変換する。

依存:
    core: numpy ＋ 共有（common.typical_price / moving_averages.exponential_ma_on_buffer）。
    rmm:  pandas（成果物層）。loader: pandas。lwc_chart: numpy/pandas（描画 lib 非 import）。
"""

from __future__ import annotations

from . import core, lwc_chart, rmm  # noqa: F401
from .core import (
    DEFAULT_MA_PERIOD,
    DEFAULT_OSC_PERIOD,
    RmmResult,
    compute_rmm,
    compute_rmm_levels,
)
from .loader import load_ohlcv_csv
from .lwc_chart import add_rmm
from .rmm import LEVEL_COUNT_COLUMN, build_rmm, rmm_levels

__all__ = [
    "core",
    "rmm",
    "lwc_chart",
    "build_rmm",
    "rmm_levels",
    "compute_rmm",
    "compute_rmm_levels",
    "load_ohlcv_csv",
    "add_rmm",
    "RmmResult",
    "DEFAULT_OSC_PERIOD",
    "DEFAULT_MA_PERIOD",
    "LEVEL_COUNT_COLUMN",
]
