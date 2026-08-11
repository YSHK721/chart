"""U-EdgeRuin: 「エッジと破産確率 — f を決める」（E-4・基本設計書 §12.6）。

位置づけ: ``integrated_position_sizing_calculator.html`` の Step 1 は**プロトタイプ
（Python 移植用）**であり（§12.6 依頼者言明）、本モジュールがその本実装である。移植後は
Python 側が権威となる。§12.3-3「複製禁止」の複製には当たらない（プロトタイプの本実装化）。

移植範囲（§12.6・Step 2/3 は対象外）:
    入力  p（勝率）・R（利益率）・破産水準・α（目標破産確率）・T（ホライズン）・N（口座分割数）
    出力  EV・フルケリー f*・RoR(f)・破産確率制約 f（RoR ≤ α の最大 f）・参考 (q/p)^N

移植元の行対応（参照実装をそのまま写す・条件を足さない／削らない）:
    :582      kelly(p,R)                        → :func:`kelly_fraction`
    :583      growth(f,p,R)                     → :func:`growth_rate`
    :586      q / ev=R*p−q                      → EdgeRuinResult.loss_rate / expected_value
    :587      refR = p>q ? (q/p)^N : 1          → EdgeRuinResult.equal_bet_ruin_reference
    :598      SIMS=4000                         → :data:`SIMS`
    :599-605  simRoR                            → :func:`simulate_ruin_probability`
    :628      fk / fmax / steps                 → :func:`solve_edge_ruin`
    :629-632  grid・gPts・rorPts                 → 同上
    :636-638  fSafe（走査＋線形補間）            → 同上
    :639-640  rorAtKelly・gK・gS・refRuin        → 同上
    :643      fFull / fHalf                     → 同上
    :686      mulberry32(a)                     → :class:`Mulberry32`

決定性（§12.6）: プロトタイプの Step 1 はシード無しの ``Math.random`` を使うが、
バックテストの「同一入力→同一出力」要件のため、**乱数シードのみ**を設定項目化する
（既定は固定値 1）。PRNG は参照実装が同ファイル :686 に持つ ``mulberry32`` を移植した。
別の PRNG を持ち込むと参照実装との照合が統計的比較に落ちるが、同一 PRNG なら
**bit 単位で照合できる**（検定 ``test_edge_ruin.py`` はこの厳密一致で固定している）。

usecase 層・**標準ライブラリのみ**（§12.6）。numpy / pandas を持ち込まない。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# :598 const SIMS=4000
SIMS = 4000
# :628 steps=60
_STEPS = 60
# mulberry32 の定数（:686 そのまま）
_MULBERRY_INC = 0x6D2B79F5
_U32 = 0xFFFFFFFF


class Mulberry32:
    """参照実装 :686 の ``mulberry32`` の移植（32bit PRNG）。

    JS は ``|0`` / ``Math.imul`` / ``>>>`` で 32bit 演算を行う。Python では全ての中間値を
    uint32 でマスクすることで同一のビット列を得る（``^`` ``+`` ``*`` はいずれも
    2^32 を法として同じ結果を与えるため、符号の有無は最終マスクで吸収される）。
    """

    __slots__ = ("_a",)

    def __init__(self, seed: int) -> None:
        self._a = seed & _U32

    def random(self) -> float:
        """[0,1) の一様乱数を返す（JS 版と同一の浮動小数を返す）。"""
        # a = a + 0x6D2B79F5 | 0
        self._a = (self._a + _MULBERRY_INC) & _U32
        a = self._a
        # t = Math.imul(a ^ a>>>15, 1|a)
        t = (((a ^ (a >> 15)) * (a | 1)) & _U32)
        # t = t + Math.imul(t ^ t>>>7, 61|t) ^ t
        t = ((t + (((t ^ (t >> 7)) * (t | 61)) & _U32)) & _U32) ^ t
        # return ((t ^ t>>>14) >>> 0) / 4294967296
        return ((t ^ (t >> 14)) & _U32) / 4294967296.0


def kelly_fraction(p: float, payoff_ratio: float) -> float:
    """:582 ``kelly(p,R) = (R·p − q)/R``。EV≤0 のとき負値を返す（=賭けない）。"""
    q = 1 - p
    return (payoff_ratio * p - q) / payoff_ratio


def growth_rate(f: float, p: float, payoff_ratio: float) -> float:
    """:583 幾何成長率 ``g(f) = p·ln(1+R·f) + q·ln(1−f)``。

    ``f<=0`` は 0、``f>=1`` は −∞（参照実装の条件をそのまま保つ）。
    """
    if f <= 0:
        return 0.0
    if f >= 1:
        return -math.inf
    q = 1 - p
    return p * math.log(1 + payoff_ratio * f) + q * math.log(1 - f)


def simulate_ruin_probability(
    f: float,
    p: float,
    payoff_ratio: float,
    ruin_level: float,
    horizon: int,
    sims: int,
    rng: Mulberry32,
) -> float:
    """:599-605 ``simRoR``。対数資産が破産水準を割った試行の比率を返す。

    各トレードを独立 2 値（勝ち +R·f ／ 負け −f）と仮定する（参照実装 :270 の前提注記）。
    ``rng`` は呼び出し側が保持する（参照実装が単一の ``Math.random`` ストリームを
    grid の順に消費するのと同じ消費順にするため）。
    """
    if f <= 0:
        return 0.0
    log_win = math.log(1 + payoff_ratio * f)
    lose = 1 - f
    log_lose = math.log(lose) if lose > 0 else -math.inf
    log_ruin = math.log(ruin_level)
    ruined = 0
    for _ in range(sims):
        le = 0.0
        for _ in range(horizon):
            le += log_win if rng.random() < p else log_lose
            if le <= log_ruin:
                ruined += 1
                break
    return ruined / sims


@dataclass(frozen=True)
class EdgeRuinSpec:
    """Step 1 の入力（参照実装 :278-289 の 6 入力＋§12.6 のシード）。

    ``win_rate`` / ``ruin_level`` / ``alpha`` は比（%ではない）。範囲外は ValueError
    （無音の誤動作を作らない＝黙って既定値へ倒さない）。
    """

    win_rate: float
    payoff_ratio: float
    ruin_level: float
    alpha: float
    horizon: int
    split_count: int
    seed: int = 1          # §12.6 既定は固定値
    sims: int = SIMS

    def __post_init__(self) -> None:
        if not 0.0 <= self.win_rate <= 1.0:
            raise ValueError(f"win_rate は [0,1] の比です: {self.win_rate}")
        if self.payoff_ratio <= 0:
            raise ValueError(f"payoff_ratio は正である必要があります: {self.payoff_ratio}")
        if not 0.0 < self.ruin_level <= 1.0:
            raise ValueError(f"ruin_level は (0,1] の比です: {self.ruin_level}")
        if not 0.0 <= self.alpha <= 1.0:
            raise ValueError(f"alpha は [0,1] の比です: {self.alpha}")
        if self.horizon < 1:
            raise ValueError(f"horizon は 1 以上です: {self.horizon}")
        if self.split_count < 1:
            raise ValueError(f"split_count は 1 以上です: {self.split_count}")
        if self.sims < 1:
            raise ValueError(f"sims は 1 以上です: {self.sims}")


@dataclass(frozen=True)
class EdgeRuinResult:
    """Step 1 の出力（参照実装 :643-644 の store＋カード 3 種＋図 1/図 2 の系列）。"""

    loss_rate: float                    # q = 1−p（:586）
    expected_value: float               # EV = R·p − q（:586）
    kelly_fraction: float               # フルケリー f*（:643 S.fFull）
    half_kelly_fraction: float          # ハーフケリー（:643 S.fHalf）
    constrained_fraction: float         # 破産確率制約 f（:643 S.fSafe）
    ror_at_constrained: float           # 制約点の RoR（:636-638 rorAtSafe）
    ror_at_kelly: float                 # フルケリー点の RoR（:639）
    growth_at_kelly: float              # g(f*)（:640 gK）
    growth_at_constrained: float        # g(fSafe)（:640 gS）
    equal_bet_ruin_reference: float     # 参考 (q/p)^N（:587/:640 refRuin）
    f_max: float                        # 走査上限（:628 fmax）
    ror_curve: "tuple[tuple[float, float], ...]" = field(default=())      # 図 2
    growth_curve: "tuple[tuple[float, float], ...]" = field(default=())   # 図 1


def solve_edge_ruin(spec: EdgeRuinSpec) -> EdgeRuinResult:
    """:624-644 ``runMC`` の計算部（DOM 描画を除く）をそのまま実行する。

    乱数の消費順は参照実装と同一（grid 昇順に ``sims`` 回ずつ → 最後にフルケリー点で
    ``sims*2`` 回）。順序を変えると同一シードでも系列がずれるため変更してはならない。
    """
    p = spec.win_rate
    payoff_ratio = spec.payoff_ratio
    q = 1 - p
    rng = Mulberry32(spec.seed)

    # :628 fk / fmax
    fk = kelly_fraction(p, payoff_ratio)
    f_max = min(0.9, max(0.35, (fk if fk > 0 else 0.1) * 2.4))

    # :629 grid（i=1..steps）
    grid = [f_max * i / _STEPS for i in range(1, _STEPS + 1)]
    # :630 gPts
    growth_curve = tuple((f, growth_rate(f, p, payoff_ratio)) for f in grid)
    # :631-633 rorPts（grid 昇順に消費）
    ror_curve = tuple(
        (
            f,
            simulate_ruin_probability(
                f, p, payoff_ratio, spec.ruin_level, spec.horizon, spec.sims, rng
            ),
        )
        for f in grid
    )

    # :636-637 先頭から連続して RoR≤α である最後の格子点
    f_safe = 0.0
    ror_at_safe = 0.0
    for f, ror in ror_curve:
        if ror <= spec.alpha:
            f_safe = f
            ror_at_safe = ror
        else:
            break
    # :638 最初の跨ぎ区間で線形補間（跨ぎが無ければ上の走査結果のまま）
    for i in range(1, len(ror_curve)):
        a_f, a_ror = ror_curve[i - 1]
        b_f, b_ror = ror_curve[i]
        if a_ror <= spec.alpha < b_ror:
            t = (spec.alpha - a_ror) / (b_ror - a_ror)
            f_safe = a_f + (b_f - a_f) * t
            ror_at_safe = spec.alpha
            break

    # :639 フルケリー点の RoR は 2 倍の試行数で測る
    ror_at_kelly = (
        simulate_ruin_probability(
            fk, p, payoff_ratio, spec.ruin_level, spec.horizon, spec.sims * 2, rng
        )
        if fk > 0
        else 0.0
    )
    # :640
    g_kelly = growth_rate(max(fk, 0.0), p, payoff_ratio)
    g_safe = growth_rate(f_safe, p, payoff_ratio)
    ref_ruin = (q / p) ** spec.split_count if p > q else 1

    return EdgeRuinResult(
        loss_rate=q,
        expected_value=payoff_ratio * p - q,
        kelly_fraction=fk,
        half_kelly_fraction=max(fk, 0.0) / 2,   # :643 S.fHalf
        constrained_fraction=f_safe,
        ror_at_constrained=ror_at_safe,
        ror_at_kelly=ror_at_kelly,
        growth_at_kelly=g_kelly,
        growth_at_constrained=g_safe,
        equal_bet_ruin_reference=ref_ruin,
        f_max=f_max,
        ror_curve=ror_curve,
        growth_curve=growth_curve,
    )
