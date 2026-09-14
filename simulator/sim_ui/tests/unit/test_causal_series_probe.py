"""CausalSeriesProbe（案 i / 案 ii の系列取得・adapter）の単体検定。

固定する規則（Phase 3 構造設計 §新規ファイル #6・§絶対制約・契約改訂裁定 A）:
    1. **指標式を再実装しない**。値はライブの `causal_compute` 経由でのみ得る。
       本検定はフェイクの `CausalComputePort` を挿し、**呼び出しの形**を固定する。
    2. `limit=None` 固定。tail で窓長が変わると EMA 系の seed 位置が変わり、
       実装差ではない不一致を作る（検定の前提が壊れる）。
    3. 案 i（`series_upto`）も **full 計算**（`probe_mode="full"`）。`latest` は
       min_window tail での同値性が未検証のため既定にしない。
    4. 1 回の計算で**全系列**を返す（束契約）。系列ごとに計算を呼ばない。
    5. 案 i は `until_time` で truncate した系列の**その時刻の点**だけを返す。
       末尾点の時刻がずれている場合は `None`（時刻をずらして返さない）。
    6. 供給対象 kind（line / histogram / level_dash）以外は束に入れず、除外名として残す。
    7. 系列名の重複は `SeriesNameCollisionError`（無音上書き禁止）。
    8. 値の未定義（null / NaN）は `None` に正規化する。
    9. `bar_times` は**データセット先頭から** count 本（窓の左端を動かさない）。
   10. prefix 関係: `series_full(until=t)` の各系列の末尾点 == `series_upto(t)`。

方式: フェイク `CausalComputePort`（合成バー・合成系列）。indicator_ui も実データも触らない。
"""
from __future__ import annotations

import pytest

from simulator.sim_ui.adapter.causal_series_probe import CausalSeriesProbe
from simulator.sim_ui.usecase.indicator_models import (
    IndicatorSpec,
    SeriesNameCollisionError,
)

_BARS = [
    {"time": 100, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0},
    {"time": 160, "open": 2.0, "high": 2.0, "low": 2.0, "close": 2.0},
    {"time": 220, "open": 3.0, "high": 3.0, "low": 3.0, "close": 3.0},
]
_SPEC = IndicatorSpec(
    indicator="moving_averages", variant="default", params={"length": 20}
)


class _FakeComputePort:
    """`CausalComputePort` の最小フェイク（close をそのまま 1 系列で返す）。"""

    def __init__(self, *, panes=None) -> None:
        self.bars = list(_BARS)
        self.load_calls: "list[tuple[str, str | None]]" = []
        self.compute_calls: "list[tuple[str, str, str, int, dict]]" = []
        self._panes = panes

    def load_source(self, ref: str, timeframe: "str | None") -> "list[dict]":
        self.load_calls.append((ref, timeframe))
        return list(self.bars)

    def compute(self, indicator, variant, mode, bars, params) -> "list[dict]":
        self.compute_calls.append((indicator, variant, mode, len(bars), dict(params)))
        if self._panes is not None:
            return self._panes(bars)
        return [{
            "name": "MA", "kind": "line",
            "data": [{"time": b["time"], "value": b["close"]} for b in bars],
        }]


def _probe(**kwargs) -> CausalSeriesProbe:
    return CausalSeriesProbe(compute_port=_FakeComputePort(**kwargs))


def _two_panes(bars):
    return [
        {"name": "UPPER", "kind": "line",
         "data": [{"time": b["time"], "value": b["close"] + 1} for b in bars]},
        {"name": "LOWER", "kind": "line",
         "data": [{"time": b["time"], "value": b["close"] - 1} for b in bars]},
    ]


# --- 1. 案 ii（series_full・束）--------------------------------------------

def test_案iiは全系列の全点を1回で返す() -> None:
    """規則 4（束契約）。"""
    # Arrange
    port = _FakeComputePort(panes=_two_panes)
    probe = CausalSeriesProbe(compute_port=port)
    # Act
    bundle = probe.series_full(_SPEC, ref="jp225", timeframe="5m", until_time=None)
    # Assert
    assert sorted(bundle) == ["LOWER", "UPPER"]
    assert [(p.time, p.value) for p in bundle["UPPER"]] == [
        (100, 2.0), (160, 3.0), (220, 4.0)
    ]
    assert len(port.compute_calls) == 1   # 系列ごとに計算しない


def test_案iiはlimitなしのfull計算で呼ばれる() -> None:
    """規則 2・3。窓を tail で切らない・latest を使わない。"""
    # Arrange
    port = _FakeComputePort()
    probe = CausalSeriesProbe(compute_port=port)
    # Act
    probe.series_full(_SPEC, ref="jp225", timeframe="5m", until_time=None)
    # Assert（全 3 本が窓に入り、mode は full）
    assert port.compute_calls == [("moving_averages", "default", "full", 3, {"length": 20})]


def test_案iiもuntil_timeで窓を切れる() -> None:
    """供給窓での測定（段 0）。until を渡した瞬間から案 i と同じ窓になる。"""
    # Arrange
    port = _FakeComputePort()
    probe = CausalSeriesProbe(compute_port=port)
    # Act
    bundle = probe.series_full(_SPEC, ref="jp225", timeframe="5m", until_time=160)
    # Assert
    assert [p.time for p in bundle["MA"]] == [100, 160]
    assert port.compute_calls[-1][3] == 2


# --- 2. 案 i（series_upto・束）--------------------------------------------

def test_案iはuntil_timeで切った窓の全系列の末尾点を返す() -> None:
    """規則 4・5。因果順守（未来バーが窓に入らない）。"""
    # Arrange
    port = _FakeComputePort(panes=_two_panes)
    probe = CausalSeriesProbe(compute_port=port)
    # Act
    tails = probe.series_upto(_SPEC, ref="jp225", timeframe="5m", until_time=160)
    # Assert
    assert sorted(tails) == ["LOWER", "UPPER"]
    assert (tails["UPPER"].time, tails["UPPER"].value) == (160, 3.0)
    assert (tails["LOWER"].time, tails["LOWER"].value) == (160, 1.0)
    assert len(port.compute_calls) == 1   # 1 バーにつき 1 回
    assert port.compute_calls[-1][3] == 2  # truncate が効いている


def test_案iもfull計算で呼ばれる() -> None:
    """規則 3。`latest` は同値性未検証なので既定にしない。"""
    # Arrange
    port = _FakeComputePort()
    probe = CausalSeriesProbe(compute_port=port)
    # Act
    probe.series_upto(_SPEC, ref="jp225", timeframe="5m", until_time=160)
    # Assert
    assert port.compute_calls[-1][2] == "full"


def test_末尾点の時刻がずれていればNoneを返す() -> None:
    """規則 5。時刻をずらして返すと突合が無音で誤る。"""
    # Arrange（末尾 1 本を落とす系列＝warmup 相当）
    probe = _probe(panes=lambda bars: [{
        "name": "MA", "kind": "line",
        "data": [{"time": b["time"], "value": b["close"]} for b in bars[:-1]],
    }])
    # Act
    tails = probe.series_upto(_SPEC, ref="jp225", timeframe="5m", until_time=160)
    # Assert
    assert tails == {"MA": None}


def test_点が1つも無ければNoneを返す() -> None:
    """境界値: 空系列。"""
    # Arrange
    probe = _probe(panes=lambda bars: [{"name": "MA", "kind": "line", "data": []}])
    # Act / Assert
    assert probe.series_upto(_SPEC, ref="jp225", timeframe="5m", until_time=160) == {
        "MA": None
    }
    assert probe.series_full(
        _SPEC, ref="jp225", timeframe="5m", until_time=None
    ) == {"MA": []}


def test_案iと案iiのキー集合は一致する() -> None:
    """束契約の前提（食い違えば usecase が比較不能にする）。"""
    # Arrange
    probe = _probe(panes=_two_panes)
    # Act
    full = probe.series_full(_SPEC, ref="jp225", timeframe="5m", until_time=160)
    tails = probe.series_upto(_SPEC, ref="jp225", timeframe="5m", until_time=160)
    # Assert
    assert set(full) == set(tails)


def test_prefix関係_案iiの末尾点は案iと一致する() -> None:
    """規則 10。窓の左端が同じなので、同じ until なら同じ入力から同じ値が出る。"""
    # Arrange
    probe = _probe(panes=_two_panes)
    # Act
    full = probe.series_full(_SPEC, ref="jp225", timeframe="5m", until_time=160)
    tails = probe.series_upto(_SPEC, ref="jp225", timeframe="5m", until_time=160)
    # Assert
    assert {name: full[name][-1] for name in full} == dict(tails)


# --- 3. 供給対象 kind と除外（規則 6）-------------------------------------

def test_供給対象外のkindは束に入らず除外名として残る() -> None:
    # Arrange
    probe = _probe(panes=lambda bars: [
        {"name": "MA", "kind": "line",
         "data": [{"time": b["time"], "value": b["close"]} for b in bars]},
        {"name": "FILL", "kind": "band_fill", "data": []},
        {"name": "LEVEL", "kind": "horizontal_line", "data": []},
    ])
    # Act
    bundle = probe.series_full(_SPEC, ref="jp225", timeframe="5m", until_time=None)
    # Assert
    assert sorted(bundle) == ["MA"]
    assert sorted(bundle.excluded) == ["FILL", "LEVEL"]


def test_同名で対象kindのpaneがあれば除外名にしない() -> None:
    """2026-08-11 実測: ma_marod は同じ名前で線と塗りの 2 pane を返す。

    両方に載せると台帳に同じ系列の行が 2 つ（判定は別）できる。供給できる pane が
    1 つでもあればその名前は「供給できる」。
    """
    # Arrange
    probe = _probe(panes=lambda bars: [
        {"name": "ma_marod", "kind": "band_fill", "data": []},
        {"name": "ma_marod", "kind": "line",
         "data": [{"time": b["time"], "value": b["close"]} for b in bars]},
    ])
    # Act
    bundle = probe.series_full(_SPEC, ref="jp225", timeframe="5m", until_time=None)
    # Assert
    assert sorted(bundle) == ["ma_marod"]
    assert bundle.excluded == ()


def test_除外名は重複しない() -> None:
    """境界値: 対象外 pane が同名で複数（台帳に同じ行を 2 つ作らない）。"""
    # Arrange
    probe = _probe(panes=lambda bars: [
        {"name": "MA", "kind": "line", "data": []},
        {"name": "FILL", "kind": "band_fill", "data": []},
        {"name": "FILL", "kind": "band_fill", "data": []},
    ])
    # Act
    bundle = probe.series_full(_SPEC, ref="jp225", timeframe="5m", until_time=None)
    # Assert
    assert bundle.excluded == ("FILL",)


@pytest.mark.parametrize("kind", ["line", "histogram", "level_dash"])
def test_供給対象kindは束に入る(kind: str) -> None:
    # Arrange
    probe = _probe(panes=lambda bars: [
        {"name": "S", "kind": kind,
         "data": [{"time": b["time"], "value": b["close"]} for b in bars]},
    ])
    # Act
    bundle = probe.series_full(_SPEC, ref="jp225", timeframe="5m", until_time=None)
    # Assert
    assert sorted(bundle) == ["S"]


# --- 4. 名前衝突（規則 7）--------------------------------------------------

def test_系列名が重複したら明示エラー() -> None:
    """後勝ちで黙って上書きしない（戦略が別系列の値を掴む）。"""
    # Arrange
    probe = _probe(panes=lambda bars: [
        {"name": "MA", "kind": "line", "data": []},
        {"name": "MA", "kind": "line", "data": []},
    ])
    # Act / Assert
    with pytest.raises(SeriesNameCollisionError) as exc:
        probe.series_full(_SPEC, ref="jp225", timeframe="5m", until_time=None)
    assert "MA" in str(exc.value)


# --- 5. 値の正規化（規則 8）------------------------------------------------

def test_未定義値はNoneへ正規化される() -> None:
    """null も NaN も「未定義点」。NaN を境界の外へ出すと突合規則が書けなくなる。"""
    # Arrange
    probe = _probe(panes=lambda bars: [{
        "name": "MA", "kind": "line",
        "data": [
            {"time": 100, "value": None},
            {"time": 160, "value": float("nan")},
            {"time": 220, "value": 3.0},
        ],
    }])
    # Act
    bundle = probe.series_full(_SPEC, ref="jp225", timeframe="5m", until_time=None)
    # Assert
    assert [p.value for p in bundle["MA"]] == [None, None, 3.0]


# --- 6. バー時刻列（規則 9）------------------------------------------------

def test_バー時刻列は先頭count本() -> None:
    """窓の左端を動かさない（末尾 N 本にすると EMA 系の seed 位置が変わる）。"""
    # Arrange
    probe = _probe()
    # Act / Assert
    assert probe.bar_times(ref="jp225", timeframe="5m", count=2) == [100, 160]


def test_countが全長以上なら全バーを返す() -> None:
    """境界値: 要求本数 >= 実本数。"""
    # Arrange
    probe = _probe()
    # Act / Assert
    assert probe.bar_times(ref="jp225", timeframe="5m", count=99) == [100, 160, 220]


def test_countが0なら全バーを返す() -> None:
    """境界値: 0 は「制限なし」（limit=None 固定の規約と揃える）。"""
    # Arrange
    probe = _probe()
    # Act / Assert
    assert probe.bar_times(ref="jp225", timeframe="5m", count=0) == [100, 160, 220]
