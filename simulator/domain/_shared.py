"""domain 層内部の共有プリミティブ（自パッケージ内のみ依存・CLEAN_ARCH §4）。

ここに集約するのは「複数エンティティに重複し、変更が波及する」語彙・式に限る。
単一エンティティでしか使わない集合（kind / direction / exit_reason 等）は
当該エンティティのモジュールに留め、ここへ昇格させない（YAGNI 遵守）。

domain 層は numpy のみ依存可。本モジュールは標準ライブラリのみ。
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from simulator.domain.exceptions import ExecutionError

# 売買方向の正準語彙。Position・Order の不変条件検査で共有する。
SIDES = frozenset({"buy", "sell"})


def sign_of(side: str) -> int:
    """売買方向の符号を返す（buy=+1 / sell=-1）。

    METRICS §5.1 / §5.2 の損益式で共通に用いる符号判定。Position・Deal・
    TradeRecord の 3 箇所に重複していたため集約する。

    不正 side（"BUY"・タイポ等）を黙って sell(-1) 扱いし損益符号を静かに
    反転させないよう、SIDES 非該当を明示的に拒否する（🟡-3）。
    """
    if side not in SIDES:
        raise ExecutionError("side は {buy, sell} のいずれか", context={"side": side})
    return 1 if side == "buy" else -1


def round_profit(value: float, digits: "int | None") -> float:
    """約定損益を口座通貨の桁数へ丸める（digits=None なら丸めず素値を返す）。

    実 MT5 は約定損益を口座通貨の精度（JPY=0 桁）へ丸めて balance に反映する。
    digits=None（既定）は従来どおり丸めず byte-identical を保つ。指定時は
    half-away-from-zero（商習慣丸め・MT5 整合）で丸める。Deal.profit と
    TradeRecord.pnl の双方が本関数を共有し、判定/balance/stats を一致させる。

    丸めは Decimal（ROUND_HALF_UP＝ゼロから遠い側へ同点丸め）で行い、float の
    二進表現に起因する誤丸め（例 0.285→0.28）を避ける。repr(value) を介して float の
    最短表現を Decimal 化するため、digits>0（例 USD=2 桁）でも会計上正しく丸まる。
    """
    if digits is None:
        return value
    quantum = Decimal(1).scaleb(-digits)  # 10**-digits（digits=0 → Decimal("1")）
    return float(Decimal(repr(value)).quantize(quantum, rounding=ROUND_HALF_UP))
