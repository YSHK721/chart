"""market_profile（adapter/compute/market_profile.py）の検証。

対象: compute_candle_profile(candles, n_bins=60, va_pct=0.70) -> dict
      足ベース TPO マーケットプロファイルの純関数（I/O なし）。

テスト設計方針:
    - Arrange-Act-Assert の 3 区分で構造化する。
    - ゴールデン: 手計算可能な 3 本足 / n_bins=6 で bins/POC/VA を厳密照合する。
    - 境界: 単一足 / low==high（ゼロ割回避）/ 空リスト。
    - 性質: POC=最頻ビン / VA=総 tpo×va_pct を満たす最小ビン集合 / n_bins 非依存の整合。

手計算ゴールデン（GOLDEN_CANDLES, n_bins=6, va_pct=0.70）:
    price_min=10, price_max=16, span=6, bin_width=1
    edges  = [10,11,12,13,14,15,16]
    centers= [10.5,11.5,12.5,13.5,14.5,15.5]
    bar1 [10,14] -> bin 0..4 に +1
    bar2 [11,13] -> bin 1..3 に +1
    bar3 [12,16] -> bin 2..5 に +1
    tpo    = [1, 2, 3, 3, 2, 1]  (総和=12, tpo_units=3, tpo_max=3)
    POC    = 12.5（tpo 最大は bin2/bin3 の同値 → 先頭 bin2 の中心）
    VA(0.70): 閾値=12*0.70=8.4。降順に 3,3,2,2 と積むと累積 10>=8.4。
              集合={bin1,bin2,bin3,bin4} -> va_low=11.5, va_high=14.5
"""

from __future__ import annotations

import pytest

from adapter.compute import market_profile
from adapter.compute.market_profile import compute_candle_profile


def _mk(time, o, h, low, c):
    return {"time": time, "open": o, "high": h, "low": low, "close": c}


# 手計算ゴールデン用の 3 本足（time 昇順）
GOLDEN_CANDLES = [
    _mk(1, 10.0, 14.0, 10.0, 13.0),
    _mk(2, 13.0, 13.0, 11.0, 12.0),
    _mk(3, 12.0, 16.0, 12.0, 15.0),
]


class TestComputeCandleProfileGoldenStructure:
    """ゴールデン: bins 集計・価格レンジ・tpo_units・n_bins の厳密照合。"""

    def test_golden_returns_expected_bins_range_units_and_nbins(self):
        # Arrange
        candles = GOLDEN_CANDLES

        # Act
        result = compute_candle_profile(candles, n_bins=6, va_pct=0.70)

        # Assert
        assert result["price_min"] == 10.0
        assert result["price_max"] == 16.0
        assert result["tpo_units"] == 3
        assert result["n_bins"] == 6
        assert [b["price"] for b in result["bins"]] == [10.5, 11.5, 12.5, 13.5, 14.5, 15.5]
        assert [b["tpo"] for b in result["bins"]] == [1, 2, 3, 3, 2, 1]
        assert [b["norm"] for b in result["bins"]] == [
            0.3333,
            0.6667,
            1.0,
            1.0,
            0.6667,
            0.3333,
        ]


class TestComputeCandleProfilePOC:
    """ゴールデン: POC=tpo 最大ビン中心（同値時は先頭ビン）。"""

    def test_golden_poc_is_center_of_first_max_tpo_bin(self):
        # Arrange
        candles = GOLDEN_CANDLES

        # Act
        result = compute_candle_profile(candles, n_bins=6, va_pct=0.70)

        # Assert: tpo=[1,2,3,3,2,1] の最大 3 は bin2/bin3、先頭 bin2 の中心 12.5
        assert result["poc"] == 12.5


class TestComputeCandleProfileValueArea:
    """ゴールデン: VA=総 tpo×va_pct に達する最小ビン集合の中心価格レンジ。"""

    def test_golden_value_area_low_and_high(self):
        # Arrange
        candles = GOLDEN_CANDLES

        # Act
        result = compute_candle_profile(candles, n_bins=6, va_pct=0.70)

        # Assert: 総=12, 閾値=8.4。降順 3,3,2,2 累積 10>=8.4 →
        #         集合={bin1,bin2,bin3,bin4} → 中心 11.5..14.5
        assert result["va_low"] == 11.5
        assert result["va_high"] == 14.5


class TestComputeCandleProfileEmpty:
    """異常系: 空リストは例外でなく空/ゼロの安全な返りを返す。"""

    def test_empty_candles_returns_safe_zeros_without_exception(self):
        # Arrange
        candles = []

        # Act
        result = compute_candle_profile(candles, n_bins=6, va_pct=0.70)

        # Assert
        assert result["bins"] == []
        assert result["tpo_units"] == 0
        assert result["poc"] == result["price_min"]
        assert result["va_low"] == result["price_min"]
        assert result["va_high"] == result["price_min"]
        assert result["n_bins"] == 6


class TestComputeCandleProfileFlatRange:
    """境界: 全足 low==high（レンジ縮退）でゼロ割せず動く。"""

    def test_flat_candles_avoid_zero_division(self):
        # Arrange: 高安が等しい足のみ → price_max<=price_min
        candles = [_mk(1, 10.0, 10.0, 10.0, 10.0), _mk(2, 10.0, 10.0, 10.0, 10.0)]

        # Act
        result = compute_candle_profile(candles, n_bins=6, va_pct=0.70)

        # Assert: ゼロ割回避（price_max=price_min+1）・全 tpo が単一ビンに集約
        assert result["price_min"] == 10.0
        assert result["price_max"] == 11.0
        assert result["tpo_units"] == 2
        assert sum(b["tpo"] for b in result["bins"]) == 2
        assert result["price_min"] <= result["poc"] <= result["price_max"]


# --------------------------------------------------------------------------- #
# 修正2: price_range 公開ヘルパ（価格レンジ定義の単一情報源）
# --------------------------------------------------------------------------- #
class TestPriceRange:
    """price_range(candles) -> (price_min, price_max)。compute_candle_profile と同一定義。"""

    def test_price_range_normal_returns_min_low_and_max_high(self):
        # Arrange
        candles = [_mk(1, 1000, 1110, 990, 1005), _mk(2, 1005, 1108, 992, 1002)]
        # Act
        pmin, pmax = market_profile.price_range(candles)
        # Assert: min(low)=990, max(high)=1110
        assert (pmin, pmax) == (990.0, 1110.0)

    def test_price_range_empty_returns_zeros(self):
        # Arrange / Act / Assert: 空は (0.0, 0.0) の安全値。
        assert market_profile.price_range([]) == (0.0, 0.0)

    def test_price_range_degenerate_adds_one(self):
        # Arrange: low==high（price_max<=price_min の縮退）。
        candles = [_mk(1, 1000, 1000, 1000, 1000)]
        # Act
        pmin, pmax = market_profile.price_range(candles)
        # Assert: 縮退は price_max=price_min+1 に安全化（compute と同一挙動）。
        assert (pmin, pmax) == (1000.0, 1001.0)

    def test_compute_candle_profile_uses_same_range_as_helper(self):
        # Arrange: compute の返す price_min/price_max が price_range と一致（単一情報源）。
        candles = GOLDEN_CANDLES
        # Act
        pmin, pmax = market_profile.price_range(candles)
        result = compute_candle_profile(candles, n_bins=6, va_pct=0.70)
        # Assert
        assert result["price_min"] == pmin
        assert result["price_max"] == pmax


class TestComputeCandleProfileInvariantAfterRefactor:
    """回帰: price_range 抽出後も compute_candle_profile の返り値が従来値と一致（リファクタ不変）。"""

    def test_golden_profile_unchanged_after_price_range_extraction(self):
        # Arrange / Act
        result = compute_candle_profile(GOLDEN_CANDLES, n_bins=6, va_pct=0.70)
        # Assert: 抽出前の既知ゴールデン値（本ファイル冒頭 docstring）と完全一致。
        assert result == {
            "bins": [
                {"price": 10.5, "tpo": 1, "norm": 0.3333},
                {"price": 11.5, "tpo": 2, "norm": 0.6667},
                {"price": 12.5, "tpo": 3, "norm": 1.0},
                {"price": 13.5, "tpo": 3, "norm": 1.0},
                {"price": 14.5, "tpo": 2, "norm": 0.6667},
                {"price": 15.5, "tpo": 1, "norm": 0.3333},
            ],
            "poc": 12.5,
            "va_low": 11.5,
            "va_high": 14.5,
            "price_min": 10.0,
            "price_max": 16.0,
            "tpo_units": 3,
            "n_bins": 6,
        }

    def test_flat_range_profile_unchanged_after_extraction(self):
        # Arrange: 縮退足でも従来の price_max=price_min+1 挙動が保たれる。
        candles = [_mk(1, 10.0, 10.0, 10.0, 10.0), _mk(2, 10.0, 10.0, 10.0, 10.0)]
        # Act
        result = compute_candle_profile(candles, n_bins=6, va_pct=0.70)
        # Assert
        assert result["price_min"] == 10.0
        assert result["price_max"] == 11.0
        assert result["tpo_units"] == 2


# --- 以下は実装済み振る舞いに対する回帰 / 性質テスト（Red 駆動ではない） ---


class TestComputeCandleProfileSingleCandle:
    """境界: 単一足でも壊れず、レンジ内に profile が出る。"""

    def test_single_candle_produces_profile_within_range(self):
        # Arrange
        candles = [_mk(1, 10.0, 12.0, 10.0, 11.0)]

        # Act
        result = compute_candle_profile(candles, n_bins=60, va_pct=0.70)

        # Assert
        assert result["tpo_units"] == 1
        assert result["price_min"] == 10.0
        assert result["price_max"] == 12.0
        assert sum(b["tpo"] for b in result["bins"]) > 0
        assert result["price_min"] <= result["poc"] <= result["price_max"]
        assert result["price_min"] <= result["va_low"] <= result["va_high"] <= result["price_max"]


class TestComputeCandleProfilePOCProperty:
    """性質: POC は tpo 最大ビンの中心価格に一致する。"""

    def test_poc_equals_price_of_max_tpo_bin(self):
        # Arrange
        candles = GOLDEN_CANDLES

        # Act
        result = compute_candle_profile(candles, n_bins=6, va_pct=0.70)

        # Assert: 最大 tpo を持つビンの price に POC が一致
        max_tpo = max(b["tpo"] for b in result["bins"])
        max_prices = [b["price"] for b in result["bins"] if b["tpo"] == max_tpo]
        assert result["poc"] in max_prices


class TestComputeCandleProfileValueAreaProperty:
    """性質: VA レンジ内ビンの tpo 合計が総 tpo×va_pct 以上（最小性の下限充足）。"""

    def test_value_area_covers_at_least_va_pct(self):
        # Arrange
        candles = GOLDEN_CANDLES
        va_pct = 0.70

        # Act
        result = compute_candle_profile(candles, n_bins=6, va_pct=va_pct)

        # Assert
        total = sum(b["tpo"] for b in result["bins"])
        covered = sum(
            b["tpo"]
            for b in result["bins"]
            if result["va_low"] <= b["price"] <= result["va_high"]
        )
        assert covered >= total * va_pct


class TestComputeCandleProfileNBinsConsistency:
    """性質: n_bins を変えても POC/VA がレンジ・大小関係で整合する。"""

    def test_poc_consistent_across_bin_counts(self):
        # Arrange
        candles = GOLDEN_CANDLES

        # Act
        r10 = compute_candle_profile(candles, n_bins=10, va_pct=0.70)
        r60 = compute_candle_profile(candles, n_bins=60, va_pct=0.70)

        # Assert: レンジ不変、POC は両者ともレンジ内・VA 大小関係維持、
        #         POC は集中領域(12〜14 付近)で概ね一致（1 ビン幅の許容差）
        assert r10["price_min"] == r60["price_min"] == 10.0
        assert r10["price_max"] == r60["price_max"] == 16.0
        for r in (r10, r60):
            assert r["price_min"] <= r["va_low"] <= r["poc"] <= r["va_high"] <= r["price_max"]
        assert abs(r10["poc"] - r60["poc"]) <= (16.0 - 10.0) / 10


# --------------------------------------------------------------------------- #
# want_sessions（日別プロファイル分割・移植元 prototype_260630-01 mp_core want_sessions）
# --------------------------------------------------------------------------- #
_DAY = 86400
# 2024-01-01 00:00 UTC。以降 1D 刻みで別カレンダー日になる。
_D0 = 1704067200

# 各足に UTC 日付が付くよう time を日境界へ置く 3 日ぶんの足（time 昇順）。
#   day0 [10,14] / day1 [11,13] / day2 [12,16]（GOLDEN と同レンジで手計算可能）。
_SESSION_CANDLES = [
    _mk(_D0 + 0 * _DAY, 10.0, 14.0, 10.0, 13.0),
    _mk(_D0 + 1 * _DAY, 13.0, 13.0, 11.0, 12.0),
    _mk(_D0 + 2 * _DAY, 12.0, 16.0, 12.0, 15.0),
]


class TestComputeCandleProfileSessions:
    """want_sessions=True: 各カレンダー日の表示 bin プロファイルを返す（candle 経路）。

    手計算（n_bins=6, price_min=10, price_max=16, bin_width=1, edges=[10..16]）:
      day0 [10,14] -> bin 0..4 に +1 = [1,1,1,1,1,0]
      day1 [11,13] -> bin 1..3 に +1 = [0,1,1,1,0,0]
      day2 [12,16] -> bin 2..5 に +1 = [0,0,1,1,1,1]
    省略時（want_sessions=False）は sessions キーを付けない（後方互換）。
    """

    def test_omitted_has_no_sessions_key(self):
        result = compute_candle_profile(_SESSION_CANDLES, n_bins=6, va_pct=0.70)
        assert "sessions" not in result

    def test_sessions_returns_one_entry_per_calendar_day(self):
        # Act
        result = compute_candle_profile(
            _SESSION_CANDLES, n_bins=6, va_pct=0.70, want_sessions=True
        )
        # Assert: 3 日ぶん・日付昇順・各 tpo 長 = n_bins。
        sessions = result["sessions"]
        assert [s["date"] for s in sessions] == ["2024-01-01", "2024-01-02", "2024-01-03"]
        for s in sessions:
            assert len(s["tpo"]) == 6

    def test_sessions_include_per_day_poc_and_va(self):
        # 各セッションへ当日 POC/VA（_value_area 単一定義）を付与する（当日 MP 読み取りと VA 定義一致）。
        result = compute_candle_profile(
            _SESSION_CANDLES, n_bins=6, va_pct=0.70, want_sessions=True
        )
        for s in result["sessions"]:
            assert {"poc", "va_low", "va_high"} <= set(s)
            assert s["va_low"] <= s["poc"] <= s["va_high"]
        # 手計算: 2024-01-01 は bin0..4 に各1（tpo=[1,1,1,1,1,0]）→ POC=最頻(先頭 bin0 中心)。
        s0 = next(s for s in result["sessions"] if s["date"] == "2024-01-01")
        centers = [b["price"] for b in result["bins"]]
        assert s0["poc"] == round(centers[0], 2)  # argmax は同値時に先頭 index。

    def test_sessions_per_day_shape_matches_hand_calc(self):
        # Act
        result = compute_candle_profile(
            _SESSION_CANDLES, n_bins=6, va_pct=0.70, want_sessions=True
        )
        sessions = {s["date"]: s["tpo"] for s in result["sessions"]}
        # Assert: 各日が触れた [low,high] の bin へ +1（手計算）。
        assert sessions["2024-01-01"] == [1, 1, 1, 1, 1, 0]
        assert sessions["2024-01-02"] == [0, 1, 1, 1, 0, 0]
        assert sessions["2024-01-03"] == [0, 0, 1, 1, 1, 1]

    def test_sessions_sum_matches_cumulative_tpo(self):
        # 性質: 全日 sessions の bin 別合計 = 累積 tpo（分割の保存則）。
        result = compute_candle_profile(
            _SESSION_CANDLES, n_bins=6, va_pct=0.70, want_sessions=True
        )
        acc = [0] * 6
        for s in result["sessions"]:
            for j, v in enumerate(s["tpo"]):
                acc[j] += v
        cum = [b["tpo"] for b in result["bins"]]
        assert acc == cum

    def test_sessions_empty_candles(self):
        # 境界: 空リストは sessions=[] を返す（例外化しない・後方互換の安全値）。
        result = compute_candle_profile([], n_bins=6, va_pct=0.70, want_sessions=True)
        assert result["sessions"] == []
