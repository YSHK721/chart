"""E-4 窓内の実ティック価格列（domain・依存ゼロ）。

接点検証の tick 窓算出（中央値ベースの外れ値除去）の純ロジック。parquet IO は adapter へ隔離する。
現時点で挙動を固定しているのは ``tests/unit/test_tick_mid_series.py``（境界値 AAA）である。

入力は ``(sec, price)`` の列である。価格は adapter が **ref の価格基準**（台帳の ``price_basis``）で
畳んだ値で渡る（ISSUE-512 段階 4 の前提）。以前は本モジュールが ``mid=(bid+ask)/2`` を自前で
持っていたため、確定足が bid の ref（jp225_mt5）でもリプレイの足内ティックが mid で描かれていた
（ISSUE-515 と同型）。価格の畳み方の唯一の規則は marketdata/tick_m1.py にあり、本モジュールは
窓と外れ値だけを担う（規則を 2 箇所に持たない）。

    1. 窓 [start, end) フィルタ（secs>=start & secs<end）
    2. 窓内価格の中央値 m を取り、m>0 のとき |p/m - 1| <= threshold のみ残す（外れ値除去）
    3. cap 無し（接点検証＝全件・絶対仕様）

中央値は偶数個で中央 2 点平均（pandas .median() と一致 = statistics.median）。
pandas/numpy を import しない。
"""
from __future__ import annotations

from statistics import median
from typing import Iterable, Sequence, Tuple

# 外れ値補正の許容相対乖離（0.3 = ±30%）。
#
# ISSUE-032 の裁定（2026-07-30）: 本定数は ``marketdata.outlier_policy.OUTLIER_THRESHOLD``
#   （同値 0.3）とは **意図的に独立** の定数である。統合しない理由:
#     - 対象が異なる: 本定数は「バー内 tick の価格系列」に対する中央値ベースの外れ値除去、
#       marketdata 側は「確定足 OHLC」に対するクランプで、アルゴリズムが別物である。
#     - 層が異なる: 本モジュールは replay_ui の domain 層であり、データ取得基盤である
#       marketdata へ依存させると domain → infrastructure の逆流になる。
#   値が偶々一致しているだけなので、一方の調整が他方へ波及してはならない。
OUTLIER_THRESHOLD = 0.3


def price_series(
    prices: "Iterable[Sequence[float]]",
    start: int,
    end: int,
    *,
    threshold: float = OUTLIER_THRESHOLD,
) -> "list[Tuple[int, float]]":
    """窓 ``[start, end)`` の ``[(sec, price), ...]`` を外れ値除去して時系列順で返す（cap 無し）。"""
    win_secs: "list[int]" = []
    win_prices: "list[float]" = []
    for row in prices:
        sec = int(row[0])
        if start <= sec < end:
            win_secs.append(sec)
            win_prices.append(float(row[1]))

    if not win_prices:
        return []

    # NaN 価格は中央値算出から除外する＝pandas ``median()``（skipna）と一致。statistics.median は
    #   NaN 混入で破損するため、事前に除く。
    valid = [v for v in win_prices if v == v]  # v == v は NaN で False
    if not valid:
        return []
    m = float(median(valid))
    if m > 0:
        # NaN 行は ``abs(nan) <= threshold`` が False で自然に落ちるが、明示して意図を固定する。
        return [
            (s, v)
            for s, v in zip(win_secs, win_prices)
            if v == v and abs(v / m - 1.0) <= threshold
        ]
    return [(s, v) for s, v in zip(win_secs, win_prices) if v == v]
