"""モード定義表（`unified_ui/web/js/mode_table.js`）を読む唯一の口（ISSUE-502 D-11 の Python 対応物）。

なぜ在るか（実測に基づく欠陥）:
  モード集合の唯一源は front のモード定義表である（`mode_table.js` 冒頭・基本設計書 §3.5.6）。
  router の既定表（router モジュールの _DEFAULT_UPSTREAMS）と URL prefix はその Python 側の写しだが、
  一致は `router.py` の docstring に**文章で宣言されているだけ**で、突合の検定が 0 だった。
  そのためルータにだけモードを足しても、front にだけ足しても、何ひとつ落ちない：

    - front に在って router に無い … front が `/<mode>/*` を出すが上流が無く、**無音の 404**
    - router に在って front に無い … 誰も叩かない prefix が増え、静的配信面と衝突しうる

  どちらも起動時に何のエラーも出ず、「押しても何も起きない」形でしか現れない。

なぜ読み取りを 1 モジュールへ閉じるのか（SRP）:
  変わる理由は 1 つだけ ―― **表の書字形式**（`id: 'live',` / `prefix: '/live',`）である。
  読み取り式を各検定へ書き写すと、形式が変わった日に直す先が増え、かつ「直し忘れても
  落ちない写し」を新たに作ることになる。是正しようとしている欠陥と同型のものを、
  検定側に作らないための配置。

なぜ生成物（`tools/gen_js_parity_golden.py` 方式）を採らないのか:
  同スクリプトの向きは **Python → JS**（規則の権威が Python 側にあり JS が生成物）である。
  本件の唯一源は JS 側なので向きが逆で、同居させると 1 スクリプトが相反する 2 つの権威方向を
  持つことになる。逆向きの生成物（JS を読んで Python を吐く）は、生成物が古いまま実行できて
  しまう分だけ突合検定より弱い（生成物と源のずれを検出するのに結局突合が要る）。

読み取り回数:
  1 プロセス 1 回（`mode_table()` を `lru_cache`）。検定はモードごとに parametrize されるため、
  問い合わせのたびに読み直す形だと読み取りが**モード数に比例**して増える。増えた読み取りは
  答えを 1 文字も変えないので状態検証では原理的に落ちない。無駄の不在は `test_router.py` の
  計算量テストが Test Spy で固定する（`read_table_text` が実 I/O の唯一点＝spy 先）。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

#: モード定義表の実体。本ファイルの位置から一意に決まる（cwd・引数に依存させない）。
TABLE_PATH = Path(__file__).resolve().parents[1] / "web" / "js" / "mode_table.js"

#: 表の 1 行から属性値を取る。表の書字形式を知るのは本モジュールだけ（上記 SRP）。
#: 形式は `Object.freeze({ id: 'live', prefix: '/live', … })` の 1 属性 1 行。
_ID_LINE = re.compile(r"^\s*id:\s*'([a-z][a-z0-9_]*)',", re.MULTILINE)
_PREFIX_LINE = re.compile(r"^\s*prefix:\s*'(/[a-z][a-z0-9_]*)',", re.MULTILINE)


def read_table_text() -> str:
    """モード定義表の本文を読む。

    **実 I/O はここ 1 箇所だけ**にする。計算量テストはこの関数を Test Spy へ差し替えて
    発行回数を数えるため、読み取り口が複数あると数え漏れる（＝浪費を通す）。
    """
    return TABLE_PATH.read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def mode_table() -> tuple[tuple[str, str], ...]:
    """表の `(id, prefix)` を表の順で返す。

    表の書式が変わって 1 件も取れない場合は即座に落とす。空集合を黙って返すと、突合検定が
    「両辺とも空」で通ってしまい、検定が在るのに何も守らない状態になる（最悪の失敗形）。
    """
    text = read_table_text()
    ids = _ID_LINE.findall(text)
    prefixes = _PREFIX_LINE.findall(text)
    if not ids:
        raise AssertionError(
            f"モード定義表からモード名を取れていない（表の書字形式が変わった）: {TABLE_PATH}"
        )
    if len(ids) != len(prefixes):
        raise AssertionError(
            f"モード定義表の id 数 {len(ids)} と prefix 数 {len(prefixes)} が食い違う: {TABLE_PATH}"
        )
    return tuple(zip(ids, prefixes))


def mode_ids() -> frozenset[str]:
    """表に載っているモード名の集合（順序は router 側の関心ではないため集合で返す）。"""
    return frozenset(mode_id for mode_id, _ in mode_table())


def mode_prefixes() -> frozenset[str]:
    """表に載っている URL prefix の集合。"""
    return frozenset(prefix for _, prefix in mode_table())
