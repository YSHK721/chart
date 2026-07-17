"""PendingLifecycleEngine: ペンディング注文ライフサイクルの純ロジック（ISSUE-094 🔴-1）。

RunBacktestInteractor._execute_every_tick に散在していたペンディング注文（指値/逆指値）の
「トリガ評価 + OCO 取消」判定と、実 MT5 OHLC クォート規約（bid=価格 / ask=価格+spread×point）
を単一モジュールへ抽出する。本エンジンは純ロジック（副作用なし・account/trade 状態を持たない）。

責務分離（ISSUE-094）:
    - 本エンジン: 「どの resting order がこのクォートで約定し、どれが持ち越されるか」を決める
      （trigger 条件は fill_pending_order へ委譲・OCO で約定発生時に非約定分を取消す）。
    - 呼び出し側（Interactor）: 約定 Position を account.open_positions / open_trades へ反映し、
      証拠金を加算する（口座状態の変更は口座アクターが所有）。

抽出前は fill 判定と account 反映が同一ループ内で交錯していた。fill_pending_order は
account 状態を読まない（order + bid/ask のみ）ため、判定（本エンジン）と反映（Interactor）
の分離は評価順・浮動小数演算順を変えず byte-identical である。

usecase 層は domain のみ依存可。本エンジンは domain（Order/Position）と usecase 内
_execution（fill_pending_order）のみに依存する。
"""
from __future__ import annotations

from typing import Any

from simulator.usecase._execution import fill_pending_order, mt5_bid_ask


class PendingLifecycleEngine:
    """ペンディング注文のトリガ評価と OCO を担う純ロジック（状態を持たない）。"""

    @staticmethod
    def tick_quote(
        price: float, *, spread: int, point_size: float
    ) -> "tuple[float, float]":
        """実 MT5 OHLC クォート規約 (bid, ask) を導く。

        bid=price（ティック価格＝OHLC）、ask=price + spread×point_size。ペンディングの
        トリガ評価・保有玉 SL/TP 判定・含み損評価で共通に用いる MT5 校正クォート規約。
        規約の実体は _execution.mt5_bid_ask（単一プリミティブ・ISSUE-100 🟡-1）へ委譲する。
        """
        return mt5_bid_ask(price, spread=spread, point=point_size)

    @staticmethod
    def evaluate_triggers(
        resting: list, *, bid: "float | None", ask: "float | None", oco: bool
    ) -> "tuple[list[tuple[Any, Any]], list]":
        """resting を (bid, ask) で 1 回評価し (filled, carried) を返す。

        戻り値:
            filled  … トリガして約定した [(order, position)]（resting の走査順を保持）。
            carried … 未約定で持ち越す order のリスト。OCO 有効かつ約定が 1 本以上
                      発生した場合は空（＝約定と同一評価点で trigger しなかった残
                      ペンディングを EA が取消す・CancelOpposite）。

        trigger 条件は fill_pending_order（指値/逆指値の 4 種別）へ委譲する。本メソッドは
        account/open_trades を一切変更せず、約定の反映は呼び出し側が filled を走査して行う
        （評価順＝反映順を保つため byte-identical）。
        """
        filled: list[tuple[Any, Any]] = []
        carried: list = []
        for order in resting:
            pos = fill_pending_order(order, bid=bid, ask=ask)
            if pos is None:
                carried.append(order)
            else:
                filled.append((order, pos))
        if oco and filled:
            carried = []
        return filled, carried
