"""chart_overlay_writer — 足の時刻を書かず、値の列が run の足に対応することを書く前に確かめる（ISSUE-552/554 段階 1）。

足の時刻の列はジョブの成果物の 1 か所（report.json の足）にだけ持つ。chart_overlay.json は値の列だけを
持ち、位置 i の値は足 i の値である。時刻を捨てた後は front が口座の列のずれを検出できないので、
書き手が口座の行の時刻と Bar 列の時刻の一致を確かめ、違えば書かない（黙ってずらさない）。

計算量（絶対命令 2026-08-28）: 観測口 `usecase.bar_times.set_observer` だけを使う（内部名を差し替えない）。
    - 変換はどれも Bar 列全体を一括で扱う（足ごとに変換しない）
    - 変換の呼び出しの数は足の本数（2 点）で変わらない
    - 書く時刻の列は 0（時刻を 2 ファイルに書かない）
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest

from simulator.domain.bar import Bar
from simulator.sim_ui.adapter import chart_overlay_writer
from simulator.usecase import bar_times

_EPOCH = 1_704_067_200


@dataclass
class _Plot:
    series: str
    placement: str


@dataclass
class _Indicators:
    columns: "dict[str, list[float]]"

    def get(self, name: str) -> "list[float]":
        return self.columns[name]


@dataclass
class _Account:
    """`AccountCurveRecorder` と同じ属性（時刻は epoch 秒の列）。"""

    times: "list[int]"
    balance: "list[float]" = field(default_factory=list)
    equity: "list[float]" = field(default_factory=list)
    margin: "list[float]" = field(default_factory=list)
    margin_level: "list[float | None]" = field(default_factory=list)


@dataclass
class _Result:
    trades: list = field(default_factory=list)


def _bars(n: int) -> "list[Bar]":
    return [
        Bar(time=np.datetime64(_EPOCH + 60 * i, "s"), open=1.0, high=2.0, low=0.5, close=1.5,
            volume=1.0, spread=0)
        for i in range(n)
    ]


def _account(times: "list[int]") -> _Account:
    n = len(times)
    return _Account(times=times, balance=[1000.0] * n, equity=[1000.0] * n,
                    margin=[0.0] * n, margin_level=[None] * n)


def _write(tmp: Path, bars: "list[Bar]", account: _Account) -> Path:
    _, overlay = chart_overlay_writer.write(
        tmp, result=_Result(), bars=bars, symbol="SYNTH", digits=1, ea_name="CalcProbe_EA",
        indicators=_Indicators({"sma": [1.0] * len(bars)}), plots=[_Plot("sma", "price")],
        account=account, initial_deposit=1000.0, dataset_ref=None,
    )
    return overlay


def _times(n: int) -> "list[int]":
    return [_EPOCH + 60 * i for i in range(n)]


def test_the_overlay_has_no_time_columns_and_the_value_columns_follow_the_bars(tmp_path: Path) -> None:
    # Act
    overlay = json.loads(_write(tmp_path, _bars(4), _account(_times(4))).read_text(encoding="utf-8"))

    # Assert
    assert "time" not in overlay["account"]
    assert all("time" not in ind for ind in overlay["indicators"])
    assert {len(v) for v in overlay["account"].values()} == {4}
    assert [len(ind["value"]) for ind in overlay["indicators"]] == [4]


def test_account_rows_on_other_bar_times_are_refused(tmp_path: Path) -> None:
    # Arrange: 口座の行が 1 本ずれている（最後の足の代わりに 1 本先の時刻）。
    times = _times(4)
    times[-1] += 60

    # Act / Assert
    with pytest.raises(ValueError, match="口座の行の時刻"):
        _write(tmp_path, _bars(4), _account(times))
    assert not (tmp_path / chart_overlay_writer.CHART_OVERLAY_FILENAME).exists()


def test_fewer_account_rows_than_bars_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="口座の行の時刻"):
        _write(tmp_path, _bars(4), _account(_times(3)))


def test_time_conversions_are_whole_run_and_do_not_grow_with_the_bars(tmp_path: Path) -> None:
    calls_per_size = []
    for n in (3, 300):
        calls: "list[tuple[int, bool]]" = []
        bar_times.set_observer(lambda count, batched: calls.append((count, batched)))
        try:
            (tmp_path / str(n)).mkdir()
            _write(tmp_path / str(n), _bars(n), _account(_times(n)))
        finally:
            bar_times.set_observer(None)
        # Assert: 変換は行われ（空振りしない）、どれも Bar 列全体を一括で扱う。
        assert calls, "足の時刻の変換が観測口を通っていない"
        assert all(count == n and batched for count, batched in calls), calls
        calls_per_size.append(len(calls))
    # 足の本数を 100 倍にしても変換の呼び出しは増えない。
    assert calls_per_size[0] == calls_per_size[1]
