"""quote_spread — 分内の気配幅（spread）を整数 points へ畳む規則の唯一源（ISSUE-511 段階 2）。

規則（MT5 テスター M1 の ``<SPREAD>`` 参照実装・依頼者裁定 2026-09-14）:
    spread = 分内 min(ask − bid) / point を最近接整数へ丸めた int points。
    .5 ちょうどの同値は偶数丸め（銀行丸め）。

``x ↦ round(x / point)`` は point > 0 で単調なので、「分内 min(ask − bid) → / point → 丸め」
の順に計算しても値は同じであり、丸めの発行は分の数だけで済む。

依存方向: pandas のみ。point は呼出側が注入する（銘柄仕様は読まない）。
"""
from __future__ import annotations

import math

import pandas as pd

# 偶数丸めの前に points 値を寄せる小数桁（浮動小数の表現誤差の除去）。
#   価格同士の差は表現誤差を持つ（実測: (63044.95 − 63037.8) / 0.1 = 71.49999999994179・
#   (63037.6 → 63044.65) は 70.5000000000291）。そのまま丸めると真値 x.5 の偶数丸めが誤差の向きで
#   決まってしまう。誤差は 1e-10 points 程度、実在する幅の桁は points で高々小数 2〜3 桁なので、
#   小数 6 桁へ寄せれば誤差だけが消え、x.5 は正確に x.5（2 進で表現可能）になる。
_SNAP_DECIMALS = 6


def validate_point(point: float) -> float:
    """point を正の有限な実数に限定して返す（fail-fast）。

    bool・0・負・NaN・inf・非数値は :class:`ValueError`。黙って計算すると 0 除算や
    符号反転した spread が正しく見える値として CSV に残る。
    """
    if isinstance(point, bool) or not isinstance(point, (int, float)):
        raise ValueError(f"point は正の有限な実数です: {point!r}")
    if not math.isfinite(point) or point <= 0:
        raise ValueError(f"point は正の有限な実数です: {point!r}")
    return float(point)


def minute_spread_points(
    bid: pd.Series, ask: pd.Series, minute: pd.Series, *, point: float
) -> pd.Series:
    """分ごとの spread（int64 points）を返す。index は分の一意値の昇順。"""
    point = validate_point(point)
    narrowest = (ask - bid).groupby(minute.to_numpy(), sort=True).min()
    # 換算はモジュールグローバル名で呼ぶ（分ごとに 1 回であることを検定する Spy の継ぎ目）。
    return _width_to_points(narrowest, point)


def _width_to_points(width: pd.Series, point: float) -> pd.Series:
    """気配幅（価格差）を int64 points へ換算する（除算と丸めの唯一の置き場所）。

    分内 min を取った**後**の幅だけを受ける（ティックごとに丸めない・R-3）。
    """
    # pandas の round は偶数丸め（numpy.round と同じ）。
    return (width / point).round(_SNAP_DECIMALS).round().astype("int64")
