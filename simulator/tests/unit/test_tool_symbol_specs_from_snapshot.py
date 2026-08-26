"""検出ゲート: 本番ツール（Composition Root）の銘柄仕様が供給元スナップショットと一致する。

由来: ISSUE-445 RC-1 の**本番コード残渣**の是正（2026-08-26）。段階 2〜3-E2 では fixture・
カタログ・突合テストの権威を供給元スナップショット
（``marketdata/symbol_specs/OANDA-Japan-MT5-Live/JP225.json``・MT5 端末から機械取得）へ
移したが、**テスト外の実行経路**である下記 2 ツールには人が書いたリテラルが残っていた:

    - ``simulator/tools/export_trade_markers.py``      … ``contract_size=10.0`` / ``leverage=100.0``
    - ``simulator/report_ui/tools/export_report_payload.py`` … ``contract_size=10.0``

**2026-08-26 追記（最後の残渣）**: 実行入口 CLI 3 件（``run_is_oos_cli`` / ``optimize_cli`` /
``walk_forward_cli``）は同じ値を argparse の**既定値**として持っていた。既定値は台帳と同じ
（人が書いた値が権威になる）うえ、コマンド行に現れないぶん台帳より見えない。走査に 4 形目
（``add_argument("--contract-size", …, default=10.0)``）を足し、3 CLI と新設の単一ソース
``simulator/tools/symbol_spec_args.py`` を走査対象に加えた。**値の一致**の側は
``simulator/tests/unit/test_cli_symbol_spec_args.py`` が担う（CLI は引数解決を経るため、
組み上がったパーサと解決結果で見る必要がある）。

**なぜ値の差し替えでは足りないか（RC-1）**: 誤りの本体は「値が 1 つ違ったこと」ではなく
**人が値を書ける構造**である。よって本ゲートは (1) 値の一致 と (2) ソース上に数値リテラルが
無いこと の両方を固定する。(2) が無ければ、同じ誤りが同じ場所に再発できる。

固定する不変条件:

    1. 両ツールが ``build_interactor`` へ渡す銘柄仕様 8 項目が供給元スナップショットと一致する。
       **期待値をテスト側にリテラルで書かない**（比較相手は ``load_spec_fields`` が引く）。
    2. 走査対象 6 ファイル（両ツール + CLI 3 件 + ``symbol_spec_args``）のソースに銘柄仕様
       8 キーへの数値リテラル束縛が 1 つも無い（AST 走査）。keyword 引数・単純代入
       （クラス属性を含む）・dict リテラル・argparse 既定値の 4 形を検出する。
    3. ``export_trade_markers`` の EA 入力 lot が供給元の volume 制約を満たす
       （``domain.order.Order.validate`` ＝ 実行時に発注を弾く当のコードで判定する）。

    3 について ``export_report_payload``（``StopEntryProbe_EA``）を対象にしないのは、当該戦略が
    原典 ``2026-04_stop-probe/ea.mq5`` の ``NormalizeLot`` を移植済み（ISSUE-445 段階 3-B）で
    あり、EA 入力 ``lot=0.1`` は**実行時に** ``volume_min`` へ持ち上がるためである（実測:
    是正後の実走で ``report.json`` の ``segments.*.trades[].volume`` が 1.0 になる）。
    ``TC24051901`` は原典 ``.mq5`` を持たず ``cfg["lot_size"]`` を素通しする（段階 3-B の申し送り）
    ため、Composition Root 側で発注可能な lot を供給しなければ ``InvalidPriceError`` で落ちる。

負の対照を各検定に対で置く（落ちないゲートは無価値であるため）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from marketdata.symbol_spec_snapshot import (
    OANDA_JAPAN_MT5_LIVE,
    SPEC_FIELD_SOURCES,
    load_spec_fields,
)
from simulator.domain.exceptions import InvalidPriceError
from simulator.domain.order import Order
from simulator.report_ui.tools import export_report_payload as report_tool
from simulator.tools import (
    export_trade_markers as markers_tool,
    optimize_cli,
    run_is_oos_cli,
    symbol_spec_args,
    walk_forward_cli,
)
from simulator.tools.symbol_spec_args import spec_option
from simulator.usecase.models import SymbolSpec

_SYMBOL = "JP225"
#: 銘柄仕様のキー集合は対応表が唯一源（ここに列挙を書き写さない）。
_SPEC_KEYS = tuple(SPEC_FIELD_SOURCES)
#: CLI オプション名 → フィールド名。導出規則の所有者は ``symbol_spec_args.spec_option``。
_SPEC_OPTIONS = {spec_option(key): key for key in _SPEC_KEYS}

#: 是正前に各ツールが直書きしていた値のうち、供給元と**食い違っていた**もの。
#: 期待値ではなく負の対照（「一致検定が空虚でない」ことの実証）として持つ。
_REMOVED_LITERALS = {
    "contract_size": 10.0,
    "volume_min": 0.01,
    "volume_max": 100.0,
    "volume_step": 0.01,
    "stops_level": 0,
    "leverage": 100.0,
}


@pytest.fixture(scope="module")
def spec() -> dict:
    """供給元スナップショットの銘柄仕様 8 項目（唯一の権威）。"""
    return load_spec_fields(OANDA_JAPAN_MT5_LIVE, _SYMBOL)


# --- 1. 値の一致 ---------------------------------------------------------------------


def test_markers_tool_meta_takes_the_spec_from_the_snapshot(spec):
    meta = markers_tool._meta("dummy.csv", "TC24051901")
    assert {key: meta[key] for key in _SPEC_KEYS} == spec


def test_report_tool_common_takes_the_spec_from_the_snapshot(spec):
    assert {key: report_tool.COMMON[key] for key in _SPEC_KEYS} == spec


def test_report_tool_uc_spec_view_takes_the_spec_from_the_snapshot(spec):
    """UC へ渡す仕様ビュー（``_Spec``）も同じ供給元から引く（同一ファイル内の二重管理を作らない）。"""
    view = report_tool._Spec()
    keys = ("point_size", "digits", "stops_level")
    assert {key: getattr(view, key) for key in keys} == {key: spec[key] for key in keys}


def test_snapshot_disagrees_with_the_removed_literals(spec):
    """**負の対照**: 撤去したリテラルは供給元と一致しない（上の一致検定は空虚でない）。"""
    disagreeing = {
        key: value for key, value in _REMOVED_LITERALS.items() if spec[key] != value
    }
    assert disagreeing == _REMOVED_LITERALS


# --- 2. ソースに数値リテラルが無い（人が値を書けない構造）------------------------------

_SOURCES = {
    "export_trade_markers": Path(markers_tool.__file__),
    "export_report_payload": Path(report_tool.__file__),
    # 実行入口 CLI 3 件と、その引数宣言・解決を担う単一ソース（2026-08-26 に追加）。
    # 値の一致は `test_cli_symbol_spec_args.py` が組み上がったパーサ側で見る。ここは
    # 「ソース上に人が書いた数値が無い」ことだけを、既存 2 ツールと同じ走査で固定する。
    "run_is_oos_cli": Path(run_is_oos_cli.__file__),
    "optimize_cli": Path(optimize_cli.__file__),
    "walk_forward_cli": Path(walk_forward_cli.__file__),
    "symbol_spec_args": Path(symbol_spec_args.__file__),
}


def _spec_literals(source: str) -> "list[str]":
    """銘柄仕様 8 キーへ数値リテラルを直に束ねている箇所を列挙する（AST 走査）。

    検出する 4 形（是正前に実在した形をすべて含む）:
        ``f(contract_size=10.0)`` / ``stops_level = 0``（クラス属性を含む）/
        ``{"leverage": 100.0}`` / ``add_argument("--contract-size", …, default=10.0)``。
    4 形目は本番 CLI 3 件に残っていた argparse 既定値（2026-08-26 に撤去）。オプション名は
    ``symbol_spec_args.spec_option``（フィールド名からの導出）で同定する——綴りをここに
    書き写すと、導出規則を変えたときに走査だけが黙って外れる。
    文字列（docstring・コメント相当）は対象外。値が ``bool`` のものも数値扱いしない。
    """
    found: "list[str]" = []

    def numeric(node: ast.AST) -> bool:
        return isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(
            node.value, bool
        )

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.keyword) and node.arg in _SPEC_KEYS and numeric(node.value):
            found.append(f"L{node.value.lineno}: {node.arg}={node.value.value!r}")
        elif isinstance(node, ast.Assign) and numeric(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in _SPEC_KEYS:
                    found.append(f"L{node.lineno}: {target.id} = {node.value.value!r}")
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and key.value in _SPEC_KEYS
                    and numeric(value)
                ):
                    found.append(f"L{value.lineno}: {key.value!r}: {value.value!r}")
        elif isinstance(node, ast.Call):
            option = next(
                (
                    arg.value
                    for arg in node.args
                    if isinstance(arg, ast.Constant) and arg.value in _SPEC_OPTIONS
                ),
                None,
            )
            if option is not None:
                for kw in node.keywords:
                    if kw.arg == "default" and numeric(kw.value):
                        found.append(
                            f"L{kw.value.lineno}: {option} default={kw.value.value!r}"
                        )
    return found


@pytest.mark.parametrize("name", sorted(_SOURCES))
def test_tool_sources_hold_no_symbol_spec_literals(name):
    assert _spec_literals(_SOURCES[name].read_text(encoding="utf-8")) == []


def test_scanner_detects_a_reintroduced_literal():
    """**負の対照**: 撤去した 4 形を書き戻すと検出される（走査が機能することの実証）。"""
    assert _spec_literals("build_interactor(contract_size=10.0)")
    assert _spec_literals("class S:\n    stops_level = 0\n")
    assert _spec_literals('COMMON = {"leverage": 100.0}')
    # 4 形目＝argparse 既定値（本番 CLI 3 件に残っていた形・2026-08-26）。
    assert _spec_literals('p.add_argument("--contract-size", type=float, default=10.0)')
    assert _spec_literals('p.add_argument("--stops-level", type=int, default=0)')
    # 供給元から引く形は検出しない（偽陽性を出さない）。
    assert _spec_literals("build_interactor(**load_spec_fields(SERVER, SYMBOL))") == []
    # 既定値を置かない形も検出しない（None は数値でない）。
    assert _spec_literals('p.add_argument("--contract-size", type=float, default=None)') == []
    # 銘柄仕様でないオプションの既定値は対象外（過検出しない）。
    assert _spec_literals('p.add_argument("--lot-size", type=float, default=0.1)') == []


# --- 3. EA 入力 lot が供給元の volume 制約を満たす -------------------------------------


def test_markers_tool_lot_is_orderable_under_the_snapshot_volume_rules(spec):
    """``TC24051901`` は lot を素通しするため、Root が供給する lot 自体が発注可能でなければならない。"""
    meta = markers_tool._meta("dummy.csv", "TC24051901")
    symbol_spec = SymbolSpec(**{key: meta[key] for key in _SPEC_KEYS})
    order = Order(side="buy", kind="market", volume=meta["lot_size"], price=None)
    order.validate(symbol_spec)  # InvalidPriceError が出ないこと＝発注可能


def test_previous_lot_is_rejected_under_the_snapshot_volume_rules(spec):
    """**負の対照**: 従来の ``lot_size=0.1`` は供給元の ``volume_min`` の下で発注不成立。

    是正前の値（0.1）と是正前の ``volume_min``（0.01）が**対で**成立していたことを示す
    （どちらか一方だけを真値へ寄せると実行時に落ちる＝ISSUE-445 の「相殺」と同型）。
    """
    symbol_spec = SymbolSpec(**spec)
    with pytest.raises(InvalidPriceError):
        Order(side="buy", kind="market", volume=0.1, price=None).validate(symbol_spec)
