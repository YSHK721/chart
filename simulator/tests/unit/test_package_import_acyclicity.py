"""パッケージ同士が**相互に** import しないことを構文木で固定するゲート（ISSUE-502 段階 3）。

固定する仕様（依存の非循環性）:
    `simulator` 配下の 2 つのパッケージ P・Q について、「P の何かが Q を import し、かつ
    Q の何かが P を import する」状態（双方向の辺＝長さ 2 の循環）を 0 件に保つ。

なぜ既存の層順序ゲートでは足りないか（ISSUE-502 C-2 の見逃し）:
    `test_layer_dependency_direction.py` は「内側 4 層 → 外側」の辺だけを見る。
    実際に本番へ入り込んでいた循環は `simulator.main` ⇄ `simulator.main.tester_settings`
    ——どちらも main 層であり、層順序表の観点では違反が 1 件も無い。層の中の
    パッケージ同士が相互参照していても、層ゲートは永久に検出しない。
    実測（是正前）: 本ゲートの走査規則で数えると当該 1 対が検出され、是正後は 0 件になる。

なぜ「関数内 import」も数えるか:
    C-2 は `simulator/main/__init__.py` が関数の中で子パッケージを import することで
    成立していた。関数内へ退避すると import 実行時の ImportError は消えるが、
    依存の辺は消えない（消えたのは症状だけ）。構文木は関数の中の import 文も等しく
    持つので、退避による回避を構造的に無効化できる。

なぜ検出器を自前で書かないか:
    import 5 形態（絶対 / from / 相対 / `from <親> import <サブモジュール>` /
    `importlib.import_module`）の検出は `test_layer_dependency_direction` が既に所有し、
    自分の検出力を実コードで固定している。同じ判定を書き写すと取り残しが生まれるため、
    本ゲートは**その関数を import して使う**（単一ソース）。

走査範囲（列挙ではなく構造で決める）:
    `simulator` 配下の `.py` のうち、パス成分に `tests` を含むもの・`__pycache__` を
    除いた全ファイル。除外は「テストである」という構造条件であって、違反しているパッケージ
    の免除表ではない（免除表は取り残しを恒久化する）。
    テストを含めて数えると `simulator.tests` ⇄ `simulator.tests.unit`（フィクスチャが
    unit の下のモジュールを参照する形）が出るが、これは本番の依存グラフではない。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from simulator.tests.unit.test_layer_dependency_direction import (
    _imported_modules_in_source,
    _package_of,
)

#: リポジトリ内の `simulator` パッケージ本体。
_SIMULATOR_DIR = Path(__file__).resolve().parents[2]

#: 走査から外すパス成分（構造条件）。
_EXCLUDED_PARTS = ("__pycache__", "tests")


def _production_files() -> "list[Path]":
    """本番モジュール（テストでない `.py`）を列挙する。"""
    return sorted(
        path
        for path in _SIMULATOR_DIR.rglob("*.py")
        if not any(part in _EXCLUDED_PARTS for part in path.parts)
    )


def _package_names() -> "frozenset[str]":
    """`simulator` 配下の全パッケージ名（`__init__.py` を持つディレクトリ）。"""
    return frozenset(
        ".".join(path.parent.relative_to(_SIMULATOR_DIR.parent).parts)
        for path in _SIMULATOR_DIR.rglob("__init__.py")
        if "__pycache__" not in path.parts
    )


def _owning_package(module_name: str, packages: "frozenset[str]") -> "str | None":
    """モジュール名を所属パッケージ名へ畳む（最長のパッケージ接頭辞）。

    事前条件: `packages` は既知のパッケージ名集合。
    事後条件: `simulator.main.engine_data_consistency` → `simulator.main`、
             `simulator.main.tester_settings.kwargs_mapper` →
             `simulator.main.tester_settings`。既知パッケージに畳めない名前は `None`。
    """
    parts = module_name.split(".")
    while parts:
        candidate = ".".join(parts)
        if candidate in packages:
            return candidate
        parts.pop()
    return None


def _read_source(path: Path) -> str:
    """走査の読込点（計算量検定が発行回数を数えるための単一の入口）。"""
    return path.read_text(encoding="utf-8")


def _edges_over(files, packages, read=None) -> "tuple[list[Path], dict]":
    """`(path, package)` の並びを走査してパッケージ間の辺を組む。

    **1 ファイルにつき読込 1 回**。読込点を引数で差し替えられるようにしてあるのは、
    計算量検定が「読み捨てが無い」ことを数えるためである（免除リストを持たない＝
    走査ファイル数 == 判定に使ったファイル数）。

    事前条件: 各要素の第 2 成分は当該ファイルが属するパッケージ名（相対 import の基点）。
    事後条件: 返る辞書の鍵は `(src_package, dst_package)`（`src != dst`）、値は
             その辺を作ったファイルのパスの並び。
    """
    reader = read or _read_source
    scanned: "list[Path]" = []
    edges: "dict[tuple[str, str], list[str]]" = {}
    for path, source_package in files:
        scanned.append(path)
        for module in _imported_modules_in_source(
            reader(path), package=source_package, filename=str(path)
        ):
            if module != "simulator" and not module.startswith("simulator."):
                continue
            target = _owning_package(module, packages)
            if target is None or target == source_package:
                continue
            edges.setdefault((source_package, target), []).append(_label(path))
    return scanned, edges


def _label(path: Path) -> str:
    """報告用のパス表記（リポジトリ配下ならリポジトリ相対、外なら絶対）。"""
    root = _SIMULATOR_DIR.parent
    return str(path.relative_to(root)) if path.is_relative_to(root) else str(path)


def _production_targets() -> "list[tuple[Path, str]]":
    """走査対象を `(path, package)` の並びで返す（本番モジュールのみ）。"""
    packages = _package_names()
    return [
        (path, _package_of(path))
        for path in _production_files()
        if _package_of(path) in packages
    ]


def _bidirectional_pairs(edges) -> "list[str]":
    """相互 import になっているパッケージ対を、根拠ファイル付きで列挙する。"""
    reported: "set[tuple[str, str]]" = set()
    out: "list[str]" = []
    for source, target in sorted(edges):
        if (target, source) not in edges:
            continue
        key = tuple(sorted((source, target)))
        if key in reported:
            continue
        reported.add(key)
        out.append(
            f"{source} ⇄ {target}"
            f"（{source}→{target}: {', '.join(sorted(set(edges[(source, target)])))}"
            f" / {target}→{source}: {', '.join(sorted(set(edges[(target, source)])))}）"
        )
    return out


def _production_scan() -> "tuple[list[Path], dict]":
    """本番モジュール全体を走査する。"""
    return _edges_over(_production_targets(), _package_names())


class TestNoTwoPackagesImportEachOther:
    """本番の依存グラフに双方向の辺が 1 件も無いこと。"""

    def test_there_is_no_bidirectional_package_dependency(self):
        _, edges = _production_scan()
        pairs = _bidirectional_pairs(edges)
        assert pairs == [], (
            "相互 import になっているパッケージ対（循環）:\n  " + "\n  ".join(pairs)
            + "\n  一方の辺を消してください。双方が必要とする部分は、どちらでもない"
            "第三のモジュールへ移すと辺が 2 本とも内向きになり循環が構造的に消えます"
            "（例: simulator/main/engine_data_consistency.py）。"
            "\n  関数内 import への退避は辺を消しません（本ゲートは関数内も数えます）。"
        )

    def test_the_c2_pair_is_one_way_from_child_to_parent(self):
        """C-2 の 2 パッケージが「子 → 親」の一方向だけであること（回帰の錨）。"""
        _, edges = _production_scan()
        parent, child = "simulator.main", "simulator.main.tester_settings"
        assert (child, parent) in edges, "子 → 親の辺（正しい向き）が消えている"
        assert (parent, child) not in edges, (
            "親 → 子の辺が復活している: " + ", ".join(sorted(set(edges[(parent, child)])))
        )


class TestTheGateHasDetectionPower:
    """ゲートが空振り（恒真式への退化）していないこと。"""

    def test_the_scan_covers_production_files(self):
        scanned, _ = _production_scan()
        assert len(scanned) > 0

    def test_both_packages_of_the_c2_finding_are_in_scope(self):
        """C-2 の当事者 2 パッケージが実際に走査対象に入っていること。"""
        scanned = {_package_of(path) for path in _production_files()}
        assert "simulator.main" in scanned
        assert "simulator.main.tester_settings" in scanned

    def test_a_bidirectional_pair_is_detected_end_to_end(self, tmp_path):
        """C-2 と同じ形（親が関数内で子を、子が module 直下で親を import）を検出する。"""
        parent = tmp_path / "parent.py"
        child = tmp_path / "child.py"
        parent.write_text(
            "def build():\n"
            "    from simulator.main.tester_settings.kwargs_mapper import x\n"
            "    return x\n",
            encoding="utf-8",
        )
        child.write_text("from simulator.main import build_interactor\n", encoding="utf-8")
        _, edges = _edges_over(
            [(parent, "simulator.main"), (child, "simulator.main.tester_settings")],
            _package_names(),
        )
        assert _bidirectional_pairs(edges) != [], edges

    def test_a_one_way_pair_is_not_flagged(self):
        assert _bidirectional_pairs({("pkg.sub", "pkg"): ["child.py"]}) == []

    @pytest.mark.parametrize(
        "form,source",
        [
            ("静的 import（from 形）", "from simulator.main import build_interactor"),
            ("静的 import（絶対形）", "import simulator.main as sim_main"),
            ("from <親> import <サブモジュール>", "from simulator import main as sim_main"),
            (
                "importlib（文字列リテラル）",
                'import importlib\nimportlib.import_module("simulator.main.run_config")',
            ),
            (
                "関数内 import（C-2 が実際に使っていた形）",
                "def f():\n    from simulator.main import build_interactor\n    return build_interactor\n",
            ),
        ],
        ids=["from", "absolute", "from_parent", "importlib", "function_local"],
    )
    def test_every_edge_form_reaches_the_package_graph(self, form, source, tmp_path):
        path = tmp_path / "probe.py"
        path.write_text(source, encoding="utf-8")
        _, edges = _edges_over(
            [(path, "simulator.main.tester_settings")], _package_names()
        )
        assert ("simulator.main.tester_settings", "simulator.main") in edges, (form, edges)

    def test_the_owning_package_folds_submodules_not_prefixes(self):
        packages = _package_names()
        assert (
            _owning_package("simulator.main.engine_data_consistency", packages)
            == "simulator.main"
        )
        assert (
            _owning_package("simulator.main.tester_settings.kwargs_mapper", packages)
            == "simulator.main.tester_settings"
        )
        # 存在しないパッケージ名は畳めない（誤って親へ吸い上げない）。
        assert _owning_package("pandas.core", packages) is None

    def test_a_module_is_not_an_edge_to_its_own_package(self, tmp_path):
        """同一パッケージ内の参照は辺にならない（自己ループを循環と呼ばない）。"""
        path = tmp_path / "probe.py"
        path.write_text("from simulator.main.run_config import RunConfig\n", encoding="utf-8")
        _, edges = _edges_over([(path, "simulator.main")], _package_names())
        assert edges == {}


class TestTheGateDoesNotWasteWork:
    """計算量検定（Test Spy・発行 − 使用 = 0）。測るのは時間ではなく回数。"""

    def test_every_scanned_file_is_read_exactly_once(self):
        reads: "list[Path]" = []
        scanned, _ = _edges_over(
            _production_targets(),
            _package_names(),
            read=lambda p: (reads.append(p), _read_source(p))[1],
        )
        # 発行（読込）− 使用（走査したファイル）= 0。読み捨てが 1 件も無い。
        assert len(reads) - len(scanned) == 0
        assert len(set(scanned)) - len(scanned) == 0  # 同じファイルを二度走査しない

    def test_the_read_count_is_determined_by_the_file_count_alone(self, tmp_path):
        """走査対象 3 件 / 6 件の 2 点で「読込数 == ファイル数」（オーダーの表明）。"""
        measured = {}
        for count in (3, 6):
            files = []
            for index in range(count):
                path = tmp_path / f"m{count}_{index}.py"
                path.write_text(
                    "from simulator.domain.exceptions import ConfigError\n",
                    encoding="utf-8",
                )
                files.append((path, "simulator.main"))
            reads: "list[Path]" = []
            _edges_over(
                files,
                _package_names(),
                read=lambda p: (reads.append(p), p.read_text(encoding="utf-8"))[1],
            )
            measured[count] = (len(reads), count)
        for count, (reads_done, files_given) in measured.items():
            assert reads_done - files_given == 0, (count, measured)
