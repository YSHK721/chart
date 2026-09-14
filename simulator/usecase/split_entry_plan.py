"""U-SplitEntryPlan: 「分割エントリー — f をロットに変換」（Step 3・ISSUE-368 スライス 0）。

位置づけ: ``integrated_position_sizing_calculator.html`` の Step 3 は**プロトタイプ
（Python 移植用）**であり、本モジュールがその本実装＝**権威**である（``edge_ruin.py`` が
Step 1 に対して置かれているのと同じ関係。§12.3-3「複製禁止」の複製には当たらない）。
JS 側（``domain/split_entry_plan.js``）は本モジュールから生成した golden fixture との
一致検定で従う（``.doc/LAYERING_CONVENTIONS.md:28-30``）。

移植範囲（TBD-1 裁定 2026-08-20 を反映）:
    建値は**価格の単一ソース＝チャート側**に一本化する。参照実装の `pmode='gap'`
    （間隔から建値を算出する系統）は移植しない。したがって
      - :908-913 ``offsetsFrom``（gap 専用）は**対応元から除外**
      - :964 の gap 分岐（``P0 ± sign·off[i]``）も**除外**
      - ``build(mode)`` の ``mode``（up/down）引数は**落とす**
        （参照実装 :1098 が「建値を直接指定したため順張り／逆張りの区別はなく、
        両カードは同一結果」と明示している＝direct では 2 系統が同一）
    ATR 系（:427-450, :852-873, :950 の atr 分岐）と `drawDunits`（:727-748）は対象外。

移植元の行対応（参照実装をそのまま写す・条件を足さない／削らない）:
    :880-888  genWeights(K,pattern)              → :func:`generate_weights`
    :928-933  ensureCustomPLen（建値既定シード）  → 呼び出し側（本モジュールは価格列を受ける）
    :953      effectiveTP（利確価格→距離）        → :class:`SplitEntrySpec`.take_price
    :957      E,V,TP,f の取得                     → :class:`SplitEntrySpec`
    :958      K（1..10 に丸め）                   → :class:`SplitEntrySpec`.__post_init__
    :963      direct の建値列                     → :class:`SplitEntrySpec`.entry_prices
    :965      nearest（long=min / short=max）     → :func:`build_split_entry_plan`
    :966-972  D・stop（smode='price' 系統のみ）    → 同上（stop_price 入力・D は逆算）
    :974      d[i]（建値→損切りの向き付き距離）   → SplitEntryPlan.distances
    :975      w = genWeights                      → SplitEntryPlan.weights
    :976-977  Swd・L1 = f·E/(V·Σwᵢdᵢ)             → weighted_distance_sum / base_lot
    :978-979  Lraw・L（int は Math.floor）        → lots_raw / lots
    :980-984  totalLot・sumLd・sumLP・avgP        → total_lot / avg_price
    :982      risk[i] = Lᵢdᵢ/ΣLᵢdᵢ                → risk_shares
    :983      totalRisk = V·ΣLᵢdᵢ                 → total_risk
    :985      roundZeroed                         → round_zeroed
    :986-995  rr / profitYen / winRate /
              breakeven / excess / evYen          → rr / profit_yen / profit_rate /
                                                    breakeven / excess / ev_yen
    :996      lossRate = totalRisk/E              → loss_rate
    :998-1001 mr・notional・reqMargin・marginUse   → required_margin / margin_use
    :1002     U = V·totalLot                      → （内部）
    :1009-1011 lcDist・immediateLC・lcPrice        → losscut_distance / immediate_lc /
                                                    losscut_price
    :1012-1013 stopDist・lcBeforeStop              → stop_distance / lc_before_stop
    :1015-1024 marginCapLot / lcCapLot / capLot    → cap_lot / cap_target
    :1025-1028 scaleMargin・totalLotBuild・effRisk → scale / buildable_lot / effective_risk
    :1029     marginBinds                         → margin_binds

権威式（§12.3-3 複製絶対禁止・``sizing_ports.py:42-52``）:
    必要証拠金・ロスカット価格は ``simulator.usecase.account_engine`` の
    ``official_required_margin`` / ``official_losscut_price`` を**呼ぶ**。閉形式を書き下さない。
    `cap_lot`（ロスカット価格基準）は上限を解いて書き下すと式の写しになるため、
    ``account_margin_sizing.py:166-184`` と同型の**単調性を使った二分探索**で権威関数に
    判定させる。

浮動小数の既知差（実測 2026-08-20・17 ケース）:
    参照実装は ``avgP − (E−reqMargin)/U`` という代数的に等価だが結合順の異なる式を使う。
    権威 ``official_losscut_price``（``avgP·(1±mr) ∓ E/U``）との差は
    losscut_price で相対 ≤ 2.44e-16（1〜2 ULP）、losscut_distance で差の相殺により
    相対 ≤ 1.04e-14（絶対 ≤ 1e-11 pt）。**複製禁止規律を優先し権威式を採る**。

usecase 層・**標準ライブラリのみ**。numpy / pandas を持ち込まない。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from simulator.usecase.account_engine import (
    official_losscut_price,
    official_required_margin,
)

LONG = "long"
SHORT = "short"

#: 参照実装 :484 の K の下限・上限（``min="1" max="10"``・:958 で丸め）
MIN_SPLITS = 1
MAX_SPLITS = 10

#: 重みパターン（:880-888）
WEIGHT_PATTERNS = ("equal", "linear", "double", "custom")
#: ロット単位（:366-369）
LOT_MODES = ("int", "dec")
#: 建て制約の基準（:515-518）
CAP_BASES = ("margin", "lc")

#: cap_lot の二分探索の反復回数（``account_margin_sizing.py`` と同型）。
#: 上限 :data:`_BISECT_UPPER` から double の仮数を尽くすのに十分な回数。
_BISECT_ITERS = 200
#: 二分探索の上限口数。ここで述語が真なら「制限なし」（参照実装の Infinity）とみなす。
_BISECT_UPPER = 1e18

#: :1029 marginBinds の判定に使う許容（参照実装そのまま）
_BINDS_EPS = 1e-9


def generate_weights(splits: int, pattern: str,
                     custom_weights: "tuple[float, ...] | None" = None) -> "tuple[float, ...]":
    """:880-888 ``genWeights(K,pattern)``。equal=1 / linear=i+1 / double=2^i / custom。"""
    if pattern not in WEIGHT_PATTERNS:
        raise ValueError(f"未知の weight_pattern です: {pattern!r} (既知: {WEIGHT_PATTERNS})")
    if pattern == "custom":
        if custom_weights is None:
            raise ValueError("weight_pattern='custom' には custom_weights が必要です")
        if len(custom_weights) < splits:
            raise ValueError(
                f"custom_weights の長さが分割数に足りません: {len(custom_weights)} < {splits}")
        return tuple(custom_weights[:splits])
    if pattern == "equal":
        return tuple(1.0 for _ in range(splits))
    if pattern == "double":
        return tuple(float(2 ** i) for i in range(splits))
    return tuple(float(i + 1) for i in range(splits))


@dataclass(frozen=True)
class SplitEntrySpec:
    """Step 3 の入力（TBD-1 反映＝建値は価格で受ける。gap・mode は無い）。

    範囲外は ValueError（無音の誤動作を作らない＝黙って既定値へ倒さない）。
    """

    direction: str
    entry_prices: "tuple[float, ...]"
    stop_price: float
    fraction: float
    balance: float
    point_value: float
    margin_rate: float
    win_rate: float
    take_price: "float | None" = None
    weight_pattern: str = "linear"
    custom_weights: "tuple[float, ...] | None" = None
    lot_mode: str = "int"
    cap_basis: str = "lc"

    def __post_init__(self) -> None:
        object.__setattr__(self, "entry_prices", tuple(float(p) for p in self.entry_prices))
        if self.custom_weights is not None:
            object.__setattr__(self, "custom_weights",
                               tuple(float(w) for w in self.custom_weights))
        if self.direction not in (LONG, SHORT):
            raise ValueError(f"direction は {LONG!r} / {SHORT!r} です: {self.direction!r}")
        if not MIN_SPLITS <= len(self.entry_prices) <= MAX_SPLITS:
            raise ValueError(
                f"entry_prices は {MIN_SPLITS}〜{MAX_SPLITS} 本です: {len(self.entry_prices)}")
        if self.lot_mode not in LOT_MODES:
            raise ValueError(f"lot_mode は {LOT_MODES} です: {self.lot_mode!r}")
        if self.cap_basis not in CAP_BASES:
            raise ValueError(f"cap_basis は {CAP_BASES} です: {self.cap_basis!r}")
        if self.weight_pattern not in WEIGHT_PATTERNS:
            raise ValueError(f"weight_pattern は {WEIGHT_PATTERNS} です: {self.weight_pattern!r}")
        if self.point_value <= 0:
            raise ValueError(f"point_value は正である必要があります: {self.point_value}")
        if self.margin_rate < 0:
            raise ValueError(f"margin_rate は 0 以上の比です: {self.margin_rate}")
        if not 0.0 <= self.win_rate <= 1.0:
            raise ValueError(f"win_rate は [0,1] の比です: {self.win_rate}")

    @property
    def splits(self) -> int:
        """分割本数 K（:958）。"""
        return len(self.entry_prices)


@dataclass(frozen=True)
class SplitEntryPlan:
    """Step 3 の出力（参照実装 :1030 の返り値と 1:1）。"""

    distances: "tuple[float, ...]"
    entry_prices: "tuple[float, ...]"
    weights: "tuple[float, ...]"
    weighted_distance_sum: float
    base_lot: float
    lots_raw: "tuple[float, ...]"
    lots: "tuple[float, ...]"
    total_lot: float
    avg_price: float
    risk_shares: "tuple[float, ...]"
    total_risk: float
    loss_rate: float
    stop_price: float
    stop_invalid: bool
    round_zeroed: bool
    rr: "float | None"
    profit_yen: "float | None"
    profit_rate: "float | None"
    breakeven: "float | None"
    excess: "float | None"
    ev_yen: "float | None"
    win_rate: float
    required_margin: float
    margin_use: float
    losscut_price: float
    losscut_distance: float
    stop_distance: float
    lc_before_stop: bool
    immediate_lc: bool
    cap_target: "float | None"
    cap_lot: float
    scale: float
    buildable_lot: float
    effective_risk: float
    margin_binds: bool


def _max_lot_with_losscut_beyond_target(direction: str, avg_price: float, target: float,
                                        balance: float, margin_rate: float,
                                        point_value: float) -> float:
    """ロスカットが ``target`` より手前に来ない最大の合計ロットを返す（:1017-1023 の権威版）。

    参照実装は上限を閉形式で書き下すが、それは ``official_losscut_price`` の式の写しに
    なるため採らない（§12.3-3）。ロスカット価格は合計ロットに対し単調（long は増加・
    short は減少）なので、``account_margin_sizing.py:166-184`` と同型の二分探索で
    **権威関数に判定させる**。
    """
    long = direction == LONG

    def safe(total_lot: float) -> bool:
        price = official_losscut_price(direction, [(avg_price, total_lot)],
                                       balance, margin_rate, point_value)
        if price is None:
            return True
        # 参照実装 :1021 は X ≦ target（long）／X ≧ target（short）を満たす上限を採る。
        return price <= target if long else price >= target

    if safe(_BISECT_UPPER):
        return math.inf
    lo, hi = 0.0, _BISECT_UPPER
    for _ in range(_BISECT_ITERS):
        mid = (lo + hi) / 2
        if mid <= lo or mid >= hi:
            break
        if safe(mid):
            lo = mid
        else:
            hi = mid
    return lo


def build_split_entry_plan(spec: SplitEntrySpec) -> SplitEntryPlan:
    """:956-1031 ``build()`` の計算部（DOM 直読み ``num()`` を除いた純関数部）。"""
    long = spec.direction == LONG
    k = spec.splits
    prices = spec.entry_prices
    balance = float(spec.balance)
    point_value = float(spec.point_value)
    margin_rate = float(spec.margin_rate)

    # :965 損切り側に最も近い建玉
    nearest = min(prices) if long else max(prices)
    # :966-972 損切りは価格指定（smode='price'）系統のみ移植。D は nearest から逆算する。
    stop = float(spec.stop_price)
    stop_invalid = not ((nearest - stop) > 0 if long else (stop - nearest) > 0)

    # :974 各建玉→損切りの向き付き距離
    distances = []
    for price in prices:
        di = (price - stop) if long else (stop - price)
        distances.append(di)
        if not di > 0:
            stop_invalid = True
    distances = tuple(distances)

    # :975-979 重み・基準ロット・各建玉のロット
    weights = generate_weights(k, spec.weight_pattern, spec.custom_weights)
    swd = 0.0
    for i in range(k):
        swd += weights[i] * distances[i]
    base_lot = spec.fraction * balance / (point_value * swd) if swd > 0 else 0.0
    lots_raw = tuple(base_lot * w for w in weights)
    # :979 OANDA は整数・切り捨て（保守側）
    lots = tuple(float(math.floor(x)) for x in lots_raw) if spec.lot_mode == "int" else lots_raw

    # :980-985 合計・リスク配分・加重平均建値
    total_lot = 0.0
    sum_ld = 0.0
    sum_lp = 0.0
    for i in range(k):
        total_lot += lots[i]
        sum_ld += lots[i] * distances[i]
        sum_lp += lots[i] * prices[i]
    risk_shares = tuple(lots[i] * distances[i] / sum_ld if sum_ld > 0 else 0.0 for i in range(k))
    total_risk = point_value * sum_ld
    avg_price = sum_lp / total_lot if total_lot > 0 else 0.0
    round_zeroed = spec.lot_mode == "int" and any(
        lots[i] == 0 and lots_raw[i] > 0 for i in range(k))

    # :986-995 利確ブロック。参照実装の TP は「第1建値 P₀ からの値幅」（:359 のラベル
    # 「第1建値 P₀」＝ :931 の direct 既定シード customP[0]=P0）であり、TBD-1 で P₀ 入力が
    # 消えた後は entry_prices[0] がその役割を負う。式の形（差を取ってから足し戻す）は
    # :953 / :989 のまま保つ（丸め経路を変えない）。
    rr = profit_yen = profit_rate = breakeven = excess = ev_yen = None
    if spec.take_price is not None:
        first = prices[0]
        take_distance = (spec.take_price - first) if long else (first - spec.take_price)
        if take_distance > 0 and total_lot > 0:
            target = (first + take_distance) if long else (first - take_distance)
            profit = 0.0
            for i in range(k):
                profit += lots[i] * abs(target - prices[i])
            rr = profit / (total_risk / point_value)
            profit_yen = profit * point_value
            profit_rate = profit_yen / balance if balance > 0 else 0.0
            breakeven = 1 / (1 + rr)
            excess = spec.win_rate - breakeven
            ev_yen = spec.win_rate * profit_yen - (1 - spec.win_rate) * total_risk

    # :996 損失率
    loss_rate = total_risk / balance if balance > 0 else 0.0

    # :998-1001 必要証拠金・使用率（権威式を呼ぶ）
    entries = [(prices[i], lots[i]) for i in range(k)]
    required_margin = official_required_margin(entries, margin_rate, point_value)
    margin_use = required_margin / balance if balance > 0 else 0.0

    # :1009-1011 ロスカット（権威式を呼ぶ。距離は加重平均建値からの向き付き差として派生）
    if total_lot > 0:
        losscut_price = official_losscut_price(spec.direction, entries, balance,
                                               margin_rate, point_value)
        losscut_distance = (avg_price - losscut_price) if long else (losscut_price - avg_price)
    else:
        # :1009 U=0 のとき参照実装は逆行距離 0・価格は avgP（=0）とする
        losscut_distance = 0.0
        losscut_price = (avg_price - losscut_distance) if long else (avg_price + losscut_distance)
    immediate_lc = margin_use >= 1

    # :1012-1013
    stop_distance = abs(avg_price - stop)
    lc_before_stop = immediate_lc or losscut_distance < stop_distance

    # :1015-1024 建て制約
    margin_cap_lot = total_lot / margin_use if margin_use > 0 else math.inf
    if spec.cap_basis == "lc":
        cap_target = stop            # :1018 ロスカット目標＝損切り価格
        cap_lot = _max_lot_with_losscut_beyond_target(
            spec.direction, avg_price, cap_target, balance, margin_rate, point_value)
    else:
        cap_target = None
        cap_lot = margin_cap_lot

    # :1025-1029
    scale = min(1.0, cap_lot / total_lot) if total_lot > 0 else 1.0
    buildable_lot = total_lot * scale
    if spec.lot_mode == "int":
        buildable_lot = float(math.floor(buildable_lot))
    effective_risk = total_risk * (buildable_lot / total_lot) if total_lot > 0 else 0.0
    margin_binds = cap_lot < total_lot - _BINDS_EPS

    return SplitEntryPlan(
        distances=distances,
        entry_prices=prices,
        weights=weights,
        weighted_distance_sum=swd,
        base_lot=base_lot,
        lots_raw=lots_raw,
        lots=lots,
        total_lot=total_lot,
        avg_price=avg_price,
        risk_shares=risk_shares,
        total_risk=total_risk,
        loss_rate=loss_rate,
        stop_price=stop,
        stop_invalid=stop_invalid,
        round_zeroed=round_zeroed,
        rr=rr,
        profit_yen=profit_yen,
        profit_rate=profit_rate,
        breakeven=breakeven,
        excess=excess,
        ev_yen=ev_yen,
        win_rate=spec.win_rate,
        required_margin=required_margin,
        margin_use=margin_use,
        losscut_price=losscut_price,
        losscut_distance=losscut_distance,
        stop_distance=stop_distance,
        lc_before_stop=lc_before_stop,
        immediate_lc=immediate_lc,
        cap_target=cap_target,
        cap_lot=cap_lot,
        scale=scale,
        buildable_lot=buildable_lot,
        effective_risk=effective_risk,
        margin_binds=margin_binds,
    )
