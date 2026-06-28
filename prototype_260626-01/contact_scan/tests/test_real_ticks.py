"""実データ突合（@slow）: window_ticks 直呼びの素朴 sign 変化数 == engine の同 bar 接点数。

トグル ON/OFF で candidate_bars が完全一致し、OFF は ticks_scanned==0 であることも固定する。
実 parquet / m1 CSV を要するため slow マーク（`-m slow` で実行）。
"""
import pytest

from contact_scan.bar_window import bar_window
from contact_scan.engine import ScanConfig, build_context, scan
from contact_scan.spec import MovingAverageContact

REF = "jp225_tick"
TF = "1m"
LAST_N = 500


def _load_ctx():
    from adapter.compute import dataset, IndicatorComputeAdapter
    from adapter.compute.latest_dispatch import full_compute
    df = dataset.load_dataframe(REF, TF).tail(LAST_N)
    params = {"ma_type": "ema", "length": 9, "source": "close", "offset": 0, "wait_for_close": False}
    series = full_compute(IndicatorComputeAdapter(), "moving_averages", "default", df, params)
    ma_data = next(s["data"] for s in series if s.get("name") == "MA")
    return build_context(df, ma_data), params


def _cfg(params, full_scan):
    return ScanConfig(ref=REF, timeframe=TF, indicator="moving_averages",
                      variant="default", params=params, full_scan=full_scan)


def _naive_sign_changes(ticks, level):
    """crossings 規約と独立な素朴ループでの sign 変化数（タッチ=0 は基準維持）。"""
    n = 0
    last = 0
    for _, v in ticks:
        s = 1 if v > level else (-1 if v < level else 0)
        if s == 0:
            continue
        if last != 0 and s != last:
            n += 1
        last = s
    return n


@pytest.mark.slow
def test_window_ticks_direct_matches_engine_events_for_a_candidate_bar():
    from contact_scan.tick_window import window_ticks
    ctx, params = _load_ctx()
    spec = MovingAverageContact()

    # full scan の接点を bar_index ごとに集計
    counts_on = {}
    by_bar = {}
    for ev in scan(ctx, spec, _cfg(params, True), summary=counts_on,
                   ticks_fn=window_ticks, bar_window_fn=bar_window):
        by_bar[ev["bar_index"]] = by_bar.get(ev["bar_index"], 0) + 1

    assert counts_on["candidate_bars"] > 0, "候補足が 0（データ窓を確認）"

    # ティックが存在し接点を持つ候補足を 1 本選ぶ
    target = None
    for i in range(len(ctx.bar_times)):
        levels = spec.levels(ctx, i)
        if not levels or not spec.straddles(ctx, i, levels[0]):
            continue
        s, e = bar_window(ctx.bar_times, i, TF)
        ticks = window_ticks(s, e)
        if ticks:
            target = (i, levels[0].value, ticks)
            break

    assert target is not None, "ティックを持つ候補足が見つからない"
    i, level, ticks = target

    # 独立突合: 素朴 sign 変化数 == engine の同 bar 接点数
    naive = _naive_sign_changes(ticks, level)
    assert by_bar.get(i, 0) == naive, f"bar {i}: engine={by_bar.get(i,0)} naive={naive}"


@pytest.mark.slow
def test_toggle_candidate_bars_identical_and_preview_scans_no_ticks():
    from contact_scan.tick_window import window_ticks
    ctx, params = _load_ctx()
    spec = MovingAverageContact()

    counts_on = {}
    list(scan(ctx, spec, _cfg(params, True), summary=counts_on,
              ticks_fn=window_ticks, bar_window_fn=bar_window))

    called = {"n": 0}

    def guarded_ticks(s, e):
        called["n"] += 1
        return window_ticks(s, e)

    counts_off = {}
    list(scan(ctx, spec, _cfg(params, False), summary=counts_off,
              ticks_fn=guarded_ticks, bar_window_fn=bar_window))

    assert counts_off["candidate_bars"] == counts_on["candidate_bars"]
    assert counts_off["ticks_scanned"] == 0
    assert called["n"] == 0          # preview はティックを一切読まない
