"""CATALOG_SCHEMA（ISSUE-092 ③）— 指標 param 既定値の単一情報源（single source of truth）。

背景（ISSUE-091 A5）: 指標追加・param 変更時に既定値が 4 面（back ``call_binding._TABLE`` /
指標 src の ``add_*`` シグネチャ / front ``usecase/catalog.js`` 静的レジストリ / latest_meta）へ
分散し、front/back の乖離が構造的に検出できなかった。

本モジュールは param **既定値**（名前 → 既定値）を Python 側に単一定義する。この定義が「正」で
あり、front は ``GET /catalog`` 経由でこれを runtime overlay して既定値を解決する。フェッチ失敗時
のみ front 静的値（``catalog.js`` リテラル）へフォールバックする（オフライン耐性・後方互換）。
front 静的値と本定義の一致は back/front 双方のテスト（``catalog_defaults.json`` 契約）で固定する。

対象は ``call_binding._TABLE`` に登録された 19 compute_id（tgp_btlm / profit_band /
price_range_power / moving_averages ＋ profit_* 15）。``market_profile`` は独立アクター
（market_profile モジュール）が所有するため本 schema の対象外（front 側で静的定義を維持）。

表示ラベル・制約・UI メタ等の純 UI 情報は front（``catalog.js``）に残す（既定値のみ単一情報源化）。
"""

from __future__ import annotations

import copy
from typing import Any

# compute_id → {param_name: default}。param **既定値**の正（single source）。
# 値は front ``usecase/catalog.js`` の現行既定値と完全一致させる（UI 実効値を不変に保つ）。
# 一致は ``catalog_defaults.json`` 契約経由で back（test_catalog_schema）/ front
# （catalog_schema_sync.test.js）双方のテストが固定し、乖離を検出する。
PARAM_DEFAULTS: dict[str, dict[str, Any]] = {
    "tgp_btlm": {
        "fitter": "ols",
        "price": "open",
        "maxbars": 100,
        "q_low": 0.05,
        "q_high": 0.95,
        "mcmc_samples": "standard",
        "color": "rgba(123, 104, 238, 1)",
    },
    "btlm_trail": {
        "source": "close",
        "maxbars": 100,
        "q_low": 0.05,
        "q_high": 0.95,
        "band_method": "ols",
        "empirical_n": 500,
        "q_out": None,
        "show_metrics": True,
        "n_cov": 250,
        "color": "rgba(123, 104, 238, 1)",
    },
    "btlm_trail_marod": {
        "source": "close",
        "maxbars": 100,
        "color": "rgba(123, 104, 238, 1)",
    },
    "profit_band": {
        "probabilities": [0.51, 0.8, 0.85, 0.9, 0.95, 0.98, 0.99],
        "buckets": ["nOH", "pOL", "pOH", "nOL"],
        "require_full": True,
        "legend": False,
        "normalize": "return",
        "window": "expanding",
        "atr_period": 14,
        "min_obs": 30,
    },
    "price_range_power": {
        "interval": 0.1,
        "range_from": None,
        "range_to": None,
        "top_n": 5,
        "width": 2,
        "bull_color": "rgba(46, 158, 91, 0.9)",
        "bear_color": "rgba(210, 67, 58, 0.9)",
    },
    "moving_averages": {
        "ma_type": "ema",
        "length": 9,
        "source": "close",
        "offset": 0,
        "smoothing_type": "none",
        "smoothing_length": 9,
        "bb_stddev": 2.0,
        "timeframe": "chart",
        "wait_for_close": False,
    },
    "profit_adx_needle": {
        "period": 6,
        "window": 120,
    },
    "profit_arctan": {
        "period": 6,
        "ma_method": 1,
        "bar_width": 0.1,
        "window": 120,
    },
    "profit_mfi": {
        "mfi_period": 14,
        "ma_period": 5,
    },
    "profit_rsi": {
        "rsi_period": 6,
        "apply": 5,
        "ma_period": 5,
    },
    "profit_stc": {
        "period": 70,
    },
    "profit_oscillator": {
        "period_a": 6,
        "period_b": 60,
        "window": 120,
    },
    "profit_oscillator2": {
        "osc_period": 6,
        "stc_slow": 6,
        "ma_period": 60,
        "rci_period": 12,
        "direction": False,
    },
    "profit_osi_ma": {
        "ma_mode": 1,
        "ma_period": 21,
    },
    "profit_rmm": {
        "osc_period": 6,
        "ma_period": 6,
        "window": 120,
    },
    "profit_volatility": {
        "period": 6,
        "window": 120,
    },
    "profit_hl_band": {
        "window": 120,
    },
    "profit_hlband": {
        "draw_levels": True,
    },
    "profit_mfi_macd": {
        "mfi_period": 13,
        "fast": 4,
        "slow": 8,
        "signal": 4,
    },
    "profit_rmm_macd": {
        "osc_period": 6,
        "ma_period": 6,
        "fast": 4,
        "slow": 8,
        "signal": 4,
        "window": 120,
    },
    "profit_rsi_macd": {
        "rsi_period": 13,
        "fast": 4,
        "slow": 8,
        "signal": 4,
    },
}


def catalog_defaults() -> dict[str, dict[str, Any]]:
    """serving 用に ``PARAM_DEFAULTS`` の deep copy を返す（source を呼び出し側の変更から守る）。

    ``GET /catalog`` の controller（``handle_catalog``）がこれを JSON 応答へ載せる。
    """
    return copy.deepcopy(PARAM_DEFAULTS)
