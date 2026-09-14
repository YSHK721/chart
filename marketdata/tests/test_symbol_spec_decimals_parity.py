"""「刻みの小数桁」規則が JS と Python で同値であることの検定（ISSUE-368 工程 5 🟡-5）。

なぜ必要か（`.doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md`「マージ前に是正する項目」🟡-5）:
    同じ規則が**別アルゴリズムで 2 回**実装されている。

    - JS  : ``indigators/indicator_ui/web/js/domain/price_quantize.js`` の ``decimalsOf``
            （文字列表現を仮数部と指数へ割り、仮数部の小数桁から指数を引く）
    - Python: ``marketdata/tests/test_symbol_spec_ledger.py`` の ``decimals_of``
            （``Decimal(str(tick)).normalize().as_tuple().exponent``）

    Python 側の docstring は「JS の ``decimalsOf`` と同じ規則にする」と**宣言**するが、同値性を
    機械検査する検定は 1 件も無かった。プロジェクト規約「同じコードを手書き複製するな」は、
    複製が避けられないとき**一致を検定で強制する**ことを要求する。

第 3 実装を作らない（本検定の設計上の要点）:
    どちらの規則もここに書き写さない。**実ファイルから当該関数の定義をそのまま取り出して実行**し、
    出力どうしを突き合わせる。

    - JS  : ``price_quantize.js`` は ``decimalsOf`` を export していない（``quantize`` /
            ``usableTick`` の内部関数）。ソースから当該宣言だけを切り出し、``node`` で実行する。
            切り出しに失敗する（宣言が 1 つでない）場合は**失敗**させる＝改名・分割を見逃さない。
    - Python: 台帳検定の中に閉じた入れ子関数なので import できない。AST で当該定義を取り出して
            実行する。写経しないので、片方を直したらここが赤くなる。

``node`` が無い環境では**スキップせず失敗**させる（無音のスキップで検定が消えるのを避ける）。
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import textwrap
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JS_QUANTIZE = ROOT / "indigators" / "indicator_ui" / "web" / "js" / "domain" / "price_quantize.js"
PY_LEDGER_TEST = Path(__file__).with_name("test_symbol_spec_ledger.py")

# 代表値（工程 5 是正 B の指定 ＋ 台帳の実値）。
#   境界の意図:
#     1.0 / 100.0            … 整数（Python は '1.0' / '100.0'、JS は '1' / '100' と文字列化する）
#     0.1 / 0.25 / 0.005 / 2.5 … 通常の小数
#     1e-7                   … JS が指数表記へ切り替わる境界より下（String(1e-7) === '1e-7'）
#     1e-101                 … quantize の toFixed 上限（100 桁）を超える領域
#     5e-324                 … 非正規化数の下限
#     0.30000000000000004    … 二進浮動小数の丸め誤差がそのまま桁数に出る値
_REQUIRED = (1.0, 0.1, 0.25, 1e-7, 5e-324, 1e-101, 100.0, 0.005, 2.5, 0.30000000000000004)


def _ledger_ticks() -> "tuple[float, ...]":
    """台帳が実際に持つ刻み（規則が実データで一致することまで見る）。"""
    from marketdata.symbol_spec import SYMBOL_SPECS

    return tuple(sorted({float(spec.tick) for spec in SYMBOL_SPECS.values()}))


def _values() -> "tuple[float, ...]":
    seen: "list[float]" = []
    for v in _REQUIRED + _ledger_ticks():
        if v not in seen:
            seen.append(v)
    return tuple(seen)


# --------------------------------------------------------------------------------------
# 実装の取り出し（写経しない）
# --------------------------------------------------------------------------------------
def _js_decimals_of_source() -> str:
    """``price_quantize.js`` から ``decimalsOf`` の宣言だけを切り出す（波括弧の対応で終端を取る）。"""
    src = JS_QUANTIZE.read_text(encoding="utf-8")
    marker = "function decimalsOf("
    assert src.count(marker) == 1, (
        f"`{marker}` の宣言が 1 つでない（{src.count(marker)} 件）: "
        "JS 側の実装が改名・分割された。本検定の突き合わせ対象を見直すこと"
    )
    start = src.index(marker)
    depth = 0
    i = src.index("{", start)
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
        i += 1
    raise AssertionError("decimalsOf の波括弧が閉じていない（切り出しに失敗）")


def _py_decimals_of():
    """台帳検定の入れ子関数 ``decimals_of`` を AST で取り出して実行可能にする。"""
    src = PY_LEDGER_TEST.read_text(encoding="utf-8")
    found = [
        node
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.FunctionDef) and node.name == "decimals_of"
    ]
    assert len(found) == 1, (
        f"`decimals_of` の定義が 1 つでない（{len(found)} 件）: Python 側の実装が移動・複製された"
    )
    segment = ast.get_source_segment(src, found[0])
    assert segment is not None and "Decimal(" in segment, "取り出した定義が Decimal を使っていない"
    namespace: "dict[str, object]" = {"Decimal": Decimal}
    exec(textwrap.dedent(segment), namespace)  # noqa: S102 - 実ファイルの当該定義そのもの
    return namespace["decimals_of"]


def _run_js(values: "tuple[float, ...]") -> "list[list]":
    """切り出した JS を node で実行し、``[小数桁, 値の指数表記]`` を返す。"""
    node = shutil.which("node")
    assert node is not None, (
        "node が見つからない。本検定は JS 実装を**実際に走らせて**突き合わせるため、"
        "node 無しでは成立しない（スキップすると同値性の保証が無音で消える）"
    )
    literals = ", ".join(repr(v) for v in values)
    harness = (
        f"{_js_decimals_of_source()}\n"
        f"const VALUES = [{literals}];\n"
        # 桁数と**評価された倍精度そのもの**を返す。後者は「Python と JS が同じ数を見ている」ことの証明。
        "console.log(JSON.stringify(VALUES.map((v) => [decimalsOf(v), v.toExponential(20)])));\n"
    )
    proc = subprocess.run(  # noqa: S603 - 生成物は本リポジトリ内のソースのみ
        [node, "--input-type=module", "-e", harness],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, f"node の実行に失敗した: {proc.stderr.strip()}"
    return json.loads(proc.stdout)


# --------------------------------------------------------------------------------------
# 検定
# --------------------------------------------------------------------------------------
def test_JSとPythonが同じ倍精度値を見ている():
    """突き合わせの前提（リテラルが両言語で同じ数に評価される）を先に固定する。"""
    values = _values()
    for value, (_, exponential) in zip(values, _run_js(values), strict=True):
        assert float(exponential) == value, (
            f"JS 側が別の数を見ている: python={value!r} / js={exponential}"
        )


def test_刻みの小数桁の規則がJSとPythonで一致する():
    """同一規則の 2 実装（別アルゴリズム）が代表値集合で完全一致する（🟡-5）。"""
    py_decimals_of = _py_decimals_of()
    values = _values()
    js = [row[0] for row in _run_js(values)]
    mismatched = {
        value: (js_digits, py_decimals_of(value))
        for value, js_digits in zip(values, js, strict=True)
        if js_digits != py_decimals_of(value)
    }
    assert mismatched == {}, (
        "JS `decimalsOf` と Python `decimals_of` が食い違う {値: (JS, Python)}: "
        f"{mismatched}"
    )


def test_代表値に指定された境界がすべて含まれる():
    """検定が空振り・値の取りこぼしで骨抜きにならないことを固定する。"""
    missing = [v for v in _REQUIRED if v not in _values()]
    assert missing == [], f"代表値が落ちている: {missing}"
    assert len(_values()) >= len(_REQUIRED), "代表値集合が縮んでいる"


def test_JS側はdecimalsOfをexportしていない():
    """export されたら import して突き合わせるべきで、切り出しは不要になる（記述と実装の同期）。"""
    src = JS_QUANTIZE.read_text(encoding="utf-8")
    assert "export function decimalsOf" not in src, (
        "`decimalsOf` が export された。本検定は切り出しでなく import へ切り替えること"
    )
