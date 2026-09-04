"""indicator_ui のロード面の所有者を供給側スライスへ移す（ISSUE-479 Wave2 2-5・X-1）。

固定する仕様:
    indicator_ui の compute / dataset / MP handler を読み出す結線
    （indigators/indicator_ui/api_loader.py）は、**供給側スライスが所有する**。
    旧位置 simulator/replay_ui/adapter/_indicator_ui_bridge.py（再公開層）は
    ISSUE-479 Wave2b で削除済みであり、経路は所有者ただ 1 本である。

なぜ所有者を移すのか:
    3 つの消費スライス（replay_ui / dashboard_ui / sim_ui）が、他スライスの
    **私有名**（先頭がアンダースコアのモジュール）を越境 import していた。所有者が消費者の 1 人で
    あるため、replay_ui を触ると無関係な dashboard_ui が壊れ得る。供給しているものの
    置き場所は供給側である。

移設で変わってよいのは 1 点だけ:
    リポジトリ根の導出（ファイル位置が変わるので parents の段数が変わる）。それ以外は
    逐語移設である。

「キャッシュが 1 つ」の固定の仕方が変わった:
    再公開層が在った間は「両経路が同一オブジェクトを指すこと」で 1 つを担保していた。
    旧位置を消した今は**経路が 1 本しか無いこと**（旧位置が存在せず import も解決しない）
    で担保する。後者の方が強い——同一性は写しを作れば破れるが、不在は破れない。
"""
from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[4]
_LOADER_REL = "indigators/indicator_ui/api_loader.py"
_LOADER_MODULE = "indigators.indicator_ui.api_loader"
#: 旧位置（再公開層）。ISSUE-479 Wave2b で削除済み——存在しないことを固定する。
_SHIM_REL = "simulator/replay_ui/adapter/_indicator_ui_bridge.py"
_SHIM_MODULE = "simulator.replay_ui.adapter._indicator_ui_bridge"

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


class TestTheOldLocationIsRemoved:
    """再公開層（旧位置）が**存在しない**こと（ISSUE-479 Wave2b・承認済み削除）。

    以前この class は「shim にロジックが無い」「両経路が同一オブジェクト」「キャッシュが
    1 つ」を固定していた。旧位置が消えた今、それらは**経路が 1 本しか無い**という、より
    強い形で成立する。2 経路の同一性を測る代わりに、2 経路目が生まれないことを固定する。
    """

    def test_the_shim_file_does_not_exist(self):
        assert not (_REPO / _SHIM_REL).exists(), (
            f"{_SHIM_REL} が残っています。ロード面の所有者は {_LOADER_REL} ただ 1 つであり、"
            "再公開層は移行の安全弁として一時的に置かれていたものである。"
        )

    def test_the_shim_module_is_not_importable(self):
        """ファイルを消しただけでなく、import 経路としても解決できないこと。"""
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(_SHIM_MODULE)

    def test_the_owner_remains_importable(self):
        """空振り防止: 消したのは旧位置だけで、所有者は生きている。

        「import できた」だけでは弱い（別ツリーの同名を掴んでも通る）。解決先の
        ファイルが所有者そのものであることまで見る。
        """
        owner = importlib.import_module(_LOADER_MODULE)

        assert Path(owner.__file__).resolve() == (_REPO / _LOADER_REL)


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
