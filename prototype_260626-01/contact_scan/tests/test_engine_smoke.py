"""engine.scan: 合成 OHLC + 注入 window_ticks で events / summary の形状・件数を固定する。"""
import pandas as pd

from contact_scan.engine import ScanConfig, build_context, make_summary, scan
from contact_scan.spec import MovingAverageContact


def _df(rows, times):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"],
                        index=pd.to_datetime(times, unit="s"))


def _ctx():
    # 3 足: bar0=warmup(前足なし) / bar1=候補(level=ma[0]=100 が [90,110] 内) / bar2=非候補(level=ma[1]=200 が [100,120] 外)
    times = [0, 60, 120]
    df = _df([(100, 110, 90, 105),
              (105, 110, 90, 108),
              (110, 120, 100, 115)], times)
    ma_series = [{"time": 0, "value": 100.0},
                 {"time": 60, "value": 200.0},
                 {"time": 120, "value": 123.0}]
    return build_context(df, ma_series)


def _cfg(full_scan=True):
    return ScanConfig(ref="synthetic", timeframe="1m", indicator="moving_averages",
                      variant="default", params={"ma_type": "ema", "length": 9}, full_scan=full_scan)


def test_full_scan_counts_and_event_shape():
    # Arrange: bar1 の窓ティックが level=100 を 2 回跨ぐ（up→down）合成列を返す注入関数
    ctx = _ctx()
    ticks = [(61, 99.0), (62, 101.0), (63, 98.0)]
    counts = {}

    def fake_ticks(start, end):
        return list(ticks)

    # Act
    events = list(scan(ctx, MovingAverageContact(), _cfg(True),
                       summary=counts, ticks_fn=fake_ticks))

    # Assert: 件数
    assert counts["bars_total"] == 3
    assert counts["bars_warmup_skipped"] == 1     # bar0
    assert counts["candidate_bars"] == 1          # bar1
    assert counts["skipped_bars"] == 1            # bar2
    assert counts["scanned_bars"] == 1
    assert counts["ticks_scanned"] == 3
    assert counts["contacts"] == 2
    assert len(events) == 2

    # Assert: イベント schema 形状
    ev = events[0]
    assert ev["schema"] == "contact.v1"
    assert ev["mode"] == "full_scan"
    assert ev["bar_index"] == 1
    assert ev["bar_time"] == 60
    assert ev["level"] == 100.0
    assert ev["level_id"] == "ma_prev"
    assert ev["direction"] == "up"
    assert ev["ref"] == "synthetic"
    assert [e["direction"] for e in events] == ["up", "down"]


def test_summary_schema_and_range():
    ctx = _ctx()
    counts = {}
    list(scan(ctx, MovingAverageContact(), _cfg(True), summary=counts,
              ticks_fn=lambda s, e: []))
    summary = make_summary(_cfg(True), ctx, counts)
    assert summary["schema"] == "contact.summary.v1"
    assert summary["range"] == {"from": 0, "to": 120, "n_bars": 3}
    assert summary["full_scan"] is True
    assert "generated_at" in summary


def test_preview_mode_no_ticks_read_candidate_bars_match():
    ctx = _ctx()
    called = {"n": 0}

    def fake_ticks(start, end):       # preview では呼ばれてはならない
        called["n"] += 1
        return [(61, 99.0), (62, 101.0)]

    counts = {}
    list(scan(ctx, MovingAverageContact(), _cfg(False), summary=counts, ticks_fn=fake_ticks))
    assert called["n"] == 0
    assert counts["ticks_scanned"] == 0
    assert counts["scanned_bars"] == 0
    # candidate_bars は full_scan と独立（同一）
    assert counts["candidate_bars"] == 1
