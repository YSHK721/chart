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

def _present(records, *, name="JP225", digits=1, ea_name="TC24051901", timeframe=None, tmp_path):
    from simulator.adapter.presenter.trade_markers import TradeMarkersPresenter

    out = tmp_path / "markers.json"
    TradeMarkersPresenter().present_markers(
        _Result(trades=list(records)),
        out,
        symbol=_Symbol(name=name, digits=digits),
        ea_name=ea_name,
        timeframe=timeframe,
    )
    return json.loads(out.read_text(encoding="utf-8"))


def test_payload_includes_timeframe_when_provided(tmp_path):
    # 該当時間足（建玉の時間足）を payload の top-level timeframe に出力する。
    rec = _record(side="buy", entry_price=8568.9)
    payload = _present([rec], timeframe="1m", tmp_path=tmp_path)
    assert payload["timeframe"] == "1m"


def test_payload_timeframe_is_none_when_omitted(tmp_path):
    # timeframe 未指定なら None（後方互換・フロントはゲートしない）。
    rec = _record(side="buy", entry_price=8568.9)
    payload = _present([rec], tmp_path=tmp_path)
    assert payload["timeframe"] is None


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
    # v4: meta は kind/side（+ pair=トレード通番）。kind/side の不変を維持。
    assert entry["meta"]["kind"] == "entry"
    assert entry["meta"]["side"] == "buy"


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
    # v4: meta は kind/side（+ pair）。kind/side の不変を維持。
    assert ex["meta"]["kind"] == "exit"
    assert ex["meta"]["side"] == "buy"


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
    # Assert: lwc/meta は別階層（M-2）。v4 で lwc に id、meta に pair を追加（§10.3）。
    #   従来の純フィールド（time/position/shape/color/text）は lwc 側に維持される。
    assert {"time", "position", "shape", "color", "text"} <= set(m["lwc"])
    assert {"kind", "side"} <= set(m["meta"])
    # lwc と meta はキーが交わらない（分離不変）。
    assert set(m["lwc"]).isdisjoint(set(m["meta"]))


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
    volume: float = 1.0  # ISSUE-026: pair に volume を出すため実 TradeRecord と同じ属性を持たせる。

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


# ============================================================================
# v4 追加機能（§10）: marker.lwc.id / meta.pair / JSON pairs 配列
#   設計入力: CHART_TRADE_MARKERS_DETAILED_DESIGN.md §10.3。
#   既存 markers/時刻式/配色/text/昇順/lwc・meta 分離は不変（回帰テストで担保済）。
# ============================================================================

def _markers_for_trade(payload, i):
    """meta.pair == i の (entry, exit) マーカーを返す（昇順保持）。"""
    ms = [m for m in payload["markers"] if m["meta"].get("pair") == i]
    entry = [m for m in ms if m["meta"]["kind"] == "entry"][0]
    ex = [m for m in ms if m["meta"]["kind"] == "exit"][0]
    return entry, ex


def test_v4_entry_and_exit_markers_carry_pair_index_id_in_lwc(tmp_path):
    # Arrange: 2 トレード → 各 marker の lwc.id が "t{i}:entry"/"t{i}:exit"
    recs = [
        _record(side="buy", entry_time="2025-01-02 09:00:00", exit_time="2025-01-02 10:00:00"),
        _record(side="sell", entry_price=8600.0, exit_price=8550.0,
                entry_time="2025-01-02 11:00:00", exit_time="2025-01-02 12:00:00"),
    ]
    # Act
    payload = _present(recs, tmp_path=tmp_path)
    e0, x0 = _markers_for_trade(payload, 0)
    e1, x1 = _markers_for_trade(payload, 1)
    # Assert: id は createSeriesMarkers が受理する規約 "t{i}:entry"/"t{i}:exit"
    assert e0["lwc"]["id"] == "t0:entry"
    assert x0["lwc"]["id"] == "t0:exit"
    assert e1["lwc"]["id"] == "t1:entry"
    assert x1["lwc"]["id"] == "t1:exit"


def test_v4_meta_carries_pair_trade_index(tmp_path):
    # Arrange: meta.pair に元トレード通番 i を付与（由来トレース）
    recs = [
        _record(entry_time="2025-01-02 09:00:00", exit_time="2025-01-02 10:00:00"),
        _record(entry_time="2025-01-02 11:00:00", exit_time="2025-01-02 12:00:00"),
    ]
    # Act
    payload = _present(recs, tmp_path=tmp_path)
    # Assert: trade 0 由来は pair==0、trade 1 由来は pair==1
    pairs0 = {m["meta"]["pair"] for m in payload["markers"]
              if m["meta"]["kind"] in ("entry", "exit") and m["lwc"]["id"].startswith("t0:")}
    pairs1 = {m["meta"]["pair"] for m in payload["markers"]
              if m["lwc"]["id"].startswith("t1:")}
    assert pairs0 == {0}
    assert pairs1 == {1}


def test_v4_meta_keys_extended_with_pair_only(tmp_path):
    # Arrange
    rec = _record()
    # Act
    payload = _present([rec], tmp_path=tmp_path)
    m = payload["markers"][0]
    # Assert: meta は kind/side に pair を加えた 3 キー（既存 2 キーを破壊しない）
    assert set(m["meta"]) == {"kind", "side", "pair"}


def test_v4_lwc_keys_extended_with_id_only(tmp_path):
    # Arrange
    rec = _record()
    # Act
    payload = _present([rec], tmp_path=tmp_path)
    m = payload["markers"][0]
    # Assert: lwc は従来 5 キー + id（createSeriesMarkers が受理）
    assert set(m["lwc"]) == {"time", "position", "shape", "color", "text", "id"}


def test_v4_pairs_array_present_with_one_entry_per_trade(tmp_path):
    # Arrange: 2 トレード
    recs = [
        _record(entry_time="2025-01-02 09:00:00", exit_time="2025-01-02 10:00:00"),
        _record(side="sell", entry_price=8600.0, exit_price=8550.0,
                entry_time="2025-01-02 11:00:00", exit_time="2025-01-02 12:00:00"),
    ]
    # Act
    payload = _present(recs, tmp_path=tmp_path)
    # Assert: pairs は各トレード 1 件で件数一致
    assert "pairs" in payload
    assert len(payload["pairs"]) == 2
    assert [p["i"] for p in payload["pairs"]] == [0, 1]


def test_v4_pair_record_carries_side_win_entry_exit_time_and_price(tmp_path):
    # Arrange: buy 玉・勝ち（pnl>0）。entry 8568.9 → exit 8600.0
    rec = _record(side="buy", entry_price=8568.9, exit_price=8600.0,
                  entry_time="2025-01-02 09:00:00", exit_time="2025-01-02 10:00:00")
    # Act
    payload = _present([rec], tmp_path=tmp_path)
    p = payload["pairs"][0]
    # Assert: i/side/win(pnl>0)/entry{time,price}/exit{time,price}（時刻は既存 UNIX 秒式）
    assert p["i"] == 0
    assert p["side"] == "buy"
    assert p["win"] is True
    assert p["entry"] == {"time": _unix("2025-01-02 09:00:00"), "price": 8568.9}
    assert p["exit"] == {"time": _unix("2025-01-02 10:00:00"), "price": 8600.0}


def test_issue026_pair_record_carries_profit_and_volume_for_hover_popup(tmp_path):
    # Arrange: hover 明細ポップアップ用に pair が profit(pnl) と volume(数量) を保持する。
    #   pnl = (8600-8568.9)*1*volume*10。volume=2.0 で 622.0。
    rec = _record(side="buy", entry_price=8568.9, exit_price=8600.0,
                  exit_reason="tp", volume=2.0)
    # Act
    payload = _present([rec], tmp_path=tmp_path)
    p = payload["pairs"][0]
    # Assert: profit は pnl()、volume は取引数量（取引数量＝決済数量＝同量決済）。
    assert p["profit"] == rec.pnl()
    assert p["volume"] == 2.0


def test_v4_pair_win_is_false_when_pnl_not_positive(tmp_path):
    # Arrange: buy 玉・負け（pnl<0）。pnl = (8500-8568.9)*10 < 0
    rec = _record(side="buy", entry_price=8568.9, exit_price=8500.0, exit_reason="sl")
    # Act
    payload = _present([rec], tmp_path=tmp_path)
    # Assert: pnl<=0 は win=False（マーカー配色と整合）
    assert payload["pairs"][0]["win"] is False


def test_v4_pair_win_false_at_breakeven_pnl_zero(tmp_path):
    # Arrange: pnl == 0（境界・非勝ち）
    rec = _record(side="buy", entry_price=8568.9, exit_price=8568.9, exit_reason="reverse")
    # Act
    payload = _present([rec], tmp_path=tmp_path)
    # Assert: pnl==0 は win=False（exit marker の負け色と一致）
    assert payload["pairs"][0]["win"] is False


# ============================================================================
# ISSUE-411 スライス 1: epoch int（np.int64）の時刻表現を 1970 年へ落とさない
# ============================================================================
#
# `bar.time` の実体は経路で分かれる（ISSUE-403 B-1 実測）。comma 形式 CSV ローダ
# （`adapter/repository/ohlc_csv.py`）は epoch 整数を採用し、pandas はその整数を
# **ns** と解釈するため、`pd.Timestamp(np.int64(1755183000))` は 1970-01-01 になる
# （実測: `int(pd.Timestamp(np.int64(1755183000)).timestamp())` == 1）。
# epoch 秒への正規化は `simulator.domain.bar_time.epoch_seconds` が唯一の実体であり、
# presenter はその関数を呼ぶ（規則を書き写さない）。


def _record_with_epoch_int_times(entry: int, exit_: int) -> TradeRecord:
    """時刻が epoch int（`numpy.int64`）の TradeRecord（comma 形式 CSV 経路の実型）。"""
    import numpy as np

    return TradeRecord(
        side="buy",
        volume=1.0,
        entry_time=np.int64(entry),
        exit_time=np.int64(exit_),
        entry_price=8568.9,
        exit_price=8600.0,
        contract_size=10.0,
        swap=0.0,
        commission=0.0,
        exit_reason="tp",
    )


def test_marker_time_of_numpy_int64_epoch_is_the_epoch_itself(tmp_path):
    # Arrange: comma 形式 CSV 経路の実型（np.int64 の epoch 秒）
    rec = _record_with_epoch_int_times(1755183000, 1755186600)
    # Act
    payload = _present([rec], tmp_path=tmp_path)
    # Assert: epoch を ns と誤読せずその値のまま出力する（誤読時は 1 になる）
    entry_marker = next(m for m in payload["markers"] if m["meta"]["kind"] == "entry")
    exit_marker = next(m for m in payload["markers"] if m["meta"]["kind"] == "exit")
    assert entry_marker["lwc"]["time"] == 1755183000
    assert exit_marker["lwc"]["time"] == 1755186600


def test_pair_record_time_of_numpy_int64_epoch_is_the_epoch_itself(tmp_path):
    # Arrange: pairs（線分結合用）も同じ変換実体を通る
    rec = _record_with_epoch_int_times(1755183000, 1755186600)
    # Act
    payload = _present([rec], tmp_path=tmp_path)
    # Assert
    p = payload["pairs"][0]
    assert p["entry"]["time"] == 1755183000
    assert p["exit"]["time"] == 1755186600
