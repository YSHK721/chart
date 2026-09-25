"""静的ゲートが「散文の引用」と「コード上の参照」を区別することの検定（ISSUE-532 欠陥 4）。

なにが問題だったか:
    C1 は「コメント / docstring がバッククォートで名指す記号は、そのモジュールから到達可能で
    あること」を要求する。規則そのものは正しい（到達不能な名前を書けば読者の追跡が切れる）。
    しかし C1 は**散文で名前に触れただけ**の場合も同じ扱いにするため、設計の経緯を書くだけで
    import を要求された。本セッションで 10 回連続して引っかかった（ISSUE-532 欠陥 4）。

なにを替えたか:
    引用のための書式を設けた——名前を「`name`」のように鉤括弧で包んだものは**散文の引用**と
    みなし、C1 の到達可能性を要求しない。包まずに書いたものは従来どおり**コード上の参照**で
    あり、到達不能なら落ちる。ファイル内に 1 つでも包まれていない出現があれば落ちる（引用を
    1 つ足せばファイル全体の参照が免除される、という緩みを作らない）。

どう測るか:
    ゲートを**プロセスとして**起動し、終了コードを直接測る（0 = 新規違反なし・2 = 新規違反）。
    検定用の木は一時ディレクトリに作り、ゲートの実体（scripts 5 本）をそこへ複写して走らせる。
    本リポジトリの baseline には 1 バイトも触らない。この手法は既存検定
    （.claude/scripts/test_ident_stability.py）と同じである（手法を 2 つに割らない）。
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / ".claude" / "scripts"
_COPIED = ("run_quality_gate.py", "declaration_integrity.py", "test_quality.py",
           "quality_scope.py", "violation_key.py")

_AUTHORITY = "def widget():\n    return 1\n"
# 種の違反: baseline を非空にするためだけに置く（ゲートが「初回」扱いで黙る経路を潰す）。
_SEED = '"""種の違反: 到達不能な `widget` への参照（凍結して baseline を非空にする）。"""\n'
_PROSE = '"""設計の経緯で 「`widget`」 の名に触れるだけのモジュール（import しない）。"""\n'
_CODE_REF = '"""ここで `widget` を呼ぶと書いてあるが import していない。"""\n'
_MIXED = (
    '"""経緯では 「`widget`」 に触れ、別の行では `widget` をコード参照として書く。"""\n'
)
# 走査から除外されたディレクトリ内の**実在**ファイルを、コード参照として名指す。
_EXCLUDED_REF = '"""ゲートの実体 `.claude/scripts/quality_scope.py` を名指す。"""\n'
# 同じ形で、ディスク上に存在しないファイルを名指す。
_ABSENT_REF = '"""実在しない `.claude/scripts/ghost_module.py` を名指す。"""\n'


def _gate_tree(tmp_path: Path) -> "list[str]":
    """ゲートの実体を複写した木を作り、種の違反を凍結して argv を返す。"""
    scripts = tmp_path / ".claude" / "scripts"
    scripts.mkdir(parents=True)
    for name in _COPIED:
        shutil.copy(_SCRIPTS / name, scripts / name)
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "authority.py").write_text(_AUTHORITY, encoding="utf-8")
    (pkg / "seed.py").write_text(_SEED, encoding="utf-8")
    argv = [sys.executable, str(scripts / "run_quality_gate.py")]
    subprocess.run(argv + ["--write-baseline"], check=True, capture_output=True)
    return argv


def _run_gate(argv: "list[str]") -> "subprocess.CompletedProcess[str]":
    return subprocess.run(argv + ["--verbose"], capture_output=True, text=True)


def _load_gate():
    """ゲートを module として読む（sys.path を汚さずに継ぎ目を呼ぶため）。"""
    spec = importlib.util.spec_from_file_location(
        "run_quality_gate_under_test", _SCRIPTS / "run_quality_gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_seeded_violation_is_frozen_so_the_gate_really_compares(tmp_path):
    """前提の実証: baseline が非空で、その状態のゲートは exit 0 を返す。

    baseline が空だとゲートは「初回」として無条件に 0 を返す。以降の検定が
    その経路で緑になっていない（＝恒真でない）ことを、ここで先に固定する。
    """
    argv = _gate_tree(tmp_path)
    frozen = json.loads(
        (tmp_path / ".claude" / "scripts" / "di_baseline.json").read_text(encoding="utf-8"))

    result = _run_gate(argv)

    assert frozen == ["C1|pkg/seed.py|widget"]
    assert (result.returncode, result.stdout) == (0, "")


def test_a_prose_quotation_of_an_unreachable_name_passes_the_gate(tmp_path):
    """鉤括弧で包んだ引用は通る（散文で名前に触れるだけで import を要求されない）。"""
    argv = _gate_tree(tmp_path)
    (tmp_path / "pkg" / "prose.py").write_text(_PROSE, encoding="utf-8")

    result = _run_gate(argv)

    assert (result.returncode, result.stdout) == (0, "")


def test_a_code_reference_to_an_unreachable_name_still_fails_the_gate(tmp_path):
    """包まないコード参照は従来どおり落ちる（C1 の意図を 1 バイトも弱めていない証拠）。"""
    argv = _gate_tree(tmp_path)
    (tmp_path / "pkg" / "code_ref.py").write_text(_CODE_REF, encoding="utf-8")

    result = _run_gate(argv)

    assert result.returncode == 2
    assert "pkg/code_ref.py" in result.stdout


def test_a_file_mixing_a_quotation_and_a_code_reference_still_fails(tmp_path):
    """引用を 1 つ足しても、同じファイルの包まれていない参照は免除されない。"""
    argv = _gate_tree(tmp_path)
    (tmp_path / "pkg" / "mixed.py").write_text(_MIXED, encoding="utf-8")

    result = _run_gate(argv)

    assert result.returncode == 2
    assert "pkg/mixed.py" in result.stdout


def test_naming_a_real_file_inside_an_excluded_directory_passes_the_gate(tmp_path):
    """走査から外したディレクトリの**実在**ファイルを名指しても落ちない。

    「違反を探す対象か」と「ディスク上に在るか」は別の問いである。C1 のパス分岐は前者の索引
    （走査対象）で後者を代理していたため、除外ディレクトリ（.claude 等）の実在ファイルを
    名指すと「存在しない」と報告された（2026-09-25 実測・ゲート自身の実体を名指した場合）。
    存在の判定はファイルシステムに問う。
    """
    argv = _gate_tree(tmp_path)
    (tmp_path / "pkg" / "cites_excluded.py").write_text(_EXCLUDED_REF, encoding="utf-8")

    result = _run_gate(argv)

    assert (tmp_path / ".claude" / "scripts" / "quality_scope.py").is_file()
    assert (result.returncode, result.stdout) == (0, "")


def test_naming_a_file_that_is_absent_from_disk_still_fails_the_gate(tmp_path):
    """実在しないファイルの名指しは従来どおり落ちる（存在検定を弱めていない証拠）。"""
    argv = _gate_tree(tmp_path)
    (tmp_path / "pkg" / "cites_absent.py").write_text(_ABSENT_REF, encoding="utf-8")

    result = _run_gate(argv)

    assert not (tmp_path / ".claude" / "scripts" / "ghost_module.py").exists()
    assert result.returncode == 2
    assert "ghost_module.py" in result.stdout


@pytest.mark.parametrize("paths", [1, 3])
def test_the_existence_of_a_named_path_is_asked_once_per_violation(tmp_path, monkeypatch, paths):
    """計算量: 発行した実在問い合わせ − パス形の違反数 = 0（余分な探索をしない）。

    実在をディレクトリ走査（glob）で探し回る実装が生えたら、ここで捕まえる。
    """
    gate = _load_gate()
    violations = [
        gate.di.Violation(check="C1", path="pkg/m.py", line=1,
                          key=f"pkg/named_{i}.py", detail="名指されたファイルが存在しない")
        for i in range(paths)
    ]
    probes: "list[str]" = []
    real_is_file = Path.is_file
    monkeypatch.setattr(
        Path, "is_file",
        lambda self, *a, **k: (probes.append(self.as_posix()), real_is_file(self, *a, **k))[1])

    kept = gate.drop_false_path_reports(violations, tmp_path)

    assert kept == violations                    # どれも実在しないので 1 件も落とさない
    assert len(probes) == len(violations), (
        f"実在問い合わせを {len(probes)} 回発行したが、パス形の違反は {len(violations)} 件だけである")


@pytest.mark.parametrize("files", [1, 2])
def test_each_source_is_read_once_however_many_violations_it_carries(tmp_path, monkeypatch, files):
    """計算量: 発行した読み − 使った読み（相異なるファイル数）= 0。

    違反 1 件ごとにソースを読み直す実装（O(違反数) の I/O）が生えたら、ここで捕まえる。
    ファイル数 2 点で、発行が違反数ではなくファイル数だけで決まることを固定する。
    """
    gate = _load_gate()
    names = ("alpha_name", "beta_name", "gamma_name")
    for i in range(files):
        (tmp_path / f"m{i}.py").write_text(
            '"""' + " ".join(f"「`{n}`」" for n in names) + '"""\n', encoding="utf-8")
    violations = [
        gate.di.Violation(check="C1", path=f"m{i}.py", line=1, key=name, detail="到達不能")
        for i in range(files) for name in names
    ]
    reads: "list[str]" = []
    real_read = Path.read_text
    monkeypatch.setattr(
        Path, "read_text",
        lambda self, *a, **k: (reads.append(self.as_posix()), real_read(self, *a, **k))[1])

    kept = gate.drop_prose_quotations(violations, gate.source_reader(tmp_path))

    assert kept == []
    assert len(reads) == len({v.path for v in violations}), (
        f"読みを {len(reads)} 回発行したが、必要なファイルは "
        f"{len({v.path for v in violations})} 本だけである")
