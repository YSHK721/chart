"""``indigators/`` を dev_paths 台帳へ載せる根拠を実測で固定する（ISSUE-479 Wave2 2-1）。

なぜ台帳へ載せるのか:
    moving_averages（MQL 忠実 MA の共有実装）は indigators/moving_averages に住む
    固有名トップパッケージである。これを解決するために
    ``simulator/adapter/indicator/madiff.py`` は import 時に ``sys.path`` を書き換えていた。
    実行時の ``sys.path`` 書き換えは「誰がいつ入れたか」がプロセスの import 順に依存し、
    起動経路ごとに解決先が変わる（ISSUE-279 の病因と同型）。パス解決の権威は台帳
    ``tools/dev_paths.txt`` 1 点であるべきで、モジュールの副作用であってはならない。

台帳の規律（``tools/dev_paths.txt`` の :17-19）との関係:
    載せてよいのは「衝突しない固有名を露出するツリー」だけである。``indigators/`` を
    載せると、その直下の全ディレクトリが（``__init__.py`` の有無に関わらず、PEP 420 の
    名前空間パッケージとして）トップレベル名になる。したがって「``__init__.py`` を持つ
    3 件だけを見る」調査では不十分で、**直下の全エントリが露出する名前**を対象に、
    それが既存の解決先を覆い隠さないことを実測で示す必要がある。
    本ファイルはその実測を検定として常設する（調査結果を文章で残すのではなく検査にする）。

固定する不変条件:
    1. 台帳が ``indigators`` を含み、そこから moving_averages が解決できる。
    2. ``indigators/`` が露出するどのトップレベル名も、台帳へ載せる前は解決先を持たない
       （＝新規に覆い隠すものが 1 件も無い）。
    3. 解決先はすべて ``indigators/`` ツリーの内側である。
    4. 台帳から組み立てた ``sys.path`` に ``indigators`` が現れるのはちょうど 1 回。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_LEDGER = _ROOT / "tools" / "dev_paths.txt"
_INDIGATORS = _ROOT / "indigators"

#: 台帳へ追加するエントリ（リポジトリ根からの相対）。
_ENTRY = "indigators"


def _ledger_relative() -> "list[str]":
    """台帳の有効行（`#` 始まりと空行を除く）。"""
    out: "list[str]" = []
    for raw in _LEDGER.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def _abs_entries(entries: "list[str]") -> "list[str]":
    return [str(_ROOT if e == "." else _ROOT / e) for e in entries]


def _exposed_names() -> "list[str]":
    """``indigators/`` を ``sys.path`` へ載せたときに生まれるトップレベル名の全集合。

    ディレクトリは名前空間パッケージ（PEP 420）として、``.py`` はモジュールとして
    露出する。``__init__.py`` の有無で絞らないのが本関数の要点である。
    """
    names: "set[str]" = set()
    for child in _INDIGATORS.iterdir():
        if child.name.startswith((".", "__")):
            continue
        if child.is_dir():
            names.add(child.name)
        elif child.suffix == ".py":
            names.add(child.stem)
    return sorted(names)


def _resolve_in_subprocess(names: "list[str]", pythonpath: "list[str]", cwd: Path) -> dict:
    """クリーンな interpreter で ``find_spec`` の解決先を引く（1 構成につき 1 回だけ起動）。

    起動を名前ごとに分けないのは、測るべきが「名前の解決先」であって
    「interpreter の起動回数」ではないからである（計算量検定が発行 − 使用 = 0 を数える）。

    ``-S`` が要る理由（対照条件の回復・ISSUE-482 で実測）:
        本関数は「``pythonpath`` に与えた構成だけ」で名前を引く対照実験の器である。ところが
        素の起動は site を読むため venv の ``jp225_chart_paths.pth``（``install_dev_paths.py``
        が書く台帳の恒久登録）が sys.path へ加わり、**PYTHONPATH から entry を外しても
        同じ entry が裏口から入る**。実測: ``PYTHONPATH=<repo>`` だけを与えても
        moving_averages が ``<repo>/indigators`` から解決してしまい、
        「entry を外した構成」が作れなかった。``-S`` は site を読まないため .pth が適用されず、
        PYTHONPATH は従来どおり効く（``-E`` ではないので PYTHONPATH は落ちない）。
        以前これが問題にならなかったのは .pth が未導入だったからであり、対照が成立していたのは
        偶然である。条件を変えたのではなく、変数を隔離し直したという位置づけである。
    """
    code = (
        "import importlib.util, json, sys\n"
        "out = {}\n"
        "for n in json.loads(sys.argv[1]):\n"
        "    try:\n"
        "        spec = importlib.util.find_spec(n)\n"
        "    except Exception:\n"
        "        spec = None\n"
        "    if spec is None:\n"
        "        out[n] = None\n"
        "    elif spec.origin not in (None, 'namespace'):\n"
        "        out[n] = spec.origin\n"
        "    else:\n"
        "        locs = list(spec.submodule_search_locations or [])\n"
        "        out[n] = locs[0] if locs else None\n"
        "print(json.dumps(out))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-S", "-c", code, json.dumps(names)],
        cwd=str(cwd),
        env={"PYTHONPATH": ":".join(pythonpath), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    return json.loads(proc.stdout)


class TestTheLedgerResolvesTheSharedIndicatorLibraries:
    """台帳 1 点で ``indigators/`` の固有名が解決できること。"""

    def test_the_ledger_carries_the_indigators_tree(self):
        assert _ENTRY in _ledger_relative(), (
            "tools/dev_paths.txt に indigators がありません"
            "（moving_averages の解決を madiff.py の sys.path 書き換えに頼ることになります）"
        )

    def test_moving_averages_resolves_from_the_indigators_tree(self, tmp_path: Path):
        """**実行して固定**: 台帳だけを与えた interpreter で共有 MA 実装が解決する。"""
        got = _resolve_in_subprocess(
            ["moving_averages"], _abs_entries(_ledger_relative()), tmp_path
        )
        want = str(_INDIGATORS / "moving_averages" / "__init__.py")
        assert got["moving_averages"] == want, got


class TestTheIndigatorsEntryShadowsNothing:
    """台帳へ載せることで既存の解決先が覆い隠されないこと（衝突不在の実測）。"""

    def test_the_exposed_name_set_is_not_empty(self):
        """走査が空振りしていない（恒真式に退化していない）。"""
        names = _exposed_names()
        assert "moving_averages" in names
        assert len(names) > 3, (
            "露出する名前は __init__.py を持つ 3 件だけではない"
            "（名前空間パッケージも露出する）: " + repr(names)
        )

    def test_no_exposed_name_had_a_resolution_before_the_entry(self, tmp_path: Path):
        """``indigators`` を外した構成では、露出する名前が 1 つも解決しない。"""
        without = [p for p in _abs_entries(_ledger_relative()) if p != str(_INDIGATORS)]
        got = _resolve_in_subprocess(_exposed_names(), without, tmp_path)
        collisions = {n: origin for n, origin in got.items() if origin is not None}
        assert collisions == {}, (
            "indigators/ が露出する名前が既に別の場所で解決しています（覆い隠しが起きます）: "
            + repr(collisions)
        )

    def test_every_exposed_name_resolves_inside_the_indigators_tree(self, tmp_path: Path):
        """台帳込みの構成では、露出する名前がすべて ``indigators/`` の内側から解決する。"""
        got = _resolve_in_subprocess(
            _exposed_names(), _abs_entries(_ledger_relative()), tmp_path
        )
        unresolved = sorted(n for n, origin in got.items() if origin is None)
        assert unresolved == [], unresolved
        outside = {
            n: origin
            for n, origin in got.items()
            if not Path(origin).is_relative_to(_INDIGATORS)
        }
        assert outside == {}, outside

    def test_the_entry_appears_exactly_once_on_the_path(self, tmp_path: Path):
        """台帳から組み立てた ``sys.path`` に ``indigators`` はちょうど 1 回だけ現れる。"""
        code = (
            "import json, sys\n"
            "print(json.dumps(sys.path))\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(tmp_path),
            env={
                "PYTHONPATH": ":".join(_abs_entries(_ledger_relative())),
                "PATH": "/usr/bin:/bin",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr[-800:]
        path = json.loads(proc.stdout)
        assert path.count(str(_INDIGATORS)) == 1, path


class TestTheLedgerDoesNotProduceWastedPathEntries:
    """計算量検定（Test Spy・発行 − 使用 = 0）。測るのは時間ではなく回数。"""

    @pytest.mark.parametrize("count", [2, 4], ids=["entries_2", "entries_4"])
    def test_the_path_entry_count_is_determined_by_the_ledger_line_count(
        self, tmp_path: Path, count: int
    ):
        """台帳 2 行 / 4 行の 2 点で「組み立てたエントリ数 == 台帳の行数」。

        重複エントリや読み捨てが 1 件でもあれば差が 0 にならない。行数そのものを
        期待値へ焼き込まない（オーダーの表明であって実装詳細の固定ではない）。
        """
        fake_root = tmp_path / "tree"
        (fake_root / "tools").mkdir(parents=True)
        lines = ["# 見出しは無視される", ""] + [f"pkg{i}" for i in range(count)]
        (fake_root / "tools" / "dev_paths.txt").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
        (fake_root / "tools" / "dev_paths.sh").write_text(
            (_ROOT / "tools" / "dev_paths.sh").read_text(encoding="utf-8"), encoding="utf-8"
        )

        script = (
            f'REPO_ROOT="{fake_root}"\n'
            f'. "{fake_root}/tools/dev_paths.sh"\n'
            'printf "%s" "$PYTHONPATH"\n'
        )
        proc = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            check=False,
            env={"PATH": "/usr/bin:/bin"},
        )
        assert proc.returncode == 0, proc.stderr[-800:]
        produced = [p for p in proc.stdout.split(":") if p]
        # 発行（PYTHONPATH へ載せたエントリ）− 使用（台帳の有効行）= 0。
        assert len(produced) - count == 0, (produced, count)
        assert len(set(produced)) - len(produced) == 0, produced
