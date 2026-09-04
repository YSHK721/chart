"""LatestMeta 型（Latest 計算メタの契約・ISSUE-278 #7）。

なぜ独立モジュールなのか:
    宣言側（:mod:`adapter.compute.call_binding` の ``_TABLE``）と解決側
    （:mod:`adapter.compute.latest_meta`）の**双方**が同じ型を import できるようにするため。
    以前は循環 import を避ける目的で、宣言が「3 要素または 4 要素の位置タプル」を返していた。
    その結果:
      - 型注釈は 3-tuple のまま（``call_binding._BindingSpec.latest_meta``）で、
        4 要素目（増分器名）を書き忘れても型検査を通る。
      - 書き忘れると ``incremental=None`` になり、**例外なく full 再計算へ縮退**する。
        値は正しいままなので検定は緑、性能だけが静かに落ちる。
    型を中立モジュールへ置けば循環は消え、宣言はフィールド名つきで書ける（欠落は構築時に落ちる）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LatestMeta:
    """1 指標(+variant+params) の Latest 計算メタ。

    Attributes:
        archetype: incremental / recurrence / window / lookahead / axis_distribution のいずれか。
        min_window: tail 本数。None は full（tail せず全件で adapter.compute）。
        trailing_k: 末尾切り点数。None は切らない（axis_distribution＝全件）。
        incremental: 増分状態器の名前（``adapter.compute.incremental`` のレジストリ名）。
            None は増分計算を行わない（＝従来の full 切り出し経路）。増分器が当該
            (df, params) を扱えない場合も従来経路へ落ちるため、宣言は挙動を変えない
            （OCP: 既存経路は不変・宣言した指標だけが新経路へ乗る）。
    """

    archetype: str
    min_window: "int | None"
    trailing_k: "int | None"
    incremental: "str | None" = None
