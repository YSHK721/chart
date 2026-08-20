"""symbol_spec — 銘柄仕様（呼び値 ``tick`` ・表示桁 ``digits``）の唯一源（ISSUE-368 工程 2・案 E-2）。

「価格の最小変動単位（呼び値）」はブローカー規約の所有物であり、市場データ供給（ref → CSV パス・
ロールアップ経路）とは**変更起点が独立**する（証拠金率の改定と rollup 経路の変更が同じ境界を
揺らしてはならない）。よって :mod:`marketdata.dataset_registry` の ``DatasetDescriptor`` には
同居させず、本モジュールを別アクターの台帳として分離する。ref → 銘柄の対応だけを
``DatasetDescriptor.symbol`` が持ち、銘柄 → 仕様は本台帳が持つ。

依存方向: **依存ゼロ**（stdlib のみ）。``dataset_registry.py`` が宣言する最下層規律に合わせる。
ここに ``paths`` や ``pandas`` を持ち込むと「pandas を使えない純層は同じ台帳を引けない」が
発生する（ISSUE-261 と同型）。JS 側へは ``tools/gen_js_parity_golden.py`` が
``indigators/indicator_ui/web/js/domain/symbol_spec_generated.js`` を生成して配る（HTTP route は
作らない＝規約 ``.doc/LAYERING_CONVENTIONS.md``: 権威は Python・JS は生成物）。

JP225 の ``tick=1.0`` について（**重要・A-1 裁定 2026-08-20**):
    **この値は OANDA 証券 CFD の呼び値の実測ではなく「安全側の既定」である。**
    リポジトリ内に CFD 呼び値の出典が無い（``docs/oanda_indices_cfd_about.md:104`` は
    「銘柄毎に異なる（取扱銘柄ページ参照）」とだけ述べ、出典は外部 URL ``:286``）。
    1.0 の倍数は 0.1 の倍数でもあるため、真値が 0.1 でも 1.0 は無効価格を作らない。逆に
    0.1 を採って真値が 1.0 だった場合は無効価格を作る。ゆえに未確定下では 1.0 が安全側。
    ``sim_ui`` の ``digits=1`` / ``point_size=0.1``（``sim_ui/adapter/symbol_spec_catalog.py``）は
    OANDA-Japan **MT5** の JP225（``contract_size=10``）＝**別商品**であり、本件の権威にならない
    （A-5: 本件では触らない・二重所在は TBD-D としてフォローアップ）。
    **真値が判明したときの変更点は下記台帳の 1 行のみ。**
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SymbolSpec:
    """銘柄の価格仕様（1 銘柄 = 1 仕様）。

    Attributes:
        tick: 呼び値。価格の最小変動単位（この倍数以外の価格は注文として成立しない）。
        digits: 表示桁（小数点以下の桁数）。``10 ** -digits <= tick`` を満たすこと
            （表示に出ない桁が値に入る状態を作らない）。
    """

    tick: float
    digits: int


#: 銘柄仕様台帳（唯一源）。キーは ``DatasetDescriptor.symbol`` が名乗る銘柄シンボル。
SYMBOL_SPECS: dict[str, SymbolSpec] = {
    # OANDA 証券 JP225 CFD（contract_size=1）。tick は**安全側の既定**（上記モジュール
    # docstring 参照・A-1 裁定）。真値判明時に変更するのはこの 1 行のみ。
    "JP225": SymbolSpec(tick=1.0, digits=0),
    # 同梱サンプル（datasetRef="sample"）の銘柄。実体の同定と刻みの根拠は
    # dataset_registry.py の "sample" 記述子のコメントに記す（実測に基づく）。
    "TSLA": SymbolSpec(tick=0.01, digits=2),
}


__all__ = ["SymbolSpec", "SYMBOL_SPECS"]
