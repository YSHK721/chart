"""決済記帳（`TradeLedger`）。UC-001 の協働クラス（ISSUE-502 段階 4A）。

責務は 1 つだけである: **保有玉 1 件の確定決済を帳簿へ記す**。確定トレード列・証拠金・
決済 Deal・balance 系列の 4 つは 1 回の決済で必ず同時に動くため、動かす主体を 1 つに
する。分ければ「trades にだけ載って balance_curve に載らない」形の食い違いが起こり得る
（`report_ui/usecase/build_report_payload.py` はこの 2 本が対で伸びることに依存している）。

なぜ Interactor から出したか:
    帳簿が変わる動機（丸め桁の扱い・部分決済の按分・残玉の再構築）と、run の進み方が
    変わる動機（評価点の取り方・ウォームアップ・ペンディングの持続）は別々に来る。
    同じクラスに置くと、どちらの改訂も 940 行の同じファイルを開くことになる。

不変条件（呼出側が満たすこと）:
    `account.open_positions` と呼出側の保有列（`OpenTrade` の列）は lockstep である。
    本クラスは前者だけを触り、後者の除去は呼出側（SL/TP 監視・約定執行・証拠金強制決済）
    が行う——走査順＝反映順が byte 依存であるため、除去の位置は呼出側の関心である。
"""
from __future__ import annotations

from typing import Any

from simulator.domain.account import Account
from simulator.domain.deal import Deal
from simulator.domain.position import Position
from simulator.domain.trade_record import TradeRecord
from simulator.usecase.open_trade import OpenTrade


def close_deal(trade: TradeRecord) -> Deal:
    """確定 TradeRecord から決済 Deal を生成する（balance 反映用）。"""
    return Deal.from_close(
        side=trade.side,
        entry_price=trade.entry_price,
        close_price=trade.exit_price,
        volume=trade.volume,
        contract_size=trade.contract_size,
        swap=trade.swap,
        commission=trade.commission,
        # trade と同一の通貨丸めを deal.profit にも適用（balance と pnl を一致させる）。
        profit_round_digits=trade.profit_round_digits,
    )


class TradeLedger:
    """1 run ぶんの帳簿（確定トレード・決済 Deal・balance 系列・証拠金の解放）。

    構築は run の準備段（Interactor の run 開始処理）で 1 度だけ行う。保持する
    4 つ（口座・銘柄契約・レバレッジ・丸め桁）は run のあいだ変わらないため、各決済地点が
    同じ 4 つを書き写す必要をなくす（ISSUE-308 の `close_trade` 呼び口を型にしたもの）。
    """

    def __init__(
        self,
        *,
        account: Account,
        contract_size: float,
        leverage: float,
        trades: list,
        deals: list,
        balance_curve: list,
        profit_round_digits: "int | None",
    ) -> None:
        self._account = account
        self._contract_size = contract_size
        self._leverage = leverage
        self._trades = trades
        self._deals = deals
        self._balance_curve = balance_curve
        # 約定損益の口座通貨丸め桁（run の準備段が config から確定する。既定 None＝丸めず）。
        self._profit_round_digits = profit_round_digits

    def close(
        self,
        ot: OpenTrade,
        *,
        exit_time: Any,
        exit_price: float,
        exit_reason: str,
        close_volume: "float | None" = None,
    ) -> None:
        """保有玉 1 件を確定決済する（reverse / SL / TP / 部分決済 / stop_out で共通）。

        手順は確定トレード列・margin・deal・balance_curve への反映を 1 箇所に集約する
        （reverse 決済と SL/TP 決済で重複していた処理の単一化）。TradeRecord は本メソッド
        が唯一の生成点であり、約定損益の通貨丸め桁（`profit_round_digits`・既定 None）
        を確定トレードへ付与する。振る舞いは丸め桁未設定時は不変（後方互換）。

        close_volume（Phase 7 FR-08・部分決済の単一ソース化）:
            None（既定）= 全量決済。``v = ot.position.volume`` で従来と byte-identical。
            指定時 = 部分決済。``v = close_volume`` を決済し、margin/swap/commission を
            v/total 比で按分解放する（swap=commission=0 のため 0×比=0＝全量経路 byte 不変）。
            残玉（v < total）は frozen Position を ``Position(side, total−v, entry)`` で縮小
            再構築し、account.open_positions を**同 index 置換**（走査順=反映順の byte 依存を
            保持）、ot.position も残玉へ差し替える（ot.sl/ot.tp は不変で建玉時 SL/TP を維持）。
            v == total は現状どおり remove（byte 等価）。約定数学は _execution・Deal.from_close
            を共有し、写経しない。
        """
        total = ot.position.volume
        v = close_volume if close_volume is not None else total
        trade = TradeRecord(
            side=ot.position.side,
            volume=v,
            entry_time=ot.entry_time,
            exit_time=exit_time,
            entry_price=ot.entry_price,
            exit_price=exit_price,
            contract_size=self._contract_size,
            swap=0.0,
            commission=0.0,
            exit_reason=exit_reason,
            profit_round_digits=self._profit_round_digits,
        )
        self._trades.append(trade)
        # margin を v/total 比で按分解放（v==total で比=1.0＝全量経路と byte 一致）。
        self._account.margin -= ot.position.required_margin(
            self._leverage, self._contract_size
        ) * (v / total)
        if close_volume is not None and v < total:
            # 部分決済: 残玉を縮小再構築し open_positions を同 index 置換（走査順を保持）。
            residual = Position(
                side=ot.position.side, volume=total - v, entry_price=ot.position.entry_price
            )
            idx = self._account.open_positions.index(ot.position)
            self._account.open_positions[idx] = residual
            ot.position = residual  # ot も残玉へ（同一 ot が生存継続・sl/tp は不変）
        else:
            self._account.open_positions.remove(ot.position)
        deal = close_deal(trade)
        self._deals.append(deal)
        self._account.apply_deal(deal)
        self._balance_curve.append(self._account.balance)
