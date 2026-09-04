"""供給された窓のラベルは秒境界に載る（ISSUE-479 🟡-4・前提の機械的検査化）。

なぜ検査にするか:
    形成中バーの差し替え規則は「窓の末尾 time」と「形成中バーの time」を **UNIX 秒**で比べる。
    その変換は ``int(pd.Timestamp(df.index[-1]).value // 1_000_000_000)``（切り捨て）で、
    ``indigators/indicator_ui/api/adapter/compute/forming_bar.py`` と
    同 ``adapter/compute/live_tick_tails.py`` の両方が同じ式を使う。ラベルが秒境界に載って
    いないと切り捨てで **1 秒手前**へ丸まり、「同じバー」が「1 つ前のバー」に化ける
    （置換すべき足を追加してしまう／追加すべき足を置換してしまう）。

    この前提はこれまでコメントで宣言されているだけだった。宣言は破られても誰も気づかないので、
    供給側（``marketdata.dataset.load_dataframe``）の実挙動として固定する。

data/: 実データを読まない（``tmp_path`` の合成 CSV をホワイトリストへ一時登録して測る）。
構造: Arrange-Act-Assert（AAA）。
"""

from __future__ import annotations

import csv as _csv

import pandas as pd
import pytest

from marketdata import dataset

#: 秒を ns で表した単位（変換式 ``value // 1_000_000_000`` と同じ刻み）。
_NS_PER_SEC = 1_000_000_000

#: 測る時間足。``None``（原子＝再集計なし）と再集計 3 種で、経路を 2 系統以上通す。
_TIMEFRAMES = (None, "5m", "1h", "1D")

_REF = "_second_boundary_probe"


def _sub_second_labels(index) -> "list":
    """秒境界に載っていないラベルの一覧（空なら前提充足）。"""
    return [ts for ts in index if int(pd.Timestamp(ts).value) % _NS_PER_SEC != 0]


@pytest.fixture
def _probe_ref(tmp_path, monkeypatch) -> str:
    """1 分足の合成 CSV をホワイトリストへ一時登録し、その datasetRef を返す。"""
    path = tmp_path / "second_boundary_probe.csv"
    start = pd.Timestamp("2026-01-05 00:00:00")
    with open(path, "w", newline="") as fh:
        writer = _csv.writer(fh)
        writer.writerow(("date", "open", "high", "low", "close", "volume"))
        for i in range(240):                       # 4 時間ぶん＝1D も 1 本にまとまる
            ts = start + pd.Timedelta(minutes=i)
            price = 100.0 + i * 0.25
            writer.writerow((ts, price, price + 1, price - 1, price + 0.5, float(i + 1)))
    monkeypatch.setitem(dataset.DATASET_WHITELIST, _REF, path)
    dataset._BASE_CACHE.clear()
    dataset._RESAMPLE_CACHE.clear()
    yield _REF
    dataset._BASE_CACHE.clear()
    dataset._RESAMPLE_CACHE.clear()


@pytest.mark.parametrize("timeframe", _TIMEFRAMES)
def test_every_supplied_label_sits_on_a_second_boundary(timeframe, _probe_ref) -> None:
    """どの時間足でも、供給される窓のラベルは秒境界（``value % 1e9 == 0``）に載る。"""
    # Arrange / Act
    df = dataset.load_dataframe(_probe_ref, timeframe)

    # Assert
    assert len(df) > 0, "合成 CSV から窓が得られていません（検査が空振りしています）"
    assert _sub_second_labels(df.index) == []


@pytest.mark.parametrize("timeframe", _TIMEFRAMES)
def test_the_unix_second_conversion_round_trips(timeframe, _probe_ref) -> None:
    """差し替え規則が使う変換（ns 切り捨て → UNIX 秒）が情報を落とさない。

    秒境界に載っている限り ``pd.Timestamp(sec, unit="s")`` で元のラベルへ戻る。戻らなくなった
    瞬間、末尾 time の比較は黙って別のバーを指す。
    """
    # Arrange
    df = dataset.load_dataframe(_probe_ref, timeframe)

    # Act
    seconds = [int(pd.Timestamp(ts).value // _NS_PER_SEC) for ts in df.index]

    # Assert
    assert [pd.Timestamp(s, unit="s") for s in seconds] == list(df.index)


def test_the_anchor_would_catch_a_sub_second_supply(tmp_path, monkeypatch) -> None:
    """検出力（供給経路）: 素材が秒境界を外れたら、原子経路の窓にそのまま現れる。

    原子（再集計なし）はラベルを素通しするため、CSV が秒未満を持ち込めば窓のラベルも外れる
    （再集計経路は rule の左ラベルへ丸めるので隠れる）。つまり本ファイルの錨は「起こり得ない
    ことを確かめている」のではなく、素材側の変化を実際に捕まえる。
    """
    # Arrange — 1 ms ずれた 1 分足 CSV。
    path = tmp_path / "sub_second_probe.csv"
    with open(path, "w", newline="") as fh:
        writer = _csv.writer(fh)
        writer.writerow(("date", "open", "high", "low", "close", "volume"))
        for i in range(5):
            ts = (pd.Timestamp("2026-01-05 00:00:00")
                  + pd.Timedelta(minutes=i) + pd.Timedelta(milliseconds=1))
            writer.writerow((ts, 100.0, 101.0, 99.0, 100.5, 1.0))
    ref = "_sub_second_probe"
    monkeypatch.setitem(dataset.DATASET_WHITELIST, ref, path)
    dataset._BASE_CACHE.clear()
    dataset._RESAMPLE_CACHE.clear()

    # Act
    try:
        offenders = _sub_second_labels(dataset.load_dataframe(ref, None).index)
    finally:
        dataset._BASE_CACHE.clear()
        dataset._RESAMPLE_CACHE.clear()

    # Assert
    assert len(offenders) > 0


def test_the_boundary_check_detects_a_sub_second_label() -> None:
    """検出力: ラベルが 1 ms でもずれれば検査は落ちる（恒真式ではない）。"""
    # Arrange
    shifted = pd.DatetimeIndex(
        [pd.Timestamp("2026-01-05 00:00:00"), pd.Timestamp("2026-01-05 00:01:00.001")]
    )

    # Act
    offenders = _sub_second_labels(shifted)

    # Assert
    assert len(offenders) == 1
    assert int(pd.Timestamp(offenders[0]).value // _NS_PER_SEC) < int(shifted[-1].value // _NS_PER_SEC) + 1
