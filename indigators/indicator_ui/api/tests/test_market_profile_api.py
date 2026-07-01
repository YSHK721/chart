"""market_profile_controller（adapter/controller/market_profile_controller.py）の検証。

対象: handle_market_profile(ref, timeframe, limit, bins, va) -> (status, payload)
      GET /market_profile の純ロジック（HTTP 殻・ソケット非依存）。handle_compute と同型で
      (HTTPステータス, ボディ) を返し、サーバ起動なしで直接呼び出し検証できる。

テスト設計方針（AAA・handle_compute / candles テストの流儀に合わせる）:
    - 正常系: 既知 ref 'sample' で 200 / {ok, profile}、profile の妥当性（poc/va/bins のレンジ整合）。
    - パラメータ有効性: bins が n_bins に反映 / limit が tpo_units に反映 / va がバリューエリア幅に反映。
    - 異常系: 未知 ref / 未知 timeframe は 400 nested error（error.type == "validation"）。
    - 配線: サーバ経由（GET /market_profile）でも 200/{ok,profile} と未知 ref 400 を確認（薄殻の1本分岐）。
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from adapter.controller.market_profile_controller import _MAX_BINS, handle_market_profile


# --------------------------------------------------------------------------- #
# 純ロジック（handle_market_profile）— サーバ起動不要の直接呼び出し
# --------------------------------------------------------------------------- #
class TestHandleMarketProfileNormal:
    """正常系: 既知 ref で 200 / {ok, profile} と profile の妥当性。"""

    def test_known_ref_returns_200_with_valid_profile(self):
        # Arrange
        ref = "sample"

        # Act
        status, payload = handle_market_profile(ref)

        # Assert
        assert status == 200
        assert payload["ok"] is True
        profile = payload["profile"]
        # 応答スキーマ: compute_candle_profile の返す全キーが揃う。
        assert set(profile) >= {
            "bins", "poc", "va_low", "va_high",
            "price_min", "price_max", "tpo_units", "n_bins",
        }
        # poc / va はレンジ内で整合する。
        assert profile["price_min"] <= profile["poc"] <= profile["price_max"]
        assert profile["price_min"] <= profile["va_low"] <= profile["va_high"] <= profile["price_max"]
        assert profile["n_bins"] == 60  # 既定 bins。
        assert len(profile["bins"]) == 60


class TestHandleMarketProfileParams:
    """パラメータ有効性: bins / limit / va がそれぞれ profile に反映される。"""

    def test_bins_param_controls_n_bins(self):
        # Arrange / Act
        status, payload = handle_market_profile("sample", bins="12")

        # Assert
        assert status == 200
        assert payload["profile"]["n_bins"] == 12
        assert len(payload["profile"]["bins"]) == 12

    def test_limit_param_controls_tpo_units(self):
        # Arrange / Act: 直近 10 本のみで集計 → tpo_units == 10。
        status, payload = handle_market_profile("sample", limit="10")

        # Assert
        assert status == 200
        assert payload["profile"]["tpo_units"] == 10

    def test_va_param_widens_value_area(self):
        # Arrange / Act: va を大きくするとバリューエリア幅は縮まない（単調・同一 bins/limit で比較）。
        _, narrow = handle_market_profile("sample", limit="50", va="0.5")
        _, wide = handle_market_profile("sample", limit="50", va="0.9")

        # Assert
        narrow_w = narrow["profile"]["va_high"] - narrow["profile"]["va_low"]
        wide_w = wide["profile"]["va_high"] - wide["profile"]["va_low"]
        assert wide_w >= narrow_w


class TestHandleMarketProfileInputBounds:
    """入力境界クランプ（回帰）: 不正な bins/va でも 500/退化させず安全範囲へ丸める。"""

    def test_bins_zero_is_clamped_not_500(self):
        # bins=0 は以前 np.zeros(0).max() で 500 になっていた → クランプで 200・n_bins>=1。
        status, payload = handle_market_profile("sample", bins="0")
        assert status == 200
        assert payload["profile"]["n_bins"] >= 1

    def test_bins_huge_is_clamped_to_max(self):
        # 巨大 bins は単一スレッド常駐サーバを占有しうる → _MAX_BINS にクランプ。
        status, payload = handle_market_profile("sample", bins="100000000")
        assert status == 200
        assert payload["profile"]["n_bins"] == _MAX_BINS

    def test_va_out_of_range_is_clamped(self):
        # 負/>1/NaN の va は無言退化(VA消失/全域)を招く → 有限 [0.01,1.0] にクランプし妥当な VA。
        for bad in ("-1", "5", "nan", "inf"):
            status, payload = handle_market_profile("sample", limit="50", va=bad)
            assert status == 200, bad
            p = payload["profile"]
            assert p["price_min"] <= p["va_low"] <= p["va_high"] <= p["price_max"], bad


class TestHandleMarketProfileErrors:
    """異常系: 未知 ref / 未知 timeframe は 400 nested error。"""

    def test_unknown_ref_returns_400_validation(self):
        # Act
        status, payload = handle_market_profile("unknown")

        # Assert
        assert status == 400
        assert payload["ok"] is False
        assert payload["error"]["type"] == "validation"

    def test_unknown_timeframe_returns_400_validation(self):
        # Act
        status, payload = handle_market_profile("sample", timeframe="9z")

        # Assert
        assert status == 400
        assert payload["error"]["type"] == "validation"


# --------------------------------------------------------------------------- #
# 配線スモーク: GET /market_profile（薄殻の 1 本分岐が handler へ届くか）
# --------------------------------------------------------------------------- #
@pytest.fixture()
def server():
    from framework.server import IndicatorUIRequestHandler

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), IndicatorUIRequestHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _get(base: str, path: str):
    try:
        with urllib.request.urlopen(base + path, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_get_market_profile_returns_200_with_profile(server):
    status, payload = _get(server, "/market_profile?datasetRef=sample&bins=20&limit=30")
    assert status == 200
    assert payload["ok"] is True
    assert payload["profile"]["n_bins"] == 20
    assert payload["profile"]["tpo_units"] == 30


def test_get_market_profile_unknown_ref_returns_400(server):
    status, payload = _get(server, "/market_profile?datasetRef=unknown")
    assert status == 400
    assert payload["error"]["type"] == "validation"
