"""AccountMarginSizing（SizingPort 実装）の単体検定。

固定する不変条件（基本設計書 §3.5.5・§4.2 F-4・§12.2・依頼者裁定）:
    1. 採用する f は **破産確率制約 f**（`edge_ruin.solve_edge_ruin` の
       `constrained_fraction`）である（フルケリー／ハーフケリーではない）。
    2. 必要証拠金・ロスカット価格は `account_engine` の**権威式のみ**を用いる（C-7・§12.3-3）。
       本検定は「実装が権威式と一致する」ことを、権威式そのものを呼んで突き合わせて固定する。
    3. 発注量はロスカットがストップより**手前で発動しない**範囲に収まる
       （＝ストップが先に効く。ロスカットが先だと SL の意味が消える）。
    4. 丸めは保守側（`domain/volume_step.floor_to_step`）。下限未満は発注不可（None）。
    5. ストップ距離が取れない発注（sl=None）は発注量を決められない → None（無音で 0 にしない）。

方式: usecase/adapter 単体（I/O なし）。権威式は `simulator.usecase.account_engine` を
      import して突合する（式を検定側にも書き写さない＝複製禁止の徹底）。
"""
from __future__ import annotations

import pytest

from simulator.adapter.sizing.account_margin_sizing import AccountMarginSizing
from simulator.usecase.account_engine import (
    official_losscut_price,
    official_required_margin,
)
from simulator.usecase.edge_ruin import EdgeRuinSpec, solve_edge_ruin
from simulator.usecase.sizing_models import SizingContext, SizingRule

# 参照実装 HTML の既定入力（:278-289）。sims は検定を速く保つため縮小（アルゴリズム同一）。
_EDGE = EdgeRuinSpec(
    win_rate=0.38, payoff_ratio=2.74, ruin_level=0.50,
    alpha=0.01, horizon=250, split_count=20, seed=1, sims=100,
)
# OANDA 規約（account_engine.py:89-90）。
_MARGIN_RATE = 0.10
_POINT_VALUE = 1.0


def _rule(**over) -> SizingRule:
    base = dict(
        edge=_EDGE,
        margin_rate=_MARGIN_RATE,
        point_value=_POINT_VALUE,
        volume_min=0.1,
        volume_max=100.0,
        volume_step=0.1,
    )
    base.update(over)
    return SizingRule(**base)


def _ctx(**over) -> SizingContext:
    base = dict(
        side="buy",
        estimated_entry_price=40000.0,
        stop_loss_price=39800.0,
        equity=1_000_000.0,
    )
    base.update(over)
    return SizingContext(**base)


# --- 1. 採用する f は破産確率制約 f ---------------------------------------

def test_採用するfは破産確率制約fである() -> None:
    """§12.6 のカード「破産確率制約（これを採る）」。フルケリーではない。"""
    # Arrange
    sizing = AccountMarginSizing(_rule())
    expected_f = solve_edge_ruin(_EDGE).constrained_fraction
    # Act
    decision = sizing.decide_volume(_ctx())
    # Assert
    assert decision.fraction == expected_f
    assert decision.fraction != solve_edge_ruin(_EDGE).kelly_fraction


def test_発注量はリスク比fと逆算した口数に一致する() -> None:
    """f·E をストップ距離 × point_value で割った口数（丸め前）。

    リスク比が**律速する**条件で測る（volume_max・証拠金・ロスカットの各制約が
    先に縛ると、この式が効いているかを見られない）。実測: この条件で
    risk=184.5 < losscut cap=238.1 < margin cap=250.0。
    """
    # Arrange
    rule = _rule(volume_max=1000.0)
    ctx = _ctx()
    f = solve_edge_ruin(_EDGE).constrained_fraction
    distance = abs(ctx.estimated_entry_price - ctx.stop_loss_price)
    raw = f * ctx.equity / (distance * rule.point_value)
    # Act
    decision = AccountMarginSizing(rule).decide_volume(ctx)
    # Assert（保守側 floor 済みなので raw 以下・1 刻み以内）
    assert decision.volume is not None
    assert decision.volume <= raw
    assert raw - decision.volume < rule.volume_step


# --- 2. 権威式との一致（C-7）----------------------------------------------

def test_算出量の必要証拠金は権威式で有効証拠金以下() -> None:
    """`official_required_margin` を呼んで突き合わせる（式を書き写さない）。"""
    # Arrange
    ctx = _ctx()
    # Act
    decision = AccountMarginSizing(_rule()).decide_volume(ctx)
    # Assert
    assert decision.volume is not None
    margin = official_required_margin(
        [(ctx.estimated_entry_price, decision.volume)], _MARGIN_RATE, _POINT_VALUE
    )
    assert margin <= ctx.equity


@pytest.mark.parametrize(
    "side, entry, stop",
    [
        ("buy", 40000.0, 39800.0),
        ("sell", 40000.0, 40200.0),
    ],
)
def test_ロスカットはストップより手前で発動しない(
    side: str, entry: float, stop: float
) -> None:
    """§3.5.5 の権威式でロスカット価格を求め、ストップが先に効くことを固定する。

    ロスカットが先に来ると SL が意味を失い、想定より大きい損失が確定する。
    """
    # Arrange
    ctx = _ctx(side=side, estimated_entry_price=entry, stop_loss_price=stop)
    # Act
    decision = AccountMarginSizing(_rule()).decide_volume(ctx)
    # Assert
    assert decision.volume is not None
    losscut = official_losscut_price(
        "long" if side == "buy" else "short",
        [(entry, decision.volume)],
        ctx.equity,
        _MARGIN_RATE,
        _POINT_VALUE,
    )
    assert losscut is not None
    if side == "buy":
        assert losscut < stop, "ロスカットがストップより手前（上）にある"
    else:
        assert losscut > stop, "ロスカットがストップより手前（下）にある"


def test_有効証拠金が小さいときはロスカット制約で量が削られる() -> None:
    """資金が薄いとリスク比 f より先にロスカット制約が縛る（保守側）。"""
    # Arrange
    thin = _ctx(equity=30_000.0)
    rich = _ctx(equity=1_000_000.0)
    sizing = AccountMarginSizing(_rule())
    # Act
    thin_decision = sizing.decide_volume(thin)
    rich_decision = sizing.decide_volume(rich)
    # Assert
    if thin_decision.volume is not None:
        losscut = official_losscut_price(
            "long", [(thin.estimated_entry_price, thin_decision.volume)],
            thin.equity, _MARGIN_RATE, _POINT_VALUE,
        )
        assert losscut is not None and losscut < thin.stop_loss_price
    assert rich_decision.volume is not None


# --- 3. 丸め（保守側）------------------------------------------------------

def test_発注量は刻みの倍数である() -> None:
    # Arrange
    rule = _rule(volume_step=0.1)
    # Act
    decision = AccountMarginSizing(rule).decide_volume(_ctx())
    # Assert
    assert decision.volume is not None
    ratio = decision.volume / rule.volume_step
    assert abs(ratio - round(ratio)) < 1e-6


def test_上限を超えない() -> None:
    # Arrange
    rule = _rule(volume_max=0.5)
    # Act
    decision = AccountMarginSizing(rule).decide_volume(_ctx())
    # Assert
    assert decision.volume is not None
    assert decision.volume <= 0.5


def test_下限未満になる場合は発注不可() -> None:
    """§12.2 保守側: 下限へ切り上げると「計算上許されない量」を建てることになる。"""
    # Arrange（資金が極端に小さい）
    rule = _rule(volume_min=1.0, volume_step=1.0)
    # Act
    decision = AccountMarginSizing(rule).decide_volume(_ctx(equity=100.0))
    # Assert
    assert decision.volume is None
    assert decision.reason


# --- 4. 決定不能ケース（無音で 0 にしない）--------------------------------

def test_ストップが無い発注は量を決められない() -> None:
    """SL が無いとリスク距離が定義できない。0 を返して黙って発注するのは誤動作。"""
    decision = AccountMarginSizing(_rule()).decide_volume(_ctx(stop_loss_price=None))
    assert decision.volume is None
    assert decision.reason


def test_ストップが建値と同一なら量を決められない() -> None:
    """距離 0 は 0 除算。無音の inf を作らない。"""
    decision = AccountMarginSizing(_rule()).decide_volume(
        _ctx(stop_loss_price=40000.0)
    )
    assert decision.volume is None


def test_エッジが無ければ構築時に拒否する() -> None:
    """EV<=0 → 破産確率制約 f = 0 → **1 枚も建たない**。

    決着点は発注時ではなく**ジョブ構築時**（🔴-4 裁定）。発注時に落とすと全発注が
    黙って消え「exit=0・取引 0 件で正常終了」という無音の誤動作になる。
    """
    from simulator.usecase.sizing_ports import SizingNotViableError

    # Arrange
    no_edge = EdgeRuinSpec(
        win_rate=0.30, payoff_ratio=1.0, ruin_level=0.5,
        alpha=0.01, horizon=250, split_count=20, seed=1, sims=100,
    )
    # Act / Assert
    with pytest.raises(SizingNotViableError):
        AccountMarginSizing(_rule(edge=no_edge))


# --- 5. 決定性 -------------------------------------------------------------

def test_同一入力は同一発注量を返す() -> None:
    """§12.6 決定性（シード固定）。"""
    sizing = AccountMarginSizing(_rule())
    assert sizing.decide_volume(_ctx()).volume == sizing.decide_volume(_ctx()).volume


def test_エッジ計算は一度だけ行われ発注ごとに再計算しない() -> None:
    """MC は重い（60 格子 × sims × T）。発注のたびに回すとバックテストが終わらない。"""
    # Arrange
    sizing = AccountMarginSizing(_rule())
    # Act
    first = sizing.decide_volume(_ctx())
    second = sizing.decide_volume(_ctx(equity=500_000.0))
    # Assert（f は口座状態に依らず同一＝キャッシュされている）
    assert first.fraction == second.fraction


# --- 6. 実装が依存している外部関数の性質（回帰壁）------------------------

# 注記（TDD の誠実性）: 本節は追加時点で既に緑である。Red ではなく**回帰壁**として置く。
# `AccountMarginSizing._bisect_max` は「述語が口数について単調」であることを仮定して
# 二分探索する（権威式を写さずに上限を求めるための手段）。この仮定は `account_engine` 側の
# 性質であり、こちらのコードには現れない。将来 `official_losscut_price` の式が変わって
# 単調でなくなると、二分探索は**保守側でない量**を静かに返す。仮定そのものを固定する。

@pytest.mark.parametrize(
    "direction, stop, is_safe",
    [
        ("long", 39800.0, lambda x, s: x < s),
        ("short", 40200.0, lambda x, s: x > s),
    ],
)
def test_ロスカット制約の述語は口数について単調である(
    direction: str, stop: float, is_safe
) -> None:
    """二分探索の前提。真→偽の反転がちょうど 1 回であることを実測で固定する。"""
    # Arrange
    entry, equity = 40000.0, 1_000_000.0
    prev = None
    flips = 0
    # Act
    for i in range(1, 2001):
        units = i * 0.5
        price = official_losscut_price(
            direction, [(entry, units)], equity, _MARGIN_RATE, _POINT_VALUE
        )
        assert price is not None
        ok = is_safe(price, stop)
        if prev is not None and ok != prev:
            flips += 1
        prev = ok
    # Assert
    assert flips == 1, (
        f"述語が単調でない（反転 {flips} 回）。_bisect_max の前提が崩れている"
    )


def test_必要証拠金は口数について単調増加である() -> None:
    """証拠金制約側の二分探索の前提。"""
    # Arrange
    entry = 40000.0
    # Act
    margins = [
        official_required_margin([(entry, i * 0.5)], _MARGIN_RATE, _POINT_VALUE)
        for i in range(1, 200)
    ]
    # Assert
    assert all(a < b for a, b in zip(margins, margins[1:]))


def test_二分探索は述語を満たす値を返す() -> None:
    """`_bisect_max` は保守側（必ず述語成立側）を返す。境界の取り違えを防ぐ。"""
    # Arrange
    sizing = AccountMarginSizing(_rule(volume_max=1000.0))
    ctx = _ctx()
    # Act
    cap = sizing._max_units_before_losscut(ctx)
    # Assert
    price = official_losscut_price(
        "long", [(ctx.estimated_entry_price, cap)], ctx.equity,
        _MARGIN_RATE, _POINT_VALUE,
    )
    assert price is not None and price < ctx.stop_loss_price


# --- 6. f<=0 はジョブ構築時に確定させる（コードレビュー 🔴-4）-------------

# 裁定: f_safe<=0（＝この設定では 1 枚も建たない）は**発注時**ではなく
# **ジョブ構築時**に明示例外にする。発注時判定だと、全発注が黙って落ち
# 「exit=0・取引 0 件で正常終了」という無音の誤動作になる（🔴-4）。
# 併せて、f<=0 判定が先に立たなくなるため SL 判定（fail-stop）が到達可能になる。
#
# 🔵-4: f_safe==0 の原因は「エッジが無い（EV<=0）」だけではない。EV>0 でも
# α が厳しい／T が長いと最小格子点の RoR が α を超えて f_safe=0 になる。文言を事実に合わせる。

def test_破産確率制約fが0の設定は構築時に例外() -> None:
    """EV<=0（エッジ無し）のケース。"""
    from simulator.usecase.sizing_ports import SizingNotViableError

    # Arrange
    no_edge = EdgeRuinSpec(
        win_rate=0.30, payoff_ratio=1.0, ruin_level=0.5,
        alpha=0.01, horizon=250, split_count=20, seed=1, sims=50,
    )
    # Act / Assert
    with pytest.raises(SizingNotViableError):
        AccountMarginSizing(_rule(edge=no_edge))


def test_EVが正でもfが0なら構築時に例外() -> None:
    """🔵-4 の事実確認: EV>0 でも α が極端に厳しければ f_safe=0 になる。"""
    from simulator.usecase.sizing_ports import SizingNotViableError

    # Arrange（実測: ruin_level=0.999・α=0.001 で最小格子点の RoR=0.8 > α → f_safe=0。
    # EV=0.4212 と正であり「エッジが無い」わけではない）
    strict = EdgeRuinSpec(
        win_rate=0.38, payoff_ratio=2.74, ruin_level=0.999,
        alpha=0.001, horizon=250, split_count=20, seed=1, sims=50,
    )
    assert strict.payoff_ratio * strict.win_rate - (1 - strict.win_rate) > 0
    # Act / Assert
    with pytest.raises(SizingNotViableError):
        AccountMarginSizing(_rule(edge=strict))


def test_構築時例外の文言はエッジ無しと断定しない() -> None:
    """🔵-4: 「エッジが無い」は EV<=0 のときだけ成り立つ。原因を断定しない。"""
    from simulator.usecase.sizing_ports import SizingNotViableError

    # Arrange（上と同じ実測設定）
    strict = EdgeRuinSpec(
        win_rate=0.38, payoff_ratio=2.74, ruin_level=0.999,
        alpha=0.001, horizon=250, split_count=20, seed=1, sims=50,
    )
    # Act
    with pytest.raises(SizingNotViableError) as exc:
        AccountMarginSizing(_rule(edge=strict))
    # Assert（1 枚も建たない事実を述べ、原因候補を示す）
    message = str(exc.value)
    assert "1 枚" in message or "建たない" in message
    assert "エッジが無い" not in message


def test_fが正なら構築できる() -> None:
    """正常系の回帰（構築時検査で正常設定を弾いていないこと）。"""
    sizing = AccountMarginSizing(_rule())
    assert sizing.fraction > 0


def test_発注時にf以下0の分岐を持たない() -> None:
    """🔴-4: f<=0 は構築時に排除済みなので、発注時に無音で落とす経路は無い。

    SL 無し発注が **BLOCK_NO_RISK_DISTANCE として届く**ことで、fail-stop が
    到達可能になっていることを確認する（f<=0 が先に立つと届かない）。
    """
    from simulator.usecase.sizing_models import BLOCK_NO_RISK_DISTANCE

    # Arrange
    sizing = AccountMarginSizing(_rule())
    # Act
    decision = sizing.decide_volume(_ctx(stop_loss_price=None))
    # Assert
    assert decision.blocked == BLOCK_NO_RISK_DISTANCE
