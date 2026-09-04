"""TBD-14（推定建値の乖離はロット刻み吸収基準）の実測固定 — 基本設計書 §12.2。

裁定（§12.2 依頼者承認済み）:
    * バー経路の**成行注文でのみ**、発注量計算に使う推定建値と実際の建値（`derive_quotes`）に
      スプレッド分の差が生じる（実取引の成行と同一の構図・シミュレータ特有の欠陥ではない）。
    * **合格基準**: この差に起因する発注量の差が `volume_step`（ロット刻み）未満に収まること。
      刻みを跨ぐケースは保守側（少ない方）へ丸める。
    * 指値・逆指値（約定価格＝指定価格）とティック経路（bid/ask が引数）では差は生じない。

本検定の方式（合成計算で済ませない）:
    実際に `build_interactor` を通して**エンジンが使うのと同一の指標レジストリ・Bar 列・
    SymbolSpec** を得たうえで、実建値はエンジン内部の実関数
    `simulator.usecase._execution.derive_quotes` に決めさせて突き合わせる。
    推定側・実側とも同一の `AccountMarginSizing` へ通し、量の差を測る。

    さらに、`run_backtest` を sizing OFF / ON で実行し、Decorator が**実際に**エンジン経路で
    効いていることを取引結果で確認する（受け口だけ作って結線されない壊れ方の遮断）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from simulator.adapter.sizing.account_margin_sizing import AccountMarginSizing
from simulator.domain.exceptions import IndicatorBufferError
from simulator.adapter.strategy.sizing_decorator import build_sizing_decorator
from simulator.main import build_interactor, run_backtest
from simulator.usecase._execution import derive_quotes
from simulator.usecase.sizing_models import SizingConfig, SizingContext, SizingRule
from simulator.usecase.sizing_ports import required_price_series

# 合成 OHLC（既存 integration テストと同型）。bar2 に spread=200pts を置き、
# current_open 基準で Ask=open+spread×point がバー open と判別可能になるようにする。
_ROWS = [
    # time は UNIX 秒 int（UTC・2024-01-01T00:00:00Z=1704067200）。comma 形式 CSV の `time` は epoch 秒が契約であり（Candle 契約 §2.1）、ISO 文字列は `Bar.time` 契約違反になる。
    (1704067200, 1.1000, 1.1010, 1.0990, 1.0995, 1.0, 0),
    (1704067260, 1.1000, 1.1010, 1.0985, 1.0990, 1.0, 0),
    (1704067320, 1.0990, 1.1050, 1.0990, 1.1040, 1.0, 200),
    (1704067380, 1.1040, 1.1100, 1.1040, 1.1090, 1.0, 200),
    (1704067440, 1.1090, 1.1120, 1.0900, 1.0950, 1.0, 200),
    (1704067500, 1.0950, 1.0960, 1.0900, 1.0920, 1.0, 200),
]

# MT5 形式（タブ区切り）の最小 CSV。MA_Slope_Pending_EA の registry は
# {"ema","open","spread"} を持つため（`simulator/main/__init__.py` `_build_ma_slope_pending_registry`）、
# entry_price_basis="current_open" の推定（"open" 系列）を測れる唯一の経路である。
_MT5_HEADER = "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>"

_POINT_SIZE = 0.0001
_VOLUME_STEP = 0.01
_VOLUME_MIN = 0.01
_VOLUME_MAX = 100.0
# MC を軽くする（アルゴリズムは同一・§12.6 の決定性はシード固定で保たれる）。
_SIZING = SizingConfig(enabled=True, sims=50, seed=1)


def _write_csv(path: Path) -> Path:
    lines = ["time,open,high,low,close,volume,spread"]
    for t, o, h, l, c, v, s in _ROWS:
        lines.append(f"{t},{o},{h},{l},{c},{v},{s}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _meta(csv_path: Path, **overrides) -> dict:
    base = dict(
        data_path=csv_path,
        symbol="EURUSD",
        period="M1",
        ea_name="TC24051901",
        initial_deposit=100_000.0,
        contract_size=1.0,
        volume_min=_VOLUME_MIN,
        volume_max=_VOLUME_MAX,
        volume_step=_VOLUME_STEP,
        stops_level=0,
        digits=5,
        point_size=_POINT_SIZE,
        leverage=100.0,
        ma_period=2,
        ma_method="sma",
        lot_size=1.0,
        stop_loss_points=500,
        take_profit_points=3000,
    )
    base.update(overrides)
    return base


def _sizing_for(spec) -> AccountMarginSizing:
    return AccountMarginSizing(
        SizingRule(
            edge=_SIZING.to_edge_spec(),
            margin_rate=_SIZING.margin_rate,
            point_value=_SIZING.point_value,
            volume_min=spec.volume_min,
            volume_max=spec.volume_max,
            volume_step=spec.volume_step,
        )
    )


def _volume(sizing: AccountMarginSizing, price: float, *, side: str,
            stop: float, equity: float) -> "float | None":
    return sizing.decide_volume(
        SizingContext(
            side=side, estimated_entry_price=price,
            stop_loss_price=stop, equity=equity,
        )
    ).volume


# --- 1. entry_price_basis 別の推定建値差 ----------------------------------

def _write_mt5_csv(path: Path, n: int = 30, spread: int = 100) -> Path:
    lines = [_MT5_HEADER]
    base = 39400.0
    for i in range(n):
        price = base + i
        lines.append(
            f"2025.01.02\t01:{i:02d}:00\t{price}\t{price}\t{price}\t{price}\t1\t0\t{spread}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _mt5_meta(csv_path: Path, **overrides) -> dict:
    base = dict(
        data_path=csv_path,
        symbol="JP225",
        period="M1",
        ea_name="MA_Slope_Pending_EA",
        initial_deposit=1_000_000.0,
        contract_size=10.0,
        volume_min=0.1,
        volume_max=100.0,
        volume_step=0.1,
        stops_level=0,
        digits=1,
        point_size=1.0,
        leverage=100.0,
        ma_period=5,
        ma_method="ema",
        lot_size=1.0,
        stop_loss_points=500,
        take_profit_points=3000,
    )
    base.update(overrides)
    return base


def _measure_diffs(controller, request, basis: str) -> "list[float]":
    """全バーで「推定建値による量」と「実建値による量」の差を測る。"""
    registry = controller._interactor._indicators
    spec = request.symbol_spec
    series_name = required_price_series(basis)
    sizing = _sizing_for(spec)
    equity = request.account.initial_deposit
    diffs: "list[float]" = []
    for i, bar in enumerate(request.bars):
        estimated = float(registry.get(series_name).iloc[i])
        # 実建値はエンジン内部の実関数に決めさせる（式を写さない）
        bid, ask, _, _ = derive_quotes(
            bar, entry_price_basis=basis, point_size=spec.point_size
        )
        for side, actual in (("buy", ask), ("sell", bid)):
            offset = 0.005 * estimated
            stop = estimated - offset if side == "buy" else estimated + offset
            v_est = _volume(sizing, estimated, side=side, stop=stop, equity=equity)
            v_act = _volume(sizing, actual, side=side, stop=stop, equity=equity)
            assert v_est is not None and v_act is not None, (
                f"bar={i} side={side} で量が決まらない"
            )
            diffs.append(abs(v_est - v_act))
    return diffs


def test_close基準_推定建値差による発注量差が刻み未満(tmp_path: Path) -> None:
    """§12.2 合格基準（close 基準）。derive_quotes が bid=ask=close を返すため差 0。"""
    # Arrange
    csv_path = _write_csv(tmp_path / "m1.csv")
    controller, request = build_interactor(
        **_meta(csv_path, config_overrides={"entry_price_basis": "close"})
    )
    # Act
    diffs = _measure_diffs(controller, request, "close")
    # Assert
    assert diffs, "測定対象のバーが無い"
    assert max(diffs) == 0.0, f"close 基準なのに差がある: max={max(diffs)}"


def test_current_open基準_推定建値差による発注量差が刻み未満(tmp_path: Path) -> None:
    """§12.2 合格基準（current_open 基準）。スプレッド由来差が volume_step 未満。

    実測に基づく重要な限定（設計への申し送り）: 既定 TC24051901 の registry は
    {"madiff","close"} であり **"open" 系列を持たない**。したがって
    entry_price_basis="current_open" × sizing ON は TC 経路では成立せず、
    E-3（§12.5）の受付時拒否は **(ea_name, entry_price_basis) の組**で判定する必要がある。
    本ケースは "open" を持つ MA_Slope_Pending_EA の registry で測る。
    """
    # Arrange
    csv_path = _write_mt5_csv(tmp_path / "jp225.csv")
    controller, request = build_interactor(
        **_mt5_meta(csv_path, config_overrides={"entry_price_basis": "current_open"})
    )
    # Act
    diffs = _measure_diffs(controller, request, "current_open")
    # Assert
    assert diffs, "測定対象のバーが無い"
    assert max(diffs) < request.symbol_spec.volume_step, (
        "推定建値差に起因する発注量差が volume_step 以上: "
        f"max={max(diffs)} step={request.symbol_spec.volume_step}"
    )


def test_E3の拒否条件は戦略と約定価格基準の組で決まる(tmp_path: Path) -> None:
    """実測（設計への申し送り）: ea_name 単独では E-3 を判定できない。

    TC24051901 の registry は "close" を持つので basis="close" なら sizing 可能だが、
    basis="current_open" が要求する "open" は持たない。
    """
    # Arrange
    csv_path = _write_csv(tmp_path / "m1.csv")
    controller, _ = build_interactor(**_meta(csv_path))
    registry = controller._interactor._indicators
    # Act / Assert
    assert registry.get(required_price_series("close")) is not None
    with pytest.raises(IndicatorBufferError):
        registry.get(required_price_series("current_open"))


def test_close基準では推定建値差がゼロ(tmp_path: Path) -> None:
    """§12.2「close 基準＝差 0」。derive_quotes は bid=ask=bar.close を返す。"""
    # Arrange
    csv_path = _write_csv(tmp_path / "m1.csv")
    controller, request = build_interactor(
        **_meta(csv_path, config_overrides={"entry_price_basis": "close"})
    )
    registry = controller._interactor._indicators
    spec = request.symbol_spec
    # Act / Assert
    for i, bar in enumerate(request.bars):
        estimated = float(registry.get("close").iloc[i])
        bid, ask, _, _ = derive_quotes(
            bar, entry_price_basis="close", point_size=spec.point_size
        )
        assert estimated == bid == ask, "close 基準なのに推定と実建値が食い違う"


def test_current_open基準の差はスプレッド由来のみ(tmp_path: Path) -> None:
    """§12.2「current_open 基準＝スプレッド由来差」。差の正体を実測で明示する。"""
    # Arrange（"open" を持つ registry が要る＝MA_Slope_Pending_EA 経路）
    csv_path = _write_mt5_csv(tmp_path / "jp225.csv")
    controller, request = build_interactor(
        **_mt5_meta(csv_path, config_overrides={"entry_price_basis": "current_open"})
    )
    registry = controller._interactor._indicators
    spec = request.symbol_spec
    seen_nonzero = False
    # Act / Assert
    for i, bar in enumerate(request.bars):
        estimated = float(registry.get("open").iloc[i])
        bid, ask, _, _ = derive_quotes(
            bar, entry_price_basis="current_open", point_size=spec.point_size
        )
        # 売り（bid）は差 0、買い（ask）はスプレッド分だけ離れる
        assert estimated == pytest.approx(bid, abs=1e-12)
        assert ask - estimated == pytest.approx(
            bar.spread * spec.point_size, abs=1e-12
        )
        if bar.spread:
            seen_nonzero = True
    assert seen_nonzero, "スプレッド非ゼロのバーが無く、差を測れていない"


def test_ペンディング発注には推定差が生じない(tmp_path: Path) -> None:
    """§12.2: 指値・逆指値は約定価格＝指定価格のため推定しない。"""
    # Arrange
    from simulator.domain.order import Order
    from simulator.adapter.strategy.sizing_decorator import SizingDecorator
    from simulator.usecase.sizing_models import SizingDecision
    from simulator.usecase.sizing_ports import SizingPort

    class _Recorder(SizingPort):
        def __init__(self) -> None:
            self.prices: "list[float]" = []

        def decide_volume(self, context: SizingContext) -> SizingDecision:
            self.prices.append(context.estimated_entry_price)
            return SizingDecision(volume=0.5, fraction=0.05)

    class _Pending:
        def on_init(self, config, indicators) -> None: ...
        def on_position_check(self, position, bar_index, indicators) -> str:
            return "hold"

        def on_new_bar(self, bar_index, indicators, account):
            return [Order(side="buy", kind="buy_limit", volume=0.1,
                          price=1.0900, sl=1.0850)]

        def on_tick(self, bar_index, bid, ask, account):
            return []

    csv_path = _write_mt5_csv(tmp_path / "jp225.csv")
    controller, request = build_interactor(
        **_mt5_meta(csv_path, config_overrides={"entry_price_basis": "current_open"})
    )
    registry = controller._interactor._indicators

    class _Account:
        equity = 100_000.0

    recorder = _Recorder()
    dec = SizingDecorator(_Pending(), recorder, price_series="open")
    # Act
    dec.on_new_bar(2, registry, _Account())
    # Assert（系列の値ではなく order.price が使われている＝推定していない）
    assert recorder.prices == [1.0900]


# --- 2. 実結線（受け口だけ作って死ぬ壊れ方の遮断）-------------------------

def test_sizingOFFは既存挙動と同一の取引結果になる(tmp_path: Path) -> None:
    """§12.1 既定 OFF＝byte 等価。OFF を 2 回走らせて同一であることを確認する。"""
    # Arrange
    csv_path = _write_csv(tmp_path / "m1.csv")
    # Act
    code_a, result_a = run_backtest(output_dir=tmp_path / "a", **_meta(csv_path))
    code_b, result_b = run_backtest(
        output_dir=tmp_path / "b", **_meta(csv_path, strategy_decorator=None)
    )
    # Assert
    assert code_a == code_b == 0
    assert [t.volume for t in result_a.trades] == [t.volume for t in result_b.trades]


def test_sizingONで発注量が戦略の固定値から変わる(tmp_path: Path) -> None:
    """Decorator が**実際にエンジン経路で**効いていること（ISSUE-291 同型の遮断）。"""
    # Arrange
    csv_path = _write_csv(tmp_path / "m1.csv")
    _, off = run_backtest(output_dir=tmp_path / "off", **_meta(csv_path))
    _, request = build_interactor(**_meta(csv_path))
    decorator = build_sizing_decorator(
        _SIZING, symbol_spec=request[1].symbol_spec if isinstance(request, tuple)
        else request.symbol_spec,
        entry_price_basis="close",
    )
    # Act
    code, on = run_backtest(
        output_dir=tmp_path / "on", **_meta(csv_path, strategy_decorator=decorator)
    )
    # Assert
    assert code == 0
    assert off.trades, "OFF 側に取引が無く比較にならない"
    off_volumes = {t.volume for t in off.trades}
    on_volumes = {t.volume for t in on.trades}
    assert off_volumes == {1.0}, f"戦略の固定 lot_size が出ていない: {off_volumes}"
    assert on_volumes != off_volumes, "sizing ON でも発注量が変わっていない（未結線）"


def test_sizingONの発注量は刻みの倍数である(tmp_path: Path) -> None:
    """§12.2 保守側の丸めがエンジンへ渡る量そのものに効いていること。

    実測済みの事実: `Order.validate` は production 経路で呼ばれず、刻み違反は
    エンジンで例外にならない。したがって刻みの強制点は Decorator の出力そのものである。
    """
    # Arrange
    csv_path = _write_csv(tmp_path / "m1.csv")
    _, request = build_interactor(**_meta(csv_path))
    decorator = build_sizing_decorator(
        _SIZING, symbol_spec=request.symbol_spec, entry_price_basis="close"
    )
    # Act
    _, result = run_backtest(
        output_dir=tmp_path / "on", **_meta(csv_path, strategy_decorator=decorator)
    )
    # Assert
    assert result.trades
    for trade in result.trades:
        ratio = trade.volume / _VOLUME_STEP
        assert abs(ratio - round(ratio)) < 1e-6, f"刻み違反: {trade.volume}"
        assert _VOLUME_MIN <= trade.volume <= _VOLUME_MAX


# --- 3. 【従】実行中の fail-stop（§12.8・防御の二重化）---------------------

# 裁定（2026-08-11）: SL 保証は受付時検証で決着させる【主】。それをすり抜けた場合に備え、
# 実行中に SL 無し注文へ遭遇したら**内部不変条件違反としてジョブを明示失敗**させる【従】。
# 本節は「無音の取引ゼロ（exit=0・trades 0 件）にならない」ことを実 EA・実エンジンで固定する。

def test_SL距離0のsizingONは無音の取引ゼロにならない(tmp_path: Path) -> None:
    """`stop_loss_points=0` ＝ SL が建値と同一（リスク距離 0）の実 EA 設定。

    捨てる実装では exit=0・trades 0 件の「正常終了」になり、利用者からは
    「戦略が一度も発注しなかった」としか見えない。例外で止まることを固定する。
    """
    from simulator.usecase.sizing_ports import SizingRequiresStopLossError

    # Arrange
    csv_path = _write_csv(tmp_path / "m1.csv")
    _, request = build_interactor(**_meta(csv_path))
    decorator = build_sizing_decorator(
        _SIZING, symbol_spec=request.symbol_spec, entry_price_basis="close"
    )
    # Act / Assert（BacktestController の終了コード翻訳に飲み込まれずに伝播すること）
    with pytest.raises(SizingRequiresStopLossError):
        run_backtest(
            output_dir=tmp_path / "out",
            **_meta(csv_path, stop_loss_points=0, strategy_decorator=decorator),
        )


def test_fail_stopの例外はBacktestErrorではない() -> None:
    """`BacktestController.run`（adapter/controller.py:60-64）は BacktestError を
    終了コードだけに翻訳する。継承していると理由がそこで消え、運用者へ届かない。"""
    from simulator.domain.exceptions import BacktestError
    from simulator.usecase.sizing_ports import SizingRequiresStopLossError

    assert not issubclass(SizingRequiresStopLossError, BacktestError)


def test_SL距離が正なら従来どおり完走する(tmp_path: Path) -> None:
    """fail-stop の導入で正常経路を壊していないこと（回帰）。"""
    # Arrange
    csv_path = _write_csv(tmp_path / "m1.csv")
    _, request = build_interactor(**_meta(csv_path))
    decorator = build_sizing_decorator(
        _SIZING, symbol_spec=request.symbol_spec, entry_price_basis="close"
    )
    # Act
    code, result = run_backtest(
        output_dir=tmp_path / "ok", **_meta(csv_path, strategy_decorator=decorator)
    )
    # Assert
    assert code == 0
    assert result.trades
