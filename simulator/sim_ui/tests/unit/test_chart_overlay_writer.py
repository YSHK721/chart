"""chart_overlay_writer — 足の時刻を書かず、値の列が run の足に対応することを書く前に確かめる（ISSUE-552/554 段階 1）。

足の時刻の列はジョブの成果物の 1 か所（report.json の足）にだけ持つ。chart_overlay.json は値の列だけを
持ち、位置 i の値は足 i の値である。時刻を捨てた後は front が口座の列のずれを検出できないので、
書き手が口座の行の時刻と Bar 列の時刻の一致を確かめ、違えば書かない（黙ってずらさない）。

計算量（絶対命令 2026-08-28）: 宣言された観測口だけを使う（内部名を差し替えない）。
    観測口は `usecase.bar_times.set_observer`（変換の数と一括か）・`usecase.bar_times.set_result_observer`
    （変換が返した列の実体）・`chart_overlay_writer.set_observer`（照合に使った足の時刻の列の実体）。
    - 変換はどれも Bar 列全体を一括で扱う（足ごとに変換しない）
    - 変換の呼び出しの数は足の本数（2 点）で変わらない
    - 変換で作った列のうち照合に使われなかった列は 0（作って捨てない・2 点）。時刻は出力に書かないので、
      出力に使う列は無く、使い道は照合だけである
    - 書く時刻の列は 0（時刻を 2 ファイルに書かない）

足の成果物（ISSUE-552/554 段階 2-1）:
    足（位置・時刻・OHLC）＋口座の列＋指標の列を、範囲で読める 1 本（「`chart_bars.parquet`」）に書く。
    足はジョブの成果物のここ 1 か所にだけ在る。列・指標・行数は小さな宣言ファイル
    （「`chart_bars.json`」）が名乗り、読み手は列名を手書きしない。
    - 宣言ファイルの大きさは足の本数で変わらない（行数の桁数ぶんを除く・2 点）
    - 照合が通らない run では足の成果物も書かない
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest

from simulator.adapter.trace import parquet_trace_store
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
    """「`AccountCurveRecorder`」 と同じ属性（時刻は epoch 秒の列）。"""

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
    assert overlay["account"].keys().isdisjoint({"time"})
    assert all(ind.keys().isdisjoint({"time"}) for ind in overlay["indicators"])
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


def test_every_converted_time_column_is_used_for_matching(tmp_path: Path) -> None:
    for n in (3, 300):
        # Arrange: 変換が返した列（発行）と、書き手が照合に使った列（使用）を実体で集める。
        made: "list[list[int]]" = []
        used: "list[list[int]]" = []
        bar_times.set_result_observer(made.append)
        chart_overlay_writer.set_observer(used.append)
        try:
            (tmp_path / str(n)).mkdir()
            # Act
            _write(tmp_path / str(n), _bars(n), _account(_times(n)))
        finally:
            bar_times.set_result_observer(None)
            chart_overlay_writer.set_observer(None)

        # Assert: 発行 − 使用 = 0（作ったのに照合にも出力にも使わない列が無い）。
        assert made, "足の時刻の変換が観測口を通っていない"
        unused = [column for column in made if not any(column is u for u in used)]
        assert unused == [], f"足 {n} 本: 使われなかった変換 {len(unused)} 列"


# --- 足の成果物（chart_bars.parquet ＋ chart_bars.json）---------------------------------


def _declaration(directory: Path) -> dict:
    return json.loads(
        (directory / chart_overlay_writer.CHART_BARS_DECLARATION_FILENAME).read_text(encoding="utf-8")
    )


def _read_all(directory: Path, columns: "list[str]") -> "dict[str, list]":
    return parquet_trace_store.read_columns(
        directory / chart_overlay_writer.CHART_BARS_FILENAME, columns=columns,
        time_column=chart_overlay_writer.INDEX_COLUMN,
    )


def test_the_bars_artefact_holds_the_bars_the_account_and_the_indicators(tmp_path: Path) -> None:
    # Arrange
    bars = _bars(4)

    # Act
    overlay = json.loads(_write(tmp_path, bars, _account(_times(4))).read_text(encoding="utf-8"))
    declared = _declaration(tmp_path)
    got = _read_all(tmp_path, declared["columns"])

    # Assert: 位置・時刻・OHLC は run の Bar 列そのもの。
    assert got["bar_index"] == [0, 1, 2, 3]
    assert got["time"] == _times(4)
    assert (got["open"], got["high"], got["low"], got["close"]) == (
        [1.0] * 4, [2.0] * 4, [0.5] * 4, [1.5] * 4,
    )
    # 口座の列は chart_overlay.json と同じ値（同じ列を 2 通りに計算しない）。
    for name in chart_overlay_writer.ACCOUNT_COLUMNS:
        assert got[name] == overlay["account"][name], name
    # 指標は宣言が名乗る列に在る。
    (indicator,) = declared["indicators"]
    assert (indicator["series"], indicator["placement"]) == ("sma", "price")
    assert got[indicator["column"]] == overlay["indicators"][0]["value"]


def test_the_declaration_names_the_run_and_every_column(tmp_path: Path) -> None:
    # Act
    _write(tmp_path, _bars(4), _account(_times(4)))
    declared = _declaration(tmp_path)

    # Assert
    assert declared["timeframe"] == chart_overlay_writer.RUN_TIMEFRAME
    assert declared["ea_name"] == "CalcProbe_EA"
    assert declared["dataset_ref"] is None
    assert declared["rows"] == 4
    assert declared["index_column"] == chart_overlay_writer.INDEX_COLUMN
    indicator_columns = [ind["column"] for ind in declared["indicators"]]
    assert declared["columns"] == [
        *chart_overlay_writer.BAR_COLUMNS, *chart_overlay_writer.ACCOUNT_COLUMNS, *indicator_columns,
    ]
    # 宣言した列は成果物に実在する（名乗るだけで書かない列が無い）。
    assert set(_read_all(tmp_path, declared["columns"])) == set(declared["columns"])


def test_an_indicator_named_like_a_bar_column_does_not_overwrite_it(tmp_path: Path) -> None:
    # Arrange: 系列名が足の列名と同じ（列名を系列名から作ると足の終値を上書きする）。
    bars = _bars(3)

    # Act
    chart_overlay_writer.write(
        tmp_path, result=_Result(), bars=bars, symbol="SYNTH", digits=1, ea_name="CalcProbe_EA",
        indicators=_Indicators({"close": [9.0] * 3}), plots=[_Plot("close", "price")],
        account=_account(_times(3)), initial_deposit=1000.0, dataset_ref=None,
    )
    declared = _declaration(tmp_path)
    got = _read_all(tmp_path, declared["columns"])

    # Assert
    assert got["close"] == [1.5] * 3
    assert got[declared["indicators"][0]["column"]] == [9.0] * 3


def test_a_refused_run_writes_no_bars_artefact(tmp_path: Path) -> None:
    # Arrange: 口座の行が足と合わない。
    times = _times(4)
    times[-1] += 60

    # Act / Assert
    with pytest.raises(ValueError, match="口座の行の時刻"):
        _write(tmp_path, _bars(4), _account(times))
    assert not (tmp_path / chart_overlay_writer.CHART_BARS_FILENAME).exists()
    assert not (tmp_path / chart_overlay_writer.CHART_BARS_DECLARATION_FILENAME).exists()


def test_the_declaration_does_not_grow_with_the_bars(tmp_path: Path) -> None:
    sizes = []
    for n in (30, 3000):
        (tmp_path / str(n)).mkdir()
        _write(tmp_path / str(n), _bars(n), _account(_times(n)))
        declared = _declaration(tmp_path / str(n))
        size = (tmp_path / str(n) / chart_overlay_writer.CHART_BARS_DECLARATION_FILENAME).stat().st_size
        # 正の対照: 行数は足の本数を名乗っている（固定値を書いていない）。
        assert declared["rows"] == n
        # 行数の桁数だけが足の本数で変わる。それを除いた大きさを比べる。
        sizes.append(size - len(str(n)))
    # 足の本数を 100 倍にしても宣言は大きくならない（足ごとの値を宣言に書かない）。
    assert sizes[0] == sizes[1], sizes


def test_the_bar_times_are_written_once_across_the_per_bar_artefacts(tmp_path: Path) -> None:
    # Act
    overlay = json.loads(_write(tmp_path, _bars(4), _account(_times(4))).read_text(encoding="utf-8"))
    declared = _declaration(tmp_path)

    # Assert: 時刻の列は足の成果物の 1 列だけ。
    assert declared["columns"].count("time") == 1
    assert overlay["account"].keys().isdisjoint({"time"})
    assert str(_times(4)[0]) not in json.dumps(declared)
