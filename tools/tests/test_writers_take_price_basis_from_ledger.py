"""ティック由来 M1 の書き手は価格基準を**渡さない**（権威が台帳から引く・ISSUE-511 段階 3 の段階 6）。

用語: 価格基準＝ティックの bid/ask のどちらを価格とするか（``"mid"`` / ``"bid"``・語彙は
marketdata/tick_m1.py、ref ごとの値は marketdata/dataset_registry.py の ``price_basis``）。

本ファイルの目的の移設（段階 6・V-3）:
    段階 1a では「書き手（tools/live_tick_watch.py の増分・tools/build_tick_rollup.py の全量/増分）が
    台帳を引いて ``price_basis`` を**渡す**」ことを固定していた。それだと同じ事実が台帳と引数の
    2 源になり、**渡し忘れた書き手だけが既定（mid）で走る**。実例が V-6 で、CLI
    （marketdata/tools/tick_m1_cli.py）が台帳の bid を名乗る置き場へ mid の足を書いていた
    （marketdata/tests/test_tick_m1_price_basis_single_source.py の R-17 が 2026-09-17 に実測）。
    段階 6 で基準の解決を素材化の権威（marketdata/tick_m1.py）へ 1 本化したので、ここが固定するのは
    「書き手は渡さない」と「権威が ``ref`` から台帳を引く」の 2 つである。

構造: Arrange-Act-Assert（AAA）。書込はすべて ``tmp_path``。
"""
from __future__ import annotations

import datetime as dt

import pytest

from marketdata import dataset_registry, tick_m1
from tools import build_tick_rollup as btr
from tools import live_tick_watch as ltw


class _Spy:
    """tick_m1 の書き出し関数の代わり。受けた kwargs を控える（書き出しはしない）。"""

    def __init__(self) -> None:
        self.calls: "list[dict]" = []

    def __call__(self, start, end, **kwargs):  # noqa: ANN001, ARG002
        self.calls.append(kwargs)
        return "unused"


@pytest.fixture
def spies(monkeypatch):
    out = {
        name: _Spy()
        for name in (
            "append_m1_from_ticks", "build_m1_from_ticks",
            # 組の口（ISSUE-533 段階 3 の前提工事）。書き手はこちらを通る。
            "append_m1_from_ticks_for_series", "build_m1_from_ticks_for_series",
        )
    }
    for name, spy in out.items():
        monkeypatch.setattr(tick_m1, name, spy)
    return out


def _live_append(tmp_path) -> None:
    ltw._append_m1("2026-09-01", "2026-09-02", until=None, data_dir=tmp_path)


def _rollup_build(tmp_path, *, full: bool, days: int = 1) -> None:
    start = dt.date(2026, 9, 1)
    btr._build_tick_m1(
        start, start + dt.timedelta(days=days - 1), refs=(btr.REF,), data_dir=tmp_path,
        full_rebuild=full,
    )


def _basis_seam(monkeypatch) -> "list[str]":
    """継ぎ目 ``dataset_registry.tick_price_basis`` を包み、引かれた ref を記録する。"""
    real = dataset_registry.tick_price_basis
    seen: "list[str]" = []

    def recorded(ref):
        seen.append(ref)
        return real(ref)

    monkeypatch.setattr(dataset_registry, "tick_price_basis", recorded)
    return seen


# --------------------------------------------------------------------------- #
# 1. 書き手は基準を渡さない
# --------------------------------------------------------------------------- #
def test_the_live_writer_passes_no_basis(spies, tmp_path):
    """ライブの増分書き手は ``price_basis`` を渡さない（登録済み ref では拒否される）。"""
    # Arrange / Act
    _live_append(tmp_path)

    # Assert
    calls = spies["append_m1_from_ticks_for_series"].calls
    assert len(calls) == 1   # 空振り防止（実際に呼んだ）
    assert "price_basis" not in calls[0]


@pytest.mark.parametrize(
    "full, entry",
    [
        (False, "append_m1_from_ticks_for_series"),
        (True, "build_m1_from_ticks_for_series"),
    ],
)
def test_the_pipeline_writer_passes_no_basis(spies, tmp_path, full, entry):
    """パイプラインの書き手（増分・全量）も ``price_basis`` を渡さない。"""
    # Arrange / Act
    _rollup_build(tmp_path, full=full)

    # Assert
    assert len(spies[entry].calls) == 1                    # 空振り防止（実際に呼んだ）
    assert "price_basis" not in spies[entry].calls[0]


# --------------------------------------------------------------------------- #
# 2. 権威が台帳から引く
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("full", [False, True], ids=["append", "build"])
def test_the_authority_pulls_the_basis_from_the_ledger(monkeypatch, tmp_path, full):
    """書き手が渡さなくても、権威が書き手の ref で台帳を引く（既定 mid へ落ちない）。

    ティック parquet を置かないので素材化は :class:`FileNotFoundError` で止まる。基準の解決は
    その手前で済むため、ここで測るのは「引いたかどうか」だけであり、出力には触れない。
    """
    # Arrange
    seen = _basis_seam(monkeypatch)

    # Act
    with pytest.raises(FileNotFoundError):
        _rollup_build(tmp_path, full=full)

    # Assert
    assert seen == [btr.REF], f"台帳を引いた ref: {seen}"


def test_a_writer_that_passes_a_basis_is_refused(tmp_path):
    """回帰防止: 書き手が基準を渡す形へ戻すと、登録済み ref では拒否される（2 源を作れない）。"""
    # Arrange / Act / Assert
    with pytest.raises(ValueError, match="台帳"):
        tick_m1.append_m1_from_ticks(
            "2026-09-01", "2026-09-02", ref=btr.REF, data_dir=tmp_path,
            price_basis=tick_m1.PRICE_BASIS_MID,
        )


# --------------------------------------------------------------------------- #
# 3. 計算量（基準の解決は呼び出しあたり 1 回・期間の日数に比例しない）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("days", [1, 30])
def test_the_basis_is_resolved_once_per_call_whatever_the_span(monkeypatch, tmp_path, days):
    """解決回数は期間の日数（1 日 / 30 日の 2 点）に依らない（日ごとに台帳を引き直さない）。

    回数そのものは期待値に焼き込まない。固定するのは「引いた ref が書き手の ref だけ」で
    あること、すなわち日数に比例して増える読みが無いことである。
    """
    # Arrange
    seen = _basis_seam(monkeypatch)

    # Act
    with pytest.raises(FileNotFoundError):
        _rollup_build(tmp_path, full=True, days=days)

    # Assert
    assert seen == [btr.REF], f"{days} 日の期間で基準を {len(seen)} 回解決した"
