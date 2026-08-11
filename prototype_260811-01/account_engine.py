"""account_engine — 口座状態エンジン（OANDA 証券 JP225 CFD の参照実装・ISSUE-369）。

目的:
    発注計画（方向・建値・ロット・損切り・利確）を tick 系列へ順に適用し、決済までの
    口座状態（残高・有効証拠金・必要証拠金・証拠金維持率）とイベント（約定・損切り・
    利確・ロスカット）をティック粒度で再現する。本モジュールが証拠金・ロスカット計算の
    **参照実装（権威）**であり、UI 側（integrated_position_sizing_calculator.html /
    将来のチャート統合 ISSUE-368）の式はここの実測へ一致させる。

責務の分離（SRP）:
    - 本モジュール: 口座状態の計算のみ。データ読込（run_scenario.py）・レポート描画
      （report_build.py）・式の突き合わせ（verify.py）は持たない。
    - 依存: 標準ライブラリのみ（pandas / matplotlib 非依存）。tick は (ts_ms, bid, ask)
      のイテレータで受ける（DIP: データ源の形式を知らない）。

口座モデル（OANDA 証券 株価指数 CFD・JP225）:
    - 必要証拠金 = 想定元本（時価 × 数量 × V）× 証拠金率（既定 10% ＝レバレッジ 10 倍）。
      時価ベースで毎 tick 変動する（約定時建値に固定しない）。
    - 有効証拠金 = 口座残高 + 評価損益。
    - 証拠金維持率 = 有効証拠金 ÷ 必要証拠金。**100% 以下でロスカット**（株価指数 CFD に
      マージンコールは無い）。損失の大きい建玉から成行で決済し、1 建玉ごとに再評価して
      維持率が回復するまで続ける。
    - 評価価格: ロングは bid・ショートは ask（決済に使う側の価格）。

未検証事項（推測で断定しない・レポートにも明記する）:
    - [U1] 必要証拠金の「時価」が bid / ask / mid のどれかは公式資料で未確認。既定は mid
      とし、``mark_price_mode`` で切替可能にして差を数値で観測できるようにする。
    - [U2] ロスカットの執行が「1 建玉ずつ決済→再評価」か「維持率回復まで一括」かは未確認。
      本実装は 1 建玉ずつ（損失最大から）とし、README に仮定と明記する。
    - [U3] 逆指値（損切り）・ロスカットの約定価格はトリガー tick の評価価格（成行）とする。
      実際のスリッページ・板の厚みは反映しない（保証されない旨は HTML と同じ）。
    - [U4] 同一 tick で損切りとロスカットが同時成立した場合は損切りを先に適用する（仮定）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Iterator, List, Optional, Tuple

LONG = "long"
SHORT = "short"

#: 判定順序（U4）: 同一 tick 内の適用順。損切り → 利確 → ロスカット。
#: 損切りと利確が同一 tick で両立するケース（巨大レンジの tick）は損切り優先（保守側）。


@dataclass
class EntryOrder:
    """建玉注文 1 本。price=None は成行（最初の tick で約定）。それ以外は指値。

    指値の約定判定（保守側の単純モデル・U3）:
        ロング指値: ask <= price になった tick で price 約定。
        ショート指値: bid >= price になった tick で price 約定。
    """

    units: float
    price: Optional[float] = None  # None = 成行


@dataclass
class OrderPlan:
    """発注計画（エンジンへの入力）。"""

    direction: str                    # LONG | SHORT
    entries: List[EntryOrder]
    stop_price: Optional[float] = None
    tp_price: Optional[float] = None

    def validate(self) -> None:
        if self.direction not in (LONG, SHORT):
            raise ValueError(f"direction は {LONG}/{SHORT}: {self.direction}")
        if not self.entries:
            raise ValueError("entries が空")
        for e in self.entries:
            if e.units <= 0:
                raise ValueError(f"units は正: {e.units}")


@dataclass
class AccountConfig:
    """口座条件。"""

    balance: float                    # 初期残高 E（円）
    margin_rate: float = 0.10         # 証拠金率（OANDA JP225 = 10%）
    losscut_ratio: float = 1.00       # 維持率がこの値以下でロスカット（OANDA = 100%）
    point_value: float = 1.0          # V（円/pt/単位・OANDA JP225 = 1）
    mark_price_mode: str = "mid"      # 必要証拠金の時価（U1）: "mid" | "trade-side"

    def validate(self) -> None:
        if self.balance <= 0:
            raise ValueError("balance は正")
        if not (0 < self.margin_rate < 1):
            raise ValueError("margin_rate は (0,1)")
        if self.mark_price_mode not in ("mid", "trade-side"):
            raise ValueError(f"mark_price_mode: {self.mark_price_mode}")


@dataclass
class Position:
    """約定済み建玉 1 本。"""

    units: float
    entry_price: float
    entry_ts: int


@dataclass
class Event:
    """約定・決済イベント。kind: entry|stop|tp|losscut|end_of_data"""

    ts: int
    kind: str
    price: float
    units: float
    pnl: float = 0.0                  # 決済イベントのみ（確定損益・円）
    note: str = ""


@dataclass
class StateSeries:
    """tick 粒度の口座状態時系列（並列リスト・メモリ効率のため dict 行にしない）。"""

    ts: List[int] = field(default_factory=list)
    bid: List[float] = field(default_factory=list)
    ask: List[float] = field(default_factory=list)
    balance: List[float] = field(default_factory=list)
    equity: List[float] = field(default_factory=list)
    required_margin: List[float] = field(default_factory=list)
    margin_ratio: List[Optional[float]] = field(default_factory=list)  # ポジション無し = None
    open_units: List[float] = field(default_factory=list)

    def append(self, ts: int, bid: float, ask: float, balance: float, equity: float,
               req: float, ratio: Optional[float], units: float) -> None:
        self.ts.append(ts)
        self.bid.append(bid)
        self.ask.append(ask)
        self.balance.append(balance)
        self.equity.append(equity)
        self.required_margin.append(req)
        self.margin_ratio.append(ratio)
        self.open_units.append(units)


@dataclass
class RunResult:
    """エンジン実行結果。"""

    series: StateSeries
    events: List[Event]
    final_balance: float
    closed: bool                      # 全建玉が決済されて終了したか
    losscut_hit: bool


class AccountEngine:
    """口座状態エンジン（アクター 1: 口座残高などの管理）。

    使い方:
        engine = AccountEngine(plan, config)
        result = engine.run(ticks)    # ticks: Iterable[(ts_ms, bid, ask)]
    """

    def __init__(self, plan: OrderPlan, config: AccountConfig):
        plan.validate()
        config.validate()
        self._plan = plan
        self._cfg = config

    # ---- 価格の役割（方向で決まる・二重定義しない） ----

    def _eval_price(self, bid: float, ask: float) -> float:
        """評価・決済に使う価格（ロング=bid / ショート=ask）。"""
        return bid if self._plan.direction == LONG else ask

    def _entry_exec_price(self, bid: float, ask: float) -> float:
        """成行の約定価格（ロング=ask / ショート=bid）。"""
        return ask if self._plan.direction == LONG else bid

    def _mark_price(self, bid: float, ask: float) -> float:
        """必要証拠金の時価（U1・mark_price_mode で切替）。"""
        if self._cfg.mark_price_mode == "mid":
            return (bid + ask) / 2.0
        return self._eval_price(bid, ask)

    def _pnl(self, pos: Position, price: float) -> float:
        """建玉 1 本の評価損益（円）。"""
        sign = 1.0 if self._plan.direction == LONG else -1.0
        return sign * (price - pos.entry_price) * pos.units * self._cfg.point_value

    # ---- 本体 ----

    def run(self, ticks: Iterable[Tuple[int, float, float]]) -> RunResult:
        plan, cfg = self._plan, self._cfg
        long = plan.direction == LONG
        balance = cfg.balance
        positions: List[Position] = []
        pending: List[EntryOrder] = list(plan.entries)
        events: List[Event] = []
        series = StateSeries()
        losscut_hit = False
        closed = False

        def close_position(pos: Position, ts: int, price: float, kind: str, note: str = "") -> None:
            nonlocal balance
            pnl = self._pnl(pos, price)
            balance += pnl
            events.append(Event(ts=ts, kind=kind, price=price, units=pos.units, pnl=pnl, note=note))

        for ts, bid, ask in ticks:
            if closed:
                break

            # 1) エントリー約定（成行 → 即時 / 指値 → 有利側到達で指値価格約定）
            still_pending: List[EntryOrder] = []
            for order in pending:
                if order.price is None:
                    px = self._entry_exec_price(bid, ask)
                    positions.append(Position(units=order.units, entry_price=px, entry_ts=ts))
                    events.append(Event(ts=ts, kind="entry", price=px, units=order.units, note="market"))
                elif (long and ask <= order.price) or ((not long) and bid >= order.price):
                    positions.append(Position(units=order.units, entry_price=order.price, entry_ts=ts))
                    events.append(Event(ts=ts, kind="entry", price=order.price, units=order.units, note="limit"))
                else:
                    still_pending.append(order)
            pending = still_pending

            ev_px = self._eval_price(bid, ask)

            # 2) 損切り（U4: 最優先）。トリガー tick の評価価格で全建玉成行（U3）。
            if positions and plan.stop_price is not None:
                stop_hit = ev_px <= plan.stop_price if long else ev_px >= plan.stop_price
                if stop_hit:
                    for pos in positions:
                        close_position(pos, ts, ev_px, "stop")
                    positions = []
                    pending = []          # 未約定の指値も取り消す（ブラケット前提）
                    closed = True

            # 3) 利確。
            if positions and plan.tp_price is not None:
                tp_hit = ev_px >= plan.tp_price if long else ev_px <= plan.tp_price
                if tp_hit:
                    for pos in positions:
                        close_position(pos, ts, ev_px, "tp")
                    positions = []
                    pending = []
                    closed = True

            # 4) 評価とロスカット（U2: 損失最大の建玉から 1 本ずつ決済 → 再評価）。
            while positions:
                equity = balance + sum(self._pnl(p, ev_px) for p in positions)
                mark = self._mark_price(bid, ask)
                req = sum(p.units for p in positions) * mark * cfg.point_value * cfg.margin_rate
                ratio = equity / req if req > 0 else None
                if ratio is None or ratio > cfg.losscut_ratio:
                    break
                losscut_hit = True
                worst = min(positions, key=lambda p: self._pnl(p, ev_px))
                positions.remove(worst)
                close_position(worst, ts, ev_px, "losscut",
                               note=f"維持率 {ratio * 100:.1f}% <= {cfg.losscut_ratio * 100:.0f}%")
                if not positions:
                    pending = []
                    closed = True

            # 5) 状態記録（決済で closed になった tick も、その時点の状態を残す）
            equity = balance + sum(self._pnl(p, ev_px) for p in positions)
            mark = self._mark_price(bid, ask)
            req = sum(p.units for p in positions) * mark * cfg.point_value * cfg.margin_rate
            ratio = equity / req if req > 0 else None
            series.append(ts, bid, ask, balance, equity, req, ratio,
                          sum(p.units for p in positions))

        if positions and not closed:
            # tick が尽きた（決済条件未到達）。建玉は開いたまま終了＝評価のみ。
            events.append(Event(ts=series.ts[-1] if series.ts else 0, kind="end_of_data",
                                price=series.bid[-1] if series.bid else 0.0,
                                units=sum(p.units for p in positions),
                                note="tick 終端・建玉が残存（未決済）"))

        return RunResult(series=series, events=events, final_balance=balance,
                         closed=closed, losscut_hit=losscut_hit)


# ---- 静的式（現行 HTML build() の写し・verify.py の比較対象） ----

def html_losscut_price(direction: str, entries: List[Tuple[float, float]],
                       balance: float, margin_rate: float, point_value: float = 1.0) -> Optional[float]:
    """integrated_position_sizing_calculator.html build() のロスカット価格式（逐語移植）。

    long:  X = (avgP − E/U) / (1 − mr)
    short: X = (avgP + E/U) / (1 + mr)
    （HTML では lcDistCore=(E−reqMargin)/U と mFactor=1/(1∓mr) の積として書かれているが、
      代数的に上式と同値。verify.py がこの同値性とエンジン実測の両方を数値で確認する。）

    entries: [(price, units), ...]（全約定前提）。U=Σunits×V。
    """
    total_units = sum(u for _, u in entries)
    if total_units <= 0:
        return None
    avg_p = sum(p * u for p, u in entries) / total_units
    cap_u = total_units * point_value
    if direction == LONG:
        return (avg_p - balance / cap_u) / (1.0 - margin_rate)
    return (avg_p + balance / cap_u) / (1.0 + margin_rate)


def html_required_margin(entries: List[Tuple[float, float]], margin_rate: float,
                         point_value: float = 1.0) -> float:
    """現行 HTML の必要証拠金式（約定代金＝**建値**ベース。逐語移植）。

    注意: エンジンの必要証拠金は**時価**ベースで毎 tick 変動する。HTML のこの式は
    「発注時点（時価＝建値）」のスナップショットに相当する。verify.py が差を数値化する。
    """
    return sum(p * u for p, u in entries) * point_value * margin_rate
