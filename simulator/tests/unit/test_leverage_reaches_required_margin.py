"""退行防止: 口座属性 ``leverage`` が実行経路の末端（``required_margin``）まで届く。

由来: ISSUE-445 恒久策 **段階 3-D2**（``.doc/SYMBOL_SPEC_SUPPLY_BASIC_DESIGN.md`` §3.4）。
``SymbolSpec`` から ``leverage`` を外し ``RunBacktestRequest`` へ移す是正で、**配管が切れても
数値が偶然合う**（既定値・別の供給源・古い読み出しが生き残る）ことを防ぐために置く。

**何を固定するか**: エンジンが証拠金計算に使う ``leverage`` は
``RunBacktestRequest.account.leverage`` **その値**である。消費の末端は
``simulator/domain/position.py:Position.required_margin(leverage, contract_size)``
であり、本検定はその第 1 引数を実行中に記録して突き合わせる（統計量や損益といった
間接的な観測ではなく、**末端で受け取った値そのもの**を見る）。

**期待値をテスト側にリテラルで持たない**: 投入する値は供給元スナップショット
（``load_spec_fields``＝唯一の権威）から引き、突き合わせ先は「投入した ``request`` の値」
である。したがって供給元の値が変わっても本検定は追随する。

**負の対照**（落ちないゲートは無価値であるため恒久テストとして置く）:
    1. 2 つの異なる ``leverage`` で走らせ、末端が受け取った値が**それぞれに追随**すること
       （どちらかに固定された実装＝定数・既定値は必ず落ちる）。
    2. ``symbol_spec`` が**別の** ``leverage`` 属性を持っていても、末端に届くのは
       ``request`` 側の値であること（``spec.leverage`` を読む形へ退行したら落ちる。
       是正前のエンジンはまさにその形であり、この対照だけが是正の**方向**を固定する）。
    3. 記録が空でないこと（証拠金計算をそもそも通らない実行で自明に緑にしない）。

スパイ Port は ``test_run_backtest`` が単一ソース（同じスタブを書き写さない・
プロジェクト規約）。既存の前例: ``test_ma_slope_normalize_lot`` が
``test_ea_factory_registry`` の補助を import している。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest

from marketdata.symbol_spec_snapshot import (
    OANDA_JAPAN_MT5_LIVE,
    SYMBOL_FIELD_SOURCES,
    load_spec_fields,
)
from simulator.domain.order import Order
from simulator.domain.position import Position
from simulator.tests.unit.test_run_backtest import (
    SpyIndicatorPort,
    SpyStrategyPort,
    StubTickModelPort,
    _bar,
    _config,
)
from simulator.usecase.models import AccountSpec, SymbolSpec
from simulator.usecase.run_backtest import RunBacktestInteractor, RunBacktestRequest

_SYMBOL = "JP225"


def _supplied() -> "dict[str, float]":
    """供給元スナップショット（唯一の権威）。値をここに書かない。"""
    return load_spec_fields(OANDA_JAPAN_MT5_LIVE, _SYMBOL)


def _symbol_spec() -> SymbolSpec:
    """銘柄の契約だけを持つ ``SymbolSpec``（銘柄仕様の表がキー集合の単一ソース）。"""
    supplied = _supplied()
    return SymbolSpec(**{name: supplied[name] for name in SYMBOL_FIELD_SOURCES})


@dataclass
class _SpecCarryingAStaleLeverage(SymbolSpec):
    """``SymbolSpec`` に**古い** ``leverage`` 属性が同居している状態（負の対照 2）。

    ``spec.leverage`` を読む実装（是正前の形）へ退行したら、末端が受け取る値がこちらに
    なるため検定が落ちる。銘柄仕様としての振る舞いは基底と同じである（LSP）。
    """

    leverage: float = 0.0


def _bars():
    """bar0 で建て、bar1 まで保有する 2 本の合成 Bar（値は証拠金の突合に使わない）。"""
    return [
        _bar(np.datetime64("2024-01-01T00:00"), 1.10, 1.11, 1.09, 1.10),
        _bar(np.datetime64("2024-01-01T00:01"), 1.10, 1.11, 1.09, 1.10),
    ]


def _run_recording_required_margin(monkeypatch, request_) -> "list[float]":
    """1 run 実行し、``Position.required_margin`` が受け取った ``leverage`` を記録する。"""
    recorded: "list[float]" = []
    original = Position.required_margin

    def spy(self, leverage, contract_size):
        recorded.append(leverage)
        return original(self, leverage, contract_size)

    monkeypatch.setattr(Position, "required_margin", spy)
    log: list = []
    interactor = RunBacktestInteractor(
        strategy=SpyStrategyPort(
            log,
            orders_by_bar={0: [Order(side="buy", kind="market", volume=1.0, price=None)]},
        ),
        indicators=SpyIndicatorPort(log),
        tick_model=StubTickModelPort(),
    )
    interactor.execute(request_)
    # 同一テスト内で 2 回呼ばれても spy が入れ子にならないよう、その場で戻す
    # （戻さないと 2 回目の `original` が 1 回目の spy になり、記録が混ざる）。
    monkeypatch.undo()
    return recorded


def _request(*, leverage: float, symbol_spec=None) -> RunBacktestRequest:
    return RunBacktestRequest(
        config=_config(),
        bars=_bars(),
        symbol_spec=symbol_spec if symbol_spec is not None else _symbol_spec(),
        # 口座の契約（ISSUE-445 段階 3-D3）。`initial_deposit` は証拠金不足で止めない
        # だけの額、`stop_out_level` は段階 3-D2 まで既定値だった 0.0（本 run は
        # equity が正のまま推移し stop-out 判定を通らない＝結果に影響しない）。
        account=AccountSpec(
            initial_deposit=1_000_000.0,
            leverage=leverage,
            stop_out_level=0.0,
        ),
    )


class TestTheRequestLeverageReachesRequiredMargin:
    def test_the_supplied_leverage_arrives_at_the_terminal_consumer(self, monkeypatch):
        # Arrange: 供給元の値（＝口座属性の権威）をそのまま投入する。
        supplied_leverage = _supplied()["leverage"]
        request_ = _request(leverage=supplied_leverage)
        # Act
        recorded = _run_recording_required_margin(monkeypatch, request_)
        # Assert: 末端が呼ばれ、受け取った値は投入した値そのもの。
        assert recorded, "required_margin が 1 度も呼ばれていない（証拠金経路を通っていない）"
        assert set(recorded) == {request_.account.leverage}

    def test_a_different_leverage_changes_what_the_terminal_consumer_receives(
        self, monkeypatch
    ):
        """負の対照 1: 値を変えれば末端の受領値も変わる（定数化した実装は落ちる）。"""
        # Arrange: 供給元の値と、それとは異なる値の 2 通り（差は倍率で作る＝リテラル不使用）。
        supplied_leverage = _supplied()["leverage"]
        other_leverage = supplied_leverage * 2.0
        # Act
        first = _run_recording_required_margin(
            monkeypatch, _request(leverage=supplied_leverage)
        )
        second = _run_recording_required_margin(
            monkeypatch, _request(leverage=other_leverage)
        )
        # Assert
        assert set(first) == {supplied_leverage}
        assert set(second) == {other_leverage}
        assert set(first) != set(second)

    def test_the_symbol_spec_is_not_the_source_even_if_it_carries_one(self, monkeypatch):
        """負の対照 2: `spec.leverage` を読む形へ退行したら落ちる。"""
        # Arrange: request と食い違う leverage 属性を持つ銘柄仕様。
        supplied = _supplied()
        stale = _SpecCarryingAStaleLeverage(
            **{name: supplied[name] for name in SYMBOL_FIELD_SOURCES},
            leverage=supplied["leverage"] * 3.0,
        )
        request_ = _request(leverage=supplied["leverage"], symbol_spec=stale)
        # Act
        recorded = _run_recording_required_margin(monkeypatch, request_)
        # Assert
        assert recorded
        assert set(recorded) == {request_.account.leverage}
        assert stale.leverage not in set(recorded)


def test_the_margin_actually_scales_with_the_request_leverage(monkeypatch):
    """末端の値だけでなく、計算結果（必要証拠金）が投入値に反比例することを固定する。

    記録した引数が「渡っただけで使われていない」ことを排除する（``required_margin`` の
    式は ``domain`` が持つため、ここでは同じ建玉に対する 2 通りの結果の比だけを見る）。
    """
    # Arrange
    supplied_leverage = _supplied()["leverage"]
    factor = 2.0
    margins: "dict[float, float]" = {}
    original = Position.required_margin

    def spy(self, leverage, contract_size):
        value = original(self, leverage, contract_size)
        margins[leverage] = value
        return value

    monkeypatch.setattr(Position, "required_margin", spy)
    log: list = []

    def run(leverage: float) -> None:
        interactor = RunBacktestInteractor(
            strategy=SpyStrategyPort(
                log,
                orders_by_bar={0: [Order(side="buy", kind="market", volume=1.0, price=None)]},
            ),
            indicators=SpyIndicatorPort(log),
            tick_model=StubTickModelPort(),
        )
        interactor.execute(_request(leverage=leverage))

    # Act
    run(supplied_leverage)
    run(supplied_leverage * factor)
    # Assert: leverage を factor 倍すると必要証拠金は 1/factor になる。
    assert margins[supplied_leverage] == pytest.approx(
        margins[supplied_leverage * factor] * factor
    )
