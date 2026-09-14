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

from adapter.compute import dataset
from market_profile_api.controller.market_profile_controller import _MAX_BINS, handle_market_profile


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


class TestHandleMarketProfileReplayTo:
    """リプレイ時間カーソル ``to``（UNIX 秒・as-seen-at-t）: candle 経路が T までの足だけで計算される。

    移植元: prototype_260630-01（as-seen-at-t・アンカー）。``to`` 省略時は従来（全期間・後方互換）、
    T を跨ぐ足は除外し未来リーク無し、不正 ``to`` は無視（全期間）。
    """

    def test_to_truncates_candles_to_prefix(self):
        # Arrange: sample の idx=99 の足 time を境界 T にする（0..99 の 100 本のみが対象）。
        candles = dataset.load_candles("sample", None, None)
        t = candles[99]["time"]

        # Act: to=T を指定（tpo_units = T までの足数）。
        status, payload = handle_market_profile("sample", to=str(t))

        # Assert: T までの足だけ（100 本）で集計される。
        assert status == 200
        assert payload["profile"]["tpo_units"] == 100

    def test_to_excludes_candles_after_t_golden(self):
        # Arrange: T を跨ぐ（T より後の）足が除外される golden。to=T-1（idx=99 の直前）で 99 本。
        candles = dataset.load_candles("sample", None, None)
        t_minus = candles[99]["time"] - 1  # idx=99 の足 time 未満 → 0..98 の 99 本のみ。

        # Act
        _, payload = handle_market_profile("sample", to=str(t_minus))

        # Assert: T より後の足は入らない（未来リーク無し）。
        assert payload["profile"]["tpo_units"] == 99

    def test_to_omitted_is_full_period_backward_compat(self):
        # Arrange / Act: to 省略は従来どおり全期間集計（後方互換）。
        _, full = handle_market_profile("sample")
        _, none_to = handle_market_profile("sample", to=None)

        # Assert: to=None と to 省略は同一（全 2981 本）。
        assert full["profile"]["tpo_units"] == none_to["profile"]["tpo_units"]
        assert full["profile"]["tpo_units"] == len(dataset.load_candles("sample", None, None))

    def test_invalid_to_is_ignored_full_period(self):
        # Arrange / Act: 不正 to（非数値）は無視して全期間（500/退化させない）。
        _, full = handle_market_profile("sample")
        for bad in ("abc", "", "nan"):
            status, payload = handle_market_profile("sample", to=bad)
            assert status == 200, bad
            assert payload["profile"]["tpo_units"] == full["profile"]["tpo_units"], bad

    def test_to_before_first_candle_yields_empty_profile(self):
        # Arrange: 全足より前の to は空プロファイル（従来の空応答挙動・500 化しない）。
        candles = dataset.load_candles("sample", None, None)
        t = candles[0]["time"] - 1

        # Act
        status, payload = handle_market_profile("sample", to=str(t))

        # Assert
        assert status == 200
        assert payload["profile"]["tpo_units"] == 0


class TestHandleMarketProfileReplayFrom:
    """ローリング窓 ``from``（UNIX 秒）: candle 経路が from..to の窓だけで計算される（増分2 A）。

    移植元: prototype_260630-01（ローリング窓 = T 直前 N 本）。``from`` 省略時は従来（全期間・
    後方互換）、``from`` 指定時は ``from <= time`` の足だけで集計、不正/範囲外 ``from`` は無視（全期間）。
    """

    def test_from_truncates_candles_to_suffix(self):
        # Arrange: idx=99 の足 time を from 下限にする（idx 99.. が残る）。
        candles = dataset.load_candles("sample", None, None)
        f = candles[99]["time"]
        total = len(candles)

        # Act
        status, payload = handle_market_profile("sample", **{"from": str(f)})

        # Assert: from 以上の足だけ（total-99 本）で集計される。
        assert status == 200
        assert payload["profile"]["tpo_units"] == total - 99

    def test_from_and_to_form_a_window(self):
        # Arrange: from=idx60 の time, to=idx79 の time → 60..79 の 20 本の窓（ローリング窓）。
        candles = dataset.load_candles("sample", None, None)
        f = candles[60]["time"]
        t = candles[79]["time"]

        # Act
        status, payload = handle_market_profile("sample", **{"from": str(f), "to": str(t)})

        # Assert: [from, to] の窓 20 本だけ（未来リークも過去リークも無い）。
        assert status == 200
        assert payload["profile"]["tpo_units"] == 20

    def test_from_omitted_is_full_period_backward_compat(self):
        # Arrange / Act: from 省略は従来どおり全期間（後方互換）。
        _, full = handle_market_profile("sample")
        _, none_from = handle_market_profile("sample", **{"from": None})

        # Assert
        assert none_from["profile"]["tpo_units"] == full["profile"]["tpo_units"]

    def test_invalid_from_is_ignored_full_period(self):
        # Arrange / Act: 不正 from（非数値・負）は無視して全期間（500/退化させない）。
        _, full = handle_market_profile("sample")
        for bad in ("abc", "", "nan", "-5"):
            status, payload = handle_market_profile("sample", **{"from": bad})
            assert status == 200, bad
            assert payload["profile"]["tpo_units"] == full["profile"]["tpo_units"], bad

    def test_from_greater_than_to_yields_empty_not_error(self):
        # Arrange: from > to は空窓（交差なし）。500 化せず空プロファイル。
        candles = dataset.load_candles("sample", None, None)
        f = candles[80]["time"]
        t = candles[40]["time"]

        # Act
        status, payload = handle_market_profile("sample", **{"from": str(f), "to": str(t)})

        # Assert
        assert status == 200
        assert payload["profile"]["tpo_units"] == 0


class TestHandleMarketProfileSnapshotToday:
    """スナップショット ``today=1``（増分2 C）: 応答に today[]/today_max（窓最終足ぶんの表示bin値）が付く。

    移植元: prototype_260630-01 mp_core want_today（candle=最終足の寄与）。today 省略時は today[]/
    today_max を付けない（後方互換）。today[] の長さは n_bins。合成足で最終足の寄与のみが乗ることを検証。
    """

    def test_today_omitted_has_no_today_keys(self):
        # Arrange / Act: today 省略 → 応答に today/today_max を付けない（後方互換）。
        _, payload = handle_market_profile("sample", bins="30")
        # Assert
        assert "today" not in payload["profile"]
        assert "today_max" not in payload["profile"]

    def test_today_1_returns_today_array_len_nbins(self):
        # Arrange / Act: today=1 で today[] が長さ n_bins・today_max>0 で返る。
        _, payload = handle_market_profile("sample", bins="30", today="1")
        profile = payload["profile"]
        # Assert
        assert "today" in profile
        assert len(profile["today"]) == 30
        assert profile["today_max"] >= 1.0

    def test_today_reflects_last_candle_only(self, monkeypatch):
        # Arrange: 合成 3 足。最終足だけが high/low を張る帯を today[] に持つ（前 2 足とは別価格帯）。
        import market_profile_api.controller.market_profile_controller as ctrl
        candles = [
            {"time": 100, "open": 1000, "high": 1010, "low": 1000, "close": 1005},
            {"time": 200, "open": 1005, "high": 1015, "low": 1002, "close": 1010},
            {"time": 300, "open": 1010, "high": 1090, "low": 1080, "close": 1085},  # 最終足＝高帯。
        ]
        monkeypatch.setattr(ctrl.dataset, "load_candles", lambda ref, tf, limit: candles)

        # Act
        _, payload = handle_market_profile("sample", bins="20", today="1")
        profile = payload["profile"]

        # Assert: today[] の非ゼロ bin は最終足(1080..1090)に対応する高価格帯のみ。
        price_min = profile["price_min"]
        price_max = profile["price_max"]
        binw = (price_max - price_min) / profile["n_bins"]
        nonzero = [i for i, v in enumerate(profile["today"]) if v > 0]
        assert nonzero, "最終足の寄与で today[] に非ゼロ bin がある"
        # 非ゼロ bin の中心価格はすべて 1080 付近以上（前 2 足 1000-1015 帯には乗らない）。
        for i in nonzero:
            center = price_min + (i + 0.5) * binw
            assert center >= 1050, (i, center)


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


# --------------------------------------------------------------------------- #
# sessions=1（日別プロファイル分割・candle 経路・移植元 prototype_260630-01）
# --------------------------------------------------------------------------- #
class TestHandleMarketProfileSessions:
    """sessions=1: 応答トップレベルに sessions[{date,tpo[]}] が付き、profile 8 キーは不変（追加キーのみ）。"""

    def test_sessions_omitted_no_sessions_key(self):
        # 後方互換: sessions 省略時はトップレベルにも profile にも sessions を付けない。
        _, payload = handle_market_profile("sample", limit="10")
        assert "sessions" not in payload
        assert "sessions" not in payload["profile"]

    def test_sessions_1_adds_toplevel_sessions_list(self):
        # Act: sessions=1（kwargs 経由・?sessions=1 相当）。
        status, payload = handle_market_profile("sample", limit="5", **{"sessions": "1"})
        # Assert: 200・sessions はトップレベル・profile 内には入れない（8 キー不変）。
        assert status == 200
        assert "sessions" in payload
        assert "sessions" not in payload["profile"]
        sessions = payload["sessions"]
        # sample は 1 日 1 足なので 5 本 = 5 日ぶん。各 tpo 長 = n_bins。
        assert len(sessions) == 5
        n_bins = payload["profile"]["n_bins"]
        for s in sessions:
            assert isinstance(s["date"], str)
            assert len(s["tpo"]) == n_bins

    def test_sessions_windowed_by_to(self):
        # to 窓で日が絞られる: to=T 以下の足だけが sessions に含まれる。
        candles = dataset.load_candles("sample", None, None)
        t = candles[2]["time"]
        _, payload = handle_market_profile("sample", to=str(t), **{"sessions": "1"})
        assert len(payload["sessions"]) == 3  # index 0,1,2 の 3 日。

    def test_sessions_capped_to_recent_days(self):
        # 回帰（応答肥大防止）: 全期間要求でも sessions は直近 _SESSIONS_MAX_DAYS 日にキャップされ、
        # 直近日（末尾）が保持される（UI は直近 nFit 列しか描かないため十分）。
        from market_profile_api.controller.market_profile_controller import _SESSIONS_MAX_DAYS
        candles = dataset.load_candles("sample", None, None)
        _, payload = handle_market_profile("sample", **{"sessions": "1"})
        sessions = payload["sessions"]
        assert len(sessions) <= _SESSIONS_MAX_DAYS
        if len(candles) > _SESSIONS_MAX_DAYS:
            assert len(sessions) == _SESSIONS_MAX_DAYS
        # 末尾（直近日）が最後の candle の日付と一致（先頭でなく直近を残す）。
        import datetime as _dt
        last_date = _dt.datetime.fromtimestamp(candles[-1]["time"], _dt.UTC).strftime("%Y-%m-%d")
        assert sessions[-1]["date"] == last_date

    def test_sessions_total_equals_pre_cap_day_count_when_capped(self):
        # 注記の意味論整合（修正1）: キャップ発火時（sample は全期間 > 60 日）でも sessions_total は
        #   キャップ前の実日数（= キャップされた len(sessions)=60 ではない）を返す。
        #   primitive 注記「直近N/全M日」の M を実日数にするための素材（キャップ後 60 の誤読を防ぐ）。
        from market_profile_api.controller.market_profile_controller import _SESSIONS_MAX_DAYS
        candles = dataset.load_candles("sample", None, None)
        assert len(candles) > _SESSIONS_MAX_DAYS  # 前提: sample はキャップ発火する日数を持つ。
        _, payload = handle_market_profile("sample", **{"sessions": "1"})
        # sessions_total はキャップ前の実日数と一致する（キャップ後 len(sessions)=60 とは異なる）。
        assert payload["sessions_total"] == len(candles)
        assert payload["sessions_total"] > _SESSIONS_MAX_DAYS
        assert payload["sessions_total"] != len(payload["sessions"])
        assert isinstance(payload["sessions_total"], int)

    def test_sessions_total_omitted_when_sessions_not_requested(self):
        # 追加キーのみ（後方互換）: sessions を要求しない場合は sessions_total を付けない。
        _, payload = handle_market_profile("sample", limit="10")
        assert "sessions_total" not in payload
