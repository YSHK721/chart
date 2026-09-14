"""dataset.load_atom_window（原子の任意時間窓・ISSUE-132）の検証。

replay /intraday の m1 素材供給を dataset（単一権威）へ完全委譲するための additive API:
全期間原子（_load_base_dataframe・mtime キャッシュ）＋ clamp 補正（_clamp_outlier_bars）＋
窓スライス [start, end)。末尾有界の load_dataframe と異なり任意の過去窓へ届く。
"""

from __future__ import annotations

import csv as _csv
import os as _os

import pytest

from adapter.compute import dataset


def _write_csv(path, rows):
    with open(path, "w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["date", "open", "high", "low", "close"])
        w.writerows(rows)


def _register(monkeypatch, ref, csv_path, clamp=True):
    monkeypatch.setitem(dataset.DATASET_WHITELIST, ref, csv_path)
    if clamp:
        monkeypatch.setitem(dataset._OUTLIER_CLAMP_REFS_SET, ref, True)
    dataset._BASE_CACHE.clear()
    dataset._ATOM_WINDOW_CACHE.clear()


_ROWS = [
    ("2020-01-01 00:00:00", 100.0, 105.0, 99.0, 101.0),   # 1577836800
    ("2020-01-01 00:01:00", 101.0, 106.0, 100.0, 102.0),  # 1577836860
    ("2020-01-01 00:02:00", 102.0, 107.0, 30.0, 103.0),   # 外れ安値（-70%）→ clamp
    ("2020-01-01 00:03:00", 103.0, 108.0, 101.0, 104.0),  # 1577836980
]
_T0 = 1577836800


def test_window_is_half_open(tmp_path, monkeypatch):
    csv_path = tmp_path / "aw.csv"
    _write_csv(csv_path, _ROWS)
    _register(monkeypatch, "_tmp_aw", csv_path, clamp=False)
    df = dataset.load_atom_window("_tmp_aw", _T0 + 60, _T0 + 180)  # [00:01, 00:03)
    assert len(df) == 2
    assert float(df.iloc[0]["open"]) == 101.0
    assert float(df.iloc[-1]["open"]) == 102.0


def test_clamp_is_applied_for_market_ref(tmp_path, monkeypatch):
    csv_path = tmp_path / "aw_clamp.csv"
    _write_csv(csv_path, _ROWS)
    _register(monkeypatch, "_tmp_aw_clamp", csv_path, clamp=True)
    df = dataset.load_atom_window("_tmp_aw_clamp", _T0, _T0 + 240)
    assert len(df) == 4  # clamp は行を消さない（値補正のみ＝供給規約）
    assert float(df["low"].min()) > 40.0  # 30.0（-70% 外れ）はクランプ済み


def test_mtime_cache_refreshes_on_csv_update(tmp_path, monkeypatch):
    csv_path = tmp_path / "aw_mt.csv"
    _write_csv(csv_path, _ROWS[:2])
    _register(monkeypatch, "_tmp_aw_mt", csv_path, clamp=False)
    assert len(dataset.load_atom_window("_tmp_aw_mt", _T0, _T0 + 600)) == 2
    _write_csv(csv_path, _ROWS)  # 4 行へ更新
    _os.utime(csv_path, (9999999999, 9999999999))  # mtime を確実に前進
    dataset._BASE_CACHE.clear()  # base 段の mtime 検知は既存機構（ここでは窓キャッシュの追随を見る）
    assert len(dataset.load_atom_window("_tmp_aw_mt", _T0, _T0 + 600)) == 4


def test_unknown_ref_raises_keyerror():
    with pytest.raises(KeyError):
        dataset.load_atom_window("_no_such_ref", 0, 1)
