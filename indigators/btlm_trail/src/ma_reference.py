"""MA 参考線: moving_averages（無改変）を btlm_mean へ適用する参照利用アダプタ。

層名/責務:
    参照利用アダプタ。btlm_trail の MA 参考線（方向確認用）は btlm_mean 系列そのものに
    移動平均を当てるもので、種別（sma/ema/smma/lwma）は moving_averages と同期する。
    moving_averages のパッケージ src は top-level 名 ``src`` を用い ``import`` では衝突する
    ため、その core.py（純 numpy・相対 import なし）をファイルパスから一意名でロードして
    バッファ関数をそのまま利用する（moving_averages src は read-only・無改変）。

依存:
    標準: __future__, importlib, pathlib / 外部: numpy / プロジェクト内: moving_averages/src/core（動的ロード）
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np

# btlm_trail/src/ma_reference.py → parents[2] = indigators/。
_MA_CORE_PATH = Path(__file__).resolve().parents[2] / "moving_averages" / "src" / "core.py"
_MA_CORE_MODNAME = "_moving_averages_core_for_btlm_trail"

# core が「最初の有効値」を index=0 から定義する種別（moving_averages/src/lwc_chart._FROM_ZERO と同一）。
_FROM_ZERO = {"ema"}


def _load_ma_core():
    """moving_averages/src/core.py を一意名でロードする（``src`` 名衝突を回避・無改変）。"""
    cached = sys.modules.get(_MA_CORE_MODNAME)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(_MA_CORE_MODNAME, _MA_CORE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MA_CORE_MODNAME] = module
    spec.loader.exec_module(module)
    return module


def _ma_funcs():
    core = _load_ma_core()
    return {
        "sma": core.simple_ma_on_buffer,
        "ema": core.exponential_ma_on_buffer,
        "smma": core.smoothed_ma_on_buffer,
        "lwma": core.linear_weighted_ma_on_buffer,
    }


def moving_average_on_series(series: np.ndarray, ma_type: str, length: int) -> np.ndarray:
    """``series``（例: btlm_mean）へ moving_averages の MA を当て、warm-up を NaN マスクする。

    NaN を含む系列（btlm_mean のウォームアップ）は、有限区間のみに MA を適用し、先頭の
    NaN 位置は NaN のまま保つ（moving_averages core は有限入力を前提とするため）。

    Args:
        series: 対象系列（昇順・NaN 可）。
        ma_type: sma / ema / smma / lwma。
        length: 平均本数（>=2）。

    Returns:
        MA 系列（series と同長・warm-up と入力 NaN は NaN）。

    Raises:
        ValueError: 未知の MA 種別。
    """
    ma_type = str(ma_type).lower()
    funcs = _ma_funcs()
    if ma_type not in funcs:
        raise ValueError(f"未知の MA 種別です: {ma_type}")
    length = max(2, int(round(float(length))))

    series = np.asarray(series, dtype=np.float64).ravel()
    n = series.size
    out = np.full(n, np.nan)
    finite_mask = np.isfinite(series)
    if not finite_mask.any():
        return out
    # 有限区間は先頭 NaN（btlm_mean のウォームアップ）を除いた連続後半。最初の有限 index から末尾へ。
    start = int(np.argmax(finite_mask))
    finite_slice = series[start:]
    if not np.isfinite(finite_slice).all():
        # 途中に NaN が混在する場合は安全側で NaN 埋め区間を除外できないため未適用（実運用は連続後半のみ）。
        finite_slice = np.nan_to_num(finite_slice, nan=finite_slice[np.isfinite(finite_slice)][0])
    m = finite_slice.size
    if length > m:
        return out
    buffer = np.zeros(m, dtype=np.float64)
    funcs[ma_type](m, 0, 0, length, finite_slice, buffer)
    valid_from = 0 if ma_type in _FROM_ZERO else length - 1
    if valid_from > 0:
        buffer[:valid_from] = np.nan
    out[start:] = buffer
    return out
