"""アーキ回帰: ``tools`` ⇄ ``simulator`` の循環依存を禁ずる（ISSUE-502 C-1）。

何が壊れていたか（精査台帳 2026-09-06 の実測）:
    - simulator → tools: sim_ui の Composition Root が import パス台帳の読み手
      （運用スクリプト層の install_dev_paths）を掴んでいた
      （コメント自身が「循環辺になる」と明記し、遅延 import で封じ込めていた）。
    - tools → simulator: 検証・取得スクリプト計 7 箇所が simulator 側の tools 配下を掴んでいた。

向きの裁定（本検定が固定する不変条件）:
    ``tools`` は横断的な運用スクリプト／合成点のアクターであり、**product（simulator /
    indigators / marketdata）を駆動する側**である。したがって
      - product → tools は禁止（これが循環を作る辺）。
      - tools → product は許可（ops が product を駆動する自然な向き）。ただし
        **どのモジュールがその辺を持ってよいかを表で宣言**し、無宣言の増殖を Red にする。
    同型の裁定は ``tools/tests/test_no_cycle_tools_indigators.py``（C-2）で既に確立済みで
    あり、本検定はその simulator 版である。

どう消したか（封じ込めではなく所有権の移動）:
    - 台帳の読み手 → 中立核 common/dev_paths.py（stdlib のみ・どちらのアクターにも
      属さない汎用抽象。common/watch_loop.py と同じ解）。
    - 生ティック列 RAW_COLUMNS → 産出側 marketdata/tick_raw_schema.py
      （列を作るのは Dukascopy の tick source。消費側 simulator が供給規則を持っていた）。
    - 取得ランナー → tools/fetch_ticks_ymd.py（simulator を 1 行も import せず、
      呼出側 3 本すべてが tools。取得＋landing は運用スクリプト層の役割）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SIMULATOR = _REPO_ROOT / "simulator"
_TOOLS = _REPO_ROOT / "tools"

#: 循環を作る向き（product → ops）。simulator の本番コードは 1 本も持ってはならない。
_FORBIDDEN_FROM_SIMULATOR = "tools"

#: 許される向き（ops → product）だが、辺を持ってよい tools モジュールは宣言制にする。
#: 値は「なぜその辺が要るか」。表に無いモジュールが simulator を import したら Red。
_DECLARED_TOOLS_TO_SIMULATOR: "dict[str, str]" = {
    "acquire_marketdata.py": (
        "取得パイプラインの合成点（Composition Root 相当）。ingest 段は canonical tick-store"
        " の所有者（simulator の adapter/repository にある parquet tick リポジトリ）へ書くため、その束縛を"
        " ここが持つ。合成点が具象を名指すのは DIP の定めた位置であり、辺の向きも"
        " ops → product で循環しない。"
    ),
}


def _production_sources(root: Path) -> "list[Path]":
    """``root`` 配下の本番コード（テスト・パッケージ初期化子・キャッシュを除く）。"""
    return sorted(
        p
        for p in root.rglob("*.py")
        if "tests" not in p.relative_to(root).parts
        and "__pycache__" not in p.parts
        and p.name != "__init__.py"
    )


def _imported_roots(source: str) -> "set[str]":
    """import 文（関数内の遅延 import を含む）からトップレベルパッケージ根を集める。

    相対 import は自スライス内の同名サブパッケージを指すため対象外にする
    （``from ..tools import x`` / ``simulator.report_ui.tools`` を誤検出しない——判定を
    **根**で行うのはこの取り違えを避けるためである）。
    ``importlib.import_module("tools.x")`` の文字列形も拾う（宣言を迂回する穴を作らない）。
    """
    roots: "set[str]" = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module and not node.level:
                roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and _is_dynamic_import(node.func):
            roots.update(
                arg.value.split(".")[0]
                for arg in node.args
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            )
    return roots


def _is_dynamic_import(func: ast.expr) -> bool:
    """``importlib.import_module`` / ``__import__`` 相当の呼び出しか。"""
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    return name in {"import_module", "__import__"}


# =====================================================================
# 走査の生存確認（恒真式への退化を防ぐ）
# =====================================================================

def test_scan_targets_are_not_empty() -> None:
    assert _production_sources(_SIMULATOR), f"走査対象が空です: {_SIMULATOR}"
    assert _production_sources(_TOOLS), f"走査対象が空です: {_TOOLS}"


def test_the_scan_reaches_the_modules_that_held_the_cycle() -> None:
    """かつて辺を持っていた実ファイルが走査対象に入っている。"""
    sim = {str(p.relative_to(_SIMULATOR)) for p in _production_sources(_SIMULATOR)}
    assert "sim_ui/main/composition_root_jobs.py" in sim
    assert "tools/ingest_ticks.py" in sim
    tools = {p.name for p in _production_sources(_TOOLS)}
    for name in ("acquire_marketdata.py", "build_tick_rollup.py", "live_tick_watch.py",
                 "verify_tick_immutability.py", "verify_profile_micro_structure.py",
                 "fetch_ticks_ymd.py", "install_dev_paths.py"):
        assert name in tools, name


#: 依存方向違反の全形態（検出力の自己検査）。1 形態でも取り逃せば検出の穴である。
_VIOLATION_FORMS = {
    "絶対 from": "from tools.install_dev_paths import path_entries\n",
    "絶対 import": "import tools.install_dev_paths\n",
    "親から子を取る form": "from tools import install_dev_paths\n",
    "importlib 文字列": (
        "import importlib\n"
        'm = importlib.import_module("tools.install_dev_paths")\n'
    ),
}

#: 違反ではない形（誤検出の自己検査）。
_INNOCENT_FORMS = {
    "他スライスの同名サブパッケージ": "from simulator.report_ui.tools.int_time_views import to_view\n",
    "相対 import": "from ..tools import helper\n",
    "中立核": "from common.dev_paths import path_entries\n",
    "産出側": "from marketdata.tick_raw_schema import RAW_COLUMNS\n",
    "stdlib": "import subprocess\n",
    "散文での言及": '"""束縛は tools.install_dev_paths が持っていた。"""\n',
    "文字列だが import ではない": 'p = base / "tools" / "dev_paths.txt"\n',
}


@pytest.mark.parametrize("form", sorted(_VIOLATION_FORMS))
def test_the_detector_sees_every_violation_form(form: str) -> None:
    assert _FORBIDDEN_FROM_SIMULATOR in _imported_roots(_VIOLATION_FORMS[form]), form


@pytest.mark.parametrize("form", sorted(_INNOCENT_FORMS))
def test_the_detector_does_not_flag_innocent_forms(form: str) -> None:
    assert _FORBIDDEN_FROM_SIMULATOR not in _imported_roots(_INNOCENT_FORMS[form]), form


# =====================================================================
# 向きの固定
# =====================================================================

def test_no_simulator_production_module_imports_the_tooling_actor() -> None:
    """``simulator`` の本番コードは ``tools`` を import しない（循環を作る辺）。

    識別力: sim_ui の Composition Root の束縛を ``from tools.install_dev_paths import
    path_entries`` へ戻すと Red になる。台帳の読み手は中立核 common.dev_paths が所有する。
    """
    offenders = sorted(
        str(path.relative_to(_REPO_ROOT))
        for path in _production_sources(_SIMULATOR)
        if _FORBIDDEN_FROM_SIMULATOR in _imported_roots(path.read_text(encoding="utf-8"))
    )
    assert offenders == [], (
        f"simulator が運用スクリプト層を import しています: {offenders}（循環 C-1）。"
        " 共有したい規則は中立核（common）か産出側（marketdata）へ置き、双方がそこを参照すること。"
    )


def test_only_the_declared_composition_point_imports_simulator_from_tools() -> None:
    """``tools`` → ``simulator`` の辺を持ってよいのは宣言済みの合成点だけ。

    向き自体は許可（ops が product を駆動する）だが、無宣言に増えると「どこが simulator を
    知っているか」が誰にも分からなくなり、逆流（product → tools）の抑止も効かなくなる。
    識別力: ``tools/verify_tick_immutability.py`` の RAW_COLUMNS import を
    simulator 側の ingest_ticks へ戻すと Red になる。
    """
    offenders = sorted(
        str(path.relative_to(_TOOLS))
        for path in _production_sources(_TOOLS)
        if "simulator" in _imported_roots(path.read_text(encoding="utf-8"))
        and str(path.relative_to(_TOOLS)) not in _DECLARED_TOOLS_TO_SIMULATOR
    )
    assert offenders == [], (
        f"未宣言の tools → simulator 依存です: {offenders}。"
        " 供給規則（列・レイアウト）なら marketdata、汎用抽象なら common へ所有権を移すこと。"
        " 合成点として真に必要なら _DECLARED_TOOLS_TO_SIMULATOR へ理由付きで宣言すること。"
    )


def test_the_declaration_table_has_no_stale_entries() -> None:
    """宣言表に、現に辺を持たないモジュールが残っていない（宣言の陳腐化を防ぐ）。"""
    stale = sorted(
        name
        for name in _DECLARED_TOOLS_TO_SIMULATOR
        if "simulator" not in _imported_roots((_TOOLS / name).read_text(encoding="utf-8"))
    )
    assert stale == [], (
        f"宣言表に未使用のエントリが残っています: {stale}。辺が消えたら宣言側も狭めてください。"
    )


def test_the_moved_owners_are_where_the_declaration_says() -> None:
    """所有権の移動先が実在し、旧所在に第 2 定義が残っていない（是正の固定点）。

    判定は **オブジェクト同一性**で行う（値の一致ではない）。旧所在に等価な第 2 定義を
    書き戻しても値は一致してしまうが、別オブジェクトになるので同一性は落ちる。
    """
    import common.dev_paths as neutral
    import marketdata.tick_raw_schema as raw_schema
    import simulator.tools.ingest_ticks as ingest
    import tools.install_dev_paths as installer

    assert neutral.path_entries.__module__ == "common.dev_paths"
    assert installer.path_entries is neutral.path_entries, (
        "台帳の読み手が運用スクリプト層に再定義されています（唯一源は common.dev_paths）。"
    )
    assert ingest.RAW_COLUMNS is raw_schema.RAW_COLUMNS, (
        "simulator 側に生列の第 2 定義が復活しています（唯一源は marketdata.tick_raw_schema）。"
    )
    assert not (_SIMULATOR / "tools" / "fetch_ticks_ymd.py").exists(), (
        "旧所在の fetch_ticks_ymd が復活しています（所在は tools/fetch_ticks_ymd.py）。"
    )


# =====================================================================
# 計算量検定（Test Spy・発行 − 使用 = 0）
# =====================================================================

def _roots_over(files, parse=None) -> "list[set[str]]":
    """与えたファイル群を 1 ファイル 1 パースで判定する（免除リストを持たない）。"""
    parser = parse or ast.parse
    out: "list[set[str]]" = []
    for path in files:
        roots: "set[str]" = set()
        for node in ast.walk(parser(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                roots.add(node.module.split(".")[0])
        out.append(roots)
    return out


@pytest.mark.parametrize("count", [4, 16], ids=["files_4", "files_16"])
def test_the_parse_count_is_determined_by_the_file_count_alone(tmp_path, count) -> None:
    """走査対象 4 件 / 16 件の 2 点で「パース発行 − 判定に使ったソース数 = 0」。

    回数リテラルは焼き込まない。固定するのは無駄の不在（読み捨て・二度パースが 0）と、
    発行がファイル数以外の要因（ファイルの行数・import 文の本数）で増えないことである。
    """
    files = []
    for i in range(count):
        path = tmp_path / f"s{count}_{i}.py"
        # 行数と import 本数を意図的に増やす（発行がこれらに比例しないことの表明）。
        path.write_text(
            "\n".join(["import os", "import sys", "from common.dev_paths import path_entries"] * 5),
            encoding="utf-8",
        )
        files.append(path)

    issued: "list[str]" = []

    def _spy(source, *args, **kwargs):
        issued.append(source)
        return ast.parse(source, *args, **kwargs)

    used = _roots_over(files, parse=_spy)

    assert len(issued) - len(used) == 0, "パース発行が判定使用数を超えています（作って捨てた）"
    assert len(issued) == count, "パース発行がファイル数以外の要因で増えています"


def test_each_real_source_is_parsed_exactly_once() -> None:
    """実走査でも読み捨て・二度パースが無い（発行 − 使用 = 0）。"""
    files = _production_sources(_SIMULATOR)
    issued: "list[str]" = []

    def _spy(source, *args, **kwargs):
        issued.append(source)
        return ast.parse(source, *args, **kwargs)

    used = _roots_over(files, parse=_spy)
    assert len(issued) - len(used) == 0
    assert len(issued) == len(files)
