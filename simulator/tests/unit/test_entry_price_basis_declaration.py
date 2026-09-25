"""判定の瞬間の宣言（ISSUE-533 段階 1）— 宣言の不在・食い違いの Fail-Stop。

固定する不変条件:
    1. 宣言を持たない戦略は `build_interactor` の段で落ちる（run を始めない）。
       既定を置かないので、宣言を書き忘れた戦略が静かに誰かの既定で走ることはない。
    2. 各戦略の宣言が、その戦略が**実際に読む足**と一致する。宣言をリテラルで並べず、
       実装から読み出した参照の集合（系列名・shift）を `basis_for_reads` に通して導く。
       戦略を 1 本足すと本検定が自動的にそれを見る（表を更新する作業が要らない）。

なぜ実装を読むのか（測り方の選択）:
    振る舞いで測る案（合成バーで約定価格を観測する）は、EA ごとに発注が成立する条件を
    作り込む必要があり、条件を作り込めた EA しか見られない。判定の瞬間は「どの足のどの値を
    読むか」で決まる構文的な性質なので、実装の参照集合から導けば全 EA を同一の規則で見られる
    （振る舞いでの実証は合成バーの回帰検定
    `simulator/tests/integration/test_entry_price_basis_regression.py` が別に持つ）。

なぜ `build_interactor` なのか（置き場所）:
    戦略は `strategy_override` で差し替えられ `strategy_decorator` で包まれる。最終的に
    エンジンへ渡る実体が確定するのは Composition Root であり、そこが「宣言があるか」を
    問える唯一の点である。`StrategyPort` の抽象メソッドにすると、宣言を必要としない
    クライアント（決済判断だけを使う呼出側）にも実装を強制し、かつ失敗が
    `TypeError` になって「何が足りないか」を名前で言えない。
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import simulator.adapter.strategy as strategy_package
from simulator.main import build_interactor
from simulator.domain.exceptions import ConfigError
from simulator.usecase.entry_price_basis import (
    EntryPriceBasisDeclarationError,
    basis_for_reads,
)
from simulator.usecase.ports import StrategyPort

#: 2024-01-01T00:00:00Z（comma 形式 CSV の 「`time`」 は epoch 秒 int が契約）。
_EPOCH_2024_01_01 = 1_704_067_200


def _write_csv(path: Path) -> Path:
    rows = []
    for i in range(12):
        base = 1.1000 + i * 0.0001
        rows.append(
            {
                "time": _EPOCH_2024_01_01 + 3600 * i,
                "open": base,
                "high": base + 0.0005,
                "low": base - 0.0005,
                "close": base + 0.0002,
                "volume": 100,
                "spread": 10,
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _meta(csv_path: Path, **overrides: Any) -> dict:
    base = dict(
        data_path=csv_path,
        symbol="EURUSD",
        period="M1",
        ea_name="TC24051901",
        initial_deposit=10_000.0,
        contract_size=1.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=0,
        digits=5,
        point_size=0.0001,
        leverage=100.0,
        ma_period=2,
        ma_method="sma",
        lot_size=1.0,
        stop_loss_points=500,
        take_profit_points=3000,
    )
    base.update(overrides)
    return base


class _DeclaresBarOpen(StrategyPort):
    """足の始まりで判定すると名乗る戦略（設定との食い違いを作るための代役）。"""

    entry_price_basis = "current_open"

    def on_init(self, config: Any, indicators: Any) -> None:
        return None

    def on_new_bar(self, bar_index: int, indicators: Any, account: Any) -> list:
        return []

    def on_position_check(self, position: Any, bar_index: int, indicators: Any) -> str:
        return "hold"


class _DeclaresNoBarBoundaryDecision(_DeclaresBarOpen):
    """足境界で判定しないと名乗る戦略（設定と食い違いようがないことの検定用）。"""

    entry_price_basis = None


def test_the_config_cannot_carry_a_basis_at_all(tmp_path: Path) -> None:
    """設定へ建値基準を書いた run は**始まらない**（ISSUE-533 段階 2）。

    段階 1 は受け口を残し「宣言と食い違えば Fail-Stop」だった。段階 2 で供給そのものを
    撤去したので、食い違いは原理的に起きない——決定論設定の語彙から外れ、書けば
    `ConfigError` になる。以前ここに在った 3 件（食い違いで落ちる／一致すれば通る／
    足境界で判定しない戦略は食い違わない）は、いずれも**受け口の存在を前提にした検定**で
    あり、受け口が無くなった時点で表明する対象が消えた。

    実測（2026-09-25・段階 2 着手前）: 両検定スイートの `build_interactor` 呼出のうち
    58 件が「宣言 None ＋ 設定 current_open」の形だった（StopEntryProbe_EA 30 件・
    Math calculations の NullStrategy 28 件）。撤去でその 58 件は設定を 1 つも渡さなくなり、
    宣言だけが残る。
    """
    # Arrange
    csv_path = _write_csv(tmp_path / "m1.csv")
    meta = _meta(
        csv_path,
        strategy_override=_DeclaresBarOpen(),
        config_overrides={"entry_price_basis": "current_open"},
    )

    # Act / Assert（宣言と一致する値でも受け付けない＝受け口が無い）
    with pytest.raises(ConfigError):
        build_interactor(**meta)


def test_the_declaration_alone_decides_without_any_config(tmp_path: Path) -> None:
    """設定を 1 つも渡さずに、宣言だけで判定の瞬間が決まる。"""
    # Arrange
    csv_path = _write_csv(tmp_path / "m1.csv")

    # Act
    controller, _ = build_interactor(
        **_meta(csv_path, strategy_override=_DeclaresBarOpen())
    )
    no_moment, _ = build_interactor(
        **_meta(csv_path, strategy_override=_DeclaresNoBarBoundaryDecision())
    )

    # Assert
    assert controller._interactor._strategy.entry_price_basis == "current_open"
    assert no_moment._interactor._strategy.entry_price_basis is None


class _OrdersWithoutABarBoundaryMoment(_DeclaresNoBarBoundaryDecision):
    """足境界で判定しないと名乗りながら足境界で成行を出す戦略（宣言と実装の食い違い）。"""

    def on_new_bar(self, bar_index: int, indicators: Any, account: Any) -> list:
        from simulator.domain.order import Order

        return [Order(side="buy", kind="market", volume=1.0, price=None, sl=None, tp=None)]


def test_ordering_without_a_declared_moment_stops_instead_of_filling_silently(
    tmp_path: Path,
) -> None:
    """判定の瞬間を名乗らない戦略が足境界で約定しようとしたら落ちる。

    ここを既定（終値）へ倒すと、「`NO_BAR_BOUNDARY_DECISION`」 が「終値の別名」に化け、
    宣言を書かないことが黙って通る抜け道になる。
    """
    # Arrange
    csv_path = _write_csv(tmp_path / "m1.csv")
    controller, request = build_interactor(
        **_meta(csv_path, strategy_override=_OrdersWithoutABarBoundaryMoment())
    )
    # Act / Assert
    with pytest.raises(EntryPriceBasisDeclarationError):
        controller.execute(request)


class _Undeclared(StrategyPort):
    """判定の瞬間を名乗らない戦略（宣言の欠落そのものを表す最小実装）。"""

    def on_init(self, config: Any, indicators: Any) -> None:
        return None

    def on_new_bar(self, bar_index: int, indicators: Any, account: Any) -> list:
        return []

    def on_position_check(self, position: Any, bar_index: int, indicators: Any) -> str:
        return "hold"


#: 宣言が「自分の状態から導かれる」ことを表す標識（クラス定数では足りない戦略）。
_DERIVED = "<derived>"
#: 宣言がそもそも無いことを表す標識（期待側には現れないので差分として露出する）。
_MISSING = "<missing>"


def _strategy_classes() -> "list[tuple[type, Path]]":
    """`simulator/adapter/strategy` が定義する `StrategyPort` 実装とその定義ファイル。"""
    import importlib

    root = Path(strategy_package.__file__).resolve().parent
    found: "list[tuple[type, Path]]" = []
    for path in sorted(root.glob("*.py")):
        if path.name == "__init__.py":
            continue
        module = importlib.import_module(
            f"{strategy_package.__name__}.{path.stem}"
        )
        for obj in vars(module).values():
            if not isinstance(obj, type) or not issubclass(obj, StrategyPort):
                continue
            if obj is StrategyPort or obj.__module__ != module.__name__:
                continue
            found.append((obj, path))
    return found


def _series_by_local_name(tree: ast.AST) -> "dict[str, str]":
    """``x = indicators.get("name")`` の束縛（局所名 -> 系列名）。"""
    bound: "dict[str, str]" = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        series = _series_of_call(node.value)
        if isinstance(target, ast.Name) and isinstance(series, str):
            bound[target.id] = series
    return bound


def _series_of_call(value: ast.AST) -> "str | None":
    """``indicators.get("name")`` なら系列名、``indicators.get(<式>)`` なら動的（None）。"""
    if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Attribute):
        return None
    if value.func.attr != "get" or not value.args:
        return None
    first = value.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _shift_of_index(index: ast.AST) -> int:
    """``.iloc[...]`` の添字が当該足からいくつ過去を指すか。

    ``bar_index - k`` は確定足（1 以上）、``bar_index`` は当該足（0）、``bar_index + k`` は
    未来（負）。それ以外（局所変数・定数）は**当該足として扱う**——足が閉じるまで確定しない
    側に倒すのが安全側だからである。
    """
    if isinstance(index, ast.BinOp) and isinstance(index.op, ast.Sub):
        return 1
    if isinstance(index, ast.BinOp) and isinstance(index.op, ast.Add):
        return -1
    return 0


def _reads_of_module(path: Path) -> "tuple[frozenset, bool]":
    """モジュールが読む (系列名, shift) の集合と、動的参照を含むか。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bound = _series_by_local_name(tree)
    reads: "set[tuple[str, int]]" = set()
    dynamic = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        holder = node.value
        if not isinstance(holder, ast.Attribute) or holder.attr != "iloc":
            continue
        shift = _shift_of_index(node.slice)
        target = holder.value
        series = _series_of_call(target)
        if series is None and isinstance(target, ast.Name):
            series = bound.get(target.id)
        if series is None:
            dynamic = True
            continue
        reads.add((series, shift))
    return frozenset(reads), dynamic


def _declared_of_class(cls: type) -> Any:
    """クラス側の宣言を「値」「導出」「不在」のいずれかとして表す。"""
    declared = getattr(cls, "entry_price_basis", _MISSING)
    if isinstance(declared, property):
        return _DERIVED
    return declared


def test_every_strategy_declares_the_bar_it_actually_reads() -> None:
    """宣言が実装の参照集合から導かれる値と一致する（動的参照は導出で名乗る）。"""
    # Arrange
    classes = _strategy_classes()
    # Act
    observed = {cls.__name__: _declared_of_class(cls) for cls, _ in classes}
    expected = {
        cls.__name__: (
            _DERIVED if _reads_of_module(path)[1] else basis_for_reads(_reads_of_module(path)[0])
        )
        for cls, path in classes
    }
    # Assert
    assert observed == expected
    assert len(observed) >= 8, "戦略の列挙が痩せている（実装を 1 本も見ていない可能性）"


def _generic_with(*conditions: Any) -> Any:
    """条件だけを変えた `GenericConditionStrategy` を組む（他は同一）。"""
    from simulator.adapter.strategy.generic_condition_strategy import (
        GenericConditionStrategy,
    )
    from simulator.domain.entry_conditions import EntryConditions

    return GenericConditionStrategy(
        entry_long=EntryConditions(list(conditions)),
        entry_short=EntryConditions([]),
    )


def test_the_spec_driven_strategy_declares_from_the_shift_it_reads() -> None:
    """設定依存の戦略は**自分の状態から**宣言を導く（クラス定数では足りない）。

    4 点で固定する: 当該足の指標 / 確定足の指標 / 当該足の ``open`` / 条件なし。
    """
    # Arrange
    from simulator.domain.entry_conditions import Condition

    forming = _generic_with(Condition(indicator="ema", shift=0, op=">", rhs=1.0))
    closed = _generic_with(Condition(indicator="ema", shift=1, op=">", rhs=1.0))
    forming_open = _generic_with(Condition(indicator="open", shift=0, op=">", rhs=1.0))
    empty = _generic_with()
    # Act
    observed = (
        forming.entry_price_basis,
        closed.entry_price_basis,
        forming_open.entry_price_basis,
        empty.entry_price_basis,
    )
    # Assert
    assert observed == ("close", "current_open", "current_open", None)


class _FlatAccount:
    """保有玉なしの口座（重複抑止を通すための最小代役）。"""

    open_positions: list = []


def test_the_spec_driven_strategy_takes_its_base_price_from_its_own_declaration() -> None:
    """SL/TP の基準価格も宣言から決まる（設定から渡す口を残さない）。

    確定足だけを読む条件（判定は足の始まり）なら、基準価格は当該足の ``open`` である。
    設定で渡せる口が残っていると、同じ値の権威が 2 つになり一致の保証が消える。
    """
    # Arrange
    from simulator.adapter.indicator.registry import PandasIndicatorRegistry
    from simulator.adapter.strategy.generic_condition_strategy import (
        GenericConditionStrategy,
    )
    from simulator.domain.entry_conditions import Condition, EntryConditions

    config = {
        "lot_size": 0.1,
        "stop_loss_points": 100,
        "take_profit_points": 200,
        "point_size": 0.0001,
    }
    strategy = _generic_with(Condition(indicator="ema", shift=1, op=">", rhs=1.0))
    indicators = PandasIndicatorRegistry(
        {
            "ema": pd.Series([2.0, 2.0]),
            "open": pd.Series([1.5000, 1.5000]),
            "close": pd.Series([9.9999, 9.9999]),
        }
    )
    strategy.on_init(config, indicators)
    # Act
    orders = strategy.on_new_bar(1, indicators, _FlatAccount())
    # Assert: 基準は open[1]=1.5000（close は使わない）
    assert orders[0].sl == pytest.approx(1.5000 - 100 * 0.0001)
    with pytest.raises(TypeError):
        GenericConditionStrategy(
            entry_long=EntryConditions([]),
            entry_short=EntryConditions([]),
            entry_price_basis="close",
        )


def test_a_strategy_without_a_declaration_stops_the_run_at_build_time(
    tmp_path: Path,
) -> None:
    """宣言の無い戦略は `build_interactor` で落ちる（既定へ倒さない）。"""
    # Arrange
    csv_path = _write_csv(tmp_path / "m1.csv")
    meta = _meta(csv_path, strategy_override=_Undeclared())
    # Act / Assert
    with pytest.raises(EntryPriceBasisDeclarationError) as caught:
        build_interactor(**meta)
    assert "_Undeclared" in str(caught.value)
