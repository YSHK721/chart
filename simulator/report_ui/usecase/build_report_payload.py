"""BuildReportPayload UC（詳細設計 §4・§5）。

BacktestResult(IS/OOS) を read-only 消費し報告ドメインモデル ReportPayloadModel を組み立てる。
ステージ① F-1 スコープ: meta・bars・trades(16キー)・summary・degradation・verdict・balance_curve を
実体化し、orders[]・agg の heat/scatter/graph 詳細は遅延（空/最小キー確保）とする。

ISSUE-094 🟡-5: 本 UC は「表示形状の写像」という単一アクターに収束させる。合否方法論
（degradation/verdict の閾値・判定木）は AssessmentPolicy へ、特定実験の所与（EA 名・試験
条件・分割日/ノート・銘柄/時間足の既定）は ReportMeta へ外出しし、本体は EA 非依存とする。

依存: domain（BacktestResult/TradeRecord は属性アクセスのみ）＋ report_models ＋ derive
＋ assessment_policy ＋ report_meta。pandas 非依存・int 時刻のみ（時刻 int 化と bars の
int 化は上流 tools が担う）。
"""
from __future__ import annotations

from typing import Any

from simulator.report_ui.usecase import derive
from simulator.report_ui.usecase.assessment_policy import AssessmentPolicy
from simulator.report_ui.usecase.report_meta import ReportMeta
from simulator.report_ui.usecase.report_models import (
    ReportPayloadModel,
    SegmentModel,
    SummaryModel,
    TradeRow,
)

INITIAL = 10000.0

# exit_reason → comment 正規化値（詳細設計 §4.2.4）。
_EXIT_REASON_COMMENT = {
    "tp": "tp",
    "sl": "sl",
    "reverse": "reverse",
    "expire": "expire",
    "stop_out": "stop out",
    "end_of_test": "end of test",
    "partial": "partial",  # Phase 7 FR-08 部分決済（full-TP と区別・依頼者裁定 2026-08-13）
}


class BuildReportPayload:
    """BacktestResult(IS/OOS)→ReportPayloadModel（表示形状の写像）。

    合否方法論は ``policy``（AssessmentPolicy）へ委譲し、特定実験の所与は execute の
    ``report_meta``（ReportMeta）引数で受け取る。いずれも未指定なら現行既定で byte 不変。
    """

    def __init__(self, policy: "AssessmentPolicy | None" = None) -> None:
        self._policy = policy or AssessmentPolicy()

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
        contacts_is: "list | None" = None,
        contacts_oos: "list | None" = None,
        report_meta: "ReportMeta | None" = None,
    ) -> ReportPayloadModel:
        report_meta = report_meta or ReportMeta()
        seg_is, sum_is = self._build_segment(
            result_is, bars_is, spec, ea_params, meta_is, report_meta, contacts_is)
        seg_oos, sum_oos = self._build_segment(
            result_oos, bars_oos, spec, ea_params, meta_oos, report_meta, contacts_oos)

        summary = {"is": sum_is, "oos": sum_oos}
        degradation = self._policy.degradation(sum_is, sum_oos)
        verdict = self._policy.verdict(sum_is, sum_oos, degradation)

        return ReportPayloadModel(
            meta=self._payload_meta(meta_is, report_meta),
            segments={"is": seg_is, "oos": seg_oos},
            summary=summary,
            degradation=degradation,
            verdict=verdict,
            contract_notes=self._contract_notes(ea_params),
        )

    def execute_single(
        self,
        *,
        result: Any,
        bars: Any,
        spec: Any,
        ea_params: dict,
        meta: dict,
        contacts: "list | None" = None,
        report_meta: "ReportMeta | None" = None,
        segment_key: str = "single",
        contract_notes_extra: "list | None" = None,
    ) -> ReportPayloadModel:
        """**1 区間だけ**の ReportPayloadModel を組む（IS/OOS 比較を伴わない run 用）。

        `execute` との違いは「区間が 1 つであること」だけで、区間の写像（`_build_segment`）と
        全体 meta の写像（`_payload_meta`）は**同一の実体**を使う。写像をもう 1 本書けば、
        片方だけ直る／片方だけ腐るという形で必ず食い違う。

        比較を行わないので:
          - ``degradation`` は空 dict（None にしない。JSON の null は「算出したが空」と
            区別がつかず、未実施を「実施して差が無かった」と誤読させる）
          - ``verdict`` は `AssessmentPolicy.not_evaluated`（result="" ＝ pass/warn/fail を
            名乗らない）。実施していない判定を出力しないための構造的な歯止めである。

        ``segment_key``: segments / summary のキー。既定 "single"。**"is" を既定にしない**
        （区分の捏造になる）。
        ``contract_notes_extra``: 呼び出し側が足す注記（単一区間である旨など）。既存の
        契約注記へ**連結**する（置換しない）。
        """
        report_meta = report_meta or ReportMeta()
        segment, summary = self._build_segment(
            result, bars, spec, ea_params, meta, report_meta, contacts)

        notes = self._contract_notes(ea_params) + list(contract_notes_extra or [])

        return ReportPayloadModel(
            meta=self._payload_meta(meta, report_meta),
            segments={segment_key: segment},
            summary={segment_key: summary},
            degradation={},
            verdict=self._policy.not_evaluated(
                "単一区間のため IS/OOS 比較は未実施"
            ),
            contract_notes=notes,
        )

    # --- meta ---------------------------------------------------------------

    def _payload_meta(self, meta_seg: dict, report_meta: ReportMeta) -> dict:
        """全体 meta を組む（区間数に依存しない写像・キー順は JSON のキー順を規定する）。

        `execute`（IS/OOS）と `execute_single`（単一区間）の**共通**の写像。ここを 2 か所に
        書けば、片方だけ直る／片方だけ腐るという形で必ず食い違う。
        ``meta_seg`` は代表区間の meta dict（IS/OOS では IS を代表とする＝従来と不変）。
        """
        return {
            "symbol": meta_seg.get("symbol", report_meta.symbol),
            "timeframe": meta_seg.get("timeframe", report_meta.timeframe),
            "strategy": meta_seg.get("strategy", report_meta.expert),
            "params": report_meta.params,
            "initial_deposit": INITIAL,
            "split": report_meta.split,
            "note": report_meta.note,
        }

    # --- segment ------------------------------------------------------------

    def _build_segment(self, result, bars, spec, ea_params, meta, report_meta, contacts=None):
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
            "symbol": meta.get("symbol", report_meta.symbol),
            "timeframe": meta.get("timeframe", report_meta.timeframe),
            "strategy": meta.get("strategy", report_meta.expert),
            "bars": len(bars_out),
            "trades": len(trade_rows),
            "period": meta.get("period", ""),
        }

        agg = self._agg(trade_rows, balance_curve, contacts)

        segment = SegmentModel(
            label=meta.get("label", ""),
            meta=seg_meta,
            # §4.5 BacktestStats→report 写像
            report=self._report(result.stats, seg_meta, report_meta),
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

    def _agg(self, trade_rows, balance_curve, contacts=None):
        """agg を組み立てる。F-3 で heat を実体化（derive.heat_cells を呼ぶ組立のみ）。

        時刻分解（ts→wday/hour, UTC）は derive.heat_cells が担う（loop 内で直書きしない・
        アーキ指針 §1）。entry_time×profit を渡し entry wday|hour セルへ集計させる。
        他の集計（entries/pl/scatter/hold）も④で derive 純関数を呼ぶ組立として実体化する
        （loop 直書き禁止・アーキ指針 §1）。entries 系=entry_time 基準、pl 系=exit_time 基準。

        contacts（接点マーカー列 [{time, price, dir}]）は上流 tools（Composition Root）が
        scan_contacts usecase 経由で算出して渡す偶有的追加データ。後方互換のため None のときは
        キーを一切追加しない（既存 agg キー集合を不変に保つ・追加のみ）。空リストは「算出済み
        で接点0件」を意味するため載せる（None との区別）。
        """
        heat = derive.heat_cells((t.entry_time, t.profit) for t in trade_rows)
        entries = derive.entries_buckets(t.entry_time for t in trade_rows)
        pl = derive.pl_buckets((t.exit_time, t.profit) for t in trade_rows)
        hold = derive.hold_buckets((t.hold_sec, t.profit) for t in trade_rows)
        agg = {
            "entries_hour": entries["hour"],
            "entries_session": entries["session"],
            "entries_wday": entries["wday"],
            "entries_month": entries["month"],
            "pl_hour": pl["hour"],
            "pl_wday": pl["wday"],
            "pl_month": pl["month"],
            "balance_curve": balance_curve,
            "scatter_mfe": derive.scatter_points(
                (t.mfe, t.profit, t.id) for t in trade_rows),
            "scatter_mae": derive.scatter_points(
                (t.mae, t.profit, t.id) for t in trade_rows),
            "hold_pl": hold["pl"],
            "hold_cnt": hold["cnt"],
            "weekorder": derive.WEEK,
            "heat": heat,
        }
        if contacts is not None:
            agg["contacts"] = contacts
        return agg

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

    # --- report（§4.5 BacktestStats→report ラベル dict 写像） -----------------

    def _report(self, stats: Any, seg_meta: dict, report_meta: ReportMeta) -> dict:
        """BacktestStats を §4.5 写像で report ラベル dict（全値 str）へ写す。

        stats 直引き＋文字列整形の組立のみ（derive 化しない）。BacktestStats が保持する
        指標のみ set し、非保持（GHPR/Correlation/LR/Margin/保有時間統計/Ticks 等）は
        出力しない（§4.5 確定方針＝欠落キーは出さない）。inf は `f"{inf:.2f}"` が "inf" を
        返すため文字列 "inf" として出力される（report 値は文字列・presenter 素通し）。
        """
        def pct_n(num, den):
            p = (num / den * 100) if den else 0.0
            return f"{p:.2f}% ({num})"

        return {
            "Expert": report_meta.expert,
            "Symbol": seg_meta.get("symbol", report_meta.symbol),
            "Period": seg_meta.get("period", ""),
            "Initial Deposit": f"{stats.initial_deposit:.0f}",
            "Total Net Profit": f"{stats.profit:.0f}",
            "Gross Profit": f"{stats.gross_profit:.0f}",
            "Gross Loss": f"{stats.gross_loss:.0f}",
            "Profit Factor": f"{stats.profit_factor:.2f}",
            "Recovery Factor": f"{stats.recovery_factor:.2f}",
            "Sharpe Ratio": f"{stats.sharpe_ratio:.2f}",
            "Expected Payoff": f"{stats.expected_payoff:.2f}",
            "AHPR": f"{stats.ahpr:.4f}",
            "Total Trades": f"{stats.trades}",
            "Profit Trades (% of total)": pct_n(stats.profit_trades, stats.trades),
            "Loss Trades (% of total)": pct_n(stats.loss_trades, stats.trades),
            "Short Trades (won %)":
                f"{(stats.profit_short_trades / stats.short_trades * 100) if stats.short_trades else 0.0:.2f}% ({stats.short_trades})",
            "Long Trades (won %)":
                f"{(stats.profit_long_trades / stats.long_trades * 100) if stats.long_trades else 0.0:.2f}% ({stats.long_trades})",
            "Largest profit trade": f"{stats.max_profit_trade:.0f}",
            "Average profit trade": f"{stats.average_profit_trade:.2f}",
            "Largest loss trade": f"{stats.max_loss_trade:.0f}",
            "Average loss trade": f"{stats.average_loss_trade:.2f}",
            "Maximum consecutive wins ($)":
                f"{stats.max_con_wins} ({stats.max_con_profit_trades:.0f})",
            "Maximum consecutive losses ($)":
                f"{stats.max_con_losses} ({stats.max_con_loss_trades:.0f})",
            "Maximal consecutive profit (count)":
                f"{stats.con_profit_max:.0f} ({stats.con_profit_max_trades})",
            "Maximal consecutive loss (count)":
                f"{stats.con_loss_max:.0f} ({stats.con_loss_max_trades})",
            "Average consecutive wins": f"{stats.profit_trades_avg_con:.0f}",
            "Average consecutive losses": f"{stats.loss_trades_avg_con:.0f}",
            "Balance Drawdown Absolute": f"{stats.balance_dd_abs:.0f}",
            "Balance Drawdown Maximal":
                f"{stats.balance_dd:.0f} ({stats.balance_dd_percent:.2f}%)",
            "Balance Drawdown Relative":
                f"{stats.balance_ddrel_percent:.2f}% ({stats.balance_dd_relative:.0f})",
            "Equity Drawdown Absolute": f"{stats.equity_dd_abs:.0f}",
            "Equity Drawdown Maximal":
                f"{stats.equity_dd_max:.0f} ({stats.equity_dd_max_percent:.2f}%)",
            "Z-Score": f"{stats.z_score:.2f}",
        }

    # --- degradation / verdict は AssessmentPolicy へ委譲（ISSUE-094 🟡-5）------
    # 劣化率算出・合否判定木・閾値は self._policy（AssessmentPolicy）が担う。

    def _contract_notes(self, ea_params: dict) -> list:
        # SL/TP は実行時 EA config（ea_params）から動的に埋め込む（ISSUE-100 🔵-2）。
        #   従来は "SL200/TP500pts" を直書きし、別 EA（異なる SL/TP）で本ビルダを再利用すると
        #   契約ノートだけが陳腐化していた（build_report_payload を「EA 非依存の純写像」とする
        #   ISSUE-094 🟡-5 の主張と不整合）。ea_params は :159 の trades.sl/tp 導出と同一源。
        sl = ea_params["sl_points"]
        tp = ea_params["tp_points"]
        return [
            "trades.order は生注文番号ではなく配列index(1始点)。MT5 Order ticket とは非一致。",
            "trades.comment は exit_reason 由来の正規化値。MT5 生comment文字列とは非一致。",
            f"trades.sl/tp は EA固定パラメータ(SL{sl}/TP{tp}pts)から entry_price±距離で導出。",
            "orders[] はステージ① では空配列（後段で trades からの射影を充足）。",
        ]
