"""ref の **保存物の名前**（series）を台帳に持つ（ISSUE-511 段階 1d）。

用語（初出定義）:
    series
        ＝ ref の保存物（1 分足 CSV ``<series>_m1.csv`` とロールアップ ``rollups/<series>/<series>_<tf>.csv``）
          の名前。既定は ref 名そのもの。

なぜ必要か:
    jp225_tick を bid の系列へ切り替えるにあたり、bid 版の M1 とロールアップは **新しいファイル** に
    作ってある（段階 1b・旧 mid のファイルは上書きしない＝可逆）。ところが M1 の置き場は
    ``<ref>_m1.csv``、ロールアップは ``rollups/<ref>/`` と、どちらも ref 名から組み立てていた。
    ref 名を変えずに読み書きの先だけを新ファイルへ向けるには、ref と保存物の名前を台帳で分ける
    必要がある。1 箇所でも ref 名から組み立てたまま残ると、書き手と読み手が別のファイルを見る。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import pytest

from marketdata import dataset_registry, rollup_paths, tick_m1
from marketdata.dataset_registry import REGISTRY


def test_jp225_tick_is_stored_under_its_bid_series():
    """jp225_tick の保存物は bid 版（段階 1b で作った新ファイル）の名前。"""
    # Arrange / Act / Assert
    assert dataset_registry.series_of("jp225_tick") == "jp225_tick_bid"


@pytest.mark.parametrize("ref", ["jp225_m1", "jp225_mt5", "no_such_ref"])
def test_other_refs_are_stored_under_their_own_name(ref):
    """series を持たない ref（台帳外を含む）は ref 名のまま（従来の置き場を 1 バイトも変えない）。"""
    # Arrange / Act / Assert
    assert dataset_registry.series_of(ref) == ref


def test_the_m1_path_follows_the_series(tmp_path):
    """1 分足 CSV の置き場は series から組む（書き手・読み手が同じファイルを見る）。"""
    # Arrange / Act / Assert
    assert tick_m1.m1_csv_path(ref="jp225_tick", data_dir=tmp_path) == tmp_path / "jp225_tick_bid_m1.csv"
    assert tick_m1.m1_csv_path(ref="jp225_mt5", data_dir=tmp_path) == tmp_path / "jp225_mt5_m1.csv"


def test_the_rollup_layout_follows_the_series(tmp_path):
    """ロールアップの dir・ファイル名も series から組む。"""
    # Arrange
    out = rollup_paths.ref_dir("jp225_tick", data_dir=tmp_path)
    (out).mkdir(parents=True)
    (out / "jp225_tick_bid_1h.csv").write_text("date\n", encoding="utf-8")

    # Act
    got = rollup_paths.resolve_csv("jp225_tick", "1h", data_dir=tmp_path)

    # Assert
    assert out == tmp_path / "rollups" / "jp225_tick_bid"
    assert got == out / "jp225_tick_bid_1h.csv"


@pytest.mark.parametrize("ref", sorted(dataset_registry.tick_refs()))
def test_the_serving_path_and_the_writer_path_are_the_same_file(ref):
    """台帳の path（読み手）と m1_csv_path（書き手）はティック ref で同じファイル（単一ソース）。

    ティック ref の M1 は tick_m1 が書く。jp225_m1 は別の書き手（1 分足の直接取得）ゆえ対象外。
    """
    # Arrange / Act / Assert
    assert REGISTRY[ref].path == tick_m1.m1_csv_path(ref=ref)


@pytest.mark.parametrize("paths", [1, 5])
def test_each_path_resolves_the_series_once(monkeypatch, tmp_path, paths):
    """計算量: series の解決数 − 組み立てたパス数 = 0（パス数 1/5 の 2 点で固定）。"""
    # Arrange
    calls = []
    real = dataset_registry.series_of
    monkeypatch.setattr(dataset_registry, "series_of", lambda ref: (calls.append(ref), real(ref))[1])

    # Act
    for _ in range(paths):
        tick_m1.m1_csv_path(ref="jp225_tick", data_dir=tmp_path)

    # Assert
    assert len(calls) - paths == 0, f"{paths} 本のパスで series を {len(calls)} 回解決した"
