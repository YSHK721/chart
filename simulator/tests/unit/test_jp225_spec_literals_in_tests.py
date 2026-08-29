"""検出ゲート（段階 A で新設・段階 C で Python 側は解消）: JP225 銘柄仕様リテラルの検出。

由来: ISSUE-445 RC-1。JP225 の ``contract_size`` の真値は供給元スナップショット
``marketdata/symbol_specs/OANDA-Japan-MT5-Live/JP225.json`` の ``trade_contract_size``
＝ **1.0** である。長く書かれていた ``10`` は MT5 レポートに一度も現れない**出所の無い逆算値**。

段階 A（2026-08-26）で、fixture・カタログ・本番ツール・本番 CLI が是正済みなのに
**テストコードに残っていた 13 件**（Python 12 件＋ MT5 レポート JSON 1 件）を
``xfail(strict=True)`` の台帳として固定した（是正より先にゲートを置く規律）。
段階 C（2026-08-26）で Python 12 件を**供給元から引く形へ**是正し、台帳 ``_KNOWN`` は
**空**になった。以後、本ファイルは「JP225 を名乗る誤値がどこにも無い」ことを固定し続ける
**恒久の緑**として機能する（判定は ``test_the_ledger_agrees_with_the_scan_in_both_directions``
が持つ）。残る ``xfail`` は JSON fixture 1 件のみで、これは別裁定（下記）。

**本ファイルが見ないもの（射程の明示）**: 判定は「組み立ての Call kwargs」に限る。
**期待値側に書き写された銘柄仕様**（``assert x.volume_min == 0.1`` の類）は原理的に
検出範囲外であり、それは姉妹ゲート ``test_symbol_spec_expectation_literals_in_tests.py``
が別の判定・別の母集団で見る（ISSUE-445 段階 C の申し送り）。

**``test_tool_symbol_specs_from_snapshot.py`` と別ファイルにした理由**:
向こうの ``_spec_literals`` は**銘柄を問わない**走査（銘柄仕様 8 キーへの数値リテラルを
すべて挙げる）である。テストコードに向けると EURUSD 相当（``contract_size=100000.0``）や
合成仕様（``1.0``）まで挙がり、ISSUE-445 の残渣と区別できない。本ファイルの判定は
**JP225 を名乗る組み立てに限定**した別問いであり、共有できるロジックが無い（複製ではない）。
加えて向こうは本番コードの**恒久**不変条件で全件緑、こちらは JP225 のテストコード側の
残渣を対象とする別の走査であり、対象範囲が違う。

**走査対象を人が列挙しない**: 対象は ``git ls-files '*.py'``（tracked 全件）＋ MT5 レポート
JSON 1 件であり、そこから機械が違反を**発見**する。人が書いた一覧を検査する形にすると、
一覧の取りこぼしがそのまま検出漏れになる。実測（2026-08-26）でこの差は現に出た——先行調査が
手で挙げた 11 件に対し、tracked 全件走査は **13 件**を検出し、``simulator/sim_ui/tests/`` の
2 件が一覧から漏れていた（下記 ``_KNOWN`` 参照）。

固定する不変条件:

    1. tracked なソースのうち、JP225 を名乗る銘柄仕様の組み立てに供給元と食い違う
       ``contract_size`` リテラルを持つものが、**既知の台帳 ``_KNOWN``（段階 C 以降は空）と
       明示除外 ``_EXCLUDED_BY_INTENT`` の外に 1 つも無い**（両方向一致・**恒久の緑**）。
       左が増える＝新規混入、左が減る＝是正済みで台帳から外す合図。どちらも赤にする。
    2. その台帳が**走査結果から導出されていない**（AST で施行）。導出形に戻すと新規混入が
       自動吸収されて検定が空虚になる。
    3. 判定関数が、JP225 を名乗らない組み立て（負の対照・合成データ）を検出しない。
       ``_MUST_NOT_DETECT`` の**実ソースを食わせて** 0 件で示す（一覧に入れていない、
       ではなく検出しないことを実証する）。
    4. 判定関数が、是正後の姿（供給元から引く形／リテラル不在）を検出しない。
    5. MT5 レポート JSON 1 件は**別裁定**として ``xfail(strict=True)`` のまま残す（下記）。

判定は ``_disagreeing_with_the_snapshot`` 1 つに集約し、AST 走査・JSON 走査・負の対照が
同じ関数を呼ぶ（判定を 2 度書かない）。
"""
from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

import pytest

from marketdata.symbol_spec_snapshot import (
    OANDA_JAPAN_MT5_LIVE,
    SPEC_FIELD_SOURCES,
    load_spec_fields,
)

_SYMBOL = "JP225"
#: 銘柄仕様のキー集合は対応表が唯一源（ここに列挙を書き写さない）。
_SPEC_KEYS = frozenset(SPEC_FIELD_SOURCES)
#: 供給元スナップショット＝唯一の権威。期待値をこのファイルに書かない。
_TRUTH = load_spec_fields(OANDA_JAPAN_MT5_LIVE, _SYMBOL)


def _numeric(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    )


def _disagreeing_with_the_snapshot(items) -> "list[str]":
    """``(キー, 値, 表示ラベル)`` の並びから、供給元と食い違う銘柄仕様キーを列挙する。

    **AST 走査と JSON 走査が共有する唯一の判定**（判定を 2 度書かない）。検出の起点は
    ``contract_size`` の食い違い＝ISSUE-445 RC-1 の残渣であり、それが無い組み立ては
    他キーが食い違っていても本ゲートの対象外とする（例: JP225 を名乗る EURUSD 相当の
    合成仕様）。起点が立ったときは、同じ組み立ての食い違いを**全部**返す——
    ISSUE-445 の失敗モードは「2 つの誤りの相殺」であり、片方だけ真値へ寄せると壊れる。
    """
    disagreeing = [
        (key, value, label)
        for key, value, label in items
        if key in _SPEC_KEYS and value != _TRUTH[key]
    ]
    if not any(key == "contract_size" for key, _, _ in disagreeing):
        return []
    return [f"{label}={value!r}" for _, value, label in disagreeing]


def _jp225_settings_literals(doc: dict) -> "list[str]":
    """MT5 レポート JSON（``expected/report.json``）の ``settings`` を同じ判定にかける。

    Python の AST では走査できないため入口だけを分けるが、**判定は共有**する。
    """
    settings = doc.get("settings") or {}
    if settings.get("symbol") != _SYMBOL:
        return []
    return _disagreeing_with_the_snapshot(
        (key, value, f"settings.{key}")
        for key, value in settings.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    )


def _jp225_spec_literals(source: str) -> "list[str]":
    """JP225 を名乗る銘柄仕様の組み立ての ``contract_size`` リテラルを列挙する（AST 走査）。

    「JP225 を名乗る」の同定は次のいずれか:
        1. 当該 Call 自身の引数に文字列 ``"JP225"`` が現れる（``symbol="JP225"`` 等）。
        2. 当該 Call を囲む ``def`` の名前が ``jp225`` を含む（``_jp225_spec()`` 等）。
    """
    found: "list[str]" = []

    def visit(node: ast.AST, enclosed: bool) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            enclosed = enclosed or _SYMBOL.lower() in node.name.lower()
        if isinstance(node, ast.Call):
            names_jp225 = enclosed or any(
                isinstance(arg, ast.Constant) and arg.value == _SYMBOL
                for arg in [*node.args, *(kw.value for kw in node.keywords)]
            )
            if names_jp225:
                found.extend(
                    _disagreeing_with_the_snapshot(
                        (kw.arg, kw.value.value, f"L{kw.value.lineno}: {kw.arg}")
                        for kw in node.keywords
                        if kw.arg in _SPEC_KEYS and _numeric(kw.value)
                    )
                )
        for child in ast.iter_child_nodes(node):
            visit(child, enclosed)

    visit(ast.parse(source), False)
    return found


# --- 判定関数の振る舞い ----------------------------------------------------------------


def test_scanner_detects_a_jp225_named_contract_size_literal():
    """正: JP225 を名乗る組み立ての ``contract_size`` リテラルを検出する。"""
    assert _jp225_spec_literals(
        'build_interactor(symbol="JP225", contract_size=10.0)'
    ) == ["L1: contract_size=10.0"]


def test_scanner_ignores_a_construction_that_does_not_name_jp225():
    """**負の対照**: 銘柄を名乗らない組み立ては検出しない（合成仕様・他銘柄を巻き込まない）。"""
    assert _jp225_spec_literals('SymbolSpec(contract_size=10.0)') == []
    assert _jp225_spec_literals(
        'build_interactor(symbol="EURUSD", contract_size=100000.0)'
    ) == []


def test_scanner_detects_a_construction_named_jp225_by_its_enclosing_function():
    """正: 銘柄を引数で名乗らず、**囲む関数名**で JP225 を名乗る形も検出する。

    ``test_run_backtest.py`` の ``_jp225_spec()`` がこの形（``SymbolSpec(...)`` に
    ``symbol=`` が無く、関数名と docstring だけが JP225 を名乗る）。
    """
    assert _jp225_spec_literals(
        "def _jp225_spec():\n    return SymbolSpec(contract_size=10.0)\n"
    ) == ["L2: contract_size=10.0"]


def test_scanner_ignores_a_construction_whose_enclosing_function_is_symbol_agnostic():
    """**負の対照**: 銘柄を名乗らない helper は検出しない。

    ``test_stop_entry_probe.py`` の ``_spec()`` がこの形。同ファイルは ``contract_size=10.0``
    を持つが、``margin=1*10*100/10=100`` の手計算で ``stop_out`` を成立させる**テストの都合**
    であり、ISSUE-445 の残渣ではない（真値に寄せると当該検定が発火しなくなる）。
    """
    assert _jp225_spec_literals(
        "def _spec():\n    return SymbolSpec(contract_size=10.0)\n"
    ) == []


def test_scanner_ignores_a_jp225_literal_that_already_agrees_with_the_snapshot():
    """**負の対照**: 供給元と一致する値は残渣ではない（是正済みを赤にしない）。

    ``test_ea_factory_registry.py`` の ``_comma_kwargs`` がこの形（``symbol="JP225"`` を
    名乗るが ``contract_size=1.0``＝真値）。判定は「JP225 を名乗ること」ではなく
    「**供給元と食い違うこと**」で行う。
    """
    assert _TRUTH["contract_size"] == 1.0  # 前提の明示（真値は供給元が決める）
    assert _jp225_spec_literals(
        'build_interactor(symbol="JP225", contract_size=1.0)'
    ) == []


def test_scanner_reports_every_disagreeing_key_of_a_flagged_construction():
    """正: 検出した組み立てについて、**食い違う 8 キーすべて**を挙げる。

    ISSUE-445 の失敗モードは「2 つの誤りの相殺」であり、``contract_size`` だけを真値へ
    寄せると ``volume_min`` との組が壊れて実行時に落ちる（ISSUE.md の実測: OOS trades
    2438 → 4877）。次段階の担当者が**対で**是正できるよう、赤のメッセージに同じ組み立て
    の食い違いを全部載せる。供給元と一致するキー（``digits`` / ``point_size`` /
    ``leverage``）は載せない。
    """
    found = _jp225_spec_literals(
        'dict(symbol="JP225", contract_size=10.0, volume_min=0.01, stops_level=0,'
        " digits=1, point_size=0.1, leverage=10.0)"
    )
    assert found == [
        "L1: contract_size=10.0",
        "L1: volume_min=0.01",
        "L1: stops_level=0",
    ]


def test_json_scanner_detects_a_jp225_named_settings_mapping():
    """正: JSON の ``settings`` も同じ判定にかける（AST では走査できないため）。

    ``ma_slope_jp225_202601/expected/report.json`` がこの形。走査対象から黙って落とすと
    「Python だけ直して JSON に残る」が起きるため、同じ規律で拾う。
    """
    doc = {"settings": {"symbol": "JP225", "contract_size": 10, "digits": 1}}
    assert _jp225_settings_literals(doc) == ["settings.contract_size=10"]


def test_json_scanner_ignores_a_settings_mapping_of_another_symbol():
    """**負の対照**: 他銘柄の ``settings`` は検出しない。"""
    doc = {"settings": {"symbol": "EURUSD", "contract_size": 100000}}
    assert _jp225_settings_literals(doc) == []


# --- 走査対象（tracked なソース全件）----------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]

#: JSON fixture（MT5 レポートの期待値）。Python の AST では走査できないため入口を分ける。
_REPORT_JSON = "simulator/tests/fixtures/mt5/ma_slope_jp225_202601/expected/report.json"


def _tracked(pattern: str) -> "list[str]":
    """git の index に載っているファイル（＝是正コミットが触れる対象）を列挙する。

    ``rglob`` ではなく index を引くのは、untracked な機械生成物・手元の作業記録を
    走査から外すためである。実測（2026-08-26）: ``rglob`` だと
    ``simulator/tests/confirmation/**`` の未追跡スクリプト 15 件が混ざる。
    先例: ``tools/tests/test_no_absolute_symlinks.py``。
    """
    out = subprocess.run(
        ["git", "-C", str(_REPO_ROOT), "ls-files", pattern],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.split()


def _scan_tracked_python() -> "dict[str, list[str]]":
    found: "dict[str, list[str]]" = {}
    for rel in _tracked("*.py"):
        try:
            hits = _jp225_spec_literals(
                (_REPO_ROOT / rel).read_text(encoding="utf-8")
            )
        except (SyntaxError, UnicodeDecodeError, FileNotFoundError):
            continue
        if hits:
            found[rel] = hits
    return found


def _scan_report_json() -> "list[str]":
    path = _REPO_ROOT / _REPORT_JSON
    return _jp225_settings_literals(json.loads(path.read_text(encoding="utf-8")))


#: 走査結果は collection 時に 1 回だけ取る（xfail の reason に実測を載せるため）。
_FOUND = _scan_tracked_python()
_FOUND_JSON = _scan_report_json()

#: **検出されるが是正対象でない**もの。JP225 を名乗るが、数値がテストの成立条件そのもの
#: であり、真値へ寄せると検定の意図が壊れる。機械では判別できないため明示除外する。
_EXCLUDED_BY_INTENT = {
    "simulator/tests/integration/test_sizing_estimated_entry_price.py": (
        "合格閾値が volume_step であり、真値（1.0）にすると閾値が 10 倍に緩んで"
        "検定が空虚化する。是正には閾値の設計変更が要る（値の差し替えでは済まない）。"
    ),
}

#: 既知の違反＝**所在の台帳**（値の期待値ではない）。
#:
#: **段階 C（2026-08-26）で空になった**。段階 A が載せていた Python 12 件は、いずれも
#: ``**load_spec_fields(OANDA_JAPAN_MT5_LIVE, "JP225")`` で供給元から引く形へ是正され、
#: 走査に掛からなくなった（外し忘れは XPASS(strict) で赤になる仕組みだったため、
#: 外すこと自体が是正の完了条件でもある）。以後この空の台帳と
#: ``test_the_ledger_agrees_with_the_scan_in_both_directions`` の組が、
#: 「JP225 を名乗る誤値は明示除外の 1 件を除いてどこにも無い」を**恒久の緑**で固定する。
#: 段階 A が載せていた 12 件（是正済み・履歴として残す。再発時はここへ書き戻すのではなく
#: 是正すること）:
#:     sim_ui/tests/unit/test_run_options_api_controller.py
#:     sim_ui/tests/unit/test_symbol_spec_catalog.py
#:     tests/integration/test_composition_ma_slope.py
#:     tests/integration/test_ea_factory_selection_rule.py
#:     tests/integration/test_ea_indicator_series_accessor.py
#:     tests/integration/test_is_oos_stop_probe.py
#:     tests/integration/test_marketdata_window_mt5_path.py
#:     tests/integration/test_optimize_sp1_degenerate.py
#:     tests/integration/test_walk_forward_integration.py
#:     tests/unit/test_ea_factory_registry.py（`_mt5_kwargs` のみ）
#:     tests/unit/test_is_oos_barmode_index.py
#:     tests/unit/test_run_backtest.py（`_jp225_spec()`）
#:
#: **走査結果から導出してはならない**（空になっても同じ）。導出すると新しい違反ファイルが
#: collection 時に自動で台帳へ入って赤にならずに吸収される（＝ISSUE-445 の失敗モードの
#: 再生産）。人が書き下し、走査結果との一致を検定で見る。この規律自体を
#: ``test_the_ledger_is_written_down_and_not_derived_from_the_scan`` が AST で固定する。
_KNOWN = ()


def _clears() -> str:
    return (
        "解消条件: 当該 JP225 仕様の値が供給元"
        " load_spec_fields(OANDA_JAPAN_MT5_LIVE, 'JP225') と一致したとき。"
        "そのとき本検定は XPASS(strict) で赤に転じるので、xfail マーカーごと撤去すること。"
    )


# --- 1. ゲートの自己検査（空振りしていないこと）-----------------------------------------


def test_the_scan_reaches_the_whole_tracked_tree():
    """走査が実際に木を舐めていること。空振りする走査で「違反 0」を主張しない。"""
    tracked = _tracked("*.py")
    assert len(tracked) > 1_000
    # 走査が index を引けていること（`git ls-files` が空を返しても緑にしない）。
    assert all(rel.endswith(".py") for rel in tracked)


def test_the_ledger_is_written_down_and_not_derived_from_the_scan():
    """``_KNOWN`` が**走査結果から導出されていない**こと（自己検査・最重要）。

    ``_KNOWN = set(_FOUND) - set(_EXCLUDED)`` のように導出すると、新しい違反ファイルは
    collection 時に自動で台帳へ入って ``xfail`` が付き、**赤にならずに吸収される**。
    それは ISSUE-445 の失敗モード（誤りが 2 か月誰にも気付かれない）そのものである。
    台帳は人が書き下し、走査結果との**一致**を検定で見る——この向きでないと
    「新規混入を捕まえる」検定が構造的に空虚になる。

    段階 C で台帳は**空のタプル**になったが、本検定の趣旨は変わらない。空であることと
    導出であることは別であり、``_KNOWN = tuple(...)`` / ``set(_FOUND) - ...`` はいずれも
    ``ast.Tuple`` ではないためここで赤になる（実測で確認）。
    """
    module = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    assigned = [
        node.value
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "_KNOWN" for t in node.targets
        )
    ]
    assert len(assigned) == 1, "_KNOWN の代入が 1 つでない"
    value = assigned[0]
    assert isinstance(value, ast.Tuple), "_KNOWN は tuple リテラルでなければならない"
    assert all(
        isinstance(e, ast.Constant) and isinstance(e.value, str) for e in value.elts
    ), "_KNOWN の要素は文字列リテラルでなければならない（式で導出しない）"


def test_the_ledger_agrees_with_the_scan_in_both_directions():
    """台帳と走査結果が**完全一致**すること（段階 C 以降はこれが**恒久の緑**）。

    片側包含では足りない。左が増える＝新規混入（台帳に足す前に赤で気付く）、
    左が減る＝是正済み（台帳から外す合図）。どちらも赤にする。

    段階 C で ``_KNOWN`` が空になったため、本検定は
    「tracked な Python のうち JP225 を名乗る誤値を持つのは ``_EXCLUDED_BY_INTENT`` の
    1 件だけ」＝**是正した 12 件が是正済みのままであること**を固定し続ける。
    """
    assert set(_FOUND) == set(_KNOWN) | set(_EXCLUDED_BY_INTENT), (
        "台帳と走査結果が食い違う。\n"
        f"  走査にあって台帳・除外に無い（新規混入）: "
        f"{sorted(set(_FOUND) - set(_KNOWN) - set(_EXCLUDED_BY_INTENT))}\n"
        f"  台帳にあって走査に無い（是正済み・台帳から外す）: "
        f"{sorted(set(_KNOWN) - set(_FOUND))}"
    )


def test_the_excluded_source_is_actually_detected_by_the_scanner():
    """除外が**意図的**であること。検出されないから外れているのではない。

    これが落ちるときは、当該ファイルが是正されたか形が変わったかであり、
    ``_EXCLUDED_BY_INTENT`` の記述ごと見直す合図である。
    """
    for rel in _EXCLUDED_BY_INTENT:
        assert _FOUND.get(rel), f"{rel} は走査に掛からない。除外の記述が古い。"


# --- 2. 既知違反の固定（Python 側は段階 C で空・残るは JSON fixture 1 件）-----------------
#
# 段階 A の ``test_tracked_source_holds_no_jp225_spec_literals``（``_KNOWN`` を
# ``xfail(strict=True)`` で 1 件ずつ固定する parametrize）は、段階 C で ``_KNOWN`` が
# 空になったため撤去した。同じ判定は
# ``test_the_ledger_agrees_with_the_scan_in_both_directions`` が**両方向**で持っており
# （空の台帳＋明示除外 1 件 == 走査結果）、2 つ置くと判定の二重記述になる。


@pytest.mark.xfail(
    strict=True,
    reason=(
        f"ISSUE-445 段階 A の既知違反（段階 C の範囲外・**別裁定**）{_REPORT_JSON}: "
        f"{'; '.join(_FOUND_JSON)}。"
        "この fixture の deals[] には vol フィールドが無く、単独では実約定 volume を"
        "確定できない（証明できるのは積 lot × contract_size = 0.1 × 10 = 1.0 のみ）。"
        "是正は lot と contract_size を**対で**動かす必要がある。さらに本ケースは "
        "case.yaml を持たない不完全ケースで tracked なテストからの参照が 0 件であり、"
        "MT5 レポートを写した golden fixture としてテストコードの定数とは性質が違う。"
        + _clears()
    ),
)
def test_report_json_holds_no_jp225_spec_literals():
    assert _FOUND_JSON == [], (
        f"{_REPORT_JSON} の settings が供給元と食い違う:\n  "
        + "\n  ".join(_FOUND_JSON)
    )


# --- 3. 新規混入の検出（こちらは緑・恒久）------------------------------------------------


# --- 4. 判定関数が「含めてはならないもの」を拾わないこと（機械的な実証）-------------------

#: 走査に掛かってはならない実ファイルと、掛からない**構造上の**理由。
#: 「走査対象リストに入れていない」ではなく、**判定関数に実ソースを食わせて 0 件**で示す。
_MUST_NOT_DETECT = {
    "simulator/tests/unit/test_tool_symbol_specs_from_snapshot.py": (
        "負の対照 _REMOVED_LITERALS。Call ではなく module 直下の dict であり、"
        "JP225 を名乗る組み立てではない"
    ),
    "simulator/tests/unit/test_cli_symbol_spec_args.py": (
        "負の対照 _REMOVED_DEFAULTS。同上（撤去済みでソースから取れず、ここが唯一の記録）"
    ),
    "simulator/tests/unit/test_stop_entry_probe.py": (
        "_spec() は銘柄を名乗らない。contract_size=10.0 は margin=1*10*100/10=100 の"
        "手計算で stop_out を成立させるテストの都合"
    ),
    "simulator/tests/integration/test_hedged_margin_multi.py": (
        "contract_size=CONTRACT_SIZE は Name であって数値リテラルではない"
    ),
    "simulator/tests/unit/test_trade_markers_presenter.py": (
        "JP225 を名乗る組み立てが無い。期待値 622.0 に *10 が焼き込まれている"
    ),
}


@pytest.mark.parametrize("rel", sorted(_MUST_NOT_DETECT))
def test_scanner_does_not_detect_the_sources_that_must_stay_out(rel):
    source = (_REPO_ROOT / rel).read_text(encoding="utf-8")
    assert _jp225_spec_literals(source) == [], (
        f"{rel} を検出した（偽陽性）。除外理由: {_MUST_NOT_DETECT[rel]}"
    )


def test_scanner_does_not_detect_the_corrected_form():
    """**是正後の姿**を検出しないこと（恒久）。

    次段階が到達すべき形——供給元から引き、数値リテラルを置かない——を食わせて 0 件を
    固定する。これが無いと「是正したのに赤が消えない」ゲートになりうる。
    """
    assert _jp225_spec_literals(
        'build_interactor(symbol="JP225",'
        ' **load_spec_fields(OANDA_JAPAN_MT5_LIVE, "JP225"))'
    ) == []
    assert _jp225_spec_literals(
        "def _jp225_spec():\n"
        "    return SymbolSpec(**load_spec_fields(OANDA_JAPAN_MT5_LIVE, 'JP225'))\n"
    ) == []
    assert _jp225_settings_literals(
        {"settings": {"symbol": "JP225", **_TRUTH}}
    ) == []
