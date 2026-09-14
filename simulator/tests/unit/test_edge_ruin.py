"""edge_ruin（E-4「エッジと破産確率 — f を決める」Python 実装）の単体検定。

移植元（参照実装・§12.6 で「プロトタイプ＝Python 移植用」と裁定済み）:
    ``integrated_position_sizing_calculator.html``
      :582      kelly(p,R)          → kelly_fraction
      :583      growth(f,p,R)       → growth_rate
      :586      ev=R*p-q            → EdgeRuinResult.expected_value
      :587      refR=(q/p)^N        → EdgeRuinResult.equal_bet_ruin_reference
      :598-605  SIMS / simRoR       → SIMS / simulate_ruin_probability
      :624-644  runMC の計算部       → solve_edge_ruin
      :686      mulberry32(a)       → Mulberry32（§12.6 の「シード設定項目化」の実体）

検定の強度（§12.6「MC 部はシード固定での再現性＋参照実装と同条件での統計的整合」）:
    本実装は参照実装の PRNG（同ファイル :686 の mulberry32）を移植しているため、
    統計的整合ではなく **bit 単位の厳密一致**で固定する。golden は
    ``/tmp`` ではなくこのファイル内に literal で持ち（第三者が node で再生成できる形）、
    生成器は参照実装の該当行を切り出し、§12.6 の裁定に従って `Math.random` を
    同ファイル :686 の `mulberry32(seed)` へ差し替えた**RNG 差し替え版の参照実装**
    である（アルゴリズムは同一。乱数源のみ確定化した）。

    * 閉形式部（EV・f*・(q/p)^N）: 式から導いた期待値と**厳密一致**（許容 0）。
    * MC 部（RoR・破産確率制約 f）: 参照実装の同条件出力と**厳密一致**（許容 0）。
    * g(f)（幾何成長率）のみ: `math.log` と V8 `Math.log` の**最終桁（ULP）差**が出るため、
      相対許容 1e-15（≒ 数 ULP）で固定する。実測差は 1 ULP（0.05411532090976834 vs
      ...8366）。アルゴリズム差ではなく libm の丸め差であり、参照実装への準拠は保たれる。
      なお RoR 側は log を含むが、実測では 5 ケース全てで比較結果が一致した（下記 golden）。

実行時間（F.I.R.S.T の Fast）: 参照実装既定の SIMS=4000 は Python では 60 格子 ×4000×T
    のループになり 1 ケース約 25 秒かかる。そのため**既定値 1 ケースのみ SIMS=4000 で
    厳密照合**し、残りは sims=200 の同一アルゴリズム golden で網羅する（sims は仕様上の
    入力であり、減らしてもアルゴリズムの同一性検定としての性質は変わらない）。
"""
from __future__ import annotations

import math

import pytest

from simulator.usecase.edge_ruin import (
    SIMS,
    EdgeRuinSpec,
    Mulberry32,
    growth_rate,
    kelly_fraction,
    simulate_ruin_probability,
    solve_edge_ruin,
)

# node で再生成できる golden。生成器は参照実装 :582-686 の該当行を切り出し、
# §12.6 の裁定に従って Math.random を同ファイル :686 の mulberry32(seed) へ
# 差し替えた**RNG 差し替え版の参照実装**である（アルゴリズムは同一・乱数源のみ確定化）。
_RAW_SEED_1 = [
    0.6270739405881613,
    0.002735721180215478,
    0.5274470399599522,
    0.9810509674716741,
    0.9683778982143849,
    0.281103502959013,
    0.6128388606011868,
    0.7207431411370635,
]
_RAW_SEED_42 = [
    0.6011037519201636,
    0.44829055899754167,
    0.8524657934904099,
    0.6697340414393693,
]


# --- 1. PRNG（参照実装 :686 の移植）--------------------------------------

def test_mulberry32はJS参照実装とbit一致する() -> None:
    """§12.6: 決定性の土台。ここがずれると以下の RoR golden が全て無意味になる。"""
    # Arrange
    rng = Mulberry32(1)
    # Act
    got = [rng.random() for _ in range(8)]
    # Assert（浮動小数の同一表現＝完全一致）
    assert got == _RAW_SEED_1


def test_mulberry32は別シードで別系列を返す() -> None:
    rng = Mulberry32(42)
    assert [rng.random() for _ in range(4)] == _RAW_SEED_42


def test_同一シードは同一系列を再現する() -> None:
    """バックテストの「同一入力→同一出力」要件（§12.6）。"""
    assert [Mulberry32(5).random() for _ in range(3)] == [
        Mulberry32(5).random() for _ in range(3)
    ]


# --- 2. 閉形式部 -----------------------------------------------------------

def test_ケリー比は閉形式と一致する() -> None:
    """:582 kelly(p,R) = (R·p − q)/R。"""
    # Arrange
    p, R = 0.38, 2.74
    # Act
    got = kelly_fraction(p, R)
    # Assert
    assert got == (R * p - (1 - p)) / R
    assert got == pytest.approx(0.1537226277372263, abs=0.0)


def test_エッジが無ければケリー比は負になる() -> None:
    """EV<=0 のとき f*<=0（=賭けない）。:648「EV≤0：賭けない」。"""
    assert kelly_fraction(0.30, 1.0) == pytest.approx(-0.4)


@pytest.mark.parametrize(
    "f, expected",
    [
        (0.0, 0.0),      # :583 f<=0 → 0
        (-0.1, 0.0),     # :583 f<=0 → 0
    ],
)
def test_成長率はf以下0で0を返す(f: float, expected: float) -> None:
    assert growth_rate(f, 0.38, 2.74) == expected


def test_成長率はf1以上で負の無限大() -> None:
    """:583 f>=1 → −Infinity（全額賭けは 1 敗で 0 になる）。"""
    assert growth_rate(1.0, 0.38, 2.74) == -math.inf
    assert growth_rate(1.5, 0.38, 2.74) == -math.inf


def test_成長率は閉形式と一致する() -> None:
    """:583 g = p·ln(1+R·f) + q·ln(1−f)。"""
    # Arrange
    p, R, f = 0.38, 2.74, 0.1
    # Act / Assert
    assert growth_rate(f, p, R) == p * math.log(1 + R * f) + (1 - p) * math.log(1 - f)


# --- 3. MC 部（参照実装と厳密一致）----------------------------------------

def test_破産確率はf以下0で0を返す() -> None:
    """:600 if(f<=0) return 0。"""
    assert simulate_ruin_probability(
        0.0, 0.38, 2.74, 0.5, 250, SIMS, Mulberry32(1)
    ) == 0.0


def test_試行回数の既定は参照実装と同じ4000() -> None:
    """:598 const SIMS=4000。"""
    assert SIMS == 4000


def test_破産確率は参照実装と厳密一致する() -> None:
    """:632 相当（grid 1 点目・SIMS=4000・seed=1）。"""
    # Arrange（case1 の grid[4] = fmax*5/60, fmax=0.3689343065693431）
    rng = Mulberry32(1)
    fmax = 0.3689343065693431
    # Act（参照実装と同じ消費順＝grid の先頭から順に消費する）
    got = [
        simulate_ruin_probability(fmax * i / 60, 0.38, 2.74, 0.5, 250, SIMS, rng)
        for i in range(1, 6)
    ]
    # Assert（golden: cases[0].rorHead）
    assert got == [0.0, 0.0, 0.0, 0.0, 0.00275]


# --- 4. solve_edge_ruin（runMC :624-644 の計算部）--------------------------

# golden: node で参照実装の該当行（:582-686）をそのまま実行した出力。
# 生成器は `/tmp/edge_ruin_golden*.mjs` と同内容（本ファイル冒頭の行対応で再生成可能）。
#   キー: (p, R, ruin, alpha, T, N, seed, sims)
_EXACT_KEYS = (
    "loss_rate", "expected_value", "kelly_fraction", "half_kelly_fraction",
    "constrained_fraction", "ror_at_constrained", "ror_at_kelly",
    "equal_bet_ruin_reference", "f_max",
)
# log の ULP 差が出る項目（相対許容 1e-15 で固定）
_ULP_KEYS = ("growth_at_kelly", "growth_at_constrained")

_GOLDEN_FULL_SIMS = (
    # 参照実装 HTML の入力既定値（:278-289 の value）＋ SIMS=4000（:598）。
    (0.38, 2.74, 0.50, 0.01, 250, 20, 1, SIMS),
    {
        "loss_rate": 0.62,
        "expected_value": 0.42120000000000013,
        "kelly_fraction": 0.1537226277372263,
        "half_kelly_fraction": 0.07686131386861315,
        "constrained_fraction": 0.04061513638109874,
        "ror_at_constrained": 0.01,
        "ror_at_kelly": 0.456375,
        "growth_at_kelly": 0.030087574214345686,
        "growth_at_constrained": 0.014389583754579813,
        "equal_bet_ruin_reference": 1,
        "f_max": 0.3689343065693431,
        "ror_head": [0.0, 0.0, 0.0, 0.0, 0.00275],
        "growth_head": [
            0.0025248155091435703,
            0.004921563524003383,
            0.007193321405237143,
            0.009342999882198649,
            0.011373353000799065,
        ],
    },
)

_GOLDEN_FAST = [
    (
        (0.55, 1.20, 0.50, 0.05, 52, 10, 1, 200),
        {
            "loss_rate": 0.44999999999999996,
            "expected_value": 0.21000000000000008,
            "kelly_fraction": 0.17500000000000007,
            "half_kelly_fraction": 0.08750000000000004,
            "constrained_fraction": 0.07700000000000003,
            "ror_at_constrained": 0.05,
            "ror_at_kelly": 0.345,
            "growth_at_kelly": 0.018273846093402088,
            "growth_at_constrained": 0.012550690823898883,
            "equal_bet_ruin_reference": 0.13443063274931166,
            "f_max": 0.42000000000000015,
        },
    ),
    (   # シードのみ差し替え（§12.6 決定性の反対側＝シードが実際に効いていること）
        (0.38, 2.74, 0.50, 0.01, 250, 20, 7, 200),
        {
            "loss_rate": 0.62,
            "expected_value": 0.42120000000000013,
            "kelly_fraction": 0.1537226277372263,
            "half_kelly_fraction": 0.07686131386861315,
            "constrained_fraction": 0.03996788321167884,
            "ror_at_constrained": 0.01,
            "ror_at_kelly": 0.425,
            "growth_at_kelly": 0.030087574214345686,
            "growth_at_constrained": 0.014200811168553474,
            "equal_bet_ruin_reference": 1,
            "f_max": 0.3689343065693431,
        },
    ),
    (   # EV<=0（fk<=0）: :639 rorAtKelly=0 / :643 fHalf=0 / :640 gK=0
        (0.30, 1.00, 0.50, 0.01, 250, 20, 1, 200),
        {
            "loss_rate": 0.7,
            "expected_value": -0.39999999999999997,
            "kelly_fraction": -0.39999999999999997,
            "half_kelly_fraction": 0.0,
            "constrained_fraction": 0.0,
            "ror_at_constrained": 0.0,
            "ror_at_kelly": 0.0,
            "growth_at_kelly": 0.0,
            "growth_at_constrained": 0.0,
            "equal_bet_ruin_reference": 1,
            "f_max": 0.35,
        },
    ),
    (   # p<=q（:587/:640 の分岐で参考解 = 1）
        (0.40, 3.00, 0.50, 0.01, 100, 20, 1, 200),
        {
            "loss_rate": 0.6,
            "expected_value": 0.6000000000000002,
            "kelly_fraction": 0.20000000000000007,
            "half_kelly_fraction": 0.10000000000000003,
            "constrained_fraction": 0.058000000000000024,
            "ror_at_constrained": 0.01,
            "ror_at_kelly": 0.465,
            "growth_at_kelly": 0.05411532090976834,
            "growth_at_constrained": 0.02831668591889752,
            "equal_bet_ruin_reference": 1,
            "f_max": 0.48000000000000015,
        },
    ),
]


def _spec(args: tuple) -> EdgeRuinSpec:
    p, R, ruin, alpha, T, N, seed, sims = args
    return EdgeRuinSpec(
        win_rate=p, payoff_ratio=R, ruin_level=ruin, alpha=alpha,
        horizon=T, split_count=N, seed=seed, sims=sims,
    )


def _assert_matches_golden(got, expected: dict) -> None:
    for key in _EXACT_KEYS:
        assert getattr(got, key) == expected[key], f"{key} が参照実装と一致しない"
    for key in _ULP_KEYS:
        assert getattr(got, key) == pytest.approx(expected[key], rel=1e-15), (
            f"{key} が参照実装と ULP 許容内で一致しない"
        )


@pytest.mark.parametrize("args, expected", _GOLDEN_FAST)
def test_solve_edge_ruinは参照実装と一致する(args: tuple, expected: dict) -> None:
    """§12.6 移植範囲（EV・f*・RoR(f)・破産確率制約 f・(q/p)^N）の全出力を固定する。"""
    # Arrange / Act
    got = solve_edge_ruin(_spec(args))
    # Assert
    _assert_matches_golden(got, expected)


def test_参照実装の既定値かつSIMS4000で厳密一致する() -> None:
    """E-4 の本命。参照実装 UI の初期表示と同一条件（:278-289 の value ＋ :598 SIMS）。

    ここが一致することが「アルゴリズムに厳密準拠」（§12.6）の実証である。
    """
    # Arrange
    args, expected = _GOLDEN_FULL_SIMS
    # Act
    got = solve_edge_ruin(_spec(args))
    # Assert
    _assert_matches_golden(got, expected)
    # :628-629 steps=60・grid[i]=fmax*i/steps（i=1..60）
    assert len(got.ror_curve) == 60
    assert len(got.growth_curve) == 60
    # grid は fmax*i/steps をそのまま評価する（fmax*60/60 は fmax と bit 一致しない。
    # 「最後は fmax」と書き換えると参照実装から離れるため、式のまま固定する）。
    assert got.ror_curve[0][0] == expected["f_max"] * 1 / 60
    assert got.ror_curve[-1][0] == expected["f_max"] * 60 / 60
    assert [pt[1] for pt in got.ror_curve[:5]] == expected["ror_head"]
    assert [pt[1] for pt in got.growth_curve[:5]] == pytest.approx(
        expected["growth_head"], rel=1e-15
    )


def test_同一入力は同一出力を返す() -> None:
    """§12.6 決定性（バックテストの「同一入力→同一出力」要件）。"""
    # Arrange
    spec = _spec((0.38, 2.74, 0.50, 0.01, 250, 20, 1, 100))
    # Act / Assert
    assert solve_edge_ruin(spec) == solve_edge_ruin(spec)


def test_シードが違えばMC出力が変わる() -> None:
    """シードが実際に効いていること（固定値を返すだけの実装を弾く）。"""
    # Arrange / Act
    a = solve_edge_ruin(_spec((0.38, 2.74, 0.50, 0.01, 250, 20, 1, 200)))
    b = solve_edge_ruin(_spec((0.38, 2.74, 0.50, 0.01, 250, 20, 7, 200)))
    # Assert（閉形式部は不変・MC 部のみ変わる）
    assert a.kelly_fraction == b.kelly_fraction
    assert a.constrained_fraction != b.constrained_fraction


def test_シードと試行数の既定値は参照実装準拠の固定値である() -> None:
    """§12.6「乱数はシードを設定項目化（既定は固定値）」＋ :598 SIMS=4000。"""
    spec = EdgeRuinSpec(
        win_rate=0.38, payoff_ratio=2.74, ruin_level=0.5,
        alpha=0.01, horizon=250, split_count=20,
    )
    assert spec.seed == 1
    assert spec.sims == SIMS


# --- 5. 入力検証 -----------------------------------------------------------

@pytest.mark.parametrize(
    "field, value",
    [
        ("win_rate", -0.1),
        ("win_rate", 1.1),
        ("payoff_ratio", 0.0),
        ("payoff_ratio", -1.0),
        ("ruin_level", 0.0),
        ("ruin_level", 1.5),
        ("alpha", -0.1),
        ("alpha", 1.1),
        ("horizon", 0),
        ("split_count", 0),
        ("sims", 0),
    ],
)
def test_範囲外入力は例外(field: str, value: float) -> None:
    """無音の誤動作を作らない（範囲外を黙って既定値へ倒さない）。"""
    base = dict(
        win_rate=0.38, payoff_ratio=2.74, ruin_level=0.5,
        alpha=0.01, horizon=250, split_count=20,
    )
    base[field] = value
    with pytest.raises(ValueError):
        EdgeRuinSpec(**base)
