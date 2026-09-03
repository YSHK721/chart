"""adapter 層の import パス解決を台帳 1 点に固定する（ISSUE-479 Wave2 2-2）。

固定する仕様:
    simulator/adapter 配下のモジュールは import 時に sys.path を書き換えない。
    共有ライブラリ（indigators 配下の MQL 忠実 MA 実装など）の解決は
    tools/dev_paths.txt が唯一源であり、3 消費者（pytest の pythonpath・
    serve.sh の PYTHONPATH・venv の .pth）が台帳から導出する。

なぜ「動くから良い」では駄目か:
    実行時の sys.path 書き換えは、解決先を**プロセスの import 順**に依存させる。
    どのモジュールが先に読まれたかで探索パスが変わるため、同じコードでも起動経路
    （pytest / serve.sh / 対話シェル / worktree）ごとに別のツリーを掴み得る。
    これは ISSUE-279（worktree から起動しても main の実装が読まれ /tf_period_profile が
    500 になった）の病因と同型である。解決先は起動時に確定していなければならない。

走査対象から外すもの（意図的）:
    simulator/replay_ui/adapter の bridge は対象外である。あれが挿すのは
    indicator_ui api の**汎用名**（adapter / framework / domain）を含むツリーであり、
    台帳の規律（tools/dev_paths.txt :17-19）でスライス間衝突するため台帳へは載せられない。
    載せられないものを「載せた扱い」にする免除ではなく、**走査範囲が違う**
    （台帳で解決できる固有名だけを扱う層）という区別である。
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_LEDGER = _REPO / "tools" / "dev_paths.txt"

#: 走査する層（台帳で解決できる固有名だけを扱う層）。
_SCANNED_ROOTS = ("simulator/adapter",)

#: sys.path を壊す呼び出し（読みは含めない）。
_MUTATORS = ("insert", "append", "extend", "remove", "pop", "clear", "__setitem__")


def _ledger_pythonpath() -> str:
    out: "list[str]" = []
    for raw in _LEDGER.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            out.append(str(_REPO if line == "." else _REPO / line))
    return ":".join(out)


def _is_sys_path(node: ast.AST) -> bool:
    """式が sys.path そのものか。"""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "path"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    )


def _mutations_in_source(source: str, filename: str) -> "list[tuple[int, str]]":
    """ソース 1 本が行う sys.path の**書き換え**を列挙する（読みは挙げない）。"""
    out: "list[tuple[int, str]]" = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in _MUTATORS
                and _is_sys_path(func.value)
            ):
                out.append((node.lineno, f"sys.path.{func.attr}()"))
        elif isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                probe = target.value if isinstance(target, ast.Subscript) else target
                if _is_sys_path(probe):
                    out.append((node.lineno, "sys.path への代入"))
    return out


def _scanned_modules() -> "list[Path]":
    return sorted(
        path
        for root in _SCANNED_ROOTS
        for path in (_REPO / root).rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _scan(files=None, read=None) -> "tuple[list[Path], list[str]]":
    """走査して違反を返す。**1 ファイルにつき読込 1 回**（計算量検定の測定点）。"""
    files = _scanned_modules() if files is None else files
    reader = read or (lambda p: p.read_text(encoding="utf-8"))
    scanned: "list[Path]" = []
    offenders: "list[str]" = []
    for path in files:
        scanned.append(path)
        rel = str(path.relative_to(_REPO)) if path.is_relative_to(_REPO) else str(path)
        for line, what in _mutations_in_source(reader(path), rel):
            offenders.append(f"{rel}:{line} {what}")
    return scanned, offenders


_PROBE = r"""
import json, sys
import numpy, pandas  # 計測対象を「目的モジュールの import」だけに絞る


class _SpyPath(list):
    calls = []

    def insert(self, i, x):
        _SpyPath.calls.append("insert")
        return list.insert(self, i, x)

    def append(self, x):
        _SpyPath.calls.append("append")
        return list.append(self, x)

    def extend(self, xs):
        xs = list(xs)
        _SpyPath.calls.append("extend")
        return list.extend(self, xs)

    def __setitem__(self, k, v):
        _SpyPath.calls.append("setitem")
        return list.__setitem__(self, k, v)


targets = json.loads(sys.argv[1])
sys.path = _SpyPath(sys.path)
before = len(sys.path)
import importlib

for name in targets:
    importlib.import_module(name)
after = len(sys.path)
print(json.dumps({
    "mutations": len(_SpyPath.calls),
    "delta": after - before,
    "targets": len(targets),
}))
"""


def _measure_import(targets: "list[str]", cwd: Path, extra_path: str = "") -> dict:
    """台帳だけを与えた interpreter で ``targets`` を import し、sys.path の書き換えを数える。"""
    path_env = _ledger_pythonpath()
    if extra_path:
        path_env = f"{extra_path}:{path_env}"
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE, json.dumps(targets)],
        cwd=str(cwd),
        env={"PYTHONPATH": path_env, "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1200:]
    return json.loads(proc.stdout)


class TestTheAdapterLayerDoesNotRewriteThePath:
    """adapter 層に sys.path の書き換えが 1 件も無いこと。"""

    def test_no_adapter_module_mutates_sys_path(self):
        _, offenders = _scan()
        assert offenders == [], (
            "adapter 層が import 時に sys.path を書き換えています:\n  "
            + "\n  ".join(offenders)
            + "\n  解決先は tools/dev_paths.txt（台帳）へ載せてください"
            "（実行時の書き換えは解決先を import 順に依存させます）。"
        )

    def test_the_scan_actually_covers_files(self):
        """走査が空振りしていない（恒真式に退化していない）。"""
        scanned, _ = _scan()
        assert len(scanned) > 0
        assert any(p.name == "madiff.py" for p in scanned)


class TestTheGateHasDetectionPower:
    """ゲートが書き換えを実際に見分けること。"""

    @pytest.mark.parametrize(
        "source",
        [
            "import sys\nsys.path.insert(0, '/x')\n",
            "import sys\nsys.path.append('/x')\n",
            "import sys\nsys.path.extend(['/x'])\n",
            "import sys\nsys.path = ['/x']\n",
            "import sys\nsys.path[0] = '/x'\n",
        ],
        ids=["insert", "append", "extend", "rebind", "subscript"],
    )
    def test_every_mutating_form_is_detected(self, source):
        assert _mutations_in_source(source, "x.py") != []

    @pytest.mark.parametrize(
        "source",
        [
            "import sys\nif '/x' not in sys.path:\n    pass\n",
            "import sys\nprint(len(sys.path))\n",
            "import sys\nfirst = sys.path[0]\n",
            "other = object()\nother.path.insert(0, '/x')\n",
        ],
        ids=["membership", "length", "read_index", "not_sys"],
    )
    def test_a_read_or_another_object_is_not_flagged(self, source):
        assert _mutations_in_source(source, "x.py") == []


class TestImportingTheAdapterLeavesThePathAlone:
    """計算量検定（Test Spy・発行 − 使用 = 0）。測るのは時間ではなく回数。"""

    @pytest.mark.parametrize(
        "targets",
        [
            ["simulator.adapter.indicator.madiff"],
            [
                "simulator.adapter.indicator.madiff",
                "moving_averages",
                "simulator.adapter.indicator",
            ],
        ],
        ids=["one_module", "three_modules"],
    )
    def test_the_import_issues_no_path_mutation(self, tmp_path: Path, targets):
        """import 対象 1 件 / 3 件の 2 点で、書き換え発行 0・エントリ増分 0。

        発行（sys.path の書き換え）− 使用（解決に必要な増分 = 0、台帳が済ませている）= 0。
        回数を期待値へ焼き込むのではなく「無駄が 0 である」ことを固定する。
        """
        got = _measure_import(targets, tmp_path)
        assert got["mutations"] == 0, got
        assert got["delta"] == 0, got

    def test_the_spy_catches_an_unguarded_insert(self, tmp_path: Path):
        """検出力の実測: 書き換える module を 1 本混ぜると Spy が数える。"""
        probe_dir = tmp_path / "probe"
        probe_dir.mkdir()
        (probe_dir / "_wave2_probe_inserts_path.py").write_text(
            "import sys\nsys.path.insert(0, '/wave2/probe')\n", encoding="utf-8"
        )
        got = _measure_import(
            ["_wave2_probe_inserts_path"], tmp_path, extra_path=str(probe_dir)
        )
        assert got["mutations"] == 1, got
        assert got["delta"] == 1, got


class TestTheGateDoesNotWasteWork:
    """走査そのものに読み捨てが無いこと（発行 − 使用 = 0）。"""

    def test_every_scanned_file_is_read_exactly_once(self):
        reads: "list[Path]" = []
        scanned, _ = _scan(
            _scanned_modules(),
            read=lambda p: (reads.append(p), p.read_text(encoding="utf-8"))[1],
        )
        assert len(reads) - len(scanned) == 0
        assert len(set(scanned)) - len(scanned) == 0

    @pytest.mark.parametrize("count", [3, 6], ids=["files_3", "files_6"])
    def test_the_read_count_is_determined_by_the_file_count_alone(self, tmp_path, count):
        files = []
        for i in range(count):
            path = tmp_path / f"m{i}.py"
            path.write_text("x = 1\n", encoding="utf-8")
            files.append(path)
        reads: "list[Path]" = []
        scanned, _ = _scan(
            files, read=lambda p: (reads.append(p), p.read_text(encoding="utf-8"))[1]
        )
        assert len(reads) - len(scanned) == 0
        assert len(scanned) - count == 0
