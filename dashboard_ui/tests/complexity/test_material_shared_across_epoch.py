"""計算量 8（ISSUE-457）: P-1 の素材は **epoch 単位で要求をまたいで共有**される。

段 2（ティック）で確定足の素材は定義上変わらない。にもかかわらず要求ごとに口
（gateway）を組み直すと、同じ確定素材の full 系列を毎秒作り直す（§9-4 実測: 要求 9,452ms の
うち P-1 が 7,440ms＝78%）。**出力は正しいままなので状態検証では原理的に落ちない**——
ISSUE-450 / ISSUE-257 と同型の「作ってから捨てる」欠陥である。

CLAUDE.md 絶対命令 §4.1 に従い、測るのは時間ではなく**回数**である。回数そのもの
（40 や 81）は期待値に焼き込まない。固定するのは次の 3 つだけである。

- **不変量 A（無駄の不在）**: epoch が 1 つも進まないまま同じ要求を N 回繰り返しても、
  `full_compute` の**追加発行は 0**。N は 2 点（5 / 20）で固定する。
- **不変量 B（発行の局所性）**: 時間足 X の epoch だけが進んだとき、作り直されるのは
  X に束縛された素材だけであり、他の時間足の追加発行は 0。
- **不変量 C（鮮度）**: 形成中バーが動いた（＝現在値・走行 H/L が変わった）だけのときは
  `full_compute` の追加発行が 0 のまま、`bars()` の末尾・`forming_bar()`・系列の末尾点が
  **新しい値になる**。共有と引き換えに値を古くしていないことを、共有と同じ検査で押さえる。
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from dashboard_ui.adapter.gateway.indicator_ui_compute_gateway import (
    IndicatorUiComputeGateway,
)
from dashboard_ui.adapter.gateway.material_store import MaterialStore

REF = "jp225_tick"
#: 2026-08-28 20:10:00 UTC（分・5 分の境界に載る時刻）。
START = 1_787_003_400
_STEP = {"1m": 60, "5m": 300}


class Material:
    """時間足 1 本ぶんの素材（確定足 ＋ 形成中足）。テストから周期を進め、値を動かせる。"""

    def __init__(self, timeframe: str, *, rows: int, base: float) -> None:
        self.timeframe = timeframe
        self.step = _STEP[timeframe]
        self.times = [START + index * self.step for index in range(rows)]
        self.closes = [base + index for index in range(rows)]

    # --- テストが素材を動かす操作 ---
    def advance_epoch(self) -> None:
        """周期を 1 つ進める（新しい足が現れる＝確定素材が変わる）。"""
        self.times.append(self.times[-1] + self.step)
        self.closes.append(self.closes[-1] + 1.0)

    def move_forming_bar(self, close: float) -> None:
        """形成中足だけを動かす（周期は進まない＝確定素材は変わらない）。"""
        self.closes[-1] = float(close)

    # --- 供給 ---
    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "open": list(self.closes),
                "high": [close + 5.0 for close in self.closes],
                "low": [close - 5.0 for close in self.closes],
                "close": list(self.closes),
                "volume": [1.0] * len(self.closes),
            },
            index=pd.to_datetime(self.times, unit="s"),
        )


class BridgeSpy:
    """dataset ＋ 計算面の Test Spy。**数えるのは `full_compute` の発行だけ**である。

    値は素材の `close` をそのまま返す（指標の値そのものは本検査の対象ではない）。
    `latest_compute` は末尾 1 点だけを返す（ライブ core の増分経路と同じ形）。
    """

    def __init__(self, materials: "dict[str, Material]") -> None:
        self._materials = dict(materials)
        self.full_calls: "list[tuple[str, str]]" = []
        self.latest_calls: "list[tuple[str, str]]" = []

    # --- dataset 面 ---
    def is_known(self, ref) -> bool:
        return ref == REF

    def is_known_timeframe(self, timeframe) -> bool:
        return timeframe in self._materials

    def load_dataframe(self, ref, timeframe=None):
        return self._materials[timeframe].frame()

    # --- 計算面 ---
    def _timeframe_of(self, df) -> str:
        """素材の先頭時刻は時間足ごとに一意なので、そこから時間足を引く。"""
        first = int(df.index[0].value // 1_000_000_000)
        for material in self._materials.values():
            if material.times[0] == first and material.step == _step_of(df):
                return material.timeframe
        raise AssertionError("素材の時間足を特定できません")

    def full_compute(self, adapter, indicator_id, variant, df, params):
        self.full_calls.append((indicator_id, self._timeframe_of(df)))
        return [_line(indicator_id, df)]

    def latest_compute(self, adapter, indicator_id, variant, df, params):
        self.latest_calls.append((indicator_id, self._timeframe_of(df)))
        return [_line(indicator_id, df.tail(1))]

    def namespace(self) -> SimpleNamespace:
        return SimpleNamespace(
            dataset=self, adapter=object(), full_compute=self.full_compute,
            latest_compute=self.latest_compute, compute_error=(),
        )


def _step_of(df) -> int:
    seconds = df.index.values.astype("datetime64[s]").astype("int64").tolist()
    return int(seconds[1] - seconds[0])


def _line(name: str, df) -> dict:
    seconds = df.index.values.astype("datetime64[s]").astype("int64").tolist()
    closes = df["close"].tolist()
    return {
        "name": name,
        "kind": "line",
        "data": [
            {"time": int(time), "value": float(close)}
            for time, close in zip(seconds, closes)
        ],
    }


def _request(spy: BridgeSpy, store: MaterialStore, *, timeframes=("1m",)) -> "dict":
    """要求 1 件ぶん（**口は要求ごとに組み直す**——共有するのは素材ストアだけ）。"""
    gateway = IndicatorUiComputeGateway(bridge=spy.namespace(), store=store)
    supplied = {}
    for timeframe in timeframes:
        supplied[timeframe] = {
            "series": gateway.full_series(
                indicator_id="osc", variant="default", params={},
                dataset_ref=REF, timeframe=timeframe,
            ),
            "bars": gateway.bars(dataset_ref=REF, timeframe=timeframe),
        }
    return supplied


# ------------------------------------------------------- 不変量 A: 無駄の不在
def test_repeating_the_same_request_issues_no_additional_full_compute() -> None:
    """epoch が進まない限り、要求を何回繰り返しても full 系列の発行は増えない。

    オーダーの表明（2 点固定）: 繰り返し数 5 / 20 のどちらでも**追加は 0**。回数そのものは
    焼き込まない（焼き込むと浪費が仕様へ昇格する）。
    """
    additional = {}
    for repeats in (5, 20):
        spy = BridgeSpy({"1m": Material("1m", rows=6, base=100.0)})
        store = MaterialStore()
        _request(spy, store)                       # 1 回目（この epoch の素材を作る）
        warmed = len(spy.full_calls)
        for _ in range(repeats):
            _request(spy, store)
        additional[repeats] = len(spy.full_calls) - warmed

    assert additional[5] == 0
    assert additional[20] == 0


def test_the_shared_material_is_the_same_material_that_the_output_uses() -> None:
    """共有しても出力は変わらない（発行 − 使用 = 0 の裏返し）。"""
    spy = BridgeSpy({"1m": Material("1m", rows=6, base=100.0)})
    store = MaterialStore()

    first = _request(spy, store)["1m"]["series"]
    second = _request(spy, store)["1m"]["series"]

    assert second == first


# --------------------------------------------------- 不変量 B: 発行の局所性
def test_only_the_timeframe_whose_epoch_advanced_is_recomputed() -> None:
    """1m の周期が進んでも、5m の素材は作り直さない（時間足ごとに独立した版を持つ）。"""
    materials = {"1m": Material("1m", rows=6, base=100.0),
                 "5m": Material("5m", rows=6, base=200.0)}
    spy = BridgeSpy(materials)
    store = MaterialStore()
    _request(spy, store, timeframes=("1m", "5m"))
    warmed = len(spy.full_calls)

    materials["1m"].advance_epoch()
    _request(spy, store, timeframes=("1m", "5m"))

    reissued = spy.full_calls[warmed:]
    assert [timeframe for _indicator, timeframe in reissued] == ["1m"]


def test_an_advanced_epoch_does_not_invalidate_the_other_timeframes_forever() -> None:
    """オーダーの表明（2 点固定）: 1m を 1 回 / 3 回進めても、5m の追加発行は 0 のまま。"""
    additional = {}
    for advances in (1, 3):
        materials = {"1m": Material("1m", rows=6, base=100.0),
                     "5m": Material("5m", rows=6, base=200.0)}
        spy = BridgeSpy(materials)
        store = MaterialStore()
        _request(spy, store, timeframes=("1m", "5m"))
        warmed = len(spy.full_calls)
        for _ in range(advances):
            materials["1m"].advance_epoch()
            _request(spy, store, timeframes=("1m", "5m"))
        additional[advances] = len(
            [1 for _indicator, tf in spy.full_calls[warmed:] if tf == "5m"]
        )

    assert additional[1] == 0
    assert additional[3] == 0


# ------------------------------------------------------------ 不変量 C: 鮮度
def test_the_forming_bar_stays_fresh_without_reissuing_the_material() -> None:
    """現在値と走行 H/L はティック鮮度を保つ（共有と引き換えに値を古くしない）。"""
    materials = {"1m": Material("1m", rows=6, base=100.0)}
    spy = BridgeSpy(materials)
    store = MaterialStore()
    _request(spy, store)
    warmed = len(spy.full_calls)

    materials["1m"].move_forming_bar(999.0)
    supplied = _request(spy, store)["1m"]

    assert len(spy.full_calls) - warmed == 0          # 素材は作り直していない
    assert supplied["bars"][-1].close == 999.0        # 現在値は新しい
    assert supplied["bars"][-1].high == 1004.0        # 走行 H
    assert supplied["bars"][-1].low == 994.0          # 走行 L


def test_the_series_tail_follows_the_forming_bar_without_reissuing() -> None:
    """系列の末尾点（形成中バーの値）も新しくなる（§7 段 2 の観測値更新）。"""
    materials = {"1m": Material("1m", rows=6, base=100.0)}
    spy = BridgeSpy(materials)
    store = MaterialStore()
    _request(spy, store)
    warmed = len(spy.full_calls)

    materials["1m"].move_forming_bar(999.0)
    series = _request(spy, store)["1m"]["series"]

    assert len(spy.full_calls) - warmed == 0
    assert series["osc"][-1] == (materials["1m"].times[-1], 999.0)


def test_the_forming_point_is_never_dropped_from_the_shared_material() -> None:
    """共有した確定素材と形成中の点を継いだ系列は、素材の全点を覆う（点を落とさない）。"""
    materials = {"1m": Material("1m", rows=6, base=100.0)}
    spy = BridgeSpy(materials)

    series = _request(spy, MaterialStore())["1m"]["series"]

    assert [time for time, _value in series["osc"]] == materials["1m"].times
    assert [value for _time, value in series["osc"]] == materials["1m"].closes
