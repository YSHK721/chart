"""素材消失を「キャッシュ有効」に潰さない（ISSUE-278 #5・#1 の回帰検証）。

由来（実測 2026-08-06・SOLID 精査）:
  - `csv_mtime` が `stat()` の OSError を None へ潰し、その None を「mtime 不変＝キャッシュヒット」
    として扱っていたため、CSV を削除・退避しても配信プロセスは気付かず、削除時点の断面を
    **無期限に配信し続けた**（ログも出ず、プロセス再起動でしか復旧しない）。
  - 同型の隠蔽がティック取得にもあり、取得失敗（通信断）を空 DataFrame（＝休場）へ潰していた。
    呼出側は空を `.empty` マーカーとして永続化し、その日を二度と取得しない＝欠損の恒久化。

不変条件:
  1. 素材が消えたら **落ちる**（古い断面を配信しない）。torn-read（追記中の一過性）とは区別する。
  2. ティック取得の空 DataFrame が意味するのは「取得成功かつ 0 件」だけ。失敗は例外で伝播する。
"""

from __future__ import annotations

import csv as _csv
from datetime import datetime

import pandas as pd
import pytest

from marketdata import rollup_store, serving_cache


def _write_csv(path, rows):
    with open(path, "w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(("date", "open", "high", "low", "close"))
        w.writerows(rows)


class _Loader:
    def load_ohlc_csv(self, path, *, time_column):
        return pd.read_csv(path).set_index(time_column)


def test_base_cache_fails_closed_when_csv_disappears(tmp_path):
    csv_path = tmp_path / "gone.csv"
    _write_csv(csv_path, [("2020-01-01", 10.0, 12.0, 9.0, 11.0)])
    serving_cache._BASE_CACHE.clear()

    df = serving_cache.load_base_dataframe(
        "_gone_base", path=csv_path, loader_factory=_Loader, time_column="date"
    )
    assert float(df["close"].iloc[-1]) == 11.0   # 事前条件: キャッシュが焼かれている

    csv_path.unlink()   # 素材の消失（退避・マウント断・data-dir 切替と同じ状況）

    with pytest.raises(FileNotFoundError):
        serving_cache.load_base_dataframe(
            "_gone_base", path=csv_path, loader_factory=_Loader, time_column="date"
        )


def test_rollup_cache_fails_closed_when_csv_disappears(tmp_path, monkeypatch):
    ref, tf = "_gone_ref", "5m"
    csv_path = tmp_path / f"{ref}_{tf}.csv"
    _write_csv(csv_path, [("2020-01-01", 10.0, 12.0, 9.0, 11.0)])
    monkeypatch.setattr(rollup_store, "path", lambda r, t: csv_path)
    rollup_store._ROLLUP_CACHE.clear()

    rollup_store.read(ref, tf)   # 事前条件: キャッシュが焼かれている
    csv_path.unlink()

    with pytest.raises(FileNotFoundError):
        rollup_store.read(ref, tf)


def test_tick_fetch_failure_propagates_instead_of_looking_like_a_holiday(monkeypatch):
    """取得失敗を空 DataFrame（＝休場）に見せない。

    空に潰すと呼出側（simulator/tools/fetch_ticks_ymd.py）が `.empty` マーカーを書き、その日は
    二度と取得されない＝一過性の通信断がデータ欠損として恒久化する。
    """
    from marketdata import dukascopy_source

    class _Boom:
        @staticmethod
        def fetch(*_args, **_kwargs):
            raise ConnectionError("upstream down")

        INTERVAL_TICK = object()
        OFFER_SIDE_BID = object()

    monkeypatch.setattr(dukascopy_source, "dukascopy_python", _Boom)
    src = dukascopy_source.DukascopyTickSource()

    with pytest.raises(ConnectionError):
        src.fetch_ticks(datetime(2026, 1, 5), datetime(2026, 1, 6))
