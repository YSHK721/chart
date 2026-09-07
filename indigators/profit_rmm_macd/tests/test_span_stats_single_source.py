"""σ スパン統計の**単一情報源**を機械的に強制する検定（ISSUE-502 D-5 の再発防止）。

背景（実測された欠陥）:
    ``_series_avg`` / ``_series_std`` / ``oscillator_span`` / ``rolling_span`` の 4 関数
    84 行が ``profit_rmm/src/core.py`` と ``profit_rmm_macd/src/core.py`` に verbatim
    複製されていた（複製であることをコード自身のコメントが明記していた）。複製は必ず
    取り残しを生む（CLAUDE.md「同じコードを手書き複製するな」）。是正として実装を
    1 箇所へ集約し、その所有者を中立共有層 profit_system の span_stats モジュールとした
    （D-5 の暫定所有者は profit_rmm 直下に置いた同名モジュールだったが、それでは姉妹
    **指標**への依存が残るため後続で移送した）。

本テストが禁止する誤り（機械的遮断）:
    1. 4 関数のいずれかが、走査対象 3 パッケージの非テストソースに **2 つ目の実装（def）**
       として現れること（＝複製の再生。旧名 ``_series_avg`` / ``_series_std`` も含めて
       検出する）。
    2. 唯一の実装が ``profit_system/src/span_stats.py`` 以外に置かれること。
    3. 両 core が **同一の関数オブジェクト**を参照しなくなること（import を装って
       ローカル再実装で上書きする経路の遮断）。
    4. 単一情報源が pandas 等の重い層を連鎖 import すること（core 層＝内側から
       成果物層・アダプタ層への依存方向逆流の遮断）。
    5. いずれかの指標 core が姉妹**指標**パッケージを import すること（指標間依存の
       再発。移送の目的そのものを検定に落とす）。

宣言（コメント・docstring）ではなく **AST 走査と実オブジェクト同一性**で強制する
（CLAUDE.md「制約は機械的検査で担保」）。
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

# import 解決は台帳（tools/dev_paths.txt）由来の pythonpath が担う。テスト側で sys.path を
# 改変しない（改変するとプロダクトとモジュール同一性が食い違う）。``indigators/`` は
# pyproject.toml ``[tool.pytest.ini_options] pythonpath`` と venv の
# ``jp225_chart_paths.pth`` の双方に登録済みで、両指標は名前空間パッケージ（PEP 420）
# として ``profit_rmm`` / ``profit_rmm_macd`` の名前で解決する。
from profit_rmm.src import core as rmm_core
from profit_rmm_macd.src import core as macd_core
from profit_system import span_stats

_INDIGATORS = Path(__file__).resolve().parents[2]  # = indigators/
#: 走査対象。所有者（profit_system）と、複製が再生しうる両消費者を含む。
_PACKAGES = ("profit_rmm", "profit_rmm_macd", "profit_system")
_SINGLE_SOURCE = _INDIGATORS / "profit_system" / "src" / "span_stats.py"
#: 対等な指標パッケージ同士（この 2 つの間に import があってはならない）。
_PEER_INDICATORS = ("profit_rmm", "profit_rmm_macd")

#: 単一情報源にのみ存在してよい関数名（旧名＝アンダースコア付きも複製検出の対象に含む）。
_SHARED_FUNCTION_NAMES = frozenset(
    {
        "series_avg",
        "series_std",
        "_series_avg",
        "_series_std",
        "oscillator_span",
        "rolling_span",
    }
)


def _source_files() -> list[Path]:
    """両パッケージの非テスト .py（tests/ ・ __pycache__ ・ demo を除く）を列挙する。"""
    files: list[Path] = []
    for pkg in _PACKAGES:
        for path in sorted((_INDIGATORS / pkg).rglob("*.py")):
            parts = set(path.parts)
            if "tests" in parts or "__pycache__" in parts:
                continue  # テストの独立参照実装は検査対象外（検査装置そのもの）
            files.append(path)
    return files


def _function_definitions(path: Path) -> list[tuple[str, int]]:
    """``path`` 内で def されている関数名と行番号（ネストを含む全階層）を返す。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in _SHARED_FUNCTION_NAMES:
                found.append((node.name, node.lineno))
    return found


def test_shared_span_functions_have_exactly_one_implementation() -> None:
    """4 関数の実装は両パッケージ横断で 1 つだけ（2 つ目が現れたら落ちる）。"""
    # Arrange
    assert _source_files(), "走査対象のソースが 0 件（パス解決の誤り）"

    # Act
    sites: dict[str, list[str]] = {}
    for path in _source_files():
        for name, lineno in _function_definitions(path):
            sites.setdefault(name, []).append(
                f"{path.relative_to(_INDIGATORS)}:{lineno}"
            )

    # Assert: 各名前の def は高々 1 箇所（複製＝2 箇所以上なら失敗）
    duplicated = {name: where for name, where in sites.items() if len(where) > 1}
    assert not duplicated, (
        "σ スパン統計の実装が複製されている（単一情報源へ集約すること）: "
        f"{duplicated}"
    )


def test_the_single_implementation_lives_in_span_stats() -> None:
    """唯一の実装は profit_rmm/span_stats.py に置かれる（所有者の固定）。"""
    # Arrange / Act
    owners = {
        name: path
        for path in _source_files()
        for name, _ in _function_definitions(path)
    }

    # Assert
    assert owners, "共有関数の def が 1 つも見つからない（走査の誤り）"
    for name, path in owners.items():
        assert path == _SINGLE_SOURCE, (
            f"{name} の実装が単一情報源の外にある: {path.relative_to(_INDIGATORS)}"
        )


def test_both_cores_reference_the_identical_function_objects() -> None:
    """両 core が参照する関数は同一オブジェクト（ローカル再実装での上書きを禁止）。"""
    # Arrange: モジュールは冒頭で解決済み（span_stats / rmm_core / macd_core）

    # Act / Assert（is 比較＝同一オブジェクトであること）
    for name, canonical in (
        ("oscillator_span", span_stats.oscillator_span),
        ("rolling_span", span_stats.rolling_span),
    ):
        assert getattr(macd_core, name) is canonical, f"macd core の {name} が別実体"
        assert getattr(rmm_core, name) is canonical, f"rmm core の {name} が別実体"
    assert macd_core._series_avg is span_stats.series_avg
    assert macd_core._series_std is span_stats.series_std
    assert rmm_core._series_avg is span_stats.series_avg
    assert rmm_core._series_std is span_stats.series_std


def test_single_source_does_not_pull_heavy_layers() -> None:
    """単一情報源は numpy のみ（pandas・描画・成果物層を連鎖 import しない）。

    指標の ``src/__init__.py`` 経由（例 profit_rmm/src/core.py）で共有すると pandas と
    出力アダプタが連鎖 import され、core（内側）→ アダプタ（外側）の依存方向逆流に
    なる。共有層 profit_system は公開面（``import profit_system``＝両 core が実際に
    書いている形）まで辿ってもその逆流を持たないことを別プロセスで実測する（本プロセスは
    pytest が既に pandas を読み込んでいるため測れない）。
    """
    # Arrange: 両 core が実際に書いている import 形をそのまま踏ませる。
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(_INDIGATORS)!r})\n"
        "from profit_system import oscillator_span, rolling_span, span_stats\n"
        "leaked = [m for m in ('pandas', 'matplotlib', 'lightweight_charts')"
        " if m in sys.modules]\n"
        "print(','.join(leaked))\n"
    )

    # Act
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    # Assert
    assert proc.returncode == 0, f"単一情報源の単独 import に失敗: {proc.stderr}"
    leaked = proc.stdout.strip()
    assert leaked == "", f"単一情報源が重い層を連鎖 import している: {leaked}"


def _imported_top_level_names(path: Path) -> set[str]:
    """``path`` が import するトップレベル名の集合（ドット区切りの最初の要素だけ採る）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # 相対 import（level>0）はパッケージ内なので対象外。
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


def _peer_import_offenders() -> list[str]:
    """対等な指標パッケージ同士を結ぶ import を列挙する（無ければ空リスト）。"""
    peers = set(_PEER_INDICATORS)
    return sorted(
        f"{path.relative_to(_INDIGATORS)} -> {bad}"
        for path in _source_files()
        for owner in (path.relative_to(_INDIGATORS).parts[0],)
        if owner in peers
        for bad in _imported_top_level_names(path) & (peers - {owner})
    )


def test_peer_indicators_do_not_import_each_other() -> None:
    """対等な 2 指標の間に import が無い（σ スパン共有を口実にした指標間依存の遮断）。

    移送の目的は「共有すべき計算を共有層へ置き、指標同士は対等に保つ」ことである。
    所有者が片方の指標だった間は、姉妹指標の core が **他指標パッケージ**を import して
    いた。同じ形が別の共有物で再発しないよう、宣言ではなく AST で常設検査する。
    """
    # Arrange / Act
    offenders = _peer_import_offenders()

    # Assert
    assert not offenders, (
        "対等な指標パッケージの間に import がある（共有物は共有層 profit_system へ置く）: "
        f"{offenders}"
    )
