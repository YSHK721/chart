"""S2（enabler②）の検証（TDD: Red→Green）— TickSource 新設・DukascopyTickSource 移管。

設計正典: ``MARKETDATA_TIMESERIES_BOUNDARY_DESIGN.md`` §3.2（TickSource Protocol）/
§3.2.1（戻り値 DataFrame 案A）/ §6 S2 行 / §10.2 H-2（timestamp 列化）/
§10.2 H-3（offer_side 撤去・bid/ask 両列常時返却）/ 付録B（signature）。

確定仕様:
  1. ``marketdata.port.TickSource`` Protocol 新設：
     ``fetch_ticks(self, start: datetime, end: datetime) -> pd.DataFrame``。
  2. ``marketdata.dukascopy_source.DukascopyTickSource`` 新設（fetch_ticks_dukascopy 移管）：
     - H-2: 戻り DataFrame は timestamp を**列**に持つ（reset_index 済・列名 "timestamp"）。
       ingest の RAW_COLUMNS(timestamp/bidPrice/askPrice/bidVolume/askVolume) 契約へ直接適合。
     - H-3: __init__ から offer_side 単一指定を削除。bidPrice/askPrice **両列を常に返す**。
  3. ``marketdata.__init__`` の遅延 __getattr__ 対象に DukascopyTickSource を追加。
  4. fetch_ticks_dukascopy.py は DukascopyTickSource 委譲へ（CLI 出力不変）。
     to_canonical_ticks は無改変（last=mid・naive UTC バイト不変）。

回帰観点（memory bugfix-pair-with-regression-test）:
  - offer_side 単一指定の復活（H-3 退行）／timestamp index 化（H-2 退行）が起きたら落ちる 1 本。

ネットワーク非依存（memory dukascopy-data-source）: 実 Dukascopy fetch は叩かず、
``dukascopy_python.fetch`` を monkeypatch した fake raw frame で検証する。
"""

from __future__ import annotations

import inspect
from datetime import datetime
from typing import get_type_hints

import pandas as pd
import pytest


# =========================================================================
# Section 1: TickSource Protocol（port 契約）
# =========================================================================

def test_ticksource_protocol_exists_in_port():
    # Arrange / Act: marketdata.port から TickSource を import。
    from marketdata.port import TickSource

    # Assert: Protocol であり fetch_ticks を持つ。
    assert hasattr(TickSource, "fetch_ticks")


def test_ticksource_is_runtime_checkable_protocol():
    # Arrange
    from marketdata.port import TickSource

    # Act: 構造的に fetch_ticks(start, end) を持つ任意オブジェクトが instance 判定される。
    class _Fake:
        def fetch_ticks(self, start, end):
            return pd.DataFrame()

    # Assert: runtime_checkable Protocol（CandleSource と同じ規約）。
    assert isinstance(_Fake(), TickSource)


def test_ticksource_fetch_ticks_signature_is_start_end():
    # Arrange
    from marketdata.port import TickSource

    # Act: fetch_ticks の signature を取得。
    sig = inspect.signature(TickSource.fetch_ticks)
    params = list(sig.parameters)

    # Assert: (self, start, end)（付録B signature）。
    assert params == ["self", "start", "end"]


def test_ticksource_exported_from_marketdata_package():
    # Arrange / Act: パッケージ直下から import 可能（port 公開面）。
    import marketdata

    # Assert: TickSource が公開されている。
    assert hasattr(marketdata, "TickSource")


# =========================================================================
# Section 2: DukascopyTickSource — H-2（timestamp 列化）/ H-3（bid/ask 両列）
# =========================================================================

# Dukascopy raw fetch が返す形（timestamp を index に持ち bidPrice/askPrice/bidVolume/
# askVolume を列に持つ）を再現する fake。実 fetch はネットワーク依存のため monkeypatch する。
def _fake_fetch_frame(ts_list, bids, asks, bvols, avols):
    idx = pd.to_datetime(ts_list, utc=True)
    idx.name = "timestamp"
    return pd.DataFrame(
        {
            "bidPrice": bids,
            "askPrice": asks,
            "bidVolume": bvols,
            "askVolume": avols,
        },
        index=idx,
    )


@pytest.fixture
def patched_fetch(monkeypatch):
    """dukascopy_python.fetch を 1 日分の fake raw を返す関数へ差し替え、呼出を記録する。"""
    import dukascopy_python

    calls = []

    def _fake(instrument, interval, offer_side, start, end):
        calls.append(
            {
                "instrument": instrument,
                "interval": interval,
                "offer_side": offer_side,
                "start": start,
                "end": end,
            }
        )
        # 各呼出（日次チャンク）で 2 tick を返す（start 時刻基準）。
        base = pd.Timestamp(start)
        return _fake_fetch_frame(
            [base, base + pd.Timedelta(seconds=1)],
            bids=[39000.0, 39001.0],
            asks=[39005.0, 39006.0],
            bvols=[1.0, 2.0],
            avols=[3.0, 4.0],
        )

    monkeypatch.setattr(dukascopy_python, "fetch", _fake)
    return calls


def test_dukascopy_tick_source_returns_dataframe(patched_fetch):
    # Arrange
    from marketdata.dukascopy_source import DukascopyTickSource

    src = DukascopyTickSource()

    # Act
    out = src.fetch_ticks(datetime(2025, 1, 2), datetime(2025, 1, 3))

    # Assert: 戻り値は DataFrame（§3.2.1 案A）。
    assert isinstance(out, pd.DataFrame)


def test_dukascopy_tick_source_timestamp_is_a_column_not_index(patched_fetch):
    # H-2: 戻り DataFrame は timestamp を**列**に持つ（reset_index 済）。
    from marketdata.dukascopy_source import DukascopyTickSource

    src = DukascopyTickSource()

    out = src.fetch_ticks(datetime(2025, 1, 2), datetime(2025, 1, 3))

    # Assert: "timestamp" が列に存在し、index は default RangeIndex（timestamp が index でない）。
    assert "timestamp" in out.columns, "H-2: timestamp は列であるべき（index 化は退行）"
    assert "timestamp" != out.index.name


def test_dukascopy_tick_source_returns_both_bid_and_ask_columns(patched_fetch):
    # H-3: bidPrice/askPrice **両列を常に返す**（last=mid 算出の保全）。
    from marketdata.dukascopy_source import DukascopyTickSource

    src = DukascopyTickSource()

    out = src.fetch_ticks(datetime(2025, 1, 2), datetime(2025, 1, 3))

    # Assert: 両 price 列が存在し、bid≠ask（取り違え/単一側化を検出）。
    assert "bidPrice" in out.columns and "askPrice" in out.columns
    assert out["bidPrice"].tolist() != out["askPrice"].tolist(), \
        "H-3: bid/ask は別値（片側のみ返却＝退行）"


def test_dukascopy_tick_source_concatenates_daily_chunks(patched_fetch):
    # 日次チャンク連結（fetch_range ロジック移管）: 2 日 → 各 2 tick = 4 行。
    from marketdata.dukascopy_source import DukascopyTickSource

    src = DukascopyTickSource()

    out = src.fetch_ticks(datetime(2025, 1, 2), datetime(2025, 1, 4))

    # Assert: 2 日分（2 回 fetch）が連結され 4 行。
    assert len(patched_fetch) == 2, "日次チャンクで 2 回 fetch されるべき"
    assert len(out) == 4


def test_dukascopy_tick_source_empty_range_returns_empty_frame(patched_fetch):
    # 空期間（start==end）は fetch せず空 frame を返す。
    from marketdata.dukascopy_source import DukascopyTickSource

    src = DukascopyTickSource()

    out = src.fetch_ticks(datetime(2025, 1, 2), datetime(2025, 1, 2))

    # Assert: fetch 呼出ゼロ・空 frame。
    assert len(patched_fetch) == 0
    assert out.empty


def test_dukascopy_tick_source_init_has_no_offer_side_param():
    # H-3 回帰: __init__ から offer_side 単一指定が**削除**されている。
    # offer_side が __init__ に復活したら落ちる（回帰の壁・bugfix-pair）。
    from marketdata.dukascopy_source import DukascopyTickSource

    sig = inspect.signature(DukascopyTickSource.__init__)

    # Assert: offer_side パラメータが存在しない。
    assert "offer_side" not in sig.parameters, \
        "H-3: offer_side 単一指定は撤去されるべき（復活＝退行）"


def test_dukascopy_tick_source_uses_interval_tick(patched_fetch):
    # fetch は INTERVAL_TICK を使う（OHLC 足種ではなく tick 解像度）。
    import dukascopy_python

    from marketdata.dukascopy_source import DukascopyTickSource

    src = DukascopyTickSource()
    src.fetch_ticks(datetime(2025, 1, 2), datetime(2025, 1, 3))

    # Assert: 渡された interval が INTERVAL_TICK。
    assert patched_fetch[0]["interval"] == dukascopy_python.INTERVAL_TICK


# =========================================================================
# Section 3: DukascopyTickSource は TickSource Protocol を満たす（DIP）
# =========================================================================

def test_dukascopy_tick_source_satisfies_ticksource_protocol():
    # Arrange
    from marketdata.dukascopy_source import DukascopyTickSource
    from marketdata.port import TickSource

    # Act / Assert: 構造的 Protocol を満たす（runtime_checkable）。
    assert isinstance(DukascopyTickSource(), TickSource)


# =========================================================================
# Section 4/5: 契約テスト（RAW_COLUMNS 適合・canonical 変換・fetch_range 委譲）は
#   simulator/tests/unit/test_marketdata_tick_contract.py へ移設（ISSUE-091 #8:
#   最下層 marketdata のテストからの simulator 逆依存を排除・消費者側で検証）。
# =========================================================================


