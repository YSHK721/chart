"""MarketProfileGateway 実データ e2e: as-seen-at-t の因果切断＋全TFパリティを実証する。

fake ではなく実 bridge（indicator_ui handle_market_profile）へ委譲した結果を検証する:
  1. to=T は ``time<=T`` の足だけで集計する（as-seen-at-t）＝ to を過去へ動かすと価格レンジが縮む
     （未来リーク無し・因果）。
  2. normal/sessions/replay（/market_profile as-of）は全 TF（1m〜1M）で 200 成立。特に 1W/1M も成立する
     （ticklive×{1W,1M} が forming 非対応＝400 なのに対し、as-of-cursor は candle resample で代替できる）。

実データ（data/marketdata/jp225_tick_m1.csv）を要するため、不在時は skip する。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from simulator.replay_ui.adapter.market_profile_forming_gateway import (
    MarketProfileFormingGateway,
)
from simulator.replay_ui.adapter.market_profile_gateway import MarketProfileGateway

_REPO_ROOT = Path(__file__).resolve().parents[4]
_TICK_CSV = _REPO_ROOT / "data" / "marketdata" / "jp225_tick_m1.csv"

pytestmark = pytest.mark.skipif(
    not _TICK_CSV.exists(), reason="実 tick データ（jp225_tick_m1.csv）が無いため skip"
)

# 2020-01-01 00:00:00 UTC（データ期間中の途中 T・as-seen-at-t 切断の観測点）。
_TO_2020 = "1577836800"
# 2024-01-01 頃（1W/1M パリティ観測点）。
_TO_2024 = "1704074400"


def test_as_seen_at_t_truncates_price_range_causally():
    gw = MarketProfileGateway()
    # Arrange / Act: 全期間 と to=2020 の 2 回取得（同一 TF/bins）。
    st_full, b_full = gw.profile("jp225_tick", "1D", None, "60", "0.7", "candle", None, to=None)
    st_to, b_to = gw.profile("jp225_tick", "1D", None, "60", "0.7", "candle", None, to=_TO_2020)
    # Assert: 両者 200。to=過去 は全期間より価格上限が小さい（未来足を含まない＝as-seen-at-t）。
    assert st_full == 200 and st_to == 200
    assert b_to["profile"]["price_max"] < b_full["profile"]["price_max"]


def test_all_timeframes_including_1W_1M_succeed_for_as_of_cursor():
    gw = MarketProfileGateway()
    for tf in ("1m", "5m", "15m", "1h", "1D", "1W", "1M"):
        st, b = gw.profile("jp225_tick", tf, None, "60", "0.7", "candle", None, to=_TO_2024)
        assert st == 200, tf
        assert "profile" in b, tf


def test_ticklive_1W_1M_is_400_while_as_of_is_200_documented_gap():
    # ticklive（forming）は 1W/1M 構造的非対応＝400。同 TF の as-of-cursor は 200（代替可能）。
    mp = MarketProfileGateway()
    fm = MarketProfileFormingGateway()
    for tf in ("1W", "1M"):
        st_asof, _ = mp.profile("jp225_tick", tf, None, "60", "0.7", "candle", None, to=_TO_2024)
        st_forming, body_forming = fm.forming("jp225_tick", tf, int(_TO_2024), 1, None, None, None, None)
        assert st_asof == 200, tf
        assert st_forming == 400, tf
        assert body_forming["error"]["type"] == "validation", tf
