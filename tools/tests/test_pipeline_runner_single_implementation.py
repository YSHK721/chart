"""段階パイプライン実行器の単一実装を **検定で強制する**（ISSUE-502 D-17）。

台帳（.doc/solid_audit_20260906.md の D-17）が実測した状態: 段階選択と実行ループ
（開始ログ → 実行 → 例外捕捉 → 経過計測 → 終了ログ → 失敗時中断 → サマリ）が
``tools/acquire_marketdata.py`` と ``tools/build_tick_rollup.py`` に **docstring まで一字一句
同一**で二重に存在していた。ログ書式・中断規則・戻り値規約を変えるには 2 箇所を同時に直す
必要があり、片方だけの編集が無言で通った。

本ファイルは 2 つを固定する。

1. **特性化（出力等価）**: 両 CLI が出す進捗ログ・サマリの文言と戻り値が、統合前と同一である。
2. **第 2 実装の検出**: 実行ループの語彙（``=== stage %s 開始 ===`` 等）と
   ``select_stages`` / ``run_pipeline`` の非委譲実装が、権威 ``tools/pipeline_runner.py``
   以外に現れない。
"""
from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

import pytest

from tools import acquire_marketdata as am
from tools import build_tick_rollup as btr
from tools import pipeline_runner

_TOOLS = Path(__file__).resolve().parents[1]

#: 実行器の権威（ここだけが実行ループを持ってよい）。
_AUTHORITY = _TOOLS / "pipeline_runner.py"

#: 実行ループの語彙（第 2 実装が現れれば必ずこの文言を持つ）。
_RUNNER_MARKS = ("=== stage %s 開始 ===", "---- サマリ ----", "stage %s 例外: %s: %s")

#: 実行器へ委譲していなければならない関数名。
_DELEGATING_FUNCS = ("select_stages", "run_pipeline")


# =====================================================================
# 1. 特性化（統合前後で出力・戻り値が同一）
# =====================================================================

def _run(module, ctx, stages, caplog) -> "tuple[int, list[str]]":
    caplog.clear()
    with caplog.at_level(logging.INFO):
        rc = module.run_pipeline(ctx, stages)
    return rc, [r.getMessage() for r in caplog.records]


def test_success_run_emits_the_unchanged_log_vocabulary(monkeypatch, caplog) -> None:
    monkeypatch.setattr(btr, "stage_m1", lambda ctx: 0)
    rc, messages = _run(btr, btr.PipelineContext(), ["m1"], caplog)
    assert rc == 0
    assert messages[0] == "=== stage m1 開始 ==="
    assert messages[1].startswith("=== stage m1 終了 rc=0 elapsed=") and messages[1].endswith("s OK ===")
    assert messages[2] == "---- サマリ ----"
    assert messages[3].startswith("  m1      OK rc=0 ")


def test_failure_run_reports_ng_and_returns_1(monkeypatch, caplog) -> None:
    monkeypatch.setattr(btr, "stage_m1", lambda ctx: 3)
    rc, messages = _run(btr, btr.PipelineContext(), ["m1"], caplog)
    assert rc == 1
    assert any(m.startswith("=== stage m1 終了 rc=3 ") and m.endswith("s NG ===") for m in messages)
    assert any(m.startswith("  m1      NG rc=3 ") for m in messages)


def test_exception_is_recorded_with_type_and_message(monkeypatch, caplog) -> None:
    def boom(ctx):
        raise btr.PipelineError("boom")

    monkeypatch.setattr(btr, "stage_m1", boom)
    rc, messages = _run(btr, btr.PipelineContext(), ["m1"], caplog)
    assert rc == 1
    assert "stage m1 例外: PipelineError: boom" in messages
    assert any("(PipelineError: boom)" in m for m in messages)


def test_both_clis_emit_the_same_vocabulary(monkeypatch, caplog) -> None:
    """2 CLI の進捗ログが同一書式である（＝実装が 1 つであることの観測面）。"""
    monkeypatch.setattr(btr, "stage_m1", lambda ctx: 0)
    monkeypatch.setattr(am, "stage_bars", lambda ctx: 0)
    _, btr_msgs = _run(btr, btr.PipelineContext(), ["m1"], caplog)
    _, am_msgs = _run(am, am.PipelineContext(), ["bars"], caplog)

    def _shape(messages, stage):
        # 段階名（と ``%-7s`` の桁揃え）を除いた文言の形を比べる。
        return [re.sub(r"\s+", " ", m.replace(stage, "STAGE")).strip() for m in messages]

    assert _shape(btr_msgs, "m1") == _shape(am_msgs, "bars")


def test_each_cli_keeps_its_own_logger_name(monkeypatch, caplog) -> None:
    """ロガー名は CLI ごとに保たれる（実行器へ移しても出力元が変わらない）。"""
    monkeypatch.setattr(btr, "stage_m1", lambda ctx: 0)
    monkeypatch.setattr(am, "stage_bars", lambda ctx: 0)
    caplog.clear()
    with caplog.at_level(logging.INFO):
        btr.run_pipeline(btr.PipelineContext(), ["m1"])
        am.run_pipeline(am.PipelineContext(), ["bars"])
    names = {r.name for r in caplog.records}
    assert names == {"build_tick_rollup", "acquire_marketdata"}


def test_select_stages_contract_is_unchanged() -> None:
    assert btr.select_stages([], None) == ["acquire", "m1", "rollup"]
    assert btr.select_stages(["acquire"], "rollup") == ["rollup"]
    assert am.select_stages([], None) == ["bars", "daily", "ticks", "ingest"]
    assert am.select_stages(["daily", "ingest"], None) == ["bars", "ticks"]
    assert pipeline_runner.select_stages(("a", "b"), ["a"], None) == ["b"]


def test_continue_on_error_runs_the_rest(monkeypatch) -> None:
    calls: "list[str]" = []
    rc = pipeline_runner.run_pipeline(
        ["a", "b"],
        run_stage=lambda s: (calls.append(s), 1 if s == "a" else 0)[1],
        log=logging.getLogger("probe"),
        continue_on_error=True,
    )
    assert rc == 1 and calls == ["a", "b"]


def test_default_stops_at_the_first_failure() -> None:
    calls: "list[str]" = []
    rc = pipeline_runner.run_pipeline(
        ["a", "b"],
        run_stage=lambda s: (calls.append(s), 1)[1],
        log=logging.getLogger("probe"),
    )
    assert rc == 1 and calls == ["a"]


# =====================================================================
# 2. 第 2 実装の検出（tools 配下の走査）
# =====================================================================

def _sources() -> "list[Path]":
    """``tools`` 配下の本番コード（テスト・パッケージ初期化子・権威を除く）。"""
    return sorted(
        p for p in _TOOLS.rglob("*.py")
        if p.name != "__init__.py"
        and p != _AUTHORITY
        and "tests" not in p.parts
        and "__pycache__" not in p.parts
    )


def _read_source(path: Path) -> str:
    """走査の読込点（計算量検定が発行回数を数えるための単一の入口）。"""
    return path.read_text(encoding="utf-8")


def _runner_offenders_over(files, read=None) -> "list[str]":
    """実行ループの語彙を持つ**コード行**を列挙する（1 ファイルにつき読込 1 回）。"""
    reader = read or _read_source
    offenders: "list[str]" = []
    for path in files:
        for i, line in enumerate(reader(path).splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue          # コメントの言及は対象外（説明を禁じない）
            for mark in _RUNNER_MARKS:
                if mark in line:
                    offenders.append(f"{path.name}:{i}: {mark}")
    return offenders


def _non_delegating_over(files, read=None) -> "list[str]":
    """``select_stages`` / ``run_pipeline`` を自前実装している関数を列挙する。"""
    reader = read or _read_source
    offenders: "list[str]" = []
    for path in files:
        text = reader(path)
        lines = text.splitlines()
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in _DELEGATING_FUNCS:
                continue
            src = "\n".join(lines[node.lineno - 1: node.end_lineno])
            if "pipeline_runner." not in src:
                offenders.append(f"{path.name}:{node.lineno}: {node.name}")
    return offenders


def test_the_run_loop_exists_only_in_the_authority() -> None:
    """実行ループの語彙が ``tools/pipeline_runner.py`` 以外に現れない。

    落ちた場合の直し方: 該当箇所を ``pipeline_runner.run_pipeline`` への委譲へ置換する。
    ログ書式・中断規則を変えたいなら、権威 1 箇所だけを変える。
    """
    offenders = _runner_offenders_over(_sources())
    assert not offenders, (
        "実行ループの第 2 実装があります:\n  " + "\n  ".join(offenders)
        + "\n  tools.pipeline_runner への委譲へ置換してください。"
    )


def test_stage_selection_and_run_are_always_delegated() -> None:
    """同名関数が残っていても、中身は必ず権威への委譲である。"""
    offenders = _non_delegating_over(_sources())
    assert not offenders, (
        "権威へ委譲していない実装があります:\n  " + "\n  ".join(offenders)
        + "\n  tools.pipeline_runner.select_stages / run_pipeline を呼んでください。"
    )


def test_the_authority_itself_holds_the_run_loop() -> None:
    """権威側には実行ループの語彙が存在する（走査が恒真式でないことの片側）。

    走査そのもの（``_runner_offenders_over``）を権威へ向けて実行する。ソース文字列を直接
    assertion に持ち込まない（検定 C2）。
    """
    hits = _runner_offenders_over([_AUTHORITY])
    missing = [mark for mark in _RUNNER_MARKS if not any(h.endswith(mark) for h in hits)]
    assert not missing, f"権威側に実行ループの語彙がありません: {missing}"


@pytest.mark.parametrize("mark", _RUNNER_MARKS, ids=["start", "summary", "exception"])
def test_the_runner_scan_has_detection_power(tmp_path, mark) -> None:
    """走査が恒真式に退化していないこと（合成ソースで検出できる）。"""
    p = tmp_path / "probe.py"
    p.write_text(f'LOG.info("{mark}", stage)\n', encoding="utf-8")
    assert _runner_offenders_over([p])


def test_the_runner_scan_ignores_comments(tmp_path) -> None:
    p = tmp_path / "probe.py"
    p.write_text('# 進捗は "=== stage %s 開始 ===" として出る（実体は pipeline_runner）\n',
                 encoding="utf-8")
    assert _runner_offenders_over([p]) == []


def test_the_delegation_scan_has_detection_power(tmp_path) -> None:
    p = tmp_path / "probe.py"
    p.write_text(
        "def select_stages(skip, only):\n"
        "    if only is not None:\n"
        "        return [only]\n"
        "    return [s for s in STAGE_NAMES if s not in set(skip)]\n",
        encoding="utf-8",
    )
    assert _non_delegating_over([p])


def test_the_delegation_scan_accepts_delegation(tmp_path) -> None:
    p = tmp_path / "probe.py"
    p.write_text(
        "def select_stages(skip, only):\n"
        "    return pipeline_runner.select_stages(STAGE_NAMES, skip, only)\n",
        encoding="utf-8",
    )
    assert _non_delegating_over([p]) == []


# ---------------------------------------------------------------------
# 計算量検定（Test Spy・発行 − 使用 = 0）
# ---------------------------------------------------------------------

def test_every_source_is_read_exactly_once_by_the_runner_scan() -> None:
    """読込集合 == 走査対象。読み捨ても二度読みも無い（発行 − 使用 = 0）。"""
    reads: "list[Path]" = []
    _runner_offenders_over(
        _sources(), read=lambda p: (reads.append(p), p.read_text(encoding="utf-8"))[1]
    )
    used = _sources()
    assert len(reads) - len(used) == 0
    assert set(reads) == set(used)
    assert len(set(reads)) - len(reads) == 0


def test_the_read_count_is_determined_by_the_file_count_alone(tmp_path) -> None:
    """走査対象 4 件 / 8 件の 2 点で「読込数 == ファイル数」（オーダーの表明）。"""
    measured: "dict[int, int]" = {}
    for count in (4, 8):
        files = []
        for i in range(count):
            p = tmp_path / f"m{count}_{i}.py"
            p.write_text("x = 1\n" * (i + 1), encoding="utf-8")
            files.append(p)
        reads: "list[Path]" = []
        _runner_offenders_over(
            files, read=lambda p: (reads.append(p), p.read_text(encoding="utf-8"))[1]
        )
        measured[count] = len(reads)
    assert measured == {4: 4, 8: 8}
