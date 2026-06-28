"""forming_bar adapter の検証 — ref/tf 判定・期間始端算出・marketdata コア委譲。

marketdata.tick_m1.forming_bar_from_ticks は monkeypatch で遮断し、adapter の責務（対象ref/tf判定・
floor(now,tf) の期間始端算出・委譲引数）を純粋に固定する。実ティック parquet は読まない。
"""

from __future__ import annotations

import pandas as pd
import pytest

from adapter.compute import forming_bar as fb


def _unix(s: str) -> int:
    return int(pd.Timestamp(s).tz_localize("UTC").timestamp())


def test_resolve_now_unix_prefers_override() -> None:
    assert fb.resolve_now_unix(1764500000) == 1764500000  # 明示 override を採用。
    assert not isinstance(fb.resolve_now_unix(True), bool)  # bool は override 扱いせず実 now。


def test_resolve_now_unix_demo_clock_base_with_speed_zero(monkeypatch) -> None:
    # FORMING_DEMO_NOW="<base>:0" は speed=0＝経過無視で base 固定（決定的）。
    monkeypatch.setenv("FORMING_DEMO_NOW", "1782432000:0")
    assert fb.resolve_now_unix(None) == 1782432000
    assert fb.resolve_now_unix(555) == 555  # override は env より優先。


def test_resolve_now_unix_real_time_when_env_unset(monkeypatch) -> None:
    import time as _t
    monkeypatch.delenv("FORMING_DEMO_NOW", raising=False)
    assert abs(fb.resolve_now_unix(None) - int(_t.time())) < 5  # 実 now 近傍。


def test_floor_freq_is_derived_from_resample_rules_single_source() -> None:
    # 規則源単一化（§4）: floor freq は TIMEFRAME_RULES から導出し再エンコードしない。
    from marketdata.resample import TIMEFRAME_RULES

    for tf in ("5m", "15m", "30m", "1h", "4h", "1D"):
        assert fb._floor_freq(tf) == TIMEFRAME_RULES[tf]  # rule 文字列がそのまま floor freq。
    assert fb._floor_freq("1m") == "min"  # rule=None（原子）は分床。
    assert fb._floor_freq("1W") is None and fb._floor_freq("1M") is None  # カレンダー周期は非対応。
    assert fb._floor_freq("9z") is None  # 未知 tf。


def test_is_tick_ref_and_supported_timeframe() -> None:
    assert fb.is_tick_ref("jp225_tick")
    assert not fb.is_tick_ref("jp225_m1")  # ローソク由来は対象外。
    assert fb.is_supported_timeframe("5m")
    assert fb.is_supported_timeframe("1D")
    assert not fb.is_supported_timeframe("1W")  # 週/月は固定floor不可で非対応。
    assert not fb.is_supported_timeframe("1M")


@pytest.mark.parametrize("tf,now,expected_start", [
    ("5m", "2025-01-02 09:07:30", "2025-01-02 09:05:00"),
    ("1h", "2025-01-02 09:40:00", "2025-01-02 09:00:00"),
    ("1D", "2025-01-02 09:40:00", "2025-01-02 00:00:00"),
    ("1m", "2025-01-02 09:07:30", "2025-01-02 09:07:00"),
])
def test_period_start_unix_floors_now_to_tf(tf, now, expected_start) -> None:
    assert fb.period_start_unix(_unix(now), tf) == _unix(expected_start)


def test_forming_bar_delegates_with_period_window(monkeypatch) -> None:
    seen = {}
    monkeypatch.setattr(
        fb, "forming_bar_from_ticks",
        lambda s, e, **k: (seen.update(start=s, end=e), {"time": s, "open": 1.0,
                            "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0})[1],
    )
    now = _unix("2025-01-02 09:07:30")
    bar = fb.forming_bar("jp225_tick", "5m", now)
    assert seen["start"] == _unix("2025-01-02 09:05:00")  # floor(now,5m)
    assert seen["end"] == now
    assert bar["time"] == seen["start"]


def test_forming_bar_none_for_non_tick_ref_or_unsupported_tf(monkeypatch) -> None:
    # 委譲先が呼ばれないこと（早期 None）。
    monkeypatch.setattr(fb, "forming_bar_from_ticks", lambda *a, **k: pytest.fail("呼ばれてはいけない"))
    assert fb.forming_bar("jp225_m1", "5m", _unix("2025-01-02 09:00:00")) is None  # 非tick ref
    assert fb.forming_bar("jp225_tick", "1W", _unix("2025-01-02 09:00:00")) is None  # 非対応tf


def _df(rows: list[tuple[str, float, float, float, float, float]]):
    idx = pd.DatetimeIndex([pd.Timestamp(r[0]) for r in rows], name="date")
    return pd.DataFrame(
        {"open": [r[1] for r in rows], "high": [r[2] for r in rows], "low": [r[3] for r in rows],
         "close": [r[4] for r in rows], "volume": [r[5] for r in rows]},
        index=idx,
    )


def test_apply_forming_bar_appends_new_period(monkeypatch) -> None:
    df = _df([("2025-01-02 09:00:00", 1.0, 2.0, 0.5, 1.5, 10.0)])
    bar = {"time": _unix("2025-01-02 09:05:00"), "open": 1.5, "high": 3.0, "low": 1.0,
           "close": 2.5, "volume": 7.0}
    monkeypatch.setattr(fb, "forming_bar", lambda *a, **k: bar)
    out = fb.apply_forming_bar(df, "jp225_tick", "5m", 999)
    assert len(out) == 2  # 新期間バーが末尾に追加。
    last = out.iloc[-1]
    assert (last["open"], last["high"], last["low"], last["close"], last["volume"]) == (1.5, 3.0, 1.0, 2.5, 7.0)
    assert out.index[-1] == pd.Timestamp("2025-01-02 09:05:00")
    assert len(df) == 1  # 入力 df は不変（copy）。


def test_apply_forming_bar_replaces_same_period(monkeypatch) -> None:
    df = _df([("2025-01-02 09:00:00", 1.0, 2.0, 0.5, 1.5, 10.0),
              ("2025-01-02 09:05:00", 1.5, 1.6, 1.4, 1.55, 3.0)])  # 末尾=形成中と同一期間
    bar = {"time": _unix("2025-01-02 09:05:00"), "open": 1.5, "high": 9.0, "low": 0.1,
           "close": 2.5, "volume": 8.0}
    monkeypatch.setattr(fb, "forming_bar", lambda *a, **k: bar)
    out = fb.apply_forming_bar(df, "jp225_tick", "5m", 999)
    assert len(out) == 2  # 同一期間は置換（行数不変）。
    last = out.iloc[-1]
    assert (last["high"], last["low"], last["close"], last["volume"]) == (9.0, 0.1, 2.5, 8.0)


def test_apply_forming_bar_ignores_past_time(monkeypatch) -> None:
    # 形成中バーの time が既存末尾より過去（異常）→ 触らない（df 素通し・防御分岐）。
    df = _df([("2025-01-02 09:00:00", 1.0, 2.0, 0.5, 1.5, 10.0),
              ("2025-01-02 09:05:00", 1.5, 1.6, 1.4, 1.55, 3.0)])
    bar = {"time": _unix("2025-01-02 09:00:00"), "open": 9.0, "high": 9.0, "low": 9.0,
           "close": 9.0, "volume": 9.0}  # 末尾(09:05)より過去
    monkeypatch.setattr(fb, "forming_bar", lambda *a, **k: bar)
    out = fb.apply_forming_bar(df, "jp225_tick", "5m", 999)
    assert out is df  # 不変。


def test_apply_forming_bar_passes_through_on_io_error(monkeypatch) -> None:
    # ライブ堅牢化: 形成中バー算出（parquet 読込）が例外でも指標計算を落とさず df 素通し。
    df = _df([("2025-01-02 09:00:00", 1.0, 2.0, 0.5, 1.5, 10.0)])
    def _raise(*a, **k):
        raise OSError("parquet torn-read")
    monkeypatch.setattr(fb, "forming_bar", _raise)
    assert fb.apply_forming_bar(df, "jp225_tick", "5m", 999) is df  # 例外を握り df を返す。


def test_apply_forming_bar_maps_uppercase_columns(monkeypatch) -> None:
    # 列名が大文字でも lower 対応で set/replace できる。
    df = _df([("2025-01-02 09:00:00", 1.0, 2.0, 0.5, 1.5, 10.0)])
    df.columns = [c.upper() for c in df.columns]  # OPEN/HIGH/LOW/CLOSE/VOLUME
    bar = {"time": _unix("2025-01-02 09:05:00"), "open": 1.5, "high": 3.0, "low": 1.0,
           "close": 2.5, "volume": 7.0}
    monkeypatch.setattr(fb, "forming_bar", lambda *a, **k: bar)
    out = fb.apply_forming_bar(df, "jp225_tick", "5m", 999)
    assert len(out) == 2
    assert float(out.iloc[-1]["CLOSE"]) == 2.5  # 大文字列へ正しく書込。


def test_apply_forming_bar_passthrough_when_none_or_empty(monkeypatch) -> None:
    df = _df([("2025-01-02 09:00:00", 1.0, 2.0, 0.5, 1.5, 10.0)])
    monkeypatch.setattr(fb, "forming_bar", lambda *a, **k: None)  # 対象外/ティック無し
    assert fb.apply_forming_bar(df, "jp225_tick", "5m", 999) is df  # 同一オブジェクトで素通し
    empty = df.iloc[0:0]
    monkeypatch.setattr(fb, "forming_bar", lambda *a, **k: {"time": _unix("2025-01-02 09:05:00"),
                        "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0})
    assert fb.apply_forming_bar(empty, "jp225_tick", "5m", 999) is empty  # 空 df も素通し
