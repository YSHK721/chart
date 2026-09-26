"""Dukascopy の供給常駐が**台帳由来の系列の組**へ供給する（ISSUE-533 段階 3 の前提工事）。

用語（初出定義）:
    系列の組
        ＝ 同じティック木（枝名 token）を読む datasetRef の全体。所有者は台帳であり、引く口は
          ``marketdata.dataset_registry.series_refs_of``（種 ref → その木を読む組）である。
    種（seed）
        ＝ 常駐が「どのティック木か」を指すために持つ 1 つの ref。``tools.live_tick_watch.REF``。
          **書く系列の集合ではない**（集合の決定権は台帳にある）。
    列の上位集合
        ＝ spread 列を宣言する系列。値列は spread の有無で変わらないため、畳みは 1 回で済む。
    1 回の走査
        ＝ 期間内の日別 parquet を 1 ファイル 1 回だけ読み、1 回だけ畳むこと。

なぜこの検定が要るか（実測された危険・ISSUE-511 段階 8-D-2b 段 5）:
    MT5 側では ``--ref`` がそのまま「書く系列 1 つ」を決めており、台帳が 2 系列を宣言しても
    常駐は片方しか書かなかった。**落ちも警告も出ないまま** spread 付き系列が 2026-09-14 から
    9 日間凍結した。書き漏らしは沈黙で古くなるので、組の全系列が書かれることを機械で固定する。

本検定が固定するもの（MT5 側 ``tools/tests/test_mt5_tick_watch_series_set.py`` の対称形）:
  L-1 系列の組は台帳が決める。常駐に組を名指す CLI 引数は**無い**（運用者が台帳の事実を
      再宣言できる形そのものを作らない）。
  L-2 起動時の列形照合は**組の全 ref** に対して、台帳の宣言順で走る。
  L-3 種でない系列の列形が宣言と食い違えば、``data_dir`` の下へ 1 バイトも書かずに非 0 で止まる。
  L-4 1 周期で組の**全系列**の M1 と上位足ができ、spread 列を持つのは宣言した側だけである。
  L-5 書込を発行する順は**列の上位集合が先**（途中で死んだとき遅れるのを最も見られている系列に
      する）。
  CX-1 系列 1 → 2 で日別 parquet の読込と畳みの発行が増えない。発行 − 使用 = 0。規模 2 点
      （日数 1 日と 2 日）。**回数そのものは期待値に焼き込まない**。

本検定が固定しないもの（射程の明示）:
  - 履歴の実データ生成（``data/marketdata/**`` への書込）。本ファイルの書込はすべて ``tmp_path``。
  - 畳みと射影そのもの。それは ``marketdata/tests/test_tick_m1_series_fan_out_build.py``。
  - オフラインの履歴生成パイプライン。それは ``tools/tests/test_build_tick_rollup_series_set.py``。

常駐もサーバも起動しない・ネットワークは叩かない（取得の口を差し替える）。
構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, List, NamedTuple

import pandas as pd
import pytest

from marketdata import csv_schema, dataset_registry, tick_m1
from marketdata.dataset_registry import REGISTRY
from tools import live_tick_watch as ltw

#: 固定の「現在時刻」と素材の 2 日（実時計を読まない・テスト決定性）。
_DAY1 = dt.date(2026, 9, 1)
_DAY2 = dt.date(2026, 9, 2)
_NOW = dt.datetime(2026, 9, 2, 12, 0, 30)

#: spread 列を持たない側の列形（順序は台帳側の規則 :func:`marketdata.csv_schema.header_for`）。
_PLAIN_HEADER = csv_schema.header_for(
    ["open", "high", "low", "close", "volume", "up", "dn"]
)


def _ledger_refs() -> "tuple[str, ...]":
    """種が指すティック木を読む系列の組（**綴りを書き写さない**期待値の素）。"""
    return dataset_registry.series_refs_of(ltw.REF)


def _superset_ref() -> str:
    """列の上位集合（spread 列を宣言する系列）を台帳から導く。"""
    declared = [
        ref for ref in _ledger_refs()
        if dataset_registry.spread_point_snapshot_of(ref) is not None
    ]
    return declared[0]


def _rest_refs() -> "tuple[str, ...]":
    """上位集合ではない系列（最後に書かれる側）。"""
    return tuple(ref for ref in _ledger_refs() if ref != _superset_ref())


def _ticks(day: dt.date) -> pd.DataFrame:
    """``day`` の合成ティック（bid も気配幅も分内で動く）。"""
    rows = [
        (
            pd.Timestamp(day) + pd.Timedelta(minutes=m, seconds=5 + 25 * k),
            66000.0 + 0.1 * m + 0.05 * k,
            66000.0 + 0.1 * m + 0.05 * k + 0.1 * (60 + m + (3, 0, 6)[k]),
        )
        for m in range(3) for k in range(3)
    ]
    return pd.DataFrame({
        "timestamp": pd.to_datetime([r[0] for r in rows]).tz_localize("UTC"),
        "bidPrice": [r[1] for r in rows],
        "askPrice": [r[2] for r in rows],
    })


def _put_day(data_dir: Path, day: dt.date) -> None:
    """``day`` の合成ティックを木へ置く（``update_once`` が読む素材）。"""
    path = tick_m1.day_parquet_path(day, data_dir=data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    _ticks(day).to_parquet(path, index=False)


def _fake_fetch(day: dt.date, _next: dt.date) -> pd.DataFrame:
    """取得の差し替え（当日のみ返す・他日は空＝既存 parquet を温存する）。"""
    return _ticks(day) if day == _DAY2 else pd.DataFrame()


def _m1_path(ref: str, data_dir: Path) -> Path:
    return tick_m1.m1_csv_path(ref=ref, data_dir=data_dir)


def _rollup_csv(ref: str, data_dir: Path, timeframe: str) -> Path:
    from marketdata.rollup_paths import csv_path, ref_dir

    return csv_path(
        ref_dir(ref, data_dir=data_dir), dataset_registry.series_of(ref), timeframe
    )


def _columns(path: Path) -> "list[str]":
    return path.read_text(encoding="utf-8").splitlines()[0].split(",")


def _data_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    return max(len(path.read_text(encoding="utf-8").splitlines()) - 1, 0)


def _tree(root: Path) -> "dict[str, bytes]":
    """``root`` 配下の全ファイル（単一書き手ロックは台帳ではないため除く）。"""
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.name != ltw._WRITER_LOCK_FILENAME
    }


def _stub_startup(monkeypatch) -> "dict[str, list]":
    """起動後に走る口（追い付き・1 周期・ストリーミング）を差し替え、呼ばれた回数を記録する。"""
    seen: "dict[str, list]" = {"catch_up": [], "update_once": [], "stream_loop": []}
    monkeypatch.setattr(ltw, "catch_up", lambda *a, **k: seen["catch_up"].append(a) or 0)
    monkeypatch.setattr(ltw, "update_once", lambda *a, **k: seen["update_once"].append(k))
    monkeypatch.setattr(ltw, "stream_loop", lambda *a, **k: seen["stream_loop"].append(k))
    monkeypatch.setattr(ltw, "_utc_now", lambda: _NOW)
    return seen


class _Measured(NamedTuple):
    """1 周期の観測（parquet 読込・畳み・出力に使った素材の数・書けた系列数）。"""

    reads: int
    folds: int
    used: int
    written: int


# =====================================================================
# L-1. 系列の組は台帳が決める（運用者が名指す口は無い）
# =====================================================================
def test_the_series_set_comes_from_the_ledger_not_from_the_operator():
    """L-1: 組は種が指す木から台帳が導く。常駐に組を名指す CLI 引数は無い。

    引数を置くと、運用者が台帳の事実を再宣言する形になる——その形が MT5 側で 9 日間の凍結を
    生んだ（依頼者裁定 2026-09-23）。
    """
    # Arrange
    options = {
        option
        for action in ltw.build_arg_parser()._actions
        for option in action.option_strings
    }

    # Act
    refs = ltw.series_refs(ltw.REF)

    # Assert
    assert refs == _ledger_refs()
    assert len(refs) >= 2, f"同じ木を読む系列が {len(refs)} 件（この検定が空振りしている）"
    assert {"--ref", "--refs", "--series"} & options == set(), (
        f"組を名指す引数があります: {sorted(options)}"
    )


# =====================================================================
# L-2. 起動時の列形照合は組の全 ref に対して走る
# =====================================================================
def test_the_startup_check_runs_for_every_series_of_the_set(monkeypatch, tmp_path):
    """L-2: 照合は組の全 ref に対して、台帳の宣言順で走る（種だけに掛からない）。"""
    # Arrange
    checked: "List[str]" = []
    real = tick_m1.check_series_schema

    def spy(ref, **kwargs):
        checked.append(ref)
        return real(ref, **kwargs)

    monkeypatch.setattr(tick_m1, "check_series_schema", spy)
    seen = _stub_startup(monkeypatch)

    # Act
    rc = ltw.main(["--data-dir", str(tmp_path), "--quiet", "--once"])

    # Assert
    assert rc == 0
    assert tuple(checked) == _ledger_refs()
    assert len(seen["update_once"]) == 1  # 空振り防止（起動が周期まで進んだ）


# =====================================================================
# L-3. 種でない系列の食い違いで、1 バイトも書かずに止まる
# =====================================================================
def test_a_mismatch_on_a_series_other_than_the_seed_stops_the_start(monkeypatch, tmp_path):
    """L-3: 種でない系列の列形が宣言と食い違えば、起動は何も書かずに非 0 で止まる。

    照合を種だけに掛ける実装なら、食い違いは起動を素通りして周期の中で初めて落ちる。
    """
    # Arrange: 上位集合（spread を宣言する側）へ、spread 列を持たない既存 CSV を置く。
    other = _superset_ref()
    path = _m1_path(other, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(",".join(_PLAIN_HEADER) + "\n", encoding="utf-8")
    seen = _stub_startup(monkeypatch)
    before = _tree(tmp_path)

    # Act
    rc = ltw.main(["--data-dir", str(tmp_path), "--quiet", "--once"])

    # Assert
    assert other != ltw.REF, "種と同じ系列で測っている（この検定が空振りしている）"
    assert rc != 0
    assert seen["catch_up"] == []
    assert seen["update_once"] == []
    assert _tree(tmp_path) == before


# =====================================================================
# L-4. 1 周期で組の全系列が更新される
# =====================================================================
def test_one_cycle_updates_every_series_of_the_set(monkeypatch, tmp_path):
    """L-4: 1 周期で組の全系列の M1 と上位足ができ、spread 列は宣言した側だけが持つ。"""
    # Arrange
    refs = _ledger_refs()
    _put_day(tmp_path, _DAY1)
    monkeypatch.setattr(ltw, "_fetch_day", _fake_fetch)

    # Act
    ltw.update_once(_NOW, tmp_path, interval=60, full_start=_DAY1)

    # Assert
    rows = {ref: _data_rows(_m1_path(ref, tmp_path)) for ref in refs}
    assert min(rows.values()) > 0, f"書かれていない系列があります: {rows}"
    assert len(set(rows.values())) == 1, f"系列ごとに本数が違います: {rows}"
    assert {ref: _rollup_csv(ref, tmp_path, "5m").is_file() for ref in refs} == {
        ref: True for ref in refs
    }
    assert _columns(_m1_path(_superset_ref(), tmp_path))[-1] == csv_schema.SPREAD_COLUMN
    assert {
        ref: csv_schema.SPREAD_COLUMN in _columns(_m1_path(ref, tmp_path))
        for ref in _rest_refs()
    } == {ref: False for ref in _rest_refs()}


# =====================================================================
# L-5. 書込順は列の上位集合が先
# =====================================================================
def test_the_superset_series_is_written_before_the_most_watched_one(monkeypatch, tmp_path):
    """L-5: M1 の書込を発行する順は spread 列を持つ側が先である。"""
    # Arrange
    order: "List[Path]" = []
    real_whole = tick_m1._write_m1_csv
    real_append = tick_m1._append_m1_csv

    def whole_spy(m1: pd.DataFrame, path: Any) -> None:
        order.append(Path(path))
        return real_whole(m1, path)

    def append_spy(m1: pd.DataFrame, path: Any) -> None:
        order.append(Path(path))
        return real_append(m1, path)

    monkeypatch.setattr(tick_m1, "_write_m1_csv", whole_spy)
    monkeypatch.setattr(tick_m1, "_append_m1_csv", append_spy)
    _put_day(tmp_path, _DAY1)
    monkeypatch.setattr(ltw, "_fetch_day", _fake_fetch)

    # Act
    ltw.update_once(_NOW, tmp_path, interval=60, full_start=_DAY1)

    # Assert
    expected = [_m1_path(ref, tmp_path) for ref in (_superset_ref(), *_rest_refs())]
    assert order == expected


# =====================================================================
# CX-1. 系列を増やしても parquet 読込と畳みは増えない
# =====================================================================
def _measure(monkeypatch, data_dir: Path, *, drop: "str | None", n_days: int) -> _Measured:
    """``drop`` を台帳から外して 1 周期回し、発行を数える（系列数だけが違う 2 点を作る）。"""
    days = [_DAY1, _DAY2][:n_days]
    for day in days[:-1]:
        _put_day(data_dir, day)
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
        patch.setattr(ltw, "_fetch_day", _fake_fetch)
        if drop is not None:
            patch.setattr(dataset_registry, "REGISTRY", {
                ref: d for ref, d in REGISTRY.items() if ref != drop
            })
        patch.setattr(pd, "read_parquet", read_spy)
        patch.setattr(tick_m1, "_fold_ticks", fold_spy)
        ltw.update_once(_NOW, data_dir, interval=60, full_start=days[0])
    written = len([ref for ref in refs if _data_rows(_m1_path(ref, data_dir)) > 0])
    return _Measured(
        reads=len(reads), folds=len(folds), used=n_days, written=written,
    )


@pytest.mark.parametrize("n_days", [1, 2])
def test_cx1_a_cycle_reads_and_folds_each_day_once_for_the_whole_set(
    monkeypatch, tmp_path, n_days
):
    """CX-1: 系列 1 → 2 で読込・畳みの発行が増えず、発行 − 使用 = 0（規模 2 点）。

    固定するのは**無駄の不在**であり、回数そのもの（N 回）は期待値に焼き込まない（期待値は
    その周期に実在する日別 parquet の数から導く）。系列ごとに追記の口を呼ぶ形へ戻す変異は、
    系列 1 と 2 の差としてここに現れる。
    """
    # Arrange / Act
    one = _measure(
        monkeypatch, tmp_path / f"one{n_days}", drop=_superset_ref(), n_days=n_days
    )
    two = _measure(monkeypatch, tmp_path / f"two{n_days}", drop=None, n_days=n_days)

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
