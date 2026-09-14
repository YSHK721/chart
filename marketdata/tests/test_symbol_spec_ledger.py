"""銘柄仕様台帳（symbol_spec）とその JS 生成物の検定（ISSUE-368 工程 2・案 E / S-1・S-2）。

なぜ必要か（`.doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md` 追補「原因 α」）:
    `DatasetDescriptor` は `path` / `clamp_outliers` / `rollup` / `tick` の 4 属性しか持たず、
    「その datasetRef がどの銘柄の価格か」を供給側が一度も名乗っていなかった。front は
    `CHART_SYMBOL='NI225'` を自称するしかなく、呼び値（価格の最小変動単位）を誰も決められない。
    本検定は「全 ref が銘柄を名乗る」「その銘柄に仕様がある」を構造的に固定する。

方向: Python（唯一の定義）→ JS 生成物。台帳を変えたのに ``tools/gen_js_parity_golden.py`` を
再実行し忘れると、JS だけ古い呼び値で丸め続ける（ISSUE-253 と同型の「静かなずれ」）。
S-2 群はその再生成漏れを落とす（``test_tf_ledger_parity.py`` と同型）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC_PY = ROOT / "marketdata" / "symbol_spec.py"
JS_OUT = (
    ROOT / "indigators" / "indicator_ui" / "web" / "js" / "domain" / "symbol_spec_generated.js"
)


# --------------------------------------------------------------------------------------
# S-1: Python 台帳
# --------------------------------------------------------------------------------------
def test_全datasetRefが非空の銘柄シンボルを名乗る():
    """ref → 銘柄の対応が台帳の側に存在する（front の自称 `CHART_SYMBOL` を不要にする前提）。"""
    from marketdata.dataset_registry import REGISTRY

    missing = [ref for ref, d in REGISTRY.items() if not getattr(d, "symbol", "")]
    assert missing == [], f"symbol が空の ref: {missing}（DatasetDescriptor.symbol は必須）"


def test_全datasetRefの銘柄が仕様台帳に存在する():
    """未知銘柄を残さない（front のフェイルセーフ `no_symbol_spec` に落ちる ref を作らない）。"""
    from marketdata.dataset_registry import REGISTRY
    from marketdata.symbol_spec import SYMBOL_SPECS

    unknown = sorted({d.symbol for d in REGISTRY.values()} - set(SYMBOL_SPECS))
    assert unknown == [], f"仕様の無い銘柄: {unknown}（marketdata/symbol_spec.py に追加すること）"


def test_全銘柄仕様の呼び値が正である():
    """`tick <= 0` は量子化（丸め）を定義できない（0 除算・無限ループの温床）。"""
    from marketdata.symbol_spec import SYMBOL_SPECS

    bad = {s: spec.tick for s, spec in SYMBOL_SPECS.items() if not spec.tick > 0}
    assert bad == {}, f"tick は正でなければならない: {bad}"


def test_全銘柄仕様の表示桁で呼び値を表現できる():
    """`10 ** -digits <= tick`＝刻みが表示桁で表せる（表示と入る値の乖離を作らない）。

    例: `tick=1.0` に `digits=2` は許すが、`tick=0.001` に `digits=2` は許さない
    （後者は「表示は 2 桁なのに刻みは 3 桁」＝画面に出ない桁が値に入る）。
    """
    from marketdata.symbol_spec import SYMBOL_SPECS

    bad = {
        s: (spec.tick, spec.digits)
        for s, spec in SYMBOL_SPECS.items()
        if not 10 ** -spec.digits <= spec.tick
    }
    assert bad == {}, f"10**-digits <= tick を満たさない: {bad}"


def test_表示桁が呼び値の小数桁と一致する():
    """`digits` は `tick` の小数桁と**一致**する（不等式では通ってしまう食い違いを塞ぐ）。

    JS 側の `domain/price_quantize.js:47-53` は、積の浮動小数残差を丸め戻す桁数を
    `tick` から導出する（`decimalsOf`）。台帳の `digits` は表示桁として独立に持つため、
    2 つが食い違う組を将来足すと **表示桁と量子化桁がずれる**。`10 ** -digits <= tick`
    （不等式）はこれを通す: `tick=0.25, digits=1` は `0.1 <= 0.25` で不等式を満たすが、
    量子化桁は 2 桁必要（変異実測: 本検定のみが赤・不等式の検定は緑のまま）。
    ここで等式に締めて権威を 1 つにする。

    導出は JS の `decimalsOf` と同じ規則にする（`Decimal(str(tick))` を **normalize してから**
    指数を見る）。normalize を省くと `str(1.0) == '1.0'` から 1 桁と誤り、承認済みの
    A-1（`tick=1.0` / `digits=0`）が赤になる。JS 側は `String(1.0) === '1'` で 0 桁。
    指数表記（`1e-07`）も normalize 後の指数で正しく 7 桁になる（JS 側は仮数部の桁数から
    指数を差し引いて同値を得る）。
    """
    from decimal import Decimal

    from marketdata.symbol_spec import SYMBOL_SPECS

    def decimals_of(tick: float) -> int:
        return max(0, -int(Decimal(str(tick)).normalize().as_tuple().exponent))

    bad = {
        s: (spec.tick, spec.digits, decimals_of(spec.tick))
        for s, spec in SYMBOL_SPECS.items()
        if spec.digits != decimals_of(spec.tick)
    }
    assert bad == {}, f"digits が tick の小数桁と一致しない {{銘柄: (tick, digits, 期待)}}: {bad}"


def test_JP225の呼び値は裁定値である():
    """A-1 裁定（2026-08-20）: `tick=1.0` / `digits=0`。

    真値（OANDA 証券 CFD の呼び値）はリポジトリ内に出典が無く、1.0 は**安全側の既定**である。
    値そのものを固定して、無言の変更（0.1 への「訂正」等）を検定で見えるようにする。
    """
    from marketdata.symbol_spec import SYMBOL_SPECS

    assert (SYMBOL_SPECS["JP225"].tick, SYMBOL_SPECS["JP225"].digits) == (1.0, 0)


def test_tick_m1の既定銘柄が台帳のjp225_tickと一致する():
    """既存の暗黙の対応（`tick_m1._DEFAULT_SYMBOL` / `_DEFAULT_REF`）を機械的に固定する。

    ref→銘柄の対応は本台帳の新設ではなく、既に `marketdata/tick_m1.py:58-60` に実在した事実の
    明文化である。片方だけ変えたら赤にする。
    """
    from marketdata import tick_m1
    from marketdata.dataset_registry import REGISTRY

    assert tick_m1._DEFAULT_SYMBOL == REGISTRY[tick_m1._DEFAULT_REF].symbol
    assert tick_m1._DEFAULT_REF == "jp225_tick"


def test_銘柄仕様モジュールは外部依存を持たない():
    """`symbol_spec.py` は最下層（依存 0）。stdlib 以外を import したら赤にする。

    `dataset_registry.py:12-14` が宣言する最下層規律に一致させる。ここに `paths` や `pandas` が
    入ると「pandas を使えない層は同じ台帳を引けない」が発生する（ISSUE-261 と同型）。
    """
    tree = ast.parse(SPEC_PY.read_text(encoding="utf-8"))
    stdlib = {"__future__", "dataclasses", "typing"}
    external: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            external |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and not node.level:
            external.add(node.module or "")
    assert {n for n in external if n.split(".")[0] not in stdlib} == set()


# --------------------------------------------------------------------------------------
# S-2: JS 生成物（Python 権威 → JS）。``test_tf_ledger_parity.py`` と同型。
#
# 同型としつつ ``skipif(not JS.exists())`` は**採らない**。本生成物はコミットされる資産であり、
# 「不在」は陳腐化の最悪形（front が import 解決に失敗して起動しない）だからである。skip にすると
# 再生成漏れの最重症例だけが緑で通り抜ける。
# --------------------------------------------------------------------------------------
_JS_SYMBOL_ROW = re.compile(r"^  '([^']+)': '([^']+)',$", re.M)
_JS_SPEC_ROW = re.compile(
    r"^  '([^']+)': Object\.freeze\(\{ tick: ([0-9.eE+-]+), digits: (\d+) \}\),$", re.M
)


def _js_source() -> str:
    assert JS_OUT.exists(), (
        f"JS 生成物が存在しない: {JS_OUT}。"
        " PYTHONPATH=. python3 tools/gen_js_parity_golden.py を実行すること"
    )
    return JS_OUT.read_text(encoding="utf-8")


def _js_blocks() -> "tuple[str, str]":
    """生成物を DATASET_SYMBOLS 部と SYMBOL_SPECS 部に割る（行正規表現の適用範囲を分ける）。"""
    src = _js_source()
    head, _, tail = src.partition("export const SYMBOL_SPECS")
    assert tail, "生成物に SYMBOL_SPECS の宣言が無い"
    return head, tail


def test_生成JSのref対銘柄がPython台帳と一致する():
    """ref → 銘柄（`DatasetDescriptor.symbol`）の写しがずれたら赤にする。"""
    from marketdata.dataset_registry import REGISTRY

    head, _ = _js_blocks()
    got = dict(_JS_SYMBOL_ROW.findall(head))
    assert got == {ref: d.symbol for ref, d in REGISTRY.items()}, (
        "台帳を変更したら tools/gen_js_parity_golden.py を再実行すること"
    )


def test_生成JSの銘柄仕様がPython台帳と一致する():
    """銘柄 → 仕様（tick / digits）の写しがずれたら赤にする（JS だけ古い呼び値で丸め続ける事故）。"""
    from marketdata.symbol_spec import SYMBOL_SPECS

    _, tail = _js_blocks()
    got = {s: (float(t), int(d)) for s, t, d in _JS_SPEC_ROW.findall(tail)}
    assert got == {s: (spec.tick, spec.digits) for s, spec in SYMBOL_SPECS.items()}, (
        "台帳を変更したら tools/gen_js_parity_golden.py を再実行すること"
    )


def test_生成JSは生成物であることを冒頭で明示する():
    """手編集を誘発しない（生成物であることがファイル冒頭で読める）。"""
    head = _js_source().splitlines()[0]
    assert "自動生成" in head and "編集しない" in head


def test_生成JSはimport文を持たない():
    """依存 0 の生成物（配信ルート越しの import 解決に一切依存しない）。

    通過条件の機械検査 `grep -c "^import\\|^export .* from"` が 0 であることと同義。
    """
    offenders = [
        ln
        for ln in _js_source().splitlines()
        if ln.startswith("import") or re.match(r"^export .* from", ln)
    ]
    assert offenders == [], f"生成物に import がある: {offenders}"


def test_生成器を再実行しても生成JSに差分が出ない():
    """冪等性。renderer の出力とファイル内容がバイト一致する（＝再実行で差分 0）。

    テスト内で生成器を実行して書き戻さない（テストが成果物を書き換えると、ずれを検出する側が
    ずれを消してしまう）。純粋な render 関数の戻り値と現ファイルを突き合わせる。
    """
    from marketdata.dataset_registry import REGISTRY
    from marketdata.symbol_spec import SYMBOL_SPECS
    from tools.gen_js_parity_golden import render_symbol_spec_js

    rendered = render_symbol_spec_js(
        {ref: d.symbol for ref, d in REGISTRY.items()}, SYMBOL_SPECS
    )
    assert rendered == _js_source(), (
        "台帳を変更したら tools/gen_js_parity_golden.py を再実行すること（再実行で差分 0）"
    )
