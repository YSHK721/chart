"""配信トポロジ台帳（common/core_web_topology.json）の突合（ISSUE-504 抜本要素 (ii)）。

## 何を守るか

「どの core がどこから配信し、どこへフォールバックするか」という **1 つの事実**の所有者を
1 人に保つ。是正前はこの事実が 4 つの Composition Root へ同じリテラルで写されており
（dashboard / replay / sim / sim jobs）、共有根を動かすには閉じているはずの束縛点 4 枚を
同時に開いて直す必要があった（OCP 違反）。しかも 3 枚だけ直しても残る 1 core は URL 同一の
まま 404 になり、症状は「特定の画面でだけモジュールが読めない」という遠い形で出る。

## 2 つの表明

- 台帳の core 集合とモード定義表の core 集合が**両方向で**一致する。片側だけに足すと Red。
  モードを 1 つ足して台帳を忘れると、その core は配信検定（JS 側）の走査対象から無音で
  外れる——列挙は新規を永久に検出しない、という js_layer_guard.mjs 冒頭の規律と同型。
- 4 つの Composition Root が解決する配信根・共有根が台帳由来の値と一致し、かつ main 層の
  ソースに配信根のリテラルが 1 つも無い（第 2 の所有者が復活していない）。

## モード表の読み取りについて

表の書字形式を知る口は unified_ui/tests/mode_table_source.py ただ 1 つである（ISSUE-502
D-11 の Python 対応物）。ここで正規表現を書き直すと**第 3 のパーサ**を作ることになり、
是正しようとしている欠陥（同じ事実の複数所有）を検定側で再生産する。パッケージ境界を
越えるため import は経路指定で行う（sys.path は触らない）。
"""
from __future__ import annotations

import ast
import importlib.util
import subprocess
from pathlib import Path

import pytest

from common import core_web_topology
from common.core_web_topology import (
    CORE_NAMES,
    LEDGER_PATH,
    NoFallbackRootError,
    fallback_roots,
    primary_fallback_root,
    scan_roots,
    web_root,
)

_ROOT = Path(__file__).resolve().parents[2]

#: 配信検定が読むフォールバック依存の凍結台帳（所有者は統合層の検定）。
_FALLBACK_LEDGER = (
    _ROOT / "unified_ui" / "web" / "tests" / "served_import_fallback_ledger.json"
)


def _mode_table_source():
    """モード定義表の唯一の読み取り口を経路指定で読み込む（第 2 のパーサを作らない）。"""
    path = _ROOT / "unified_ui" / "tests" / "mode_table_source.py"
    spec = importlib.util.spec_from_file_location("mode_table_source", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _main_layer_sources() -> "list[Path]":
    """main 層の production ソース（**列挙ではなく構造から**導く）。

    ディレクトリ名の表を持つと、新しい main が生まれた日にその 1 つだけが無検査になる。
    """
    return sorted(
        path
        for path in _ROOT.rglob("main/**/*.py")
        if "node_modules" not in path.parts
        and "tests" not in path.relative_to(_ROOT).parts
        and ".git" not in path.parts
    )


def _code_string_constants(path: Path) -> "frozenset[str]":
    """docstring を除いた文字列定数の集合。

    docstring は「何が既定か」を説明する文章であり、経路の所有者ではない。文章まで禁じると
    説明が書けなくなるので、見るのは**コードが組み立てに使う値**だけにする。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(
                body[0].value, ast.Constant
            ) and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    return frozenset(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    )


# --------------------------------------------------------------- R2-1 台帳突合
def test_the_ledger_covers_exactly_the_cores_of_the_mode_table() -> None:
    """台帳の core 集合とモード定義表の core 集合が両方向で一致する。"""
    front = _mode_table_source().mode_ids()

    assert front != frozenset()
    assert frozenset(CORE_NAMES) == front


def test_the_scan_roots_add_the_integration_layer_to_the_cores() -> None:
    """走査根は core の配信根＋統合層の配信根（統合層は core ではないので台帳の別欄）。

    統合層はモード表に prefix を持たない（表はモードの表であって配信根の表ではない）が、
    配信される JS は同じ規則で解決される。走査から外すと unified_ui だけが無検査になる。
    """
    roots = scan_roots(_ROOT)

    assert len(roots) == len(CORE_NAMES) + 1
    assert all(path.is_dir() for path in roots.values()), roots


def test_the_ledger_files_are_not_excluded_from_version_control() -> None:
    """台帳が版管理から漏れていないこと。

    リポジトリの無視規則には拡張子の一括指定（データファイル向け）が在り、台帳はそれに
    巻き込まれうる。巻き込まれると clean clone で**台帳ファイルごと消える**——読取口は
    起動時に落ち、配信検定は「凍結 0 件」ではなくファイル不在で実行できなくなる。症状は
    「別のチェックアウトでだけ起動しない」という遠い形で出るので、機械で塞ぐ。
    """
    ledgers = [LEDGER_PATH, _FALLBACK_LEDGER]
    ignored = subprocess.run(
        ["git", "check-ignore", *(str(path) for path in ledgers)],
        cwd=_ROOT, capture_output=True, text=True, check=False,
    )

    assert all(path.is_file() for path in ledgers), ledgers
    assert ignored.stdout == ""


# ------------------------------------------------ R2-2 第 2 の所有者が居ないこと
@pytest.mark.parametrize(
    "builder, core, attribute",
    [
        ("dashboard_ui.main.composition_root:build_dashboard_app", "dashboard", "web_dir"),
        ("dashboard_ui.main.composition_root:build_dashboard_app", "dashboard",
         "shared_js_root"),
        ("simulator.replay_ui.main.composition_root:build_replay_app", "replay",
         "shared_js_root"),
        ("simulator.sim_ui.main.composition_root:build_sim_app", "sim", "shared_js_root"),
        ("simulator.sim_ui.main.composition_root_jobs:build_sim_job_app", "sim",
         "shared_js_root"),
    ],
)
def test_the_composition_root_resolves_the_ledger_path(
    builder: str, core: str, attribute: str
) -> None:
    """既定で組んだアプリの配信根・共有根が台帳由来の値と一致する。"""
    module_name, function_name = builder.split(":")
    module = importlib.import_module(module_name)
    app = getattr(module, function_name)()
    expected = (
        web_root(core, _ROOT) if attribute == "web_dir"
        else fallback_roots(core, _ROOT)[0]
    )

    assert getattr(app, attribute) == expected


def test_no_main_layer_source_owns_a_serving_root_literal() -> None:
    """main 層のコードに配信根のリテラルが 1 つも無い（第 2 の所有者の不在）。

    判定は台帳から導く: 台帳の配信根 `a/b/web` の全セグメントが 1 ファイルの文字列定数に
    そろって現れたら、そのファイルは同じ経路を自分で組み立てている。
    """
    segments = [
        frozenset(str(path.relative_to(_ROOT)).split("/"))
        for path in scan_roots(_ROOT).values()
    ]
    offenders = {
        str(path.relative_to(_ROOT))
        for path in _main_layer_sources()
        for wanted in segments
        if wanted <= _code_string_constants(path)
    }

    assert offenders == set()


def test_the_serving_root_detector_sees_a_hand_written_literal(tmp_path: Path) -> None:
    """検出力の自己検査（上の検定が空振りしないこと）。

    台帳の 1 行を手書きで組み立てたソースを合成し、検出器がそれを拾うことを見る。
    """
    root = web_root("dashboard", _ROOT).relative_to(_ROOT)
    source = tmp_path / "sample.py"
    source.write_text(
        "shared = root"
        + "".join(f' / "{segment}"' for segment in str(root).split("/"))
        + "\n",
        encoding="utf-8",
    )

    constants = _code_string_constants(source)

    assert frozenset(str(root).split("/")) <= constants


def test_the_serving_root_detector_ignores_prose_in_docstrings(tmp_path: Path) -> None:
    """誤検出しないこと（説明文の中の経路は所有ではない）。"""
    root = web_root("dashboard", _ROOT).relative_to(_ROOT)
    source = tmp_path / "sample.py"
    source.write_text(f'"""既定は {root} である。"""\n', encoding="utf-8")

    constants = _code_string_constants(source)

    assert frozenset(str(root).split("/")) - constants != frozenset()


# --------------------------------------------- 落ち先を持たない配信面（失敗形の名付け）
def test_a_face_without_a_fallback_is_named_in_the_failure() -> None:
    """落ち先を持たない配信面への問い合わせは、名前付きの失敗になる。

    live は共有元そのものなので落ち先を持たない。優先順位の先頭を各 Composition Root が
    自分で取ると、この行を渡した日に素の IndexError が出て「どの配信面の話か」が
    スタックトレースから読み取れない。台帳に無い配信面（KeyError）とは別の失敗である。
    """
    without = [name for name in CORE_NAMES if not fallback_roots(name, _ROOT)]

    assert without != []                       # 落ち先の無い行が実在する（空虚な検査でない）
    for name in without:
        with pytest.raises(NoFallbackRootError, match=name):
            primary_fallback_root(name, _ROOT)


# 「最優先の落ち先＝優先順位の先頭」は上の test_the_composition_root_resolves_the_ledger_path
#   が既に押さえている（期待値は台帳の `fallback_roots(...)[0]`、実測値は Composition Root が
#   `primary_fallback_root` で解決した値）。ここで両者を直接比べ直すと、被検査モジュールの
#   呼び出し同士の比較になって何も固定しない。


# ------------------------------------------------------------- 計算量（無駄の不在）
def _ledger_reads_while_asking(monkeypatch, asks: int) -> int:
    """全配信面へ `asks` 巡ぶん問い合わせる間に発行された台帳の読取回数。"""
    issued: "list[str]" = []
    inner = core_web_topology.read_ledger_text
    monkeypatch.setattr(
        core_web_topology, "read_ledger_text",
        lambda: (issued.append("read"), inner())[1],
    )
    core_web_topology.topology.cache_clear()
    for _ in range(asks):
        for name in CORE_NAMES:
            web_root(name, _ROOT)
    return len(issued)


def test_the_ledger_is_read_once_however_many_faces_ask(monkeypatch) -> None:
    """台帳の読取は 1 プロセス 1 回（問い合わせ数に比例しない）。

    消費者は配信面ごとに問い合わせる。都度読むと読取が問い合わせ数に比例して増えるが、
    増えた読取は答えを 1 文字も変えない——出力の正しさを見る検査では原理的に落ちない形で
    ある（CLAUDE.md 絶対命令 §4.1）。固定するのは無駄の不在であって回数ではないので、
    2 点で「入力を増やしても発行が増えない」ことを見る。
    """
    try:
        issued = {asks: _ledger_reads_while_asking(monkeypatch, asks) for asks in (1, 4)}
    finally:
        core_web_topology.topology.cache_clear()

    # 発行した読取 − 答えに要る読取（台帳は 1 枚）= 0。
    assert issued[1] - 1 == 0
    assert issued[4] - 1 == 0


def test_the_read_counter_sees_a_ledger_reread(monkeypatch) -> None:
    """検出力の自己検査（上のゲートが空虚でないこと）。

    問い合わせごとに読み直す変異を組むと、答えは 1 文字も変わらないまま読取だけが増える。
    """
    issued: "list[str]" = []
    inner = core_web_topology.read_ledger_text
    monkeypatch.setattr(
        core_web_topology, "read_ledger_text",
        lambda: (issued.append("read"), inner())[1],
    )

    try:
        for _ in range(4):
            core_web_topology.topology.cache_clear()   # 変異: 覚えずに毎回読む
            web_root(CORE_NAMES[0], _ROOT)
    finally:
        core_web_topology.topology.cache_clear()

    assert len(issued) - 1 > 0
