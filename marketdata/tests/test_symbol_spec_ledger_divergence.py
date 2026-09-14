"""2 つの銘柄仕様台帳の**食い違いを固定する**検定（ISSUE-445 / TBD-D・段階 F-0）。

## この検定は何を主張し、何を主張しないのか（先に読むこと）

本ファイルに書き下してある数値は、**真値の主張ではない**。「2026-08-27 時点で 2 つの台帳が
こう食い違っている」という**現状の記録**である。どちらが正しいかは本ファイルの射程外であり、
統合の是非（TBD-D）にも一切踏み込まない。

なぜ記録するのか。ISSUE-445 の根本原因 RC-1 は「供給元と突き合わせる機構が無いため誤りは
検出されない」であった（`case.yaml` の `contract_size: 10` が 2 か月以上気付かれなかった）。
本件も同型である——2 つの台帳が同じ概念に別の値を持ちながら、**両者を突き合わせる機構が
1 つも無い**（実測は下記「消費者集合は素である」）。よって「気付かれないまま動く」ことだけを
遮断する。値は 1 つも変えない。

**RC-1 との違い（混同しないこと）**: RC-1 が禁じたのは「人が書いた値が**権威**として振る舞う」
構造である。ここに書く数値は権威ではない——**2 つの台帳から機械的に読んだ観測値の写し**であり、
どちらかの台帳が動いた瞬間に赤になるためだけに存在する。権威は依然として各台帳の側にある。

## 2 つの台帳（供給元が違うので所在 2 は正しい・設計書 §3.5）

| 記号 | 台帳 | 供給元 | 権威範囲 |
|---|---|---|---|
| **A** | `marketdata/symbol_spec.py:SYMBOL_SPECS` | OANDA 証券 CFD | CFD の呼び値・表示桁 |
| **B** | `marketdata/symbol_specs/OANDA-Japan-MT5-Live/<銘柄>.json` | OANDA-Japan MT5 | MT5 銘柄の全仕様（96 フィールド） |

共通概念は :data:`SHARED_CONCEPTS` の 2 つだけであり、**その 2 つとも値が食い違う**
（実測 2026-08-27・JP225）。対応の宣言は :data:`SHARED_CONCEPTS` **1 箇所**にしか無い。

## 呼び値（`tick`）と `point_size` を混同しないこと（最も踏みやすい罠）

台帳 B の `point`（`SPEC_FIELD_SOURCES["point_size"]` の供給元）は**呼び値ではない**。
`point` は「**点（point）単位のパラメータを価格差へ換算する乗数**」である。実測での用法は
1 つではない——SL/TP の点数換算（`simulator/domain/sltp.py:34-35` の `dist = points × point_size`）、
スプレッドの換算（`usecase/_execution.py:75` の `ask = bid + spread × point_size`）、
傾き閾値の換算（`adapter/strategy/ma_slope.py:82`）など。いずれも**点数 → 価格差**であって、
「注文が成立する価格の刻み」としては 1 度も使われていない。

呼び値（この倍数以外は注文として成立しない刻み）に対応する MT5 フィールドは
**`trade_tick_size`** のほうである。

JP225 のスナップショットでは `point` も `trade_tick_size` も **0.1** で偶然一致しており
（実測）、値を見比べただけでは区別が付かない。両者は別概念であり、真値が動くときに一緒に
動くとは限らない。`test_呼び値の対応先はtrade_tick_sizeでありpointではない` がこの区別を
機械的に固定する。

## 消費者集合は素である（実測 2026-08-27）

- 台帳 A と台帳 B の**両方**を読む `.py` は、**本ファイルを除いて 0 件**（AST の import 走査に
  よる全数調査＋スナップショットのパス直読みの `command grep` 全数調査。後者のヒットは
  すべて docstring・コメント）。本ファイルが 2 台帳の唯一の接点である。
- `trade_tick_size` を読む本番コードは **0 件**（`command grep -rn` による全数調査。ヒットは
  `tools/tests/test_capture_mt5_symbol_spec.py` の合成 fixture と文書のみ）。
- 台帳 A の消費者は JS の表示・入力量子化（生成物 `symbol_spec_generated.js` 経由）、
  台帳 B の消費者は Python の約定・証拠金計算。**消費者集合は素**である。

つまり 2 つの値が同じ計算に混ざる経路は無い。**同時に、ずれを検出する機構も無い**。
本ファイルがその唯一の検出点である。

## 既存検定との棲み分け（同じことを二度書かない）

- `test_symbol_spec_ledger.py`: 台帳 A の**自己完結**ピン（A-1 裁定値・`digits` と `tick` の
  整合）と JS 生成物の parity。台帳 B を 1 度も読まない。
- `test_symbol_spec_snapshot.py`: 台帳 B の**ローダ**の検定（対応表の唯一性・Fail-Stop）。
  台帳 A を 1 度も読まない。
- **本ファイル**: 「2 台帳の突合」だけを持つ。どちらのファイルも今この問いを持っていない。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import pytest

from marketdata import symbol_spec_snapshot as snapshot
from marketdata.symbol_spec import SYMBOL_SPECS, SymbolSpec

#: 台帳 B の供給元（サーバ）。取り込み済みは 1 つだけであり、列挙はローダ側が唯一源。
_SERVER = snapshot.OANDA_JAPAN_MT5_LIVE


@dataclass(frozen=True)
class SharedConcept:
    """2 台帳が**同じ概念**に対して持つ、それぞれの綴り。

    Attributes:
        name: 概念（日本語）。読む人のためのラベルであり、突合には使わない。
        cfd_attr: 台帳 A（`SYMBOL_SPECS[銘柄]`）の属性名。
        mt5_key: 台帳 B（スナップショットの `symbol` セクション）のキー。
    """

    name: str
    cfd_attr: str
    mt5_key: str


#: **共通概念の対応はここ 1 箇所だけで宣言する**（2 つしかない）。
#: `tick` の相手が `point` ではなく `trade_tick_size` である理由はモジュール docstring
#: 「呼び値と point_size を混同しないこと」を読むこと。
SHARED_CONCEPTS: "tuple[SharedConcept, ...]" = (
    SharedConcept("呼び値", "tick", "trade_tick_size"),
    SharedConcept("表示桁", "digits", "digits"),
)

#: **現状の記録**（真値の主張ではない・モジュール docstring 冒頭を読むこと）。
#: 値は `{銘柄: {概念: (台帳 A の値, 台帳 B の値)}}`。出典は各行のコメントに記す。
RECORDED_DIVERGENCE: "dict[str, dict[str, tuple[Any, Any]]]" = {
    "JP225": {
        # A: marketdata/symbol_spec.py:SYMBOL_SPECS["JP225"].tick （A-1 裁定＝安全側の既定）
        # B: symbol_specs/OANDA-Japan-MT5-Live/JP225.json の symbol.trade_tick_size
        "tick": (1.0, 0.1),
        # A: 同 SYMBOL_SPECS["JP225"].digits ／ B: 同 JSON の symbol.digits
        "digits": (0, 1),
    },
}


def concept_named(name: str) -> SharedConcept:
    """概念名で :data:`SHARED_CONCEPTS` を引く（無ければ `KeyError`）。"""
    for concept in SHARED_CONCEPTS:
        if concept.name == name:
            return concept
    raise KeyError(f"共通概念に {name!r} は無い: {[c.name for c in SHARED_CONCEPTS]}")


def pairs_from(
    cfd_spec: SymbolSpec, mt5_section: "Mapping[str, Any]"
) -> "dict[str, tuple[Any, Any]]":
    """**判定（純関数）**: 共通概念ごとに `(台帳 A の値, 台帳 B の値)` を組にして返す。

    入出力しか持たないので、合成入力を食わせて「判定が変化を捕まえるか」を実証できる
    （台帳 B は機械生成物であり書き換えて実験できないため、切り出しが要る）。
    """
    return {
        c.cfd_attr: (getattr(cfd_spec, c.cfd_attr), mt5_section[c.mt5_key])
        for c in SHARED_CONCEPTS
    }


def read_pairs(symbol: str) -> "dict[str, tuple[Any, Any]]":
    """実台帳 2 つから読んで :func:`pairs_from` にかける（読み込み + 判定の合成）。"""
    return pairs_from(SYMBOL_SPECS[symbol], snapshot.load_snapshot(_SERVER, symbol)["symbol"])


def comparable_symbols() -> "tuple[str, ...]":
    """突合できる銘柄＝**両台帳に在る**銘柄（積集合）。人が一覧を書かない。"""
    in_b = {p.stem for p in (snapshot.SNAPSHOT_ROOT / _SERVER).glob("*.json")}
    return tuple(sorted(set(SYMBOL_SPECS) & in_b))


def test_突合対象は両台帳に在る銘柄だけである():
    """片方にしか無い銘柄は突合対象から**機械的に外れる**（人が除外一覧を書かない）。

    台帳 A は `JP225` と `TSLA` を持つが、台帳 B（MT5 スナップショット）に `TSLA` は無い。
    無い側を「一致しない」と判定するのは誤りである——台帳 B は OANDA-Japan MT5 が**扱う
    銘柄だけ**を持つ供給元であり、`TSLA` の不在は食い違いではなく**取扱いの差**だからである
    （不在を欠損とみなすと、MT5 で扱わない銘柄を足すたびに赤くなる無意味なゲートになる）。

    突合対象は「両台帳に在る銘柄」＝積集合として導出する。台帳 B に `TSLA` の
    スナップショットが取り込まれたら、この検定を書き換えずに突合対象へ入る。
    """
    assert comparable_symbols() == ("JP225",)


def test_共通概念の値は記録どおり食い違う():
    """**現状の食い違いをそのまま固定する**。一致に転じても、片方が別の値になっても赤。

    一致を赤にするのは意地悪ではない。一致は「2 台帳が統合された（あるいは片方が他方に
    寄せられた）」という**重大な変化**であり、TBD-D の裁定なしに起きてはならないからである。
    赤が出たときにやることは、この期待値を書き換えることではなく、**なぜ動いたのかを
    ISSUE-445 / TBD-D に記録して裁定を仰ぐ**ことである。

    突合対象は :func:`comparable_symbols` から導出するため、台帳 B に新しい銘柄が取り込まれて
    共通銘柄が増えた場合も（記録が無い銘柄として）赤になる。
    """
    assert {s: read_pairs(s) for s in comparable_symbols()} == RECORDED_DIVERGENCE


def test_呼び値の対応先はtrade_tick_sizeでありpointではない():
    """罠の固定: 呼び値の相手は `trade_tick_size`。`point`（→`point_size`）は別概念である。

    JP225 のスナップショットでは両者とも 0.1 で**偶然一致**しているため、値を見比べても
    区別が付かない。ここで綴りの側から固定する。同時に、両者の**立場の違い**を実測で示す:

    - `point` は供給経路に載っている（`SPEC_FIELD_SOURCES["point_size"]` の供給元＝
      `simulator` 側の `SymbolSpec.point_size` になり、`domain/sltp.py` が点数換算に使う）。
    - `trade_tick_size`（＝呼び値）は供給経路に**載っていない**。リポジトリ内に消費者が
      0 件であり、台帳 B 側の呼び値は現在どこからも読まれていない。

    `trade_tick_size` が供給経路へ載った日は、台帳 A の `tick` と台帳 B の呼び値が同じ計算に
    届きうるようになった日である（＝素だった消費者集合が交わる）。そのときこの検定が赤くなり、
    TBD-D の裁定なしに通過できない。
    """
    tick_key = concept_named("呼び値").mt5_key
    supplied = {source.key for source in snapshot.SPEC_FIELD_SOURCES.values()}

    assert tick_key == "trade_tick_size"
    assert snapshot.SPEC_FIELD_SOURCES["point_size"].key == "point"
    assert tick_key not in supplied, (
        f"{tick_key} が供給経路へ載った＝2 台帳の呼び値が同じ計算に届きうる（TBD-D の裁定が要る）"
    )


@pytest.mark.parametrize(
    "label, cfd_spec, mt5_section",
    [
        (
            "台帳 B の呼び値だけが動いた",
            SymbolSpec(tick=1.0, digits=0),
            {"trade_tick_size": 0.5, "digits": 1},
        ),
        (
            "台帳 A の呼び値だけが動いた",
            SymbolSpec(tick=0.5, digits=0),
            {"trade_tick_size": 0.1, "digits": 1},
        ),
        (
            "両台帳が一致に転じた（＝統合された）",
            SymbolSpec(tick=0.1, digits=1),
            {"trade_tick_size": 0.1, "digits": 1},
        ),
    ],
)
def test_判定は合成入力の変化を検出する(label, cfd_spec, mt5_section):
    """**落ちないゲートは無価値である**——判定が実際に変化を捕まえることを実証する。

    台帳 B（スナップショット）は機械生成物であり書き換えて実験できない。そこで判定を
    :func:`pairs_from`（純関数）に切り出し、**合成入力**を食わせて示す。
    上の 3 例はいずれも「記録と違う」＝ :func:`test_共通概念の値は記録どおり食い違う`
    が赤になる状況である（一致に転じた場合も含む）。
    """
    assert pairs_from(cfd_spec, mt5_section) != RECORDED_DIVERGENCE["JP225"], label
