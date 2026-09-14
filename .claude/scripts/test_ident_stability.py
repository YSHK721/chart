"""違反 ident の行移動不変性の回帰テスト（2026-08-30 裁定の機械的強制）。

欠陥: 旧キーは `L{lineno}` を埋め込み、凍結済み違反の上流への無関係な行挿入だけで
ident が変わって Stop フックが exit 2 → asyncRewake ループを起こした（隔離環境で再現済み）。

本テストが固定する性質:
  1. 無関係な行挿入で ident 集合が変わらない（T1/T4/T5/T6/T7/T8/C2/C3 の全該当種別）
  2. 同一内容の違反の件数増減は ident に反映される（disambiguate の `#k` 付番）
  3. `--prune-baseline` は削除専用（凍結集合との積集合しか書けない＝gate 弱体化が構造的に不可能）
  4. 計算量: 発行した digest − 出力キーに使った digest = 0（Test Spy・入力を増やしても比率不変）

    pytest .claude/scripts/test_ident_stability.py
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import declaration_integrity as di
import test_quality as tq
import violation_key as vk

HERE = Path(__file__).parent
DIGEST = re.compile(r"[0-9a-f]{12}")

TQ_SAMPLE = '''\
from unittest.mock import Mock, patch
import sys

sys.path.insert(0, "src")

def test_weak():
    assert True

def test_branch(x=1):
    if x:
        assert x == 1

def test_mock():
    m = Mock()
    with patch("os.getcwd"):
        assert m is not None

def helper():
    try:
        risky()
    except Exception:
        pass
'''

DI_SUT = "def f(x):\n    return x\n"

DI_SAMPLE = '''\
from pathlib import Path
from pkg.mod import f

def test_grep():
    s = Path("pkg/mod.py").read_text()
    assert "def f" in s

def test_taut():
    assert f(1) == f(2)
'''


def _tq_idents(root: Path) -> set[str]:
    return {v.ident() for v in tq.run(root, set(tq.ALL_CHECKS))}


def _di_idents(root: Path) -> set[str]:
    return {v.ident() for v in di.run(root, ("pkg",), {"C2", "C3"})}


def _write_tree(root: Path, test_src: str, di_test_src: str) -> None:
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "pkg").mkdir(exist_ok=True)
    (root / "pkg" / "mod.py").write_text(DI_SUT, encoding="utf-8")
    (root / "tests" / "test_sample.py").write_text(test_src, encoding="utf-8")
    (root / "tests" / "test_di.py").write_text(di_test_src, encoding="utf-8")


def test_idents_survive_unrelated_line_insertion(tmp_path: Path) -> None:
    _write_tree(tmp_path, TQ_SAMPLE, DI_SAMPLE)
    before_tq, before_di = _tq_idents(tmp_path), _di_idents(tmp_path)
    # 検出対象が実在すること（空集合同士の恒真比較を禁じる）
    kinds = {i.split("|")[0] for i in before_tq | before_di}
    assert {"T1", "T4", "T6", "T7", "T8", "C2", "C3"} <= kinds

    pad = "# 無関係な挿入行\n" * 5
    _write_tree(tmp_path, pad + TQ_SAMPLE, pad + DI_SAMPLE)
    assert _tq_idents(tmp_path) == before_tq
    assert _di_idents(tmp_path) == before_di


def test_duplicate_violations_get_stable_ordinals(tmp_path: Path) -> None:
    dup = "def test_two_weak():\n    assert True\n    assert True\n"
    _write_tree(tmp_path, dup, DI_SAMPLE)
    t1 = sorted(i for i in _tq_idents(tmp_path) if i.startswith("T1|"))
    assert len(t1) == 2 and t1[1] == t1[0] + "#2"

    # 1 件解消 -> 先頭 ident は不変のまま件数減が ident 集合に現れる
    _write_tree(tmp_path, dup.replace("    assert True\n    assert True\n",
                                      "    assert True\n"), DI_SAMPLE)
    assert sorted(i for i in _tq_idents(tmp_path) if i.startswith("T1|")) == [t1[0]]


def test_prune_baseline_only_removes(tmp_path: Path) -> None:
    scripts = tmp_path / ".claude" / "scripts"
    scripts.mkdir(parents=True)
    for f in ("run_quality_gate.py", "declaration_integrity.py", "test_quality.py",
              "quality_scope.py", "violation_key.py"):
        shutil.copy(HERE / f, scripts / f)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_sample.py").write_text(
        "def test_weak():\n    assert True\n", encoding="utf-8")

    gate = [sys.executable, str(scripts / "run_quality_gate.py")]
    subprocess.run(gate + ["--write-baseline"], check=True, capture_output=True)
    frozen = set(json.loads((scripts / "tq_baseline.json").read_text(encoding="utf-8")))
    assert len(frozen) == 1

    # 現存する違反は prune で消えない（現状維持）
    subprocess.run(gate + ["--prune-baseline"], check=True, capture_output=True)
    assert set(json.loads((scripts / "tq_baseline.json").read_text(encoding="utf-8"))) == frozen

    # 違反を解消 -> prune が凍結から除去し、外部の ident を注入しても追加はされない
    (tmp_path / "tests" / "test_sample.py").write_text(
        "def test_fixed():\n    assert 1 + 1 == 2\n", encoding="utf-8")
    (scripts / "tq_baseline.json").write_text(
        json.dumps(sorted(frozen | {"T1|bogus/test_x.py|test_y:deadbeefdead"})), encoding="utf-8")
    subprocess.run(gate + ["--prune-baseline"], check=True, capture_output=True)
    assert json.loads((scripts / "tq_baseline.json").read_text(encoding="utf-8")) == []


def test_digest_issuance_equals_usage(tmp_path: Path, monkeypatch) -> None:
    """計算量テスト: 発行した digest − 出力キーに使った digest = 0。

    抑止なしの木では、digest はキーに載る違反のためだけに計算される。
    「N 回」を焼き込まず、入力規模 2 点で発行＝使用の一致（無駄の不在）だけを固定する。
    """
    calls = {"n": 0}
    orig = vk.node_digest

    def spy(node):
        calls["n"] += 1
        return orig(node)

    monkeypatch.setattr(vk, "node_digest", spy)
    for n_files in (1, 3):
        _write_tree(tmp_path, TQ_SAMPLE, DI_SAMPLE)
        for i in range(1, n_files):
            (tmp_path / "tests" / f"test_more_{i}.py").write_text(TQ_SAMPLE, encoding="utf-8")
        calls["n"] = 0
        vs = tq.run(tmp_path, set(tq.ALL_CHECKS)) + di.run(tmp_path, ("pkg",), {"C2", "C3"})
        used = sum(1 for v in vs if DIGEST.search(v.key))
        assert used > 0
        assert calls["n"] - used == 0, f"digest 発行 {calls['n']} 件のうち {calls['n'] - used} 件が未使用"
