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
    **真値が判明したときの変更点は「台帳 1 行」では済まない**（実測 2026-08-20・工程 5 是正 5）。
    値そのものを期待に持つ箇所が 6 ファイル 18 か所ある:
      1. 下記台帳 1 行（``SYMBOL_SPECS["JP225"]``）
      2. 裁定値ピン ``marketdata/tests/test_symbol_spec_ledger.py``（``test_JP225の呼び値は裁定値である``）
      3. 生成物 ``.../web/js/domain/symbol_spec_generated.js``（``tools/gen_js_parity_golden.py`` の再実行）
      4. 量子化後の期待値を持つ JS 検定 3 本
         （``position_sizing_pick_path_parity.test.js`` 3 か所 /
          ``position_sizing_symbol_spec_wiring.test.js`` 6 か所 /
          ``position_sizing_price_input_commit.test.js`` 6 か所）
    2 は「無言の変更を見えるようにする」ための意図的なピンであり、4 は刻みで丸めた結果を
    期待に書いているため刻みが変われば当然変わる。いずれも**赤で気付ける**設計だが、
    「1 行で済む」ではない。
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
    # docstring 参照・A-1 裁定）。真値判明時に変更する箇所の一覧も同 docstring に記す
    # （この 1 行では済まない＝裁定値ピン・生成物・期待値を持つ検定も動く）。
    "JP225": SymbolSpec(tick=1.0, digits=0),
    # 同梱サンプル（datasetRef="sample"）の銘柄。実体の同定と刻みの根拠は
    # dataset_registry.py の "sample" 記述子のコメントに記す（実測に基づく）。
    "TSLA": SymbolSpec(tick=0.01, digits=2),
}


__all__ = ["SymbolSpec", "SYMBOL_SPECS"]
