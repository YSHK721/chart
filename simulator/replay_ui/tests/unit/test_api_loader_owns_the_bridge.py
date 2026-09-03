"""indicator_ui のロード面の所有者を供給側スライスへ移す（ISSUE-479 Wave2 2-5・X-1）。

固定する仕様:
    indicator_ui の compute / dataset / MP handler を読み出す結線
    （indigators/indicator_ui/api_loader.py）は、**供給側スライスが所有する**。
    simulator/replay_ui/adapter/_indicator_ui_bridge.py は所有者へ委譲するだけの
    再公開層（shim）であり、ロジックを 1 行も持たない。

なぜ所有者を移すのか:
    3 つの消費スライス（replay_ui / dashboard_ui / sim_ui）が、他スライスの
    **私有名**（先頭がアンダースコアのモジュール）を越境 import していた。所有者が消費者の 1 人で
    あるため、replay_ui を触ると無関係な dashboard_ui が壊れ得る。供給しているものの
    置き場所は供給側である。

移設で変わってよいのは 1 点だけ:
    リポジトリ根の導出（ファイル位置が変わるので parents の段数が変わる）。それ以外は
    逐語移設であり、shim にロジックが無いこと・両経路が同一オブジェクトを指すこと・
    キャッシュが 1 つであることを機械的に固定する。
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[4]
_LOADER_REL = "indigators/indicator_ui/api_loader.py"
_SHIM_REL = "simulator/replay_ui/adapter/_indicator_ui_bridge.py"

#: 所有者が提供するロード面（消費者が使う全アクセサ）。
_LOAD_FACES = (
    "load",
    "load_dataset",
    "load_compute",
    "load_mp_handlers",
    "load_tickvol_handler",
    "load_catalog_handler",
)


def _ledger_path_env() -> str:
    out: "list[str]" = []
    for raw in (_REPO / "tools" / "dev_paths.txt").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            out.append(str(_REPO if line == "." else _REPO / line))
    return ":".join(out)


def _run_probe(code: str) -> dict:
    """クリーンな interpreter で計測する（同一プロセスの import 履歴に影響されない）。"""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_REPO),
        env={"PYTHONPATH": _ledger_path_env(), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr[-1500:]!r}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


class TestTheSupplyingSliceOwnsTheLoader:
    """ロード面の実体が供給側スライスに居ること。"""

    def test_the_loader_module_exists_in_the_supplying_slice(self):
        from indigators.indicator_ui import api_loader

        assert Path(api_loader.__file__).resolve() == (_REPO / _LOADER_REL)

    def test_the_default_repo_root_is_the_repository_root(self):
        """移設で唯一変わるロジック: ファイル位置に応じた根の導出。

        ここを間違えると _ensure_paths が存在しないツリーを sys.path へ挿し、
        indicator_ui の api が解決できなくなる（沈黙して別ツリーを掴む事故になる）。
        """
        from indigators.indicator_ui import api_loader

        assert api_loader._DEFAULT_REPO_ROOT == _REPO

    @pytest.mark.parametrize("face", _LOAD_FACES)
    def test_every_load_face_is_provided(self, face):
        from indigators.indicator_ui import api_loader

        assert callable(getattr(api_loader, face))


class TestTheBridgeIsAPureReExport:
    """旧所有者はロジックを持たない再公開層であること。"""

    def test_the_shim_defines_no_logic(self):
        """shim に関数 / クラス定義が 1 つも無い（実装の二重化を構文で禁じる）。"""
        tree = ast.parse((_REPO / _SHIM_REL).read_text(encoding="utf-8"), filename=_SHIM_REL)
        defined = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        assert defined == [], (
            f"{_SHIM_REL} に実装が残っています: {defined}\n"
            "  shim は所有者への再公開だけを担う（写しを持つと片方だけ腐る）。"
        )

    @pytest.mark.parametrize("face", _LOAD_FACES + ("_ensure_paths",))
    def test_both_routes_expose_the_same_object(self, face):
        from indigators.indicator_ui import api_loader
        from simulator.replay_ui.adapter import _indicator_ui_bridge as bridge

        assert getattr(bridge, face) is getattr(api_loader, face)

    def test_there_is_exactly_one_cache(self):
        """キャッシュが 2 つあると「同じ引数で違う実体」が生まれる。"""
        from indigators.indicator_ui import api_loader
        from simulator.replay_ui.adapter import _indicator_ui_bridge as bridge

        assert bridge._CACHE is api_loader._CACHE

    def test_both_routes_return_the_same_namespace(self):
        from indigators.indicator_ui import api_loader
        from simulator.replay_ui.adapter import _indicator_ui_bridge as bridge

        assert bridge.load_compute() is api_loader.load_compute()
        assert bridge.load_dataset() is api_loader.load_dataset()

    @pytest.mark.parametrize("face", ["load_compute", "load"], ids=["compute", "all"])
    def test_the_namespace_attribute_sets_match(self, face):
        """再公開層が属性を落としていない（面の欠落は呼び出し側で AttributeError になる）。"""
        from indigators.indicator_ui import api_loader
        from simulator.replay_ui.adapter import _indicator_ui_bridge as bridge

        via_shim = set(vars(getattr(bridge, face)()))
        via_owner = set(vars(getattr(api_loader, face)()))
        assert via_shim - via_owner == set()
        assert via_owner - via_shim == set()
        assert via_owner != set()  # 空集合どうしの一致で通らない


class TestTheLoaderKeepsTheIspSplit:
    """ISSUE-136 の遮断（dataset-only 経路が MP controller を引き込まない）が所有者側でも成立する。"""

    def test_load_dataset_does_not_eager_import_mp_controllers(self):
        got = _run_probe(
            "import json, sys\n"
            "from indigators.indicator_ui import api_loader\n"
            "ns = api_loader.load_dataset()\n"
            "assert hasattr(ns, 'dataset')\n"
            "leaked = [m for m in sys.modules if m.startswith('market_profile_api.controller')]\n"
            "print(json.dumps({'leaked': leaked}))\n"
        )
        assert got["leaked"] == [], got

    def test_load_compute_does_not_eager_import_mp_controllers(self):
        got = _run_probe(
            "import json, sys\n"
            "from indigators.indicator_ui import api_loader\n"
            "ns = api_loader.load_compute()\n"
            "assert hasattr(ns, 'full_compute')\n"
            "leaked = [m for m in sys.modules if m.startswith('market_profile_api.controller')]\n"
            "print(json.dumps({'leaked': leaked}))\n"
        )
        assert got["leaked"] == [], got


class TestTheLoaderDoesNotWasteWork:
    """計算量検定（Test Spy・発行 − 使用 = 0）。測るのは時間ではなく回数。"""

    @staticmethod
    def _probe_source(calls: int) -> str:
        return (
            "import json, sys\n"
            "\n"
            "class _SpyPath(list):\n"
            "    inserts = 0\n"
            "    def insert(self, i, x):\n"
            "        _SpyPath.inserts += 1\n"
            "        return list.insert(self, i, x)\n"
            "\n"
            "from indigators.indicator_ui import api_loader\n"
            "sys.path = _SpyPath(sys.path)\n"
            "before = len(sys.path)\n"
            f"namespaces = [api_loader.load_compute() for _ in range({calls})]\n"
            "after = len(sys.path)\n"
            "print(json.dumps({\n"
            "    'inserts': _SpyPath.inserts,\n"
            "    'grew': after - before,\n"
            "    'distinct_namespaces': len({id(n) for n in namespaces}),\n"
            "    'cache_size': len(api_loader._CACHE),\n"
            "    'calls': len(namespaces),\n"
            "}))\n"
        )

    @pytest.mark.parametrize("calls", [1, 16], ids=["load_1", "load_16"])
    def test_the_path_setup_is_idempotent_and_the_namespace_is_built_once(self, calls):
        """呼出 1 回 / 16 回の 2 点で、発行 − 使用 = 0。

        - sys.path への insert 発行 − 実際に増えたエントリ数 = 0（空振り挿入が無い）
        - 呼出 16 回でも namespace は 1 つ（同一実体）＝作って捨てる構築が無い
        いずれも回数そのものを期待値へ焼き込まず、無駄の不在を固定する。
        """
        got = _run_probe(self._probe_source(calls))
        assert got["calls"] == calls
        assert got["inserts"] - got["grew"] == 0, got
        assert got["distinct_namespaces"] - 1 == 0, got

    def test_the_cache_does_not_grow_with_the_call_count(self):
        """キャッシュ件数が呼出回数に比例しない（種別ごと 1 件）。"""
        one = _run_probe(self._probe_source(1))
        many = _run_probe(self._probe_source(16))
        assert many["cache_size"] - one["cache_size"] == 0, (one, many)

    def test_the_spy_catches_a_redundant_insert(self):
        """検出力の実測: 冪等ガードを持たない挿入は inserts > grew になる。"""
        got = _run_probe(
            "import json, sys\n"
            "\n"
            "class _SpyPath(list):\n"
            "    inserts = 0\n"
            "    def insert(self, i, x):\n"
            "        _SpyPath.inserts += 1\n"
            "        return list.insert(self, i, x)\n"
            "\n"
            "sys.path = _SpyPath(sys.path)\n"
            "before = len(sys.path)\n"
            "for _ in range(3):\n"
            "    if '/wave2/probe' in sys.path:\n"
            "        sys.path.remove('/wave2/probe')\n"
            "    sys.path.insert(0, '/wave2/probe')\n"
            "after = len(sys.path)\n"
            "print(json.dumps({'inserts': _SpyPath.inserts, 'grew': after - before}))\n"
        )
        assert got["inserts"] - got["grew"] == 2, got
