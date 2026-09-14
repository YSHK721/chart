"""SizingDecorator（StrategyPort を包んで発注量を差し替える）の単体検定。

固定する不変条件（基本設計書 §3.5.5・§12.1・依頼者裁定）:
    1. **LSP**: `StrategyPort` として差し替え可能。engine が呼ぶ 4 点
       （on_init / on_new_bar / on_tick / on_position_check）を全て透過的に扱う。
       戦略・エンジンは 1 行も変わらない（§12.3-2）。
    2. **ON なら SizingPort が発注量の唯一の権威**（§12.1）。戦略が返した `volume` は
       一律で上書きする。自前サイジングを持つ `weekly_vol_band` も**特別扱いしない**
       （二重権威を構造的に排除する）。戦略名による分岐＝ハードコードは持たない。
    3. 差し替わるのは `volume` **だけ**。side / kind / price / sl / tp は不変（§4.2 F-4 後条件）。
    4. `Order` は frozen（`domain/order.py:45`）なので、属性書換ではなく**同値の再構築**。
       元の Order インスタンスは書き換わらない。
    5. 推定建値の出所:
       - 成行（market）: 指標レジストリの価格系列（§3.5.5 実証 7）。
       - ペンディング（limit/stop）: `order.price` が確定済みなので推定しない（§3.5.5）。
       - ティック経路（on_tick）: 引数の bid/ask を使う（推定差が生じない・§12.2）。
    6. 量を決められない発注は**落とす**（無音で元の量のまま通さない。通すと
       「SizingPort が唯一の権威」が破れる）。

方式: fake の StrategyPort / SizingPort / indicators を注入した単体（I/O なし）。
"""
from __future__ import annotations

from typing import Any

import pytest

from simulator.adapter.strategy.sizing_decorator import SizingDecorator
from simulator.domain.order import Order
from simulator.usecase.ports import StrategyPort
from simulator.usecase.sizing_models import SizingContext, SizingDecision
from simulator.usecase.sizing_ports import SizingPort


class _FakeSeries:
    """pandas.Series の `.iloc[i]` だけを持つ最小の代役。"""

    def __init__(self, values: "list[float]") -> None:
        self.iloc = values


class _FakeIndicators:
    def __init__(self, series: "dict[str, list[float]]") -> None:
        self._series = {k: _FakeSeries(v) for k, v in series.items()}

    def get(self, name: str) -> Any:
        if name not in self._series:
            raise KeyError(name)
        return self._series[name]


class _FakeAccount:
    def __init__(self, equity: float = 1_000_000.0) -> None:
        self.equity = equity
        self.balance = equity


class _SpyStrategy(StrategyPort):
    """発注列を固定で返し、呼ばれた引数を記録する fake 戦略。"""

    def __init__(self, orders: "list[Order]", tick_orders: "list[Order]" = None) -> None:
        self._orders = orders
        self._tick_orders = tick_orders or []
        self.init_calls: list = []
        self.bar_calls: list = []
        self.tick_calls: list = []
        self.check_calls: list = []

    def on_init(self, config: Any, indicators: Any) -> None:
        self.init_calls.append((config, indicators))

    def on_new_bar(self, bar_index: int, indicators: Any, account: Any) -> "list[Order]":
        self.bar_calls.append((bar_index, indicators, account))
        return list(self._orders)

    def on_position_check(self, position: Any, bar_index: int, indicators: Any) -> str:
        self.check_calls.append((position, bar_index, indicators))
        return "close"

    def on_tick(self, bar_index: int, bid: float, ask: float, account: Any) -> "list[Order]":
        self.tick_calls.append((bar_index, bid, ask, account))
        return list(self._tick_orders)


class _FixedSizing(SizingPort):
    """常に同じ量を返し、渡された context を記録する fake SizingPort。"""

    def __init__(self, volume: "float | None" = 0.7) -> None:
        self._volume = volume
        self.contexts: "list[SizingContext]" = []

    def decide_volume(self, context: SizingContext) -> SizingDecision:
        self.contexts.append(context)
        return SizingDecision(volume=self._volume, fraction=0.05, reason="fake")


def _decorator(strategy: StrategyPort, sizing: SizingPort, series: str = "close",
               indicators: Any = None) -> SizingDecorator:
    return SizingDecorator(strategy, sizing, price_series=series)


_MARKET_BUY = Order(side="buy", kind="market", volume=0.1, price=None, sl=39800.0, tp=40500.0)
_LIMIT_BUY = Order(side="buy", kind="buy_limit", volume=0.1, price=39900.0, sl=39800.0, tp=40500.0)


# --- 1. LSP（透過）--------------------------------------------------------

def test_StrategyPortとして差し替え可能である() -> None:
    dec = _decorator(_SpyStrategy([]), _FixedSizing())
    assert isinstance(dec, StrategyPort)


def test_on_initは内側の戦略へ透過する() -> None:
    # Arrange
    inner = _SpyStrategy([])
    dec = _decorator(inner, _FixedSizing())
    config, indicators = object(), _FakeIndicators({})
    # Act
    dec.on_init(config, indicators)
    # Assert
    assert inner.init_calls == [(config, indicators)]


def test_on_position_checkは内側の戦略へ透過する() -> None:
    """abstract のため実装必須。判断を変えると戦略の決済規則が壊れる。"""
    # Arrange
    inner = _SpyStrategy([])
    dec = _decorator(inner, _FixedSizing())
    position = object()
    indicators = _FakeIndicators({})
    # Act
    got = dec.on_position_check(position, 3, indicators)
    # Assert
    assert got == "close"
    assert inner.check_calls == [(position, 3, indicators)]


def test_発注が無ければ空のまま返す() -> None:
    inner = _SpyStrategy([])
    dec = _decorator(inner, _FixedSizing())
    assert dec.on_new_bar(0, _FakeIndicators({"close": [40000.0]}), _FakeAccount()) == []


# --- 2. 発注量の上書き ----------------------------------------------------

def test_成行の発注量はSizingPortの決定で上書きされる() -> None:
    # Arrange
    inner = _SpyStrategy([_MARKET_BUY])
    dec = _decorator(inner, _FixedSizing(volume=0.7))
    # Act
    got = dec.on_new_bar(0, _FakeIndicators({"close": [40000.0]}), _FakeAccount())
    # Assert
    assert len(got) == 1
    assert got[0].volume == 0.7


def test_量以外の属性は不変である() -> None:
    """§4.2 F-4 後条件: 方向・種別・価格・SL・TP は不変。"""
    # Arrange
    inner = _SpyStrategy([_MARKET_BUY])
    dec = _decorator(inner, _FixedSizing(volume=0.7))
    # Act
    got = dec.on_new_bar(0, _FakeIndicators({"close": [40000.0]}), _FakeAccount())[0]
    # Assert
    assert (got.side, got.kind, got.price, got.sl, got.tp) == (
        _MARKET_BUY.side, _MARKET_BUY.kind, _MARKET_BUY.price,
        _MARKET_BUY.sl, _MARKET_BUY.tp,
    )


def test_元のOrderインスタンスは書き換わらない() -> None:
    """Order は frozen（domain/order.py:45）。再構築であることを固定する。"""
    # Arrange
    original = Order(side="buy", kind="market", volume=0.1, price=None, sl=39800.0)
    inner = _SpyStrategy([original])
    dec = _decorator(inner, _FixedSizing(volume=0.7))
    # Act
    got = dec.on_new_bar(0, _FakeIndicators({"close": [40000.0]}), _FakeAccount())[0]
    # Assert
    assert original.volume == 0.1
    assert got is not original


def test_自前サイジングを持つ戦略も特別扱いせず上書きする() -> None:
    """§12.1: weekly_vol_band の `volume=N` も上書き対象（二重権威の排除）。

    戦略名で分岐していないこと＝どの戦略が返した発注でも同じ扱いになることで固定する。
    """
    # Arrange（戦略が自前で計算した非固定の量）
    self_sized = Order(side="buy", kind="market", volume=3.14159, price=None, sl=39800.0)
    inner = _SpyStrategy([self_sized])
    dec = _decorator(inner, _FixedSizing(volume=0.7))
    # Act
    got = dec.on_new_bar(0, _FakeIndicators({"close": [40000.0]}), _FakeAccount())[0]
    # Assert
    assert got.volume == 0.7


def test_複数発注はすべて上書きされる() -> None:
    # Arrange
    orders = [
        _MARKET_BUY,
        Order(side="sell", kind="market", volume=0.2, price=None, sl=40200.0),
    ]
    inner = _SpyStrategy(orders)
    dec = _decorator(inner, _FixedSizing(volume=0.7))
    # Act
    got = dec.on_new_bar(0, _FakeIndicators({"close": [40000.0]}), _FakeAccount())
    # Assert
    assert [o.volume for o in got] == [0.7, 0.7]


# --- 3. 推定建値の出所 ----------------------------------------------------

def test_成行の推定建値は指標レジストリの価格系列から取る() -> None:
    """§3.5.5 実証 7: 戦略も Decorator も価格は指標レジストリ経由でしか得られない。"""
    # Arrange
    inner = _SpyStrategy([_MARKET_BUY])
    sizing = _FixedSizing()
    dec = _decorator(inner, sizing, series="close")
    indicators = _FakeIndicators({"close": [40000.0, 41000.0, 42000.0]})
    # Act（bar_index=2 の発注）
    dec.on_new_bar(2, indicators, _FakeAccount())
    # Assert
    assert sizing.contexts[0].estimated_entry_price == 42000.0


def test_推定建値の系列名は指定されたものを使う() -> None:
    """entry_price_basis="current_open" のとき "open" 系列（sizing_ports の表）。"""
    # Arrange
    inner = _SpyStrategy([_MARKET_BUY])
    sizing = _FixedSizing()
    dec = _decorator(inner, sizing, series="open")
    indicators = _FakeIndicators({"open": [39000.0], "close": [40000.0]})
    # Act
    dec.on_new_bar(0, indicators, _FakeAccount())
    # Assert
    assert sizing.contexts[0].estimated_entry_price == 39000.0


def test_ペンディング発注は自分の価格を建値に使う() -> None:
    """§3.5.5: 指値・逆指値は price 確定済みのため推定しない（差が生じない）。"""
    # Arrange
    inner = _SpyStrategy([_LIMIT_BUY])
    sizing = _FixedSizing()
    dec = _decorator(inner, sizing, series="close")
    # 系列の値（40000）と order.price（39900）を別値にして、どちらを使ったか判別する。
    indicators = _FakeIndicators({"close": [40000.0]})
    # Act
    dec.on_new_bar(0, indicators, _FakeAccount())
    # Assert
    assert sizing.contexts[0].estimated_entry_price == 39900.0


def test_ティック経路は引数のクォートを建値に使う() -> None:
    """§3.5.5: on_tick は bid/ask が引数で渡るため推定差が生じない。"""
    # Arrange
    inner = _SpyStrategy([], tick_orders=[_MARKET_BUY])
    sizing = _FixedSizing()
    dec = _decorator(inner, sizing, series="close")
    # Act（買いは ask 約定）
    got = dec.on_tick(0, 39990.0, 40010.0, _FakeAccount())
    # Assert
    assert sizing.contexts[0].estimated_entry_price == 40010.0
    assert got[0].volume == 0.7


def test_ティック経路の売りはbidを建値に使う() -> None:
    # Arrange
    sell = Order(side="sell", kind="market", volume=0.2, price=None, sl=40200.0)
    inner = _SpyStrategy([], tick_orders=[sell])
    sizing = _FixedSizing()
    dec = _decorator(inner, sizing, series="close")
    # Act
    dec.on_tick(0, 39990.0, 40010.0, _FakeAccount())
    # Assert
    assert sizing.contexts[0].estimated_entry_price == 39990.0


def test_口座の有効証拠金がSizingPortへ渡る() -> None:
    # Arrange
    inner = _SpyStrategy([_MARKET_BUY])
    sizing = _FixedSizing()
    dec = _decorator(inner, sizing)
    # Act
    dec.on_new_bar(0, _FakeIndicators({"close": [40000.0]}), _FakeAccount(equity=555.0))
    # Assert
    assert sizing.contexts[0].equity == 555.0


def test_発注のSLがSizingPortへ渡る() -> None:
    # Arrange
    inner = _SpyStrategy([_MARKET_BUY])
    sizing = _FixedSizing()
    dec = _decorator(inner, sizing)
    # Act
    dec.on_new_bar(0, _FakeIndicators({"close": [40000.0]}), _FakeAccount())
    # Assert
    assert sizing.contexts[0].stop_loss_price == _MARKET_BUY.sl
    assert sizing.contexts[0].side == "buy"


# --- 4. 決められない発注は落とす ------------------------------------------

def test_量を決められない発注は落とす() -> None:
    """元の量で通すと「SizingPort が唯一の権威」（§12.1）が破れる。"""
    # Arrange
    inner = _SpyStrategy([_MARKET_BUY])
    dec = _decorator(inner, _FixedSizing(volume=None))
    # Act
    got = dec.on_new_bar(0, _FakeIndicators({"close": [40000.0]}), _FakeAccount())
    # Assert
    assert got == []


def test_一部だけ決められないときは決まった分だけ残る() -> None:
    # Arrange
    class _Selective(SizingPort):
        def decide_volume(self, context: SizingContext) -> SizingDecision:
            volume = 0.7 if context.side == "buy" else None
            return SizingDecision(volume=volume, fraction=0.05)

    inner = _SpyStrategy([
        _MARKET_BUY,
        Order(side="sell", kind="market", volume=0.2, price=None, sl=40200.0),
    ])
    dec = _decorator(inner, _Selective())
    # Act
    got = dec.on_new_bar(0, _FakeIndicators({"close": [40000.0]}), _FakeAccount())
    # Assert
    assert len(got) == 1
    assert got[0].side == "buy"


# --- 5. 誤設定を無音にしない ----------------------------------------------

def test_必要な価格系列がレジストリに無ければ例外() -> None:
    """§12.5 の受付時拒否をすり抜けた場合の最後の砦。無音で誤った建値を使わない。"""
    # Arrange
    inner = _SpyStrategy([_MARKET_BUY])
    dec = _decorator(inner, _FixedSizing(), series="close")
    indicators = _FakeIndicators({"ema": [40000.0]})   # MA_Slope_EA 相当
    # Act / Assert
    with pytest.raises(Exception):
        dec.on_new_bar(0, indicators, _FakeAccount())


# --- 6. build_sizing_decorator（設定 → Decorator の組み立て）---------------

class _Spec:
    """SymbolSpec の代役（duck typing で量制約だけを持つ）。"""

    volume_min = 0.1
    volume_max = 100.0
    volume_step = 0.1


def test_OFFならDecoratorを作らない() -> None:
    """§12.1 既定 OFF: build_interactor へ None が渡り、既存挙動と byte 等価になる。"""
    from simulator.adapter.strategy.sizing_decorator import build_sizing_decorator
    from simulator.usecase.sizing_models import SizingConfig

    got = build_sizing_decorator(
        SizingConfig(enabled=False), symbol_spec=_Spec(), entry_price_basis="close"
    )
    assert got is None


def test_ONならStrategyPortを包む関数を返す() -> None:
    from simulator.adapter.strategy.sizing_decorator import build_sizing_decorator
    from simulator.usecase.sizing_models import SizingConfig

    # Arrange
    factory = build_sizing_decorator(
        SizingConfig(enabled=True, sims=20),
        symbol_spec=_Spec(),
        entry_price_basis="close",
    )
    inner = _SpyStrategy([])
    # Act
    assert factory is not None
    wrapped = factory(inner)
    # Assert
    assert isinstance(wrapped, SizingDecorator)
    assert isinstance(wrapped, StrategyPort)


@pytest.mark.parametrize(
    "basis, series", [("close", "close"), ("current_open", "open")]
)
def test_推定に使う系列はentry_price_basisで決まる(basis: str, series: str) -> None:
    """`sizing_ports.required_price_series` の表に従う（判断を 2 箇所に分けない）。"""
    from simulator.adapter.strategy.sizing_decorator import build_sizing_decorator
    from simulator.usecase.sizing_models import SizingConfig

    # Arrange
    factory = build_sizing_decorator(
        SizingConfig(enabled=True, sims=20),
        symbol_spec=_Spec(),
        entry_price_basis=basis,
    )
    assert factory is not None
    wrapped = factory(_SpyStrategy([_MARKET_BUY]))
    # Act（該当系列だけを持つレジストリで動くこと＝その系列を引いている）
    indicators = _FakeIndicators({series: [40000.0]})
    result = wrapped.on_new_bar(0, indicators, _FakeAccount())
    # Assert
    assert len(result) == 1


def test_未知のentry_price_basisは例外() -> None:
    """無音で "close" へ倒すと誤った建値で量を決める。"""
    from simulator.adapter.strategy.sizing_decorator import build_sizing_decorator
    from simulator.usecase.sizing_models import SizingConfig

    with pytest.raises(ValueError):
        build_sizing_decorator(
            SizingConfig(enabled=True, sims=20),
            symbol_spec=_Spec(),
            entry_price_basis="mid",
        )


def test_銘柄の量制約がSizingRuleへ渡る() -> None:
    from simulator.adapter.strategy.sizing_decorator import build_sizing_decorator
    from simulator.usecase.sizing_models import SizingConfig

    # Arrange
    class _Tight:
        volume_min = 0.5
        volume_max = 2.0
        volume_step = 0.5

    factory = build_sizing_decorator(
        SizingConfig(enabled=True, sims=20),
        symbol_spec=_Tight(),
        entry_price_basis="close",
    )
    assert factory is not None
    wrapped = factory(_SpyStrategy([_MARKET_BUY]))
    # Act（潤沢な資金でも volume_max=2.0 を超えない）
    got = wrapped.on_new_bar(
        0, _FakeIndicators({"close": [40000.0]}), _FakeAccount(equity=10_000_000.0)
    )
    # Assert
    assert got[0].volume <= 2.0
    ratio = got[0].volume / 0.5
    assert abs(ratio - round(ratio)) < 1e-6


def test_エッジ計算は戦略を包む時点で一度だけ行う() -> None:
    """MC は重い。包むたび・発注のたびに回すとバックテストが終わらない。"""
    from simulator.adapter.sizing.account_margin_sizing import AccountMarginSizing
    from simulator.adapter.strategy.sizing_decorator import build_sizing_decorator
    from simulator.usecase.sizing_models import SizingConfig

    # Arrange
    factory = build_sizing_decorator(
        SizingConfig(enabled=True, sims=20), symbol_spec=_Spec(),
        entry_price_basis="close",
    )
    assert factory is not None
    # Act（同じ factory から 2 回包む）
    a = factory(_SpyStrategy([]))
    b = factory(_SpyStrategy([]))
    # Assert（SizingPort インスタンスが共有されている＝f を再計算していない）
    assert isinstance(a._sizing, AccountMarginSizing)
    assert a._sizing is b._sizing


# --- 7. SL 無し注文の fail-stop（依頼者裁定 2026-08-11）--------------------

# 裁定: sizing ON で SL を持たない注文に遭遇したら、**黙って捨てず**（＝無音の取引ゼロ）
# ジョブを明示失敗させる（fail-stop）。実測された壊れ方: 捨てる実装では
# `on_new_bar` が [] を返すだけで例外も警告も出ず、バックテストは exit=0・取引 0 件で
# 「正常終了」する。利用者からは「戦略が一度も発注しなかった」としか見えない。
#
# 適用範囲の明示（私の解釈・要確認）: 裁定文言は「SL を持たない注文」。実装では
# **リスク距離が確定できない注文**（sl=None ／ sl==推定建値で距離 0）の両方を対象にする。
# 後者も現状は同じ「無音の取引ゼロ」になるため、片方だけ直すと問題が半分残る。
# 下限未満・エッジ無し等の他の「量を決められない」理由は裁定の対象外なので従来どおり落とす。

def _no_sl_strategy(sl=None, kind: str = "market"):
    price = None if kind == "market" else 39900.0
    return _SpyStrategy(
        [Order(side="buy", kind=kind, volume=0.1, price=price, sl=sl)]
    )


def _real_sizing(**over):
    """実物の AccountMarginSizing（fake ではなく本物の判定を通す）。"""
    from simulator.adapter.sizing.account_margin_sizing import AccountMarginSizing
    from simulator.usecase.edge_ruin import EdgeRuinSpec
    from simulator.usecase.sizing_models import SizingRule

    base = dict(
        edge=EdgeRuinSpec(
            win_rate=0.38, payoff_ratio=2.74, ruin_level=0.5,
            alpha=0.01, horizon=250, split_count=20, seed=1, sims=20,
        ),
        margin_rate=0.10, point_value=1.0,
        volume_min=0.1, volume_max=100.0, volume_step=0.1,
    )
    base.update(over)
    return AccountMarginSizing(SizingRule(**base))


def test_SL無しの成行はfail_stopで例外になる() -> None:
    """無音の取引ゼロを廃する（裁定）。"""
    from simulator.usecase.sizing_ports import SizingRequiresStopLossError

    # Arrange
    dec = _decorator(_no_sl_strategy(sl=None), _real_sizing())
    # Act / Assert
    with pytest.raises(SizingRequiresStopLossError):
        dec.on_new_bar(0, _FakeIndicators({"close": [40000.0]}), _FakeAccount())


def test_SLが建値と同一の注文もfail_stopになる() -> None:
    """距離 0 も「リスク距離が確定できない」＝同じ無音の取引ゼロ経路。"""
    from simulator.usecase.sizing_ports import SizingRequiresStopLossError

    # Arrange
    dec = _decorator(_no_sl_strategy(sl=40000.0), _real_sizing())
    # Act / Assert
    with pytest.raises(SizingRequiresStopLossError):
        dec.on_new_bar(0, _FakeIndicators({"close": [40000.0]}), _FakeAccount())


def test_fail_stopの例外は原因が読めるメッセージを持つ() -> None:
    """failure_reason に載る文言。運用者が「なぜ落ちたか」を状態だけで読めること。"""
    from simulator.usecase.sizing_ports import SizingRequiresStopLossError

    # Arrange
    dec = _decorator(_no_sl_strategy(sl=None), _real_sizing())
    # Act
    with pytest.raises(SizingRequiresStopLossError) as exc:
        dec.on_new_bar(0, _FakeIndicators({"close": [40000.0]}), _FakeAccount())
    # Assert
    message = str(exc.value)
    assert "SL" in message
    assert "サイジング" in message or "sizing" in message


def test_ティック経路のSL無しもfail_stopになる() -> None:
    from simulator.usecase.sizing_ports import SizingRequiresStopLossError

    # Arrange
    inner = _SpyStrategy(
        [], tick_orders=[Order(side="buy", kind="market", volume=0.1,
                               price=None, sl=None)]
    )
    dec = _decorator(inner, _real_sizing())
    # Act / Assert
    with pytest.raises(SizingRequiresStopLossError):
        dec.on_tick(0, 39990.0, 40010.0, _FakeAccount())


def test_ペンディングのSL無しもfail_stopになる() -> None:
    from simulator.usecase.sizing_ports import SizingRequiresStopLossError

    # Arrange
    dec = _decorator(_no_sl_strategy(sl=None, kind="buy_limit"), _real_sizing())
    # Act / Assert
    with pytest.raises(SizingRequiresStopLossError):
        dec.on_new_bar(0, _FakeIndicators({"close": [40000.0]}), _FakeAccount())


def test_SLのある注文は従来どおり上書きされる() -> None:
    """fail-stop の導入で正常経路を壊していないこと（回帰）。"""
    # Arrange
    inner = _SpyStrategy([_MARKET_BUY])   # sl=39800.0
    dec = _decorator(inner, _real_sizing())
    # Act
    got = dec.on_new_bar(0, _FakeIndicators({"close": [40000.0]}), _FakeAccount())
    # Assert
    assert len(got) == 1
    assert got[0].volume != _MARKET_BUY.volume


def test_下限未満は従来どおり落とす() -> None:
    """裁定の適用範囲を広げていないことの固定（fail-stop は距離不定のみ）。"""
    # Arrange（資金が極小＝丸めて下限未満）
    inner = _SpyStrategy([_MARKET_BUY])
    dec = _decorator(inner, _real_sizing(volume_min=1.0, volume_step=1.0))
    # Act
    got = dec.on_new_bar(0, _FakeIndicators({"close": [40000.0]}), _FakeAccount(equity=100.0))
    # Assert（例外ではなく従来どおり落ちる）
    assert got == []


def test_エッジ無しはDecoratorを組む前に弾かれる() -> None:
    """🔴-4 裁定: f<=0 は**ジョブ構築時**に確定する（発注時に黙って落とさない）。

    したがって「エッジが無い戦略で発注が落ちる」という状態自体が作れない。
    SizingPort の構築時点で例外になることを固定する。
    """
    from simulator.usecase.edge_ruin import EdgeRuinSpec
    from simulator.usecase.sizing_ports import SizingNotViableError

    # Arrange
    no_edge = EdgeRuinSpec(
        win_rate=0.30, payoff_ratio=1.0, ruin_level=0.5,
        alpha=0.01, horizon=250, split_count=20, seed=1, sims=20,
    )
    # Act / Assert
    with pytest.raises(SizingNotViableError):
        _real_sizing(edge=no_edge)
