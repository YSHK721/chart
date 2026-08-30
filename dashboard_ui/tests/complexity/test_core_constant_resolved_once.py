"""計算量 10（ISSUE-464 ②）: ライブ core の定数はプロセス寿命で 1 回だけ解決する。

RSI の上限（`levels.RSI_MAX`）は指標 core の定数である。にもかかわらず超過分の定義
（`(v - u) / (100 - u)`）がそれを**評価のたびに**引き直していた。1 回の解決は bridge の
探索パス準備とモジュール import を通るため安くない。§9-4 の実測（2026-08-30・
8 足束 1 要求）では **31,788 回 / 410 ms** を占めていた。

出力は正しいままなので状態検証では原理的に落ちない（ISSUE-450 / ISSUE-257 と同型）。
CLAUDE.md 絶対命令 §4.1 に従い、測るのは時間ではなく**回数**である。回数そのものは
期待値に焼き込まない。固定するのは次の 2 つだけである。

- **無駄の不在**: 超過分の評価を増やしても、定数の解決の**追加は 0**。
- **オーダーの表明**: 評価数 2 点（10 / 1000）で解決回数が同一である。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from dashboard_ui.adapter import series_role_table


@pytest.fixture
def resolutions(monkeypatch):
    """定数の解決だけを数える Test Spy（超過分の計算そのものは数えない）。"""
    counted: "list[tuple[str, str]]" = []

    def counting(indicator_id: str, submodule: str):
        counted.append((indicator_id, submodule))
        return SimpleNamespace(RSI_MAX=100.0)

    monkeypatch.setattr(series_role_table, "_indicator_module", counting)
    series_role_table.reset_core_constants()
    yield counted
    # 偽の定数を次の検定へ持ち越さない（プロセス寿命の保持を検査しているため後始末が要る）。
    series_role_table.reset_core_constants()


def test_more_excess_evaluations_do_not_resolve_the_ceiling_again(resolutions) -> None:
    """オーダーの表明（2 点固定）: 評価 10 回でも 1000 回でも解決回数は変わらない。"""
    resolved = {}
    for evaluations in (10, 1000):
        series_role_table.reset_core_constants()
        resolutions.clear()
        for _ in range(evaluations):
            series_role_table._rsi_headroom_excess(95.0, 90.0)   # noqa: SLF001
        resolved[evaluations] = len(resolutions)

    assert resolved[10] == resolved[1000]


def test_the_resolved_ceiling_is_the_one_the_excess_uses(resolutions) -> None:
    """発行 − 使用 = 0 の裏返し: 解決した定数がそのまま出力に使われる。"""
    value = series_role_table._rsi_headroom_excess(95.0, 90.0)   # noqa: SLF001

    assert len(resolutions) == 1
    assert value == (95.0 - 90.0) / (100.0 - 90.0)
