"""BuildReportPayload UC（詳細設計 §4・§5）。

BacktestResult(IS/OOS) を read-only 消費し報告ドメインモデル ReportPayloadModel を組み立てる。
ステージ① F-1 スコープ: meta・bars・trades(16キー)・summary・degradation・verdict・balance_curve を
実体化し、orders[]・agg の heat/scatter/graph 詳細は遅延（空/最小キー確保）とする。

依存: domain（BacktestResult/TradeRecord は属性アクセスのみ）＋ report_models ＋ derive。
pandas 非依存・int 時刻のみ（時刻 int 化と bars の int 化は上流 tools が担う）。
"""
from __future__ import annotations

from typing import Any

from simulator.report_ui.usecase import derive
from simulator.report_ui.usecase.report_models import (
    ReportPayloadModel,
    SegmentModel,
    SummaryModel,
    TradeRow,
    VerdictModel,
)

INITIAL = 10000.0

# 全体 meta の固定記述（IS/OOS 単純分割・最適化なしの試験条件・§4.8）。
# 区間に依らず一定のため execute 組立ロジックから分離して定数化する。
_META_PARAMS = "ProbeDir=2(両建て) / offset100 / Lot0.1 / SL200 / TP500"
_META_SPLIT = "2026-04-15"
_META_NOTE = "IS/OOS 単純分割（同一パラメータを両区間で評価・最適化なし）"

# exit_reason → comment 正規化値（詳細設計 §4.2.4）。
_EXIT_REASON_COMMENT = {
    "tp": "tp",
    "sl": "sl",
    "reverse": "reverse",
    "expire": "expire",
    "stop_out": "stop out",
    "end_of_test": "end of test",
}

# degradation 対象指標（詳細設計 §5.3）。
_DEG_KEYS = ["net", "profit_factor", "win_rate", "expectancy", "payoff",
             "return_pct", "max_dd_pct"]


class BuildReportPayload:
    """BacktestResult(IS/OOS)→ReportPayloadModel（派生集計・degradation・verdict）。"""

    def execute(
        self,
        *,
        result_is: Any,
        result_oos: Any,
        bars_is: Any,
        bars_oos: Any,
        spec: Any,
        ea_params: dict,
        meta_is: dict,
        meta_oos: dict,
    ) -> ReportPayloadModel:
        seg_is, sum_is = self._build_segment(result_is, bars_is, spec, ea_params, meta_is)
        seg_oos, sum_oos = self._build_segment(result_oos, bars_oos, spec, ea_params, meta_oos)

        summary = {"is": sum_is, "oos": sum_oos}
        degradation = self._degradation(sum_is, sum_oos)
        verdict = self._verdict(sum_is, sum_oos, degradation)

        meta = {
            "symbol": meta_is.get("symbol", "JP225"),
            "timeframe": meta_is.get("timeframe", "M1"),
            "strategy": meta_is.get("strategy", "StopEntryProbe_EA"),
            "params": _META_PARAMS,
            "initial_deposit": INITIAL,
            "split": _META_SPLIT,
            "note": _META_NOTE,
        }

        return ReportPayloadModel(
            meta=meta,
            segments={"is": seg_is, "oos": seg_oos},
            summary=summary,
            degradation=degradation,
            verdict=verdict,
            contract_notes=self._contract_notes(),
        )

    # --- segment ------------------------------------------------------------

    def _build_segment(self, result, bars, spec, ea_params, meta):
        trades_src = list(result.trades)
        balance_curve_src = list(result.balance_curve)

        # 致命-3 1:1 不変条件: trades と balance_curve は同長（run_backtest の
        # _close_open_trade が trades.append/balance_curve.append をペア実行）。
        # 派生に入る前に境界で検証し、崩れていれば明示エラー（balance/DD 破壊防止）。
        if len(trades_src) != len(balance_curve_src):
            raise ValueError(
                f"trades と balance_curve の長さが不一致（致命-3 1:1 違反）: "
                f"{len(trades_src)} != {len(balance_curve_src)}"
            )

        trade_rows = self._build_trade_rows(trades_src, balance_curve_src, bars, spec, ea_params)
        exit_times = [t.exit_time for t in trade_rows]

        # 致命-3: 1:1 で balance_curve 再構成（len 不一致は ValueError）。
        balance_curve = derive.reconstruct_balance_curve(exit_times, balance_curve_src)

        bars_out = [
            {"time": int(b.time), "open": b.open, "high": b.high,
             "low": b.low, "close": b.close}
            for b in bars
        ]

        seg_meta = {
            "symbol": meta.get("symbol", "JP225"),
            "timeframe": meta.get("timeframe", "M1"),
            "strategy": meta.get("strategy", "StopEntryProbe_EA"),
            "bars": len(bars_out),
            "trades": len(trade_rows),
            "period": meta.get("period", ""),
        }

        agg = self._agg(trade_rows, balance_curve)

        segment = SegmentModel(
            label=meta.get("label", ""),
            meta=seg_meta,
            report={},          # 遅延（ステージ① は空。後段で stats 写像）
            bars=bars_out,
            trades=trade_rows,
            orders=[],          # 遅延（空配列・キー確保）
            agg=agg,
        )
        summary = self._summary(trade_rows, balance_curve)
        return segment, summary

    def _build_trade_rows(self, trades_src, balance_curve_src, bars, spec, ea_params):
        """TradeRecord 列を 16 キーの TradeRow 列へ写像する（sl/tp/excursion 派生・§4.1）。

        id/order は配列 index(1始点)、balance は走行残高(balance_curve_src[i]) を 1:1 で対応付ける。
        """
        bar_times = [b.time for b in bars]
        trade_rows = []
        for i, tr in enumerate(trades_src):
            side = tr.side
            entry_price = tr.entry_price
            sl, tp = derive.derive_sl_tp(
                side, entry_price,
                sl_points=ea_params["sl_points"], tp_points=ea_params["tp_points"],
                stops_level=spec.stops_level, point_size=spec.point_size,
                digits=spec.digits,
            )
            if entry_price is None:
                mfe = mae = 0.0
            else:
                mfe, mae = derive.excursion(
                    bars, bar_times, side, entry_price,
                    tr.entry_time, tr.exit_time, spec.point_size,
                )
            trade_rows.append(TradeRow(
                id=i + 1,
                side=side,
                entry_time=int(tr.entry_time),
                exit_time=int(tr.exit_time),
                entry_price=round(entry_price, spec.digits),
                exit_price=round(tr.exit_price, spec.digits),
                profit=tr.pnl(),
                volume=str(tr.volume),
                sl=sl,
                tp=tp,
                order=i + 1,
                comment=_EXIT_REASON_COMMENT.get(tr.exit_reason, tr.exit_reason),
                balance=balance_curve_src[i],
                hold_sec=int(tr.exit_time) - int(tr.entry_time),
                mfe=mfe,
                mae=mae,
            ))
        return trade_rows

    def _agg(self, trade_rows, balance_curve):
        """agg を組み立てる。F-3 で heat を実体化（derive.heat_cells を呼ぶ組立のみ）。

        時刻分解（ts→wday/hour, UTC）は derive.heat_cells が担う（loop 内で直書きしない・
        アーキ指針 §1）。entry_time×profit を渡し entry wday|hour セルへ集計させる。
        他の集計（entries/pl/scatter/hold）は④で実体化（空/最小キー確保を維持）。
        """
        heat = derive.heat_cells((t.entry_time, t.profit) for t in trade_rows)
        return {
            "entries_hour": {},
            "entries_session": {"Asia": 0, "Europe": 0, "USA": 0},
            "entries_wday": {},
            "entries_month": {},
            "pl_hour": {},
            "pl_wday": {},
            "pl_month": {},
            "balance_curve": balance_curve,
            "scatter_mfe": [],
            "scatter_mae": [],
            "hold_pl": {},
            "hold_cnt": {},
            "weekorder": derive.WEEK,
            "heat": heat,
        }

    # --- summary（§4.8・試作 summarize 準拠） --------------------------------

    def _summary(self, trade_rows, balance_curve) -> SummaryModel:
        n = len(trade_rows)
        profits = [t.profit for t in trade_rows]
        wins = [p for p in profits if p > 0]
        losses = [p for p in profits if p < 0]
        gp = sum(wins)
        gl = sum(losses)
        net = sum(profits)
        avg_win = gp / len(wins) if wins else 0.0
        avg_loss = gl / len(losses) if losses else 0.0
        final_balance = balance_curve[-1]["value"] if balance_curve else INITIAL
        return SummaryModel(
            trades=n,
            net=round(net, 1),
            final_balance=round(final_balance, 1),
            win_rate=round(len(wins) / n * 100, 2) if n else 0.0,
            profit_factor=round(gp / abs(gl), 3) if gl else float("inf"),
            expectancy=round(net / n, 2) if n else 0.0,
            payoff=round(avg_win / abs(avg_loss), 3) if avg_loss else float("inf"),
            return_pct=round((final_balance - INITIAL) / INITIAL * 100, 2)
            if balance_curve else 0.0,
            max_dd_pct=derive.max_drawdown_pct(balance_curve),
        )

    # --- degradation（§5.3） ------------------------------------------------

    def _degradation(self, sum_is: SummaryModel, sum_oos: SummaryModel) -> dict:
        deg = {}
        for k in _DEG_KEYS:
            i = getattr(sum_is, k)
            o = getattr(sum_oos, k)
            ratio = None if i == 0 else round(o / i, 3)
            deg[k] = {"is": i, "oos": o, "ratio": ratio, "delta": round(o - i, 2)}
        return deg

    # --- verdict（§5.3・順序厳守） -------------------------------------------

    def _verdict(self, sum_is: SummaryModel, sum_oos: SummaryModel, deg: dict) -> VerdictModel:
        reasons: list[str] = []
        is_net = sum_is.net
        oos_net = sum_oos.net
        oos_pf = sum_oos.profit_factor

        if is_net > 0 and oos_net <= 0:
            result = "fail"
            reasons.append(
                f"IS黒字(+{is_net:.0f})に対しOOS赤字({oos_net:.0f})＝未知区間で優位性消失"
            )
        elif oos_pf < 1.0:
            result = "fail"
            reasons.append(f"OOS PF={oos_pf:.3f}<1.0＝検証区間で損失超過")
        elif deg["profit_factor"]["ratio"] is not None and deg["profit_factor"]["ratio"] < 0.7:
            result = "warn"
            reasons.append(f"PF劣化 比={deg['profit_factor']['ratio']}（OOS/IS<0.7）")
        else:
            result = "pass"
            reasons.append("OOSでも優位性を維持")

        if deg["win_rate"]["delta"] < -5:
            reasons.append(f"勝率差={deg['win_rate']['delta']}pt 悪化")
        if deg["expectancy"]["ratio"] is not None and deg["expectancy"]["ratio"] < 0:
            reasons.append("期待値が正→負へ反転")

        return VerdictModel(result=result, reasons=reasons)

    def _contract_notes(self) -> list:
        return [
            "trades.order は生注文番号ではなく配列index(1始点)。MT5 Order ticket とは非一致。",
            "trades.comment は exit_reason 由来の正規化値。MT5 生comment文字列とは非一致。",
            "trades.sl/tp は EA固定パラメータ(SL200/TP500pts)から entry_price±距離で導出。",
            "orders[] はステージ① では空配列（後段で trades からの射影を充足）。",
        ]
