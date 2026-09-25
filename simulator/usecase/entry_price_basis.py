"""判定の瞬間の宣言（ISSUE-533 段階 1・段階 2）。

**約定価格は「戦略が判定した瞬間に取れた価格」でなければならない。** 判定の瞬間を知って
いるのは戦略だけである（どの足のどの値を読むかは戦略の実装が決める）。したがって建値基準の
権威は戦略側にあり、run の設定（``config_overrides.entry_price_basis``）から与えるのは誤りで
あった——設定値が戦略の判定時点と一致する保証がどこにも無い（ISSUE-533 の根本原因）。

本モジュールが持つもの:
    `declared_entry_price_basis`  戦略が名乗る建値基準を読む（宣言が無ければ Fail-Stop）。
    `basis_for_reads`  戦略が読む ``(系列名, shift)`` から判定の瞬間を導く唯一の規則。
    `EntryPriceBasisDeclarationError`  宣言が無い／語彙の外／宣言の無い瞬間で約定しかけた。

段階 2（ISSUE-533）で設定からの供給経路を全廃した。設定と宣言の食い違いを問う
「`verify_entry_price_basis`」 はそこで不要になった——供給が無ければ食い違いは原理的に起きない。

語彙を増やさない: 名前は既存語 ``entry_price_basis`` のまま、値も既存の 2 値
（"close" / "current_open"）のままである。変えたのは**値の出所**だけである。

usecase 層は domain のみ依存可。
"""
from __future__ import annotations

from typing import Any

from simulator.domain.exceptions import ConfigError

#: 戦略が宣言できる建値基準。値は既存語（「`derive_quotes`」 の分岐・
#: `simulator/usecase/sizing_ports.py` の系列表）と同一である。
DECLARABLE_BASES: "tuple[str, ...]" = ("close", "current_open")

#: 「足境界で成行を出さないので判定の瞬間が足境界に無い」ことの宣言。**既定値ではない**
#: ——宣言の不在（属性が無い）とは区別され、この値を名乗った戦略が足境界で約定しようと
#: したときは 「`derive_quotes`」 が Fail-Stop する。
NO_BAR_BOUNDARY_DECISION = None

#: `declared_entry_price_basis` が「属性が無い」ことを検出するための不在標識。
_ABSENT = object()

#: 形成中の足のうち、**足の始まりで既に確定している**系列。
#:
#: ``open`` は足の最初のティックで決まるので、当該足の値を読んでも判定は足の始まりにできる。
#: 「``spread``」 は MT5 の足が持つ気配幅で、`MaSlopePending` が ``open`` と同時に読む値であり、
#: ISSUE-533 の実測が同 EA を「足の始まり」に分類したことと整合する。
#: **未検証**: 「``spread``」 が足内のどの時点の気配幅かは参照実装で確認していない。
#: 表に無い系列（「``close``」 / 「``high``」 / 「``low``」 と、それらから計算する指標）は足が閉じるまで
#: 確定しないものとして扱う（安全側＝遅い側に倒す）。
SERIES_KNOWN_AT_BAR_OPEN: "frozenset[str]" = frozenset({"open", "spread"})


class EntryPriceBasisDeclarationError(ConfigError):
    """戦略の建値基準の宣言が無い、または語彙の外の値である。"""


def basis_for_reads(reads: "Any") -> "str | None":
    """戦略が読む ``(系列名, shift)`` の集合から判定の瞬間を導く**唯一の規則**。

    ``shift`` は当該足からの過去方向の距離（``bar_index - shift``）。

    - 1 つでも「当該足以降（shift < 1）で、足の始まりには確定していない系列」を読むなら、
      判定は足が閉じたあと＝"close"。
    - すべての参照が確定足（shift >= 1）か `SERIES_KNOWN_AT_BAR_OPEN` の系列なら、
      判定は足の始まりにできる＝"current_open"。
    - 参照が 1 つも無いなら足境界で判定していない＝`NO_BAR_BOUNDARY_DECISION`。

    規則を 1 箇所に置くのは、同じ問いを 2 つの利用者——spec 駆動の戦略（自分の条件から
    宣言を導く）と、実装の参照集合を読む検定——が持つためである。2 箇所に書くと片方だけが
    腐る。
    """
    materialized = tuple(reads)
    if not materialized:
        return NO_BAR_BOUNDARY_DECISION
    for series, shift in materialized:
        if shift < 1 and series not in SERIES_KNOWN_AT_BAR_OPEN:
            return "close"
    return "current_open"


def declared_entry_price_basis(strategy: Any) -> "str | None":
    """戦略が名乗る建値基準を返す（宣言が無ければ送出する）。

    事前条件: なし。事後条件: 戻り値は `DECLARABLE_BASES` のいずれか、または
    `NO_BAR_BOUNDARY_DECISION`。

    既定へ倒さないのが本関数の存在理由である。既定を置くと、宣言を書き忘れた戦略が
    誰かの既定で走り、いま直している欠陥——判定の瞬間と約定価格が一致する保証が無い
    ——がそのまま残る。
    """
    declared = getattr(strategy, "entry_price_basis", _ABSENT)
    if declared is _ABSENT:
        raise EntryPriceBasisDeclarationError(
            "戦略が建値基準を宣言していません（判定の瞬間を名乗らない戦略は実行できません）: "
            f"{type(strategy).__name__}",
            context={"strategy": type(strategy).__name__},
        )
    if declared is not NO_BAR_BOUNDARY_DECISION and declared not in DECLARABLE_BASES:
        raise EntryPriceBasisDeclarationError(
            "戦略が宣言した建値基準が語彙の外です: "
            f"{type(strategy).__name__} -> {declared!r}",
            context={
                "strategy": type(strategy).__name__,
                "declared": repr(declared),
                "declarable": list(DECLARABLE_BASES),
            },
        )
    return declared
