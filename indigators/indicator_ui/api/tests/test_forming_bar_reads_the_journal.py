"""形成中バーの adapter が **受信ジャーナルまで読む** ことの結線検定（ISSUE-512 段階 2）。

MT5 は当日を追記専用ジャーナルで受け、日次確定で parquet 化する。adapter の読取と指紋
（キャッシュ鍵）が parquet しか見ないと、当日だけ形成中バーが None になる。指紋だけが
ジャーナルを見落とすと、追記されても記憶を使い続け、古い形成中バーを配り続ける。

data/: 実データを読まない（tmp_path・台帳への一時記述子のみ）。
構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import datetime as dt
import functools

import pytest

from marketdata import tf_meta
from marketdata import tick_day_source as tds
from marketdata.dataset_registry import REGISTRY, DatasetDescriptor
from marketdata.mt5_ticks import ingest, journal

from adapter.compute import forming_bar as fb

_TOKEN = "JP225@OANDA-Japan-MT5-Live"
_REF = "zz_journal_backed_ref"
_DAY = dt.date(2026, 8, 25)


def _label_ms(h: int, mi: int = 0, s: int = 0) -> int:
    """2026-08-25 のサーバラベル ms（夏時間＝UTC+3）。"""
    return int(dt.datetime(2026, 8, 25, h, mi, s, tzinfo=dt.timezone.utc).timestamp() * 1000)


@pytest.fixture(autouse=True)
def _clear():
    fb.clear_forming_cache()
    tds.clear_journal_cache()
    yield
    fb.clear_forming_cache()
    tds.clear_journal_cache()


@pytest.fixture
def journal_backed(monkeypatch, tmp_path):
    """tick=True・tick_token=MT5 の ref を台帳へ一時的に載せ、読取を tmp_path の木へ向ける。"""
    monkeypatch.setitem(
        REGISTRY, _REF,
        DatasetDescriptor(path=tmp_path / "x.csv", symbol="JP225", tick=True, tick_token=_TOKEN),
    )
    monkeypatch.setattr(tf_meta, "TICK_REFS", frozenset(set(tf_meta.TICK_REFS) | {_REF}))
    monkeypatch.setattr(fb, "day_tick_files", functools.partial(tds.day_tick_files, data_dir=tmp_path))
    monkeypatch.setattr(
        fb, "forming_bar_from_ticks",
        functools.partial(tds.forming_bar_from_ticks, data_dir=tmp_path),
    )
    return dict(symbol=_TOKEN, data_dir=tmp_path)


def test_the_adapter_reads_through_the_day_source():
    """adapter の 2 つのティック接点（指紋と実データ）は、読み元を解決する窓口そのものである。

    下の結線検定は tmp_path へ向けるためにこの 2 名を差し替える。差し替える前の中身が
    本番の窓口であることをここで固定し、差し替えが本番の姿を表していることを保証する。
    """
    # Arrange / Act / Assert
    assert fb.day_tick_files is tds.day_tick_files
    assert fb.forming_bar_from_ticks is tds.forming_bar_from_ticks


def test_a_journal_only_day_yields_a_forming_bar(journal_backed):
    """確定前（ジャーナルのみ）の当日でも形成中バーが出る。"""
    # Arrange
    journal.append(_DAY, [(_label_ms(12, 0, 5), 100.0, 101.0)], **journal_backed)
    utc = ingest.rows_to_frame(journal.read_rows(_DAY, **journal_backed))["timestamp"].iloc[0]
    now = int(utc.timestamp()) + 30

    # Act
    bar = fb.forming_bar(_REF, "1m", now)

    # Assert
    assert bar is not None
    assert bar["close"] == pytest.approx(100.5)


def test_an_append_invalidates_the_remembered_forming_bar(journal_backed):
    """追記されると指紋が変わり、同じ時刻の問い合わせでも新しいティックを反映する。

    指紋が parquet しか見ないと、ジャーナルへの追記を検出できず古いバーを配り続ける。
    """
    # Arrange
    journal.append(_DAY, [(_label_ms(12, 0, 5), 100.0, 101.0)], **journal_backed)
    utc = ingest.rows_to_frame(journal.read_rows(_DAY, **journal_backed))["timestamp"].iloc[0]
    now = int(utc.timestamp()) + 30
    before = fb.forming_bar(_REF, "1m", now)
    journal.append(_DAY, [(_label_ms(12, 0, 9), 200.0, 201.0)], **journal_backed)

    # Act
    after = fb.forming_bar(_REF, "1m", now)

    # Assert
    assert before["close"] == pytest.approx(100.5)
    assert after["close"] == pytest.approx(200.5)
    assert after["volume"] == 2.0
