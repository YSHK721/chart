"""分位点から始値基準の統計バンドを生成する層。

元 MQL5 の ``UpdateBuffer`` 相当: バンド値 = 始値 ± 分位点。
描画に使用する 4 系統 × 7 パーセンタイル = 28 列を生成する。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .core import (
    PROBABILITIES,
    DistanceSamples,
    collect_distance_samples,
    compute_quantiles,
)

# 始値に対する符号。元 UpdateBuffer の isNegative に対応:
#   isNegative=True  -> price - result（始値の下側）
#   isNegative=False -> price + result（始値の上側）
# 描画対象は pOL(上)/nOH(下) の塗りバンドと pOH(上)/nOL(下) の点線。
_BAND_SIGNS: dict[str, int] = {
    "pOL": +1,
    "nOH": -1,
    "pOH": +1,
    "nOL": -1,
}


class EmptyBucketError(ValueError):
    """描画必須バケット(pOL/nOH/pOH/nOL)が空でバンド生成が不能な場合の例外。

    ``ValueError`` のサブクラス（後方互換）: 既存の ``except ValueError`` /
    ``pytest.raises(ValueError)`` はそのまま本例外を捕捉する。メッセージ・送出条件は従来と
    同一で、型のみを特殊化する。結線層（indicator_ui の compute adapter）が「必須バケット空」
    (empty_series) と「normalize 不正等の検証失敗」(validation) を、日本語メッセージ片照合でなく
    *型* で識別できるようにするための専用型（LSP 是正）。
    """


def _percent_tag(probability: float) -> str:
    """0.51 -> '51'、0.99 -> '99' のように百分率表記へ変換する。"""
    return str(int(round(probability * 100)))


def build_bands(
    df: pd.DataFrame,
    probabilities: tuple[float, ...] = PROBABILITIES,
    *,
    require_full: bool = True,
) -> pd.DataFrame:
    """OHLC の DataFrame から統計バンドの DataFrame を生成する。

    Args:
        df: ``open`` / ``high`` / ``low`` / ``close`` 列を持つ DataFrame。
            列名は大文字小文字を区別しない。元 df の index を引き継ぐ。
        probabilities: 算出する確率の並び（既定は PROBABILITIES）。
        require_full: True かつ描画必須バケット(pOL/nOH/pOH/nOL)が空の場合に
            ValueError を送出する。False なら該当列を NaN で返す。

    Returns:
        各行 = 入力ローソク足、各列 = ``{bucket}_{percent}``（例 ``pOL_99``）の
        バンド値を持つ DataFrame。列順は系統(pOL,nOH,pOH,nOL)× 確率昇順。

    Raises:
        KeyError: 必須の OHLC 列が欠けている場合。
        EmptyBucketError: require_full=True で必須バケットが空の場合（ValueError サブクラス）。
    """
    cols = {c.lower(): c for c in df.columns}
    missing = [k for k in ("open", "high", "low", "close") if k not in cols]
    if missing:
        raise KeyError(f"必須列が不足しています: {missing}（存在する列: {list(df.columns)}）")

    open_ = df[cols["open"]].to_numpy(dtype=np.float64)
    high = df[cols["high"]].to_numpy(dtype=np.float64)
    low = df[cols["low"]].to_numpy(dtype=np.float64)
    close = df[cols["close"]].to_numpy(dtype=np.float64)

    samples: DistanceSamples = collect_distance_samples(open_, high, low, close)
    quantiles = compute_quantiles(samples, probabilities)

    if require_full:
        empty = [b for b in _BAND_SIGNS if np.isnan(quantiles[b]).all()]
        if empty:
            raise EmptyBucketError(
                f"バンド生成に必要なバケットが空です: {empty}。"
                "対象データに該当する陽線/陰線が存在するか確認してください。"
            )

    data: dict[str, np.ndarray] = {}
    for bucket, sign in _BAND_SIGNS.items():
        q = quantiles[bucket]
        for idx, prob in enumerate(probabilities):
            data[f"{bucket}_{_percent_tag(prob)}"] = open_ + sign * q[idx]

    return pd.DataFrame(data, index=df.index)
