"""建玉変更の適用（`PositionDirectiveApplier`）。UC-001 の協働クラス（ISSUE-502 段階 4A）。

責務は 1 つだけである: **評価点 1 つで、注入された建玉変更器（トレーリング FR-07・部分決済
FR-08）に保有玉を見せ、返ってきた指示を忠実な順序で反映する**。

なぜ Interactor から出したか:
    「どの参照価格を見せるか」「部分決済とSL/TP更新のどちらを先に反映するか」は建玉変更の
    仕様（実 MT5 の EA 動作の再現）に属する軸であり、run の進み方とは別の理由で動く。

適用しないときは呼ばれない: 建玉変更器が注入されていない run では呼出側がこの段を素通り
する（既定経路 byte 不変）。
"""
from __future__ import annotations

from typing import Any

from simulator.domain.account import Account
from simulator.usecase.open_trade import OpenTrade
from simulator.usecase.trade_ledger import TradeLedger

# 部分決済（FR-08）の確定トレードに付す exit_reason。部分決済は実現 Deal であり、full-TP-hit
# の "tp" と区別する必要がある（統計・マーカーで混同すると実トレードと乖離する）。依頼者裁定
# （2026-08-13）により domain/trade_record.py の許可集合へ "partial" を追加し、正しく分類する
# （抜本的解決）。既定 pm=None の golden 経路では本理由は一切生成されない（部分決済は opt-in）
# ため byte 等価は不変。
_PARTIAL_CLOSE_EXIT_REASON = "partial"


class PositionDirectiveApplier:
    """1 run ぶんの建玉変更適用（変更器・口座・帳簿を保持する）。"""

    def __init__(
        self,
        *,
        position_manager: Any,
        account: Account,
        ledger: TradeLedger,
    ) -> None:
        self._position_manager = position_manager
        self._account = account
        self._ledger = ledger

    def apply_all(
        self,
        open_trades: list,
        *,
        bar: Any,
        granularity: str,
        ref_buy: float,
        ref_sell: float,
    ) -> None:
        """保有玉すべてに建玉変更を適用する（両実行経路で同型だった B2/B4 の単一化）。

        呼ばれる位置は hit 判定（H）の後・口座再評価（I）の前である（サーバ hit →
        EA 動作 → 口座再評価という実 MT5 の順序）。参照価格は玉のサイドだけで決まる
        ため、**評価点ごとに 2 つ（買い用・売り用）を先に解決して玉に配る**。玉ごとに
        引き直すと、同じ答えを玉の数だけ求める形（N+1）になり、玉が増えるほど捨てる
        計算が増える。出力は 1 ビットも変わらないので状態検証では落ちない類の浪費である。

        参照価格の意味は粒度で異なる（呼出側が決めて渡す）:
            バー粒度: トレーリング方向の到達価格（買い=high / 売り=low）。SL/TP の
                到達判定が high/low で touch を見るのと対称にする。
            ティック粒度: 保有玉の決済価格（買い=Bid / 売り=Ask）＝含み損評価と同一基準。

        `list(open_trades)` を走査するのは、適用中に部分決済が保有列を書き換えうるため
        （走査中の列を直接回すと取りこぼす）。
        """
        pm = self._position_manager
        for ot in list(open_trades):
            ref = ref_buy if ot.position.side == "buy" else ref_sell
            directive = pm.evaluate(
                ot=ot, ref_price=ref, granularity=granularity, account=self._account
            )
            self._apply_one(
                directive,
                ot,
                exit_time=bar.time,
                exit_price=ref,
            )

    def _apply_one(
        self,
        directive: Any,
        ot: OpenTrade,
        *,
        exit_time: Any,
        exit_price: float,
    ) -> None:
        """PositionDirective を保有玉へ適用する（Phase 7・忠実適用順序）。

        hit 判定（H）の後に呼ばれ、部分決済（実現 Deal・証拠金を按分解放）→ SL/TP 更新
        （ot.sl/ot.tp 書換・次評価点から効く）の順で反映する。SL/TP 更新のみのトレーリングは
        TradeRecord を生成しない（含み玉の SL を締めるだけ）。directive が None/無変更なら
        何もしない（既定経路 byte 不変）。部分決済は帳簿（`TradeLedger.close`）を
        close_volume 付きで呼び、全量経路と同一の約定数学（_execution・Deal.from_close）を
        共有する。

        部分決済のフィル価格は directive.close_price を用いる（bar 粒度＝トリガー水準／tick
        粒度＝現在価格）。到達検出（極値 touch）とフィル価格を分離する（依頼者裁定 2026-08-13：
        bar は部分 TP としてトリガー水準で約定・極値でフィルしない）。close_price 未指定
        （None）の場合のみ呼出側の exit_price へフォールバックする（防御的）。
        """
        if directive is None or directive.is_noop():
            return
        if directive.close_volume is not None:
            fill_price = (
                directive.close_price if directive.close_price is not None else exit_price
            )
            self._ledger.close(
                ot,
                exit_time=exit_time,
                exit_price=fill_price,
                exit_reason=_PARTIAL_CLOSE_EXIT_REASON,
                close_volume=directive.close_volume,
            )
        if directive.new_sl is not None:
            ot.sl = directive.new_sl
        if directive.new_tp is not None:
            ot.tp = directive.new_tp
