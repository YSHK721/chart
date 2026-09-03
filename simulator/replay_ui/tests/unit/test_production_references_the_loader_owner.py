"""本番コードがロード面を所有者から直接借りることを固定する（ISSUE-479 Wave2 2-6・X-1）。

固定する仕様:
    本番モジュール（tests を除く全 .py）は
    indigators/indicator_ui/api_loader.py を直接 import する。
    旧位置 simulator/replay_ui/adapter/_indicator_ui_bridge.py（再公開層）への
    参照は本番に 0 件である。

なぜ「shim があるのだから経由してよい」では駄目か:
    再公開層を残す目的は**移行の安全弁**であって、恒久的な経路の二重化ではない。
    2 経路が並存したまま参照が残ると、旧位置の削除が永久にできず、しかも
    dashboard_ui / sim_ui は他スライスの私有名（先頭がアンダースコアのモジュール）
    へ依存し続ける。参照が 0 件であることを機械的に固定してはじめて、旧位置の削除は
    「承認を取るだけ」の作業になる。

もう 1 つ塞ぐ形（名前空間パッケージの二重解決）:
    台帳が indigators/ を sys.path へ載せたので、所有者は
    indigators.indicator_ui.api_loader とも indicator_ui.api_loader とも書ける。
    両方が使われると **別のモジュールオブジェクト**が生まれ、キャッシュが 2 つに割れる
    （同じ引数で違う実体が返る）。借り方を 1 つに固定する。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[4]

#: 所有者（本番はここから借りる）。
_OWNER = "indigators.indicator_ui.api_loader"

#: 旧位置（再公開層）。本番からの参照は 0 件でなければならない。
_SHIM = "simulator.replay_ui.adapter._indicator_ui_bridge"

#: 所有者を指すが**別の module オブジェクト**になる書き方（キャッシュが割れる）。
_ALIASED_OWNER = "indicator_ui.api_loader"

#: 走査対象から外すツリー（生成物・外部・仮想環境）。
_SKIP_PARTS = ("__pycache__", "node_modules", ".venv", "site-packages", "out")


def _production_sources() -> "list[Path]":
    """本番モジュール（tests を除く全 .py）。"""
    out: "list[Path]" = []
    for path in sorted(_REPO.rglob("*.py")):
        parts = path.relative_to(_REPO).parts
        if any(p in _SKIP_PARTS or p.startswith(".") for p in parts):
            continue
        if "tests" in parts or path.name.startswith("test_"):
            continue
        out.append(path)
    return out


def _imported_modules(source: str, filename: str) -> "list[tuple[str, int]]":
    """`from X import Y` / `import X` が指すモジュール名を (名前, 行) で返す。

    `from <pkg> import <module>` の形も、`<pkg>.<module>` として解決する
    （この形はモジュール名が `node.module` に現れないため、見落としの常連である）。
    """
    out: "list[tuple[str, int]]" = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.append((node.module, node.lineno))
            for alias in node.names:
                out.append((f"{node.module}.{alias.name}", node.lineno))
    return out


def _references(target: str, files=None, read=None) -> "tuple[list[Path], list[str]]":
    """`target`（またはその配下）を参照する本番箇所を返す。**1 ファイル 1 読込**。"""
    files = _production_sources() if files is None else files
    reader = read or (lambda p: p.read_text(encoding="utf-8"))
    scanned: "list[Path]" = []
    hits: "list[str]" = []
    for path in files:
        scanned.append(path)
        rel = str(path.relative_to(_REPO)) if path.is_relative_to(_REPO) else str(path)
        try:
            modules = _imported_modules(reader(path), rel)
        except SyntaxError:
            continue
        for module, line in modules:
            if module == target or module.startswith(target + "."):
                hits.append(f"{rel}:{line} {module}")
    return scanned, hits


class TestProductionBorrowsFromTheOwner:
    """本番の借り先が所有者 1 点であること。"""

    def test_no_production_module_references_the_shim(self):
        _, hits = _references(_SHIM)
        # 再公開層そのものは所有者を参照してよい（それが仕事である）。
        hits = [h for h in hits if not h.startswith("simulator/replay_ui/adapter/_indicator_ui_bridge.py:")]
        assert hits == [], (
            "本番が再公開層（旧位置）を経由しています:\n  "
            + "\n  ".join(hits)
            + f"\n  所有者 {_OWNER} から直接借りてください。"
        )

    def test_the_owner_is_actually_referenced(self):
        """空振り防止: 所有者への参照が本番に実在する。"""
        _, hits = _references(_OWNER)
        assert hits != [], "所有者への参照が本番に 1 件も無い（検査が空振りしている）"

    def test_no_production_module_uses_the_aliased_module_path(self):
        """別名経路（indicator_ui.api_loader）を使わない＝module オブジェクトが割れない。"""
        _, hits = _references(_ALIASED_OWNER)
        assert hits == [], (
            "所有者を別の module 名で借りています（キャッシュが 2 つに割れます）:\n  "
            + "\n  ".join(hits)
        )

    def test_the_two_spellings_would_really_be_different_modules(self):
        """上の禁止が空理空論でないことの実測: 2 つの綴りは別実体になる。"""
        import importlib

        a = importlib.import_module(_OWNER)
        b = importlib.import_module(_ALIASED_OWNER)
        assert a is not b
        assert a._CACHE is not b._CACHE


class TestTheScanHasDetectionPower:
    """検査が空振りしていないこと。"""

    @pytest.mark.parametrize(
        ("source", "found"),
        [
            ("from simulator.replay_ui.adapter import _indicator_ui_bridge\n", True),
            ("import simulator.replay_ui.adapter._indicator_ui_bridge\n", True),
            ("from simulator.replay_ui.adapter._indicator_ui_bridge import load\n", True),
            ("from indigators.indicator_ui import api_loader\n", False),
            ("from simulator.replay_ui.adapter import dataset_ports\n", False),
        ],
        ids=["from_pkg", "import_module", "from_module", "owner", "sibling"],
    )
    def test_every_shim_reference_form_is_detected(self, tmp_path, source, found):
        probe = tmp_path / "probe.py"
        probe.write_text(source, encoding="utf-8")
        _, hits = _references(_SHIM, files=[probe])
        assert bool(hits) is found, (source, hits)

    def test_the_scan_covers_the_production_tree(self):
        scanned, _ = _references(_OWNER)
        rels = {str(p.relative_to(_REPO)) for p in scanned}
        for expected in (
            "simulator/replay_ui/main/composition_root.py",
            "dashboard_ui/adapter/series_role_table.py",
            "simulator/sim_ui/adapter/indicator_catalog_source.py",
        ):
            assert expected in rels, expected


class TestTheScanDoesNotWasteWork:
    """計算量検定（Test Spy・発行 − 使用 = 0）。測るのは時間ではなく回数。"""

    def test_every_scanned_file_is_read_exactly_once(self):
        reads: "list[Path]" = []
        scanned, _ = _references(
            _SHIM,
            files=_production_sources(),
            read=lambda p: (reads.append(p), p.read_text(encoding="utf-8"))[1],
        )
        assert len(reads) - len(scanned) == 0
        assert len(set(scanned)) - len(scanned) == 0

    @pytest.mark.parametrize("count", [4, 8], ids=["files_4", "files_8"])
    def test_the_read_count_is_determined_by_the_file_count_alone(self, tmp_path, count):
        """走査 4 件 / 8 件の 2 点で「読込数 == ファイル数」（オーダーの表明）。"""
        files = []
        for i in range(count):
            path = tmp_path / f"m{i}.py"
            path.write_text("from indigators.indicator_ui import api_loader\n", encoding="utf-8")
            files.append(path)
        reads: "list[Path]" = []
        scanned, _ = _references(
            _SHIM, files=files, read=lambda p: (reads.append(p), p.read_text(encoding="utf-8"))[1]
        )
        assert len(reads) - len(scanned) == 0
        assert len(scanned) - count == 0
