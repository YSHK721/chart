"""report.json 生成 CLI / Composition Root（詳細設計 §3.5・§7）。

committed IF（build_interactor → controller._interactor.execute）で IS/OOS を実 run し、
BuildReportPayload UC → ReportUiPresenter を結線して report.json を書き出す。
main は無改変。pandas / 時刻変換（_unix）は本 tools 層に閉じ、UC へは int 時刻のみ渡す。

EA param・config_overrides は reconcile_is.py / reconcile.py の所与パラメータと完全一致
（伝播漏れ防止・R-5 対策）。EA param（SL/TP/stops_level/point_size/digits）は本 CLI が
唯一の真実源として derive へ同一値を注入する。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

# ISSUE-091 #3: 主スライスの公開 API のみ参照する（private 名 _ema_series の越境 import を解消）。
from simulator.main import build_interactor, ema_series
from simulator.report_ui.adapter.report_presenter import ReportUiPresenter
from simulator.report_ui.tools.contacts_export import compute_segment_contacts
from simulator.report_ui.usecase.build_report_payload import BuildReportPayload
from simulator.report_ui.usecase.report_meta import ReportMeta

ROOT = Path("/workspaces/app")
CONF = ROOT / "simulator/tests/confirmation/2026-04_stop-probe_oos"
OUT = ROOT / "simulator/report_ui/web/data/report.json"

# EA param（derive_sl_tp / excursion へ注入する唯一の真実源・§7）。
EA_PARAMS = {"sl_points": 200, "tp_points": 500}

# build_interactor 共通引数（reconcile_is.py / reconcile.py の所与と完全一致・§7）。
COMMON = dict(
    symbol="JP225", period="M1", ea_name="StopEntryProbe_EA",
    initial_deposit=10000.0, contract_size=10.0, volume_min=0.01, volume_max=100.0,
    volume_step=0.01, stops_level=0, digits=1, point_size=0.1, leverage=10.0,
    ma_period=60, ma_method="ema", lot_size=0.1, stop_loss_points=200,
    take_profit_points=500, entry_offset_points=100.0, entry_type="stop",
    config_overrides={
        "tick_model": "ohlc_expand",
        "entry_price_basis": "current_open",
        "floating_pnl_basis": "bid_ask",
        "stop_out_action": "close_and_halt",
        "session_calendar": "jp225",
        "profit_round_digits": 0,
        "stop_out_at_open": True,
        "pending_lifecycle": True,
        "pending_oco": True,
        "pending_persistent": True,
        "hedged_margin": True,
    },
    stop_out_level=100.0,
)

# (key, label, bars_csv, trading_start, bars_start_filter[YYYY-MM-DD or None])
SEGMENTS = [
    ("is", "IS（学習 04.01-14）", CONF / "bars_m1_is.csv", "2026-04-01", None),
    ("oos", "OOS（検証 04.15-23）", CONF / "bars_m1.csv", "2026-04-15", "2026-04-14"),
]


def _unix(t: Any) -> int:
    """numpy.datetime64 / epoch int / Timestamp を UNIX 秒 int へ正規化する（§4.3）。

    pandas は本 composition root に閉じ、UC へは int 時刻のみ渡す。
    """
    if isinstance(t, int) and not isinstance(t, bool):
        return t
    return int(pd.Timestamp(t).timestamp())


class _IntTimeBar:
    """UC の excursion 用に bar.time を int 化した read-only ビュー（high/low/open/close）。"""

    __slots__ = ("time", "high", "low", "open", "close")

    def __init__(self, bar: Any) -> None:
        self.time = _unix(bar.time)
        self.high = bar.high
        self.low = bar.low
        self.open = bar.open
        self.close = bar.close


class _IntTimeTrade:
    """UC 用に entry_time/exit_time を int 化した read-only TradeRecord ビュー。"""

    __slots__ = ("side", "entry_time", "exit_time", "entry_price", "exit_price",
                 "volume", "exit_reason", "_pnl")

    def __init__(self, tr: Any) -> None:
        self.side = tr.side
        self.entry_time = _unix(tr.entry_time)
        self.exit_time = _unix(tr.exit_time)
        self.entry_price = tr.entry_price
        self.exit_price = tr.exit_price
        self.volume = tr.volume
        self.exit_reason = tr.exit_reason
        self._pnl = tr.pnl()

    def pnl(self) -> float:
        return self._pnl


class _ResultView:
    """UC が読む BacktestResult の int 時刻ビュー（trades/balance_curve/stats を read-only 写像）。"""

    def __init__(self, result: Any) -> None:
        self.trades = [_IntTimeTrade(t) for t in result.trades]
        self.balance_curve = list(result.balance_curve)
        self.stats = result.stats
        self.deals = result.deals
        self.equity_curve = result.equity_curve


def _filter_bars(bars: Any, bars_start: "str | None") -> list:
    """bars_start（YYYY-MM-DD）以降の int 時刻バーへ絞る（OOS の表示範囲制限・§4.6）。"""
    int_bars = [_IntTimeBar(b) for b in bars]
    if bars_start is None:
        return int_bars
    start_ts = int(pd.Timestamp(bars_start, tz="UTC").timestamp())
    return [b for b in int_bars if b.time >= start_ts]


def _run_segment(bars_csv: Path, trading_start: str) -> "tuple[Any, Any]":
    """committed IF で 1 区間を実 run し (BacktestResult, request.bars) を返す。"""
    controller, request = build_interactor(
        data_path=str(bars_csv),
        trading_start=pd.Timestamp(trading_start),
        **COMMON,
    )
    result = controller._interactor.execute(request)
    return result, request.bars


def _segment_contacts(bars: "list") -> "list[dict]":
    """1 セグメントの表示足範囲で接点（agg.contacts）を算出する（scan_contacts usecase 経由）。

    ma_values は EA と同じ EMA(ma_period, close)（ema_series）を当該セグメント足へ適用して構築する
    （bar_index→EMA 値）。既定は該当セグメント足範囲のみ（性能考慮・詳細設計 A）。

    注意（表示専用オーバレイ）: EMA は**表示トリム後のセグメント足で再シード**する。OOS のように
    bars_start でトリムした窓では先頭 ~ma_period 本は EMA warmup 中で EA の EMA と厳密一致しない
    （表示域先頭のみ）。取引開始（trading_start）までに period≪バー数で収束するため取引域では
    実質一致する。本接点は分析用の表示オーバレイであり、EA のシグナル判定そのものではない。

    モード方針: 本レポートは config_overrides["tick_model"]="ohlc_expand"（合成ティック・実ティック
    非使用）で生成されるため、実ティック源を持たない。よって preview（full_scan=False・確定足 close
    クロスのみ・tick 非読込）へ安全フォールバックする。実ティック源が供給できる将来経路では
    ticks_fn を注入し full_scan=True へ切り替える。
    """
    if not bars:
        return []
    closes = pd.Series([float(b.close) for b in bars])
    ema = ema_series(closes, COMMON["ma_period"])
    ma_values = {i: float(v) for i, v in enumerate(ema.to_numpy())}
    return compute_segment_contacts(
        bars=bars,
        ma_values=ma_values,
        ref=COMMON["symbol"],
        timeframe=COMMON["period"],
        indicator="ema",
        variant="",
        params={"period": COMMON["ma_period"], "method": COMMON["ma_method"]},
        full_scan=False,
    )


def build_payload() -> Any:
    """IS/OOS を実 run し ReportPayloadModel を構築する（report.json は書かない）。"""
    runs = {}
    for key, label, bars_csv, trading_start, bars_start in SEGMENTS:
        result, bars = _run_segment(bars_csv, trading_start)
        seg_bars = _filter_bars(bars, bars_start)
        runs[key] = {
            "result": _ResultView(result),
            "bars": seg_bars,
            "label": label,
            "contacts": _segment_contacts(seg_bars),
        }

    meta_is = _meta("is", runs["is"]["label"])
    meta_oos = _meta("oos", runs["oos"]["label"])

    return BuildReportPayload().execute(
        result_is=runs["is"]["result"],
        result_oos=runs["oos"]["result"],
        bars_is=runs["is"]["bars"],
        bars_oos=runs["oos"]["bars"],
        spec=_Spec(),
        ea_params=EA_PARAMS,
        meta_is=meta_is,
        meta_oos=meta_oos,
        contacts_is=runs["is"]["contacts"],
        contacts_oos=runs["oos"]["contacts"],
        # 特定実験の所与（EA 名・試験条件・分割日/ノート・銘柄/時間足の既定）を
        # Composition Root から明示注入する（ISSUE-094 🟡-5・現行 StopEntryProbe 値）。
        report_meta=ReportMeta(),
    )


class _Spec:
    """UC へ渡す SymbolSpec 相当（point_size/digits/stops_level・§7 と一致）。"""
    point_size = 0.1
    digits = 1
    stops_level = 0


def _meta(seg: str, label: str) -> dict:
    return {
        "symbol": "JP225",
        "timeframe": "M1",
        "strategy": "StopEntryProbe_EA",
        "period": "2026.04.01-04.14" if seg == "is" else "2026.04.15-04.23",
        "label": label,
    }


def write_report(payload: Any, out: Path = OUT) -> None:
    """ReportPayloadModel を report.json へ書き出す（Presenter 経由）。"""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    ReportUiPresenter().present_report_payload(payload, out)


def main() -> None:
    payload = build_payload()
    write_report(payload, OUT)
    for key in ("is", "oos"):
        s = payload.summary[key]
        print(f"{key}: trades={s.trades} net={s.net} final_balance={s.final_balance}")
    size_mb = OUT.stat().st_size / 1e6
    print(f"WROTE {OUT} ({size_mb:.1f} MB) verdict={payload.verdict.result}")


if __name__ == "__main__":
    main()
