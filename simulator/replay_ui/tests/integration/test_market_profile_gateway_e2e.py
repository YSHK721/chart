"""MarketProfileGateway 実データ e2e: as-seen-at-t の因果切断＋全TFパリティを実証する。

fake ではなく実 bridge（indicator_ui handle_market_profile）へ委譲した結果を検証する:
  1. to=T は ``time<=T`` の足だけで集計する（as-seen-at-t）＝ to を過去へ動かすと価格レンジが縮む
     （未来リーク無し・因果）。
  2. normal/sessions/replay（/market_profile as-of）は全 TF（1m〜1M）で成功。特に 1W/1M も成功する
     （ticklive×{1W,1M} が forming 非対応＝validation 失敗なのに対し、as-of-cursor は candle
     resample で代替できる）。
  3. ISSUE-502 段階 5B: gateway は実 bridge の (status, body) を PortResult へ翻訳する。実データ経路で
     翻訳が成立すること（fail-closed 検査が本番で発火しないこと）を、ここが実測で押さえる。

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
    full = gw.profile("jp225_tick", "1D", None, "60", "0.7", "candle", None, to=None)
    to_2020 = gw.profile("jp225_tick", "1D", None, "60", "0.7", "candle", None, to=_TO_2020)
    # Assert: 両者成功。to=過去 は全期間より価格上限が小さい（未来足を含まない＝as-seen-at-t）。
    assert full.ok and to_2020.ok
    assert to_2020.payload["profile"]["price_max"] < full.payload["profile"]["price_max"]


def test_all_timeframes_including_1W_1M_succeed_for_as_of_cursor():
    gw = MarketProfileGateway()
    for tf in ("1m", "5m", "15m", "1h", "1D", "1W", "1M"):
        result = gw.profile("jp225_tick", tf, None, "60", "0.7", "candle", None, to=_TO_2024)
        assert result.ok, (tf, result.error_type)
        assert "profile" in result.payload, tf


def test_ticklive_1W_1M_fails_validation_while_as_of_succeeds_documented_gap():
    """ticklive（forming）は 1W/1M 構造的非対応＝validation 失敗。同 TF の as-of-cursor は成功。

    分類は Port の面に現れる（error_type）。HTTP ステータスは現れない——番号への写像は
    framework 層の写像関数だけが持つ（ISSUE-502 段階 5B）。
    """
    mp = MarketProfileGateway()
    fm = MarketProfileFormingGateway()
    for tf in ("1W", "1M"):
        asof = mp.profile("jp225_tick", tf, None, "60", "0.7", "candle", None, to=_TO_2024)
        forming = fm.forming("jp225_tick", tf, int(_TO_2024), 1, None, None, None, None)
        assert asof.ok, tf
        assert (forming.ok, forming.error_type) == (False, "validation"), tf
        # ボディは bridge が組んだものを無改変で運ぶ（応答 byte が変わらない根拠）。
        assert forming.payload["error"]["type"] == "validation", tf
