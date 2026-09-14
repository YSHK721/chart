"""ティック由来 M1 の書き手が **台帳の価格基準** で畳む（ISSUE-511 段階 1a）。

用語: 価格基準＝ティックの bid/ask のどちらを価格とするか（``"mid"`` / ``"bid"``・語彙は
marketdata/tick_m1.py、ref ごとの値は marketdata/dataset_registry.py の ``price_basis``）。

なぜ必要か（ISSUE-511 実測）:
    ``jp225_tick`` の書き手 2 本（tools/live_tick_watch.py の増分・tools/build_tick_rollup.py の
    全量/増分）は ``price_basis`` を渡さず、tick_m1 の既定（mid）で畳んでいた。読み手（形成中バー・
    MP・ライブバッファ）は ISSUE-515 で台帳の基準を引くようになったので、書き手だけが既定に
    頼ると、台帳を bid へ切り替えた瞬間に「確定足は mid・形成中は bid」へ割れる（確定のたびに跳ねる）。

本段階は **出力を変えない**（台帳の jp225_tick は mid のまま）。固定するのは「書き手が台帳から
引いている」ことだけで、台帳を変えれば書き手も追随することを差し替えで示す。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import datetime as dt

import pytest

from marketdata import dataset_registry, tick_m1
from tools import build_tick_rollup as btr
from tools import live_tick_watch as ltw


class _Spy:
    """tick_m1 の書き出し関数の代わり。受けた price_basis を控える（書き出しはしない）。"""

    def __init__(self) -> None:
        self.bases: "list[str]" = []

    def __call__(self, start, end, **kwargs):  # noqa: ANN001, ARG002
        self.bases.append(kwargs.get("price_basis"))
        return "unused"


@pytest.fixture
def spies(monkeypatch):
    out = {name: _Spy() for name in ("append_m1_from_ticks", "build_m1_from_ticks")}
    for name, spy in out.items():
        monkeypatch.setattr(tick_m1, name, spy)
    return out


def _live_append(tmp_path) -> None:
    ltw._append_m1("2026-09-01", "2026-09-02", until=None, data_dir=tmp_path)


def _rollup_build(tmp_path, *, full: bool, days: int = 1) -> None:
    start = dt.date(2026, 9, 1)
    btr._build_tick_m1(
        start, start + dt.timedelta(days=days - 1), ref=btr.REF, data_dir=tmp_path,
        full_rebuild=full,
    )


# --------------------------------------------------------------------------- #
# 1. 台帳の基準を渡す（今は mid＝出力不変）
# --------------------------------------------------------------------------- #
def test_the_live_writer_passes_the_ledger_basis(spies, tmp_path):
    """ライブの増分書き手は、台帳の jp225_tick の基準をそのまま渡す。"""
    # Arrange / Act
    _live_append(tmp_path)

    # Assert
    assert spies["append_m1_from_ticks"].bases == [dataset_registry.tick_price_basis(ltw.REF)]


@pytest.mark.parametrize("full, entry", [(False, "append_m1_from_ticks"), (True, "build_m1_from_ticks")])
def test_the_pipeline_writer_passes_the_ledger_basis(spies, tmp_path, full, entry):
    """パイプラインの書き手（増分・全量）は、台帳の基準をそのまま渡す。"""
    # Arrange / Act
    _rollup_build(tmp_path, full=full)

    # Assert
    assert spies[entry].bases == [dataset_registry.tick_price_basis(btr.REF)]


# --------------------------------------------------------------------------- #
# 2. 台帳に追随する（手書きの既定値を持っていない）
# --------------------------------------------------------------------------- #
def test_both_writers_follow_the_ledger_when_it_changes(monkeypatch, spies, tmp_path):
    """台帳の基準を bid へ差し替えると、2 本の書き手とも bid を渡す（既定値へ落ちない）。"""
    # Arrange
    monkeypatch.setattr(
        dataset_registry, "tick_price_basis",
        lambda ref: tick_m1.PRICE_BASIS_BID if ref == "jp225_tick" else None,
    )

    # Act
    _live_append(tmp_path)
    _rollup_build(tmp_path, full=False)

    # Assert
    assert spies["append_m1_from_ticks"].bases == [tick_m1.PRICE_BASIS_BID] * 2


# --------------------------------------------------------------------------- #
# 3. 計算量（基準の解決は呼び出しあたり 1 回・期間の日数に比例しない）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("days", [1, 30])
def test_the_basis_is_resolved_once_per_call_whatever_the_span(monkeypatch, spies, tmp_path, days):
    """解決回数は期間の日数（1 日 / 30 日の 2 点）に依らず 1 回（日ごとに台帳を引き直さない）。"""
    # Arrange
    calls = []
    real = dataset_registry.tick_price_basis
    monkeypatch.setattr(
        dataset_registry, "tick_price_basis", lambda ref: (calls.append(ref), real(ref))[1]
    )

    # Act
    _rollup_build(tmp_path, full=True, days=days)

    # Assert
    assert calls == [btr.REF], f"{days} 日の期間で基準を {len(calls)} 回解決した"
