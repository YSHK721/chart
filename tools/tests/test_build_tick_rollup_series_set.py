"""オフラインの履歴生成パイプラインが**台帳由来の系列の組**を作る（ISSUE-533 段階 3 の前提工事）。

用語（初出定義）:
    系列の組
        ＝ 同じティック木を読む datasetRef の全体。所有者は台帳
          （``marketdata.dataset_registry.series_refs_of``）。
    種（seed）
        ＝ ``tools.build_tick_rollup.REF``。「どのティック木か」を指すだけで、作る系列の集合では
          ない（``PipelineContext.refs`` が台帳から導く）。
    1 回の走査
        ＝ 期間内の日別 parquet を 1 ファイル 1 回だけ読み、1 回だけ畳むこと。14 年ぶんの履歴を
          系列ごとに読み直すと、素材の読込が系列数に比例する。

これは**履歴を生成する口**である（依頼者が別段で実行する）。本ファイルは書込先を ``tmp_path`` に
限り、``data/marketdata/**`` を 1 バイトも触らない。

本検定が固定するもの:
  P-1 ``stage_m1`` が組の**全系列**の M1 を作り、spread 列を持つのは宣言した側だけである。
  P-2 ``stage_rollup`` が組の**全系列**の上位足を、系列ごとの専用サブ dir へ作る。
  P-3 既存系列の M1 は、組で作っても**その系列だけを作った**ときと byte 一致する。
  P-4 ``--full`` の全再構築でも P-1 / P-2 が成り立つ。
  CX-1 系列 1 → 2 で日別 parquet の読込と畳みの発行が増えない。発行 − 使用 = 0。規模 2 点
      （日数 1 日と 3 日）。**回数そのものは期待値に焼き込まない**。

本検定が固定しないもの（射程の明示）:
  - 取得段（ティックの取得）。取得は木ごとであって系列ごとではない（本段で不変）。
  - 常駐の結線。それは ``tools/tests/test_live_tick_watch_series_set.py``。

構造: Arrange-Act-Assert（AAA）。ネットワークは叩かない（ティックは合成して木へ置く）。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, List, NamedTuple

import pandas as pd
import pytest

from marketdata import csv_schema, dataset_registry, tick_m1
from tools import build_tick_rollup as btr

#: 合成ティックの起点（実時計を読まない）。
_DAY0 = dt.date(2026, 9, 1)


def _ledger_refs() -> "tuple[str, ...]":
    """種が指すティック木を読む系列の組（**綴りを書き写さない**期待値の素）。"""
    return dataset_registry.series_refs_of(btr.REF)


def _spread_ref() -> str:
    """組のうち spread 列を宣言する系列。"""
    declared = [
        ref for ref in _ledger_refs()
        if dataset_registry.spread_point_snapshot_of(ref) is not None
    ]
    return declared[0]


def _plain_refs() -> "tuple[str, ...]":
    """組のうち spread 列を持たない系列。"""
    return tuple(ref for ref in _ledger_refs() if ref != _spread_ref())


def _put_days(data_dir: Path, n_days: int) -> "list[dt.date]":
    """``n_days`` 日ぶんの合成ティックを木へ置き、その日付を返す（気配幅は分内で変わる）。"""
    days = [_DAY0 + dt.timedelta(days=k) for k in range(n_days)]
    for day in days:
        rows = [
            (
                pd.Timestamp(day) + pd.Timedelta(minutes=m, seconds=5 + 25 * k),
                66000.0 + 0.1 * m + 0.05 * k,
                66000.0 + 0.1 * m + 0.05 * k + 0.1 * (60 + m + (3, 0, 6)[k]),
            )
            for m in range(3) for k in range(3)
        ]
        path = tick_m1.day_parquet_path(day, data_dir=data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({
            "timestamp": pd.to_datetime([r[0] for r in rows]).tz_localize("UTC"),
            "bidPrice": [r[1] for r in rows],
            "askPrice": [r[2] for r in rows],
        }).to_parquet(path, index=False)
    return days


def _context(data_dir: Path, days: "list[dt.date]", *, full: bool = False):
    return btr.PipelineContext(
        today=days[-1], full_start=days[0], data_dir=data_dir, full_rebuild=full,
    )


def _columns(path: Path) -> "list[str]":
    return path.read_text(encoding="utf-8").splitlines()[0].split(",")


def _data_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    return max(len(path.read_text(encoding="utf-8").splitlines()) - 1, 0)


def _m1_path(ref: str, data_dir: Path) -> Path:
    return tick_m1.m1_csv_path(ref=ref, data_dir=data_dir)


class _Issued(NamedTuple):
    """1 回のパイプライン実行で観測した発行と、出力に使った素材の数。"""

    reads: int
    folds: int
    used: int
    written: int


# =====================================================================
# P-1 / P-2 / P-4. 組の全系列の M1 と上位足ができる
# =====================================================================
@pytest.mark.parametrize("full", [False, True], ids=["incremental", "full_rebuild"])
def test_the_pipeline_generates_every_series_of_the_set(tmp_path, full):
    """P-1 / P-2 / P-4: m1 段と rollup 段が組の全系列を作る（増分・全再構築の両方）。"""
    # Arrange
    refs = _ledger_refs()
    days = _put_days(tmp_path, 2)
    ctx = _context(tmp_path, days, full=full)

    # Act
    m1_code = btr.stage_m1(ctx)
    rollup_code = btr.stage_rollup(ctx)

    # Assert
    assert len(refs) >= 2, f"同じ木を読む系列が {len(refs)} 件（この検定が空振りしている）"
    assert (m1_code, rollup_code) == (0, 0)
    assert {ref: _data_rows(_m1_path(ref, tmp_path)) > 0 for ref in refs} == {
        ref: True for ref in refs
    }
    assert {
        ref: btr.PipelineContext(data_dir=tmp_path).rollup_dir_of(ref).joinpath(
            f"{dataset_registry.series_of(ref)}_5m.csv"
        ).is_file()
        for ref in refs
    } == {ref: True for ref in refs}
    assert _columns(_m1_path(_spread_ref(), tmp_path))[-1] == csv_schema.SPREAD_COLUMN
    assert {
        ref: csv_schema.SPREAD_COLUMN in _columns(_m1_path(ref, tmp_path))
        for ref in _plain_refs()
    } == {ref: False for ref in _plain_refs()}


# =====================================================================
# P-3. 既存系列の M1 は単独で作ったものと byte 一致
# =====================================================================
@pytest.mark.parametrize("ref", _ledger_refs())
def test_a_series_built_by_the_pipeline_matches_a_single_ref_build(tmp_path, ref):
    """P-3: パイプラインが組で書いた CSV は、その系列だけを書いた CSV と byte 一致する。"""
    # Arrange
    set_dir = tmp_path / "set"
    alone_dir = tmp_path / "alone"
    days = _put_days(set_dir, 2)
    _put_days(alone_dir, 2)

    # Act
    assert btr.stage_m1(_context(set_dir, days)) == 0
    alone = tick_m1.build_m1_from_ticks(
        days[0].isoformat(), days[-1].isoformat(), ref=ref, data_dir=alone_dir
    )

    # Assert
    assert _data_rows(alone) > 0  # 空振り防止（実際に書いている）
    assert _m1_path(ref, set_dir).read_bytes() == alone.read_bytes()


# =====================================================================
# CX-1. 系列を増やしても parquet 読込と畳みは増えない
# =====================================================================
def _issued(monkeypatch, data_dir: Path, *, drop: "str | None", n_days: int) -> _Issued:
    """``drop`` を台帳から外して m1 段を回し、発行を数える（系列数だけが違う 2 点を作る）。"""
    days = _put_days(data_dir, n_days)
    refs = _ledger_refs()
    reads: "List[Any]" = []
    folds: "List[int]" = []
    real_read = pd.read_parquet
    real_fold = tick_m1._fold_ticks

    def read_spy(*args, **kwargs):
        reads.append(args[0] if args else kwargs.get("path"))
        return real_read(*args, **kwargs)

    def fold_spy(ticks, *args, **kwargs):
        folds.append(len(ticks))
        return real_fold(ticks, *args, **kwargs)

    with monkeypatch.context() as patch:
        if drop is not None:
            patch.setattr(dataset_registry, "REGISTRY", {
                r: d for r, d in dataset_registry.REGISTRY.items() if r != drop
            })
        patch.setattr(pd, "read_parquet", read_spy)
        patch.setattr(tick_m1, "_fold_ticks", fold_spy)
        assert btr.stage_m1(_context(data_dir, days)) == 0
    written = len([r for r in refs if _data_rows(_m1_path(r, data_dir)) > 0])
    return _Issued(reads=len(reads), folds=len(folds), used=n_days, written=written)


@pytest.mark.parametrize("n_days", [1, 3])
def test_cx1_the_pipeline_reads_and_folds_each_day_once_for_the_whole_set(
    tmp_path, monkeypatch, n_days
):
    """CX-1: 系列 1 → 2 で読込・畳みの発行が増えず、発行 − 使用 = 0（規模 2 点）。

    固定するのは**無駄の不在**であり、回数そのもの（N 回）は期待値に焼き込まない（期待値は
    その期間に実在する日別 parquet の数から導く）。系列ごとに口を呼ぶ形へ戻す変異は、系列 1 と
    2 の差としてここに現れる——14 年ぶんの履歴では、その差が素材の読込 2 倍そのものである。
    """
    # Arrange / Act
    one = _issued(monkeypatch, tmp_path / f"one{n_days}", drop=_spread_ref(), n_days=n_days)
    two = _issued(monkeypatch, tmp_path / f"two{n_days}", drop=None, n_days=n_days)

    # Assert
    assert (one.written, two.written) == (1, len(_ledger_refs()))  # 空振り防止（系列数が違う）
    assert (one.reads - one.used, one.folds - one.used) == (0, 0)
    assert (two.reads - two.used, two.folds - two.used) == (0, 0), (
        f"系列 2 で 読込 {two.reads} / 畳み {two.folds} − 使用 {two.used} ≠ 0"
    )
    assert (two.reads, two.folds) == (one.reads, one.folds), (
        f"系列を 1 → 2 にしたら 読込 {one.reads} → {two.reads} /"
        f" 畳み {one.folds} → {two.folds} へ増えました（系列ごとに読み直しています）。"
    )
