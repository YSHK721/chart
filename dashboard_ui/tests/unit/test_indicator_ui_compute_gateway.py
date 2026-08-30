"""P-1 / P-2 の実装（既存 `/compute` を read-only で読む）を固定する。

§7 の計算量規律（CLAUDE.md 絶対命令 §4.1）: 本シートは既存の計算結果を**読むだけ**である。
測るのは時間ではなく回数であり、固定するのは**無駄の不在**（発行 − 使用 = 0）と、
入力（消費者の数・要求の回数）を増やしても発行が増えないことである。回数そのものは
期待値に焼き込まない。

素材（pandas DataFrame）と計算面は Test Spy で差し替える。ここで見たいのは「同じ計算を
2 回発行しないこと」と「系列 JSON の読み取り規約」であり、指標の値そのものではない
（値の一致は `test_forward_matches_reference_probe.py` が参照実装と突き合わせる）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from dashboard_ui.adapter.gateway.indicator_ui_compute_gateway import (
    IndicatorUiComputeGateway,
)
from dashboard_ui.usecase.sheet_ports import SeriesSupplyUnavailable

REF = "jp225_tick"
#: 2026-08-28 20:10:00 UTC。1m 足 4 本ぶんの素材。
START = 1_787_003_400


def frame(rows: int = 4, *, step: int = 60) -> pd.DataFrame:
    index = pd.to_datetime([START + i * step for i in range(rows)], unit="s")
    return pd.DataFrame(
        {
            "open": [100.0 + i for i in range(rows)],
            "high": [110.0 + i for i in range(rows)],
            "low": [90.0 + i for i in range(rows)],
            "close": [105.0 + i for i in range(rows)],
            "volume": [10.0 + i for i in range(rows)],
        },
        index=index,
    )


class ComputeSpy:
    """`/compute` 面の Test Spy。発行した (指標, 足) を記録する。"""

    def __init__(self, series_by_indicator=None, frames=None, latest=None) -> None:
        self.issued: "list[tuple[str, str, int]]" = []
        self.latest: "list[tuple[str, str, int]]" = []
        self.loaded: "list[tuple[str, str]]" = []
        self._series = dict(series_by_indicator or {})
        self._latest = dict(latest or {})
        self._frames = dict(frames or {})

    # --- dataset 面 ---
    def is_known(self, ref) -> bool:
        return ref == REF

    def is_known_timeframe(self, timeframe) -> bool:
        return timeframe in {"1m", "5m", "1h"}

    def load_dataframe(self, ref, timeframe=None):
        self.loaded.append((ref, timeframe))
        return self._frames.get(timeframe, frame())

    # --- compute 面 ---
    def full_compute(self, adapter, indicator, variant, df, params):
        self.issued.append((indicator, variant, len(df)))
        return self._series.get(indicator, [])

    def latest_compute(self, adapter, indicator, variant, df, params):
        """形成中足ぶんの末尾 1 点（ISSUE-457 の段 2）。**`issued` には数えない**。

        数えるのは確定素材の発行（`full_compute`）だけである。末尾 1 点は段 2 の観測値更新で
        あり、要求ごとに出るのが仕様（§7）。ここで一緒に数えると「無駄の不在」の表明が
        「更新しないこと」の表明にすり替わる。
        """
        self.latest.append((indicator, variant, len(df)))
        return self._latest.get(indicator, [])

    def namespace(self) -> SimpleNamespace:
        return SimpleNamespace(
            dataset=self, adapter=object(), full_compute=self.full_compute,
            latest_compute=self.latest_compute,
        )


def line(name: str, points, kind: str = "line") -> dict:
    return {
        "name": name,
        "kind": kind,
        "data": [{"time": time, "value": value} for time, value in points],
    }


def gateway_with(spy: ComputeSpy, **kwargs) -> IndicatorUiComputeGateway:
    return IndicatorUiComputeGateway(bridge=spy.namespace(), **kwargs)


# --------------------------------------------------------------- 系列の読み取り
def test_the_series_are_returned_as_time_value_pairs() -> None:
    spy = ComputeSpy({"ma_marod": [line("ma_marod", [(START, 1.5), (START + 60, 2.5)])]})

    series = gateway_with(spy).full_series(
        indicator_id="ma_marod", variant="default", params={}, dataset_ref=REF,
        timeframe="1m",
    )

    assert series == {"ma_marod": ((START, 1.5), (START + 60, 2.5))}


def test_points_without_a_value_are_dropped() -> None:
    """warm-up の欠測（value=None）は点として持たない（NaN の水準を並びへ入れない）。"""
    spy = ComputeSpy({"x": [{"name": "s", "kind": "line",
                             "data": [{"time": START, "value": None},
                                      {"time": START + 60, "value": 3.0}]}]})

    series = gateway_with(spy).full_series(
        indicator_id="x", variant="default", params={}, dataset_ref=REF, timeframe="1m",
    )

    assert series == {"s": ((START + 60, 3.0),)}


def test_an_empty_duplicate_series_does_not_hide_the_real_one() -> None:
    """実測: 指標 ma_marod は同名で line（点あり）と horizontal_line（点なし）を返す。

    素直に名前で辞書へ入れると、後から来た空の系列が実体を消す（水準が丸ごと落ちる）。
    """
    spy = ComputeSpy({"ma_marod": [
        line("ma_marod", [(START, 1.5)]),
        line("ma_marod", [], kind="horizontal_line"),
    ]})

    series = gateway_with(spy).full_series(
        indicator_id="ma_marod", variant="default", params={}, dataset_ref=REF,
        timeframe="1m",
    )

    assert series == {"ma_marod": ((START, 1.5),)}


# ------------------------------------------------------------------- 計算量
def test_the_same_key_is_computed_once() -> None:
    """(a) 同一キーの full 系列発行は 1 回以下（T-1）。"""
    spy = ComputeSpy({"ma_marod": [line("ma_marod", [(START, 1.5)])]})
    gateway = gateway_with(spy)

    for _ in range(3):
        gateway.full_series(indicator_id="ma_marod", variant="default", params={},
                            dataset_ref=REF, timeframe="1m")

    assert len(spy.issued) == 1


def test_every_issued_computation_is_used() -> None:
    """(b) 発行 − 使用 = 0（読むだけの表が新しい計算を作らない）。"""
    spy = ComputeSpy({"ma_marod": [line("ma_marod", [(START, 1.5)])],
                      "rsi": [line("rsi", [(START, 55.0)])]})
    gateway = gateway_with(spy)

    used = [
        gateway.full_series(indicator_id=indicator, variant="default", params={},
                            dataset_ref=REF, timeframe="1m")
        for indicator in ("ma_marod", "rsi")
    ]

    assert len(spy.issued) - len([series for series in used if series]) == 0


def test_more_consumers_of_the_same_series_do_not_issue_more(
) -> None:
    """オーダーの表明: 同じ系列を読む消費者が 2 → 5 に増えても発行は変わらない。"""
    spy = ComputeSpy({"ma_marod": [line("ma_marod", [(START, 1.5)])]})
    gateway = gateway_with(spy)

    for consumers in (2, 5):
        for _ in range(consumers):
            gateway.full_series(indicator_id="ma_marod", variant="default", params={},
                                dataset_ref=REF, timeframe="1m")

    assert len(spy.issued) == 1


def test_different_parameters_are_different_keys() -> None:
    """畳み込みキーはパラメータを含む（違う設定を同じ計算で済ませない）。"""
    spy = ComputeSpy({"ma_marod": [line("ma_marod", [(START, 1.5)])]})
    gateway = gateway_with(spy)

    gateway.full_series(indicator_id="ma_marod", variant="default", params={"length": 24},
                        dataset_ref=REF, timeframe="1m")
    gateway.full_series(indicator_id="ma_marod", variant="default", params={"length": 50},
                        dataset_ref=REF, timeframe="1m")

    assert len(spy.issued) == 2


def test_the_bars_of_one_timeframe_are_loaded_once() -> None:
    """素材の読み込みも足ごとに 1 回（同じ足を 2 回組み立てない）。"""
    spy = ComputeSpy()
    gateway = gateway_with(spy)

    gateway.bars(dataset_ref=REF, timeframe="1m")
    gateway.bars(dataset_ref=REF, timeframe="1m")
    gateway.full_series(indicator_id="x", variant="default", params={},
                        dataset_ref=REF, timeframe="1m")

    assert spy.loaded == [(REF, "1m")]


# ------------------------------------------------ 確定素材と形成中足の継ぎ目（ISSUE-457）
def test_the_confirmed_material_and_the_forming_point_are_spliced() -> None:
    """確定足ぶん（共有される）＋ 形成中足の 1 点（毎要求作る）が 1 本の系列になる。"""
    spy = ComputeSpy(
        {"ma": [line("ma", [(START, 1.0), (START + 60, 2.0), (START + 120, 3.0)])]},
        latest={"ma": [line("ma", [(START + 180, 4.0)])]},
    )

    series = gateway_with(spy).full_series(
        indicator_id="ma", variant="default", params={}, dataset_ref=REF, timeframe="1m",
    )

    assert series == {"ma": ((START, 1.0), (START + 60, 2.0), (START + 120, 3.0),
                            (START + 180, 4.0))}


def test_the_confirmed_material_is_computed_without_the_forming_bar() -> None:
    """共有できるのは**確定した足だけ**である（形成中足を混ぜたら epoch の中で不変でない）。"""
    spy = ComputeSpy(frames={"1m": frame(rows=4)})

    gateway_with(spy).full_series(
        indicator_id="ma", variant="default", params={}, dataset_ref=REF, timeframe="1m",
    )

    assert [rows for _indicator, _variant, rows in spy.issued] == [3]   # 4 本 − 形成中 1 本
    assert [rows for _indicator, _variant, rows in spy.latest] == [4]   # 末尾点は全件から


def test_a_series_without_a_forming_point_ends_at_the_last_confirmed_bar() -> None:
    """増分器が末尾 1 点を出せない系列は確定足で終わる（ライブ core と同じ粒度）。

    ライブの毎ティック末尾値アダプタ（live_tick_tails）も増分宣言のある系列しか動かさない
    ＝チャートでティックごとに動く線と動かない線の区別に一致する。実測 2026-08-30 の該当は
    profit_rsi の帯外 4 系列だけで、第 1 表・第 2 表のどちらにも使われていない。
    """
    spy = ComputeSpy(
        {"x": [line("a", [(START, 1.0)]), line("b", [(START, 9.0)])]},
        latest={"x": [line("a", [(START + 180, 2.0)])]},
    )

    series = gateway_with(spy).full_series(
        indicator_id="x", variant="default", params={}, dataset_ref=REF, timeframe="1m",
    )

    assert series == {"a": ((START, 1.0), (START + 180, 2.0)), "b": ((START, 9.0),)}


def test_a_single_bar_of_material_is_computed_in_one_go() -> None:
    """確定足が 1 本も無いときは分けない（分けようがない）。"""
    spy = ComputeSpy({"ma": [line("ma", [(START, 1.0)])]}, frames={"1m": frame(rows=1)})

    series = gateway_with(spy).full_series(
        indicator_id="ma", variant="default", params={}, dataset_ref=REF, timeframe="1m",
    )

    assert series == {"ma": ((START, 1.0),)}
    assert [rows for _indicator, _variant, rows in spy.issued] == [1]
    assert spy.latest == []


def test_a_forming_point_failure_is_translated_to_the_supply_contract() -> None:
    """形成中足の計算が落ちても応答無しの接続断にしない（ISSUE-459 と同じ翻訳）。"""

    class FailingLatest(ComputeSpy):
        def latest_compute(self, adapter, indicator, variant, df, params):
            raise FakeComputeError("validation: E01_INSUFFICIENT_BARS")

        def namespace(self) -> SimpleNamespace:
            return SimpleNamespace(
                dataset=self, adapter=object(), full_compute=self.full_compute,
                latest_compute=self.latest_compute, compute_error=FakeComputeError,
            )

    gw = gateway_with(FailingLatest({"ma": [line("ma", [(START, 1.0)])]}))

    with pytest.raises(SeriesSupplyUnavailable):
        gw.full_series(indicator_id="ma", variant="default", params={},
                       dataset_ref=REF, timeframe="1m")


# ---------------------------------------------------------------------- 足
def test_the_bars_carry_unix_seconds_and_ohlc() -> None:
    spy = ComputeSpy()

    bars = gateway_with(spy).bars(dataset_ref=REF, timeframe="1m")

    assert len(bars) == 4
    assert (bars[0].time, bars[0].open, bars[0].high, bars[0].low, bars[0].close) == (
        START, 100.0, 110.0, 90.0, 105.0
    )


def test_the_bar_limit_keeps_only_the_tail() -> None:
    """費用の上限は参照実装 `probe_inverse.py` の足ごとの本数表に従う。"""
    spy = ComputeSpy(frames={"1m": frame(rows=10)})

    bars = gateway_with(spy, bar_limits={"1m": 3}).bars(dataset_ref=REF, timeframe="1m")

    assert len(bars) == 3
    assert bars[-1].time == START + 9 * 60


def test_the_forming_bar_is_the_last_bar_of_the_period_that_contains_now() -> None:
    spy = ComputeSpy()
    gateway = gateway_with(spy)

    forming = gateway.forming_bar(dataset_ref=REF, timeframe="1m",
                                  now_unix=START + 3 * 60 + 30)

    assert forming is not None
    assert forming.time == START + 3 * 60


def test_the_forming_bar_is_the_same_object_as_the_tail_of_bars() -> None:
    """P-2 の契約（レビュー 🟡-8）: `bars()` の末尾は **形成中の足でありうる**。

    `bars()` を「確定足の全件」と読むと `bars()[-1]` を確定足として扱う実装が生まれ、
    形成中の足を確定値として使う無言の誤りになる。実装の真実は「末尾は `forming_bar()` と
    同一物」であり、確定足だけを見たい呼び出し側は `bars()[-2]` を取る
    （参照実装 `tools/measure/issue449/probe_heatmap.py:131-132` の `h[-2]` と同じ位置）。
    """
    spy = ComputeSpy()
    gateway = gateway_with(spy)

    supplied = gateway.bars(dataset_ref=REF, timeframe="1m")
    forming = gateway.forming_bar(dataset_ref=REF, timeframe="1m",
                                  now_unix=START + 3 * 60 + 30)

    assert forming is supplied[-1]


def test_there_is_no_forming_bar_once_the_last_period_is_over() -> None:
    """素材が現在の周期を覆っていないときは None（古い足を「形成中」と偽らない）。"""
    spy = ComputeSpy()
    gateway = gateway_with(spy)

    forming = gateway.forming_bar(dataset_ref=REF, timeframe="1m",
                                  now_unix=START + 10 * 60)

    assert forming is None


# -------------------------------------------------------------------- 誤り
def test_an_unknown_dataset_is_rejected() -> None:
    spy = ComputeSpy()

    with pytest.raises(ValueError, match="datasetRef"):
        gateway_with(spy).bars(dataset_ref="nope", timeframe="1m")


def test_an_unknown_timeframe_is_rejected() -> None:
    spy = ComputeSpy()

    with pytest.raises(ValueError, match="timeframe"):
        gateway_with(spy).bars(dataset_ref=REF, timeframe="3s")


# ------------------------------------------ 計算そのものの失敗（実UI 502・E01 等）
class FakeComputeError(Exception):
    """ライブ core の ComputeError 相当（bridge が `compute_error` として公開する型）。"""


class FailingCompute(ComputeSpy):
    """full_compute が検定エラーを投げる（例: E01_INSUFFICIENT_BARS＝上位足の本数不足）。"""

    def full_compute(self, adapter, indicator, variant, df, params):
        raise FakeComputeError(
            "validation: E01_INSUFFICIENT_BARS: バー数 171 では出力できない"
        )

    def namespace(self) -> SimpleNamespace:
        return SimpleNamespace(
            dataset=self, adapter=object(), full_compute=self.full_compute,
            latest_compute=self.latest_compute, compute_error=FakeComputeError,
        )


def test_a_compute_failure_is_translated_to_the_supply_contract() -> None:
    """検定エラー（本数不足等）は当該 instance の供給失敗であり、未捕捉のまま
    ハンドラを貫通させない（実 UI では応答無しの接続断＝502 になっていた）。
    """
    gw = gateway_with(FailingCompute())

    with pytest.raises(SeriesSupplyUnavailable):
        gw.full_series(
            indicator_id="btlm_trail_marod", variant="default", params={},
            dataset_ref=REF, timeframe="1m",
        )
