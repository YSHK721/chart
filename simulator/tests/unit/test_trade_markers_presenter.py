"""TradeMarkersPresenter / TradeMarkerPresenterPort 単体テスト（TDD）。

設計入力: CHART_TRADE_MARKERS_DETAILED_DESIGN.md §2（Port/DTO/変換規則/アルゴリズム）、
  CHART_TRADE_MARKERS_BASIC_DESIGN.md §12（確定決定群）。
構造: Arrange-Act-Assert（AAA）。domain TradeRecord を合成し、presenter の純変換のみを観測する
  （engine/BacktestResult 生成経路には触れない＝読み取り専用消費）。

注意（upstream 検証で確定）: SymbolSpec に name 属性は無いため、symbol 名は present_markers の
  symbol 引数（name/digits を持つ軽量オブジェクト）として注入する。
"""
from __future__ import annotations

import abc
import inspect
import json
from dataclasses import dataclass
from typing import Any

import pandas as pd

from simulator.domain.trade_record import TradeRecord


# ---- テスト用ヘルパ ----------------------------------------------------------

@dataclass
class _Symbol:
    """present_markers に注入する symbol（name/digits を持つ・SymbolSpec 代替）。"""
    name: str
    digits: int


@dataclass
class _Result:
    """BacktestResult の trades 属性のみを満たす軽量スタブ（読み取り専用消費）。"""
    trades: Any


def _record(
    *,
    side: str = "buy",
    entry_time: str = "2025-01-02 09:00:00",
    exit_time: str = "2025-01-02 10:00:00",
    entry_price: float = 8568.9,
    exit_price: float = 8600.0,
    exit_reason: str = "tp",
    volume: float = 1.0,
    contract_size: float = 10.0,
    swap: float = 0.0,
    commission: float = 0.0,
    profit_round_digits: "int | None" = None,
) -> TradeRecord:
    return TradeRecord(
        side=side,
        volume=volume,
        entry_time=pd.Timestamp(entry_time).to_datetime64(),
        exit_time=pd.Timestamp(exit_time).to_datetime64(),
        entry_price=entry_price,
        exit_price=exit_price,
        contract_size=contract_size,
        swap=swap,
        commission=commission,
        exit_reason=exit_reason,
        profit_round_digits=profit_round_digits,
    )


def _unix(t: str) -> int:
    return int(pd.Timestamp(t).timestamp())


# ============================================================================
# P1: Port（TradeMarkerPresenterPort）
# ============================================================================

def test_port_is_abstract_and_cannot_be_instantiated_directly():
    # Arrange
    from simulator.usecase.marker_ports import TradeMarkerPresenterPort

    # Act / Assert: 抽象 Port は直接インスタンス化できない（present_markers が abstractmethod）
    assert issubclass(TradeMarkerPresenterPort, abc.ABC)
    try:
        TradeMarkerPresenterPort()  # type: ignore[abstract]
    except TypeError:
        pass
    else:
        raise AssertionError("抽象 Port が直接インスタンス化できてしまった")


def test_port_present_markers_signature_is_result_path_keyword_symbol_ea_name():
    # Arrange
    from simulator.usecase.marker_ports import TradeMarkerPresenterPort

    # Act
    sig = inspect.signature(TradeMarkerPresenterPort.present_markers)
    params = list(sig.parameters)

    # Assert: 確定シグネチャ present_markers(self, result, path, *, symbol, ea_name)
    assert params == ["self", "result", "path", "symbol", "ea_name"]
    assert sig.parameters["symbol"].kind is inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["ea_name"].kind is inspect.Parameter.KEYWORD_ONLY


# ============================================================================
# P2: TradeMarkersPresenter
# ============================================================================

def _present(records, *, name="JP225", digits=1, ea_name="TC24051901", tmp_path):
    from simulator.adapter.presenter.trade_markers import TradeMarkersPresenter

    out = tmp_path / "markers.json"
    TradeMarkersPresenter().present_markers(
        _Result(trades=list(records)),
        out,
        symbol=_Symbol(name=name, digits=digits),
        ea_name=ea_name,
    )
    return json.loads(out.read_text(encoding="utf-8"))


def test_presenter_implements_port_contract():
    from simulator.adapter.presenter.trade_markers import TradeMarkersPresenter
    from simulator.usecase.marker_ports import TradeMarkerPresenterPort

    assert issubclass(TradeMarkersPresenter, TradeMarkerPresenterPort)


def test_entry_buy_marker_uses_belowbar_arrowup_buycolor_and_text(tmp_path):
    # Arrange
    rec = _record(side="buy", entry_price=8568.9)
    # Act
    payload = _present([rec], tmp_path=tmp_path)
    entry = [m for m in payload["markers"] if m["meta"]["kind"] == "entry"][0]
    # Assert
    assert entry["lwc"]["position"] == "belowBar"
    assert entry["lwc"]["shape"] == "arrowUp"
    assert entry["lwc"]["color"] == "#26a69a"
    assert entry["lwc"]["text"] == "BUY 8568.9"
    assert entry["meta"] == {"kind": "entry", "side": "buy"}


def test_entry_sell_marker_uses_abovebar_arrowdown_sellcolor_and_text(tmp_path):
    # Arrange
    rec = _record(side="sell", entry_price=8600.0, exit_price=8568.9)
    # Act
    payload = _present([rec], tmp_path=tmp_path)
    entry = [m for m in payload["markers"] if m["meta"]["kind"] == "entry"][0]
    # Assert
    assert entry["lwc"]["position"] == "aboveBar"
    assert entry["lwc"]["shape"] == "arrowDown"
    assert entry["lwc"]["color"] == "#ef5350"
    assert entry["lwc"]["text"] == "SELL 8600.0"


def test_exit_buy_position_win_uses_abovebar_circle_buycolor_with_reason_and_pnl(tmp_path):
    # Arrange: buy 玉・勝ち（pnl>0）。pnl = (8600-8568.9)*1*1*10 = 311.0 → +311
    rec = _record(side="buy", entry_price=8568.9, exit_price=8600.0, exit_reason="tp")
    # Act
    payload = _present([rec], tmp_path=tmp_path)
    ex = [m for m in payload["markers"] if m["meta"]["kind"] == "exit"][0]
    # Assert
    assert ex["lwc"]["position"] == "aboveBar"  # buy 玉の決済は反対側
    assert ex["lwc"]["shape"] == "circle"
    assert ex["lwc"]["color"] == "#26a69a"  # 勝ち
    assert ex["lwc"]["text"] == "TP 8600.0 (+311)"
    assert ex["meta"] == {"kind": "exit", "side": "buy"}


def test_exit_sell_position_uses_belowbar_circle(tmp_path):
    # Arrange: sell 玉
    rec = _record(side="sell", entry_price=8600.0, exit_price=8568.9, exit_reason="tp")
    # Act
    payload = _present([rec], tmp_path=tmp_path)
    ex = [m for m in payload["markers"] if m["meta"]["kind"] == "exit"][0]
    # Assert
    assert ex["lwc"]["position"] == "belowBar"  # sell 玉の決済は反対側
    assert ex["lwc"]["shape"] == "circle"


def test_exit_loss_marker_uses_sellcolor(tmp_path):
    # Arrange: buy 玉・負け（pnl<=0）。pnl = (8500-8568.9)*10 = -689 → 負け
    rec = _record(side="buy", entry_price=8568.9, exit_price=8500.0, exit_reason="sl")
    # Act
    payload = _present([rec], tmp_path=tmp_path)
    ex = [m for m in payload["markers"] if m["meta"]["kind"] == "exit"][0]
    # Assert
    assert ex["lwc"]["color"] == "#ef5350"
    assert ex["lwc"]["text"].startswith("SL 8500.0 (-")


def test_exit_breakeven_pnl_zero_is_treated_as_loss_color(tmp_path):
    # Arrange: pnl == 0（同値は非勝ち＝負け色）
    rec = _record(side="buy", entry_price=8568.9, exit_price=8568.9, exit_reason="reverse")
    # Act
    payload = _present([rec], tmp_path=tmp_path)
    ex = [m for m in payload["markers"] if m["meta"]["kind"] == "exit"][0]
    # Assert
    assert ex["lwc"]["color"] == "#ef5350"  # pnl==0 は非勝ち


def test_marker_time_uses_pandas_timestamp_unix_seconds(tmp_path):
    # Arrange
    rec = _record(entry_time="2025-01-02 09:00:00", exit_time="2025-01-02 10:00:00")
    # Act
    payload = _present([rec], tmp_path=tmp_path)
    entry = [m for m in payload["markers"] if m["meta"]["kind"] == "entry"][0]
    ex = [m for m in payload["markers"] if m["meta"]["kind"] == "exit"][0]
    # Assert: candles と同一式 int(pd.Timestamp(t).timestamp())
    assert entry["lwc"]["time"] == _unix("2025-01-02 09:00:00")
    assert ex["lwc"]["time"] == _unix("2025-01-02 10:00:00")


def test_markers_are_sorted_ascending_by_time_across_trades(tmp_path):
    # Arrange: 2 トレードを時刻が交差する順で投入
    r1 = _record(entry_time="2025-01-02 09:00:00", exit_time="2025-01-02 11:00:00")
    r2 = _record(entry_time="2025-01-02 10:00:00", exit_time="2025-01-02 12:00:00")
    # Act
    payload = _present([r1, r2], tmp_path=tmp_path)
    times = [m["lwc"]["time"] for m in payload["markers"]]
    # Assert: 全マーカーが time 昇順（entry/exit マージソート）
    assert times == sorted(times)
    assert times == [
        _unix("2025-01-02 09:00:00"),
        _unix("2025-01-02 10:00:00"),
        _unix("2025-01-02 11:00:00"),
        _unix("2025-01-02 12:00:00"),
    ]


def test_each_trade_yields_exactly_two_markers_and_count_matches(tmp_path):
    # Arrange
    recs = [_record(), _record(side="sell", entry_price=8600.0, exit_price=8550.0)]
    # Act
    payload = _present(recs, tmp_path=tmp_path)
    # Assert
    assert len(payload["markers"]) == 4  # 2 トレード × (entry+exit)
    assert payload["count"] == 4  # 全件数（無音切り捨て禁止＝H-4）


def test_payload_header_carries_ok_symbol_name_and_ea_name(tmp_path):
    # Arrange
    rec = _record()
    # Act
    payload = _present([rec], name="JP225", ea_name="TC24051901", tmp_path=tmp_path)
    # Assert: symbol 名・ea_name は引数注入（SymbolSpec.name に依存しない＝M-1）
    assert payload["ok"] is True
    assert payload["symbol"] == "JP225"
    assert payload["ea_name"] == "TC24051901"


def test_lwc_and_meta_are_separated_into_distinct_key_hierarchies(tmp_path):
    # Arrange
    rec = _record()
    # Act
    payload = _present([rec], tmp_path=tmp_path)
    m = payload["markers"][0]
    # Assert: lwc には純フィールドのみ、meta は別階層（M-2）
    assert set(m["lwc"]) == {"time", "position", "shape", "color", "text"}
    assert set(m["meta"]) == {"kind", "side"}


def test_digits_controls_price_precision_in_text(tmp_path):
    # Arrange: digits=2 で価格が 2 桁表示される
    rec = _record(side="buy", entry_price=8568.95)
    # Act
    payload = _present([rec], digits=2, tmp_path=tmp_path)
    entry = [m for m in payload["markers"] if m["meta"]["kind"] == "entry"][0]
    # Assert
    assert entry["lwc"]["text"] == "BUY 8568.95"


def test_pnl_round_digits_controls_pnl_precision_in_exit_text(tmp_path):
    # Arrange: profit_round_digits=2 で pnl が 2 桁表示。pnl=(8600-8568.9)*10=311.00
    rec = _record(
        side="buy", entry_price=8568.9, exit_price=8600.0,
        exit_reason="tp", profit_round_digits=2,
    )
    # Act
    payload = _present([rec], tmp_path=tmp_path)
    ex = [m for m in payload["markers"] if m["meta"]["kind"] == "exit"][0]
    # Assert: pd=profit_round_digits、None 時は 0 桁
    assert ex["lwc"]["text"] == "TP 8600.0 (+311.00)"


@dataclass
class _DuckRecord:
    """TradeRecord の __post_init__ 検証（6 種 reason 制約）を回避し、未知 reason の
    フォールバックを観測するためのダックタイプ・スタブ（pnl はメソッド）。"""
    side: str
    entry_time: Any
    exit_time: Any
    entry_price: float
    exit_price: float
    exit_reason: str
    _pnl: float

    def pnl(self) -> float:
        return self._pnl


def test_unknown_exit_reason_is_uppercased_into_text_and_marker_is_still_rendered(tmp_path):
    # Arrange: TradeRecord 制約外の未知 reason（フォールバック経路・描画継続）
    rec = _DuckRecord(
        side="buy",
        entry_time=pd.Timestamp("2025-01-02 09:00:00").to_datetime64(),
        exit_time=pd.Timestamp("2025-01-02 10:00:00").to_datetime64(),
        entry_price=8568.9,
        exit_price=8600.0,
        exit_reason="margin_call",
        _pnl=311.0,
    )
    # Act
    payload = _present([rec], tmp_path=tmp_path)
    ex = [m for m in payload["markers"] if m["meta"]["kind"] == "exit"][0]
    # Assert: 未知 reason はそのまま大文字化して text に出し、マーカーは生成される
    assert ex["lwc"]["text"] == "MARGIN_CALL 8600.0 (+311)"
    assert len([m for m in payload["markers"] if m["meta"]["kind"] == "exit"]) == 1
