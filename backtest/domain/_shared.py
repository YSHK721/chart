"""domain 層内部の共有プリミティブ（自パッケージ内のみ依存・CLEAN_ARCH §4）。

ここに集約するのは「複数エンティティに重複し、変更が波及する」語彙・式に限る。
単一エンティティでしか使わない集合（kind / direction / exit_reason 等）は
当該エンティティのモジュールに留め、ここへ昇格させない（YAGNI 遵守）。

domain 層は numpy のみ依存可。本モジュールは標準ライブラリのみ。
"""
from __future__ import annotations

from backtest.domain.exceptions import ExecutionError

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
