"""発注受理の門（`admit_orders`）と、その結線が消えないことの機械ゲート。

由来: ISSUE-445 段階 3-C（`.doc/SYMBOL_SPEC_SUPPLY_BASIC_DESIGN.md` §7「非対象」に
「`Order.validate()` を実行経路へ結線すること（RC-2 とは別の欠落。段階 3 以降で別途裁定）」
として送られていた項目）。

**埋める穴**: `Order.validate` は銘柄仕様の不変条件（side/kind・volume の範囲と刻み・
SL/TP の stops_level 距離）を定義していたが、**本番の実行経路から一度も呼ばれていなかった**
（実測 2026-08-26: 本番コードの `.validate(` 呼出は `usecase/account_engine.py` の 2 件のみ）。
定義された不変条件が検査されないため、MT5 では成立しない発注がそのまま約定していた。

本モジュールは 3 つを固定する。

1. `admit_orders` が不変条件違反を送出し、適合発注を素通しすること。
2. **RC-2（`MaSlope` の `NormalizeLot` 欠落）が本門で捕まること**——実 JP225 の供給元
   スナップショット（`volume_min=1.0`）に対し `volume=0.1` の発注が棄却される。すなわち
   本門が最初から結線されていれば、ISSUE-445 は 2 か月ではなく初回実行で露見していた。
3. 戦略が発注を返す**全ての呼出点**が門を通っていること（AST 検査）。呼出点は
   `run_backtest.py` に 3 箇所あり、将来 4 箇所目が増えたときに規約ではなく機械が止める。
"""
from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from pathlib import Path

import pytest

from marketdata.symbol_spec_snapshot import OANDA_JAPAN_MT5_LIVE, load_spec_fields
from simulator.domain.exceptions import InvalidPriceError
from simulator.domain.order import Order
from simulator.usecase import run_backtest as run_backtest_module
from simulator.usecase._execution import admit_orders
from simulator.usecase.ports import StrategyPort


@dataclass(frozen=True)
class _Spec:
    """`Order.validate` が duck typing で要求する属性のみを持つスタブ。"""

    volume_step: float = 0.01
    volume_min: float = 0.01
    volume_max: float = 100.0
    stops_level: int = 10
    point_size: float = 0.001


def _order(**kw) -> Order:
    base = dict(side="buy", kind="market", volume=1.0, price=100.0, sl=None, tp=None)
    base.update(kw)
    return Order(**base)


class TestAdmitOrdersLetsValidOrdersThrough:
    def test_it_returns_the_same_orders_in_the_same_positions(self):
        orders = [_order(volume=1.0), _order(side="sell", volume=2.0)]
        admitted = admit_orders(orders, _Spec())
        # 受理は検査であって変換ではない: 同一オブジェクトが同一順序で返る。
        assert [id(o) for o in admitted] == [id(o) for o in orders]

    def test_it_materialises_a_one_shot_iterable(self):
        """呼出側は戻り値を 2 回走査する（market/pending の振り分け）。

        ジェネレータを素通しすると 2 周目が空になり、ペンディングが黙って消える。
        """
        admitted = admit_orders((o for o in [_order()]), _Spec())
        assert list(admitted) == list(admitted) != []

    def test_an_empty_batch_is_admitted(self):
        assert admit_orders([], _Spec()) == []


class TestAdmitOrdersRejectsSpecViolations:
    """不変条件の 4 分類それぞれで棄却されること（落ちない門は無価値）。"""

    def test_volume_below_the_minimum_is_rejected(self):
        with pytest.raises(InvalidPriceError):
            admit_orders([_order(volume=0.001)], _Spec())

    def test_volume_above_the_maximum_is_rejected(self):
        with pytest.raises(InvalidPriceError):
            admit_orders([_order(volume=1000.0)], _Spec())

    def test_volume_off_the_step_grid_is_rejected(self):
        with pytest.raises(InvalidPriceError):
            admit_orders([_order(volume=0.015)], _Spec(volume_step=0.01))

    def test_a_stop_inside_the_stops_level_distance_is_rejected(self):
        # stops_level=10 × point_size=0.001 = 0.01。price=100.0 に対し sl=99.995 は 0.005。
        with pytest.raises(InvalidPriceError):
            admit_orders([_order(sl=99.995)], _Spec())

    def test_a_kind_side_mismatch_is_rejected(self):
        with pytest.raises(InvalidPriceError):
            admit_orders([_order(kind="sell_limit", side="buy")], _Spec())

    def test_a_violation_anywhere_in_the_batch_is_rejected(self):
        """先頭が適合でも後続の違反を見逃さない（1 件目で打ち切らない）。"""
        with pytest.raises(InvalidPriceError):
            admit_orders([_order(), _order(volume=0.001)], _Spec())


class TestVolumeStepZeroMeansNoStepConstraint:
    """`volume_step <= 0` は「刻み制約なし」（ISSUE-445 段階 3-C）。

    実測（2026-08-26）: `WeeklyVolBand` 経路の `SymbolSpec` は `volume_step=0.0` を渡す
    （`simulator/tests/integration/test_weekly_vol_band_segments.py`）。素直に除算すると
    ZeroDivisionError になり、検査器が検査対象と無関係な理由で落ちる。戦略側の
    `NormalizeLot` 相当（`stop_entry_probe._normalize_lot` の `if step > 0` 分岐）と同じ規約。
    """

    def test_any_volume_is_admitted_when_the_step_is_zero(self):
        assert admit_orders([_order(volume=1.2345)], _Spec(volume_step=0.0)) != []

    def test_the_range_check_still_applies_when_the_step_is_zero(self):
        # 刻みを飛ばすだけであり、範囲検査まで無効化しない。
        with pytest.raises(InvalidPriceError):
            admit_orders([_order(volume=1000.0)], _Spec(volume_step=0.0))


class TestAdmissionWouldHaveCaughtRc2:
    """RC-2 が本門で捕まることを、**実 JP225 の供給元スナップショット**で固定する。

    RC-2（`.doc/SYMBOL_SPEC_SUPPLY_BASIC_DESIGN.md` §2）: `MaSlope` は原典
    `MA_Slope_EA.mq5:NormalizeLot()` を持たず、EA 入力 `Lot=0.1` をそのまま発注していた。
    実 JP225 の `volume_min` は 1.0（スナップショット実測）であり、この発注は MT5 では
    成立しない。段階 1 で `NormalizeLot` が入るまで、それが 2 か月間検出されなかった。
    """

    @pytest.fixture(scope="class")
    def jp225(self):
        fields = load_spec_fields(OANDA_JAPAN_MT5_LIVE, "JP225")
        return _Spec(
            volume_step=fields["volume_step"],
            volume_min=fields["volume_min"],
            volume_max=fields["volume_max"],
            stops_level=fields["stops_level"],
            point_size=fields["point_size"],
        )

    def test_the_snapshot_minimum_lot_is_one(self, jp225):
        # 以下 2 検定の前提（供給元が変われば赤にする）。
        assert jp225.volume_min == 1.0

    def test_the_pre_stage1_lot_is_rejected(self, jp225):
        """段階 1 以前の発注（EA 入力 `Lot=0.1` を素通し）は棄却される。"""
        with pytest.raises(InvalidPriceError):
            admit_orders([_order(volume=0.1, sl=None, tp=None)], jp225)

    def test_the_normalized_lot_is_admitted(self, jp225):
        """段階 1 以後の発注（`NormalizeLot` が 1.0 へ持ち上げた値）は受理される。"""
        assert admit_orders([_order(volume=1.0)], jp225) != []


# --- 結線が消えないことの機械ゲート -------------------------------------------------


def _signal_methods() -> "frozenset[str]":
    """`StrategyPort` のうち**発注を返す**メソッド名を Port の宣言から導く。

    ここに名前を直書きしない: Port に発注フックが増えたとき、本ゲートの対象も自動で増える
    （直書きだと新フックが黙って検査対象外になる＝ゲートが静かに穴を開ける）。
    """
    names = set()
    for name, member in vars(StrategyPort).items():
        annotation = getattr(member, "__annotations__", {}).get("return")
        if isinstance(annotation, str) and annotation.strip("'\" ") == "list[Order]":
            names.add(name)
    return frozenset(names)


def _unadmitted_signal_calls(source: str, *, signals: "frozenset[str]") -> "list[int]":
    """`self._strategy.<signal>(...)` のうち `admit_orders(...)` の引数配下に無い行を返す。"""
    tree = ast.parse(source)
    admitted: set = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "admit_orders"
        ):
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                admitted.update(ast.walk(arg))

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in signals):
            continue
        if not (isinstance(func.value, ast.Attribute) and func.value.attr == "_strategy"):
            continue
        if node not in admitted:
            offenders.append(node.lineno)
    return offenders


class TestEveryStrategySignalGoesThroughAdmission:
    @pytest.fixture(scope="class")
    def signals(self) -> "frozenset[str]":
        return _signal_methods()

    @pytest.fixture(scope="class")
    def source(self) -> str:
        return Path(inspect.getfile(run_backtest_module)).read_text(encoding="utf-8")

    def test_the_port_declares_the_two_known_order_hooks(self, signals):
        # 導出が空集合へ退化していないこと（空なら以下のゲートは何も検査しない）。
        assert signals == frozenset({"on_new_bar", "on_tick"})

    def test_the_interactor_actually_calls_the_strategy(self, source, signals):
        """**負の対照の前提**: 検査対象の呼出点が実在すること。"""
        tree = ast.parse(source)
        found = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr in signals
            and isinstance(n.func.value, ast.Attribute)
            and n.func.value.attr == "_strategy"
        ]
        assert len(found) >= 3

    def test_no_signal_call_bypasses_admission(self, source, signals):
        offenders = _unadmitted_signal_calls(source, signals=signals)
        assert not offenders, (
            "戦略の発注フックが admit_orders を通らずに実行経路へ入っている"
            f"（run_backtest.py の行 {offenders}）"
        )

    def test_the_gate_detects_a_bypassing_call(self, signals):
        """**負の対照**: 門を外した呼出点を必ず検出する。"""
        bypassing = (
            "class I:\n"
            "    def execute(self):\n"
            "        orders = admit_orders(self._strategy.on_new_bar(0, None, None), spec)\n"
            "        more = self._strategy.on_tick(0, 1.0, 1.0, None) or []\n"
        )
        assert _unadmitted_signal_calls(bypassing, signals=signals) == [4]

    def test_the_gate_accepts_a_call_nested_deep_inside_the_argument(self, signals):
        """条件式・`or []` 等で包まれていても、引数配下にあれば受理と認める。"""
        wrapped = (
            "class I:\n"
            "    def execute(self):\n"
            "        orders = [] if halted else admit_orders(\n"
            "            self._strategy.on_new_bar(0, None, None) or [], spec\n"
            "        )\n"
        )
        assert _unadmitted_signal_calls(wrapped, signals=signals) == []
