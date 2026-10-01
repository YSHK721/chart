"""待機注文（指値・逆指値）の使い方は戦略が宣言する（ISSUE-557）。

何を解くか（実測 2026-09-29）:
    待機注文を足の途中で評価するかは run の設定 「`pending_lifecycle`」 が決める。設定は呼び出し側が
    渡すもので、MT5 と照合済みの構成（`simulator/tests/confirmation/2026-03_ma-limit/reconcile.py`）は
    `pending_lifecycle=True` を渡していたが、sim の Settings 経路は渡さない。そのため
    MA_Slope_Pending_EA が毎足出す buy_limit / sell_limit（1 か月 943 件）はエンジンで
    「すべて足境界の成行」として約定し、成行の MA_Slope_EA と取引が完全一致した。

どう解くか:
    注文の種類を決めるのは戦略なので、待機注文を使うか（と、置き直すか持続させるか・OCO か）の
    権威も戦略にある（建値基準を戦略が宣言する ISSUE-533 と同じ形）。合流点（「`build_interactor`」）が
    宣言を 1 回読み、run の設定へ写す。呼び出し側が宣言と食い違う値を渡したら `ConfigError`
    （黙ってどちらかを採ると、書いた条件と違う条件で走った結果が成功として出る）。

宣言しない戦略（成行だけの戦略・検定の代役）は従来どおり呼び出し側の設定に従う。
"""
from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Any

from simulator.domain.exceptions import ConfigError
from simulator.usecase.models import BacktestConfig


@dataclass(frozen=True)
class PendingOrderUse:
    """戦略が待機注文をどう使うか。

    ``persistent``: 置いた注文を約定まで持続させる（False＝毎足置き直す）。
    ``oco``: 同時に置いた複数の注文の 1 本が約定したら残りを取り消す。
    """

    persistent: bool
    oco: bool


#: 写す 3 項目の既定値（`BacktestConfig` の宣言が唯一の出所）。呼び出し側が既定から変えた値だけを
#: 「呼び出し側の指定」とみなし、宣言と照合する。
_DECLARED_DEFAULTS: "dict[str, Any]" = {
    f.name: f.default for f in fields(BacktestConfig)
    if f.name in ("pending_lifecycle", "pending_persistent", "pending_oco")
}

#: 宣言を持たない戦略の読み取り結果（待機注文を使うと名乗らない）。
_ABSENT = object()


def declared_pending_order_use(strategy: Any) -> "PendingOrderUse | None":
    """戦略が名乗る待機注文の使い方を返す（名乗らなければ None）。"""
    declared = getattr(strategy, "pending_order_use", _ABSENT)
    if declared is _ABSENT or declared is None:
        return None
    if not isinstance(declared, PendingOrderUse):
        raise ConfigError(
            f"戦略が宣言した待機注文の使い方が型の外です: {declared!r}",
            context={"strategy": type(strategy).__name__, "declared": repr(declared)},
        )
    return declared


def apply_pending_order_use(strategy: Any, determinism: Any) -> Any:
    """宣言を run の設定（`BacktestConfig`）へ写した設定を返す（宣言が無ければそのまま返す）。

    事後条件: 宣言があれば `pending_lifecycle=True`・「`pending_persistent`」・「`pending_oco`」 が宣言どおり。
    例外: 呼び出し側が既定と違う値を渡し、それが宣言と食い違うとき `ConfigError`。
    """
    declared = declared_pending_order_use(strategy)
    if declared is None:
        return determinism
    wanted = {
        "pending_lifecycle": True,
        "pending_persistent": declared.persistent,
        "pending_oco": declared.oco,
    }
    conflicting = sorted(
        name for name, value in wanted.items()
        if getattr(determinism, name) != _DECLARED_DEFAULTS[name]
        and getattr(determinism, name) != value
    )
    if conflicting:
        raise ConfigError(
            "待機注文の使い方の設定が戦略の宣言と食い違っています: " + ", ".join(conflicting),
            context={
                "strategy": type(strategy).__name__,
                "declared": wanted,
                "given": {name: getattr(determinism, name) for name in conflicting},
            },
        )
    return replace(determinism, **wanted)

