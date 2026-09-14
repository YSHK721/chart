"""engine — 接点スキャンのオーケストレーション（yield でメモリ有界・純・stdlib のみ）。

参照実装 prototype_260626-01/contact_scan/engine.py と bit 一致。``scan`` は各バーで
levels → straddles を判定し、候補足のみ（full_scan 時）窓ティックを ``detect_crossings`` し、
接点イベントを **yield** する。summary は呼び出し側が渡す dict に集計する。試作の
``ctx.df["close"]`` 依存を plain ``ctx.closes`` へ置換（pandas 非依存）。挙動・event/summary
schema は不変。依存（ma 系列 / ticks_fn / bar_window / spec）は注入する（IO・指標計算は外側に閉じる）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from simulator.usecase.contact_scan.bar_window import bar_window as _default_bar_window
from simulator.usecase.contact_scan.crossings import detect_crossings
from simulator.usecase.contact_scan.spec import ScanContext


@dataclass
class ScanConfig:
    ref: str
    timeframe: str
    indicator: str
    variant: str
    params: dict
    full_scan: bool = True


def build_context(*, bar_times, highs, lows, closes, ma_series) -> ScanContext:
    """plain 配列と MA 系列（``[{time, value}, ...]``）から ScanContext を構築する。

    試作 ``build_context(df, ma_series)`` の ma_series→ma_by_time 写像を保持しつつ、OHLC は
    plain 配列（highs/lows/closes/bar_times）から受ける（df 非依存）。
    """
    ma_by_time = {int(d["time"]): float(d["value"]) for d in ma_series}
    return ScanContext(
        highs=[float(h) for h in highs],
        lows=[float(l) for l in lows],
        closes=[float(c) for c in closes],
        bar_times=[int(t) for t in bar_times],
        ma_by_time=ma_by_time,
    )


def _event(cfg: ScanConfig, ctx: ScanContext, i: int, level, cr: dict, mode: str) -> dict:
    return {
        "schema": "contact.v1",
        "ref": cfg.ref,
        "timeframe": cfg.timeframe,
        "indicator": cfg.indicator,
        "variant": cfg.variant,
        "params": cfg.params,
        "bar_index": i,
        "bar_time": ctx.bar_times[i],
        "level_id": level.level_id,
        "level": level.value,
        "tick_time": cr["time"],
        "price": cr["price"],
        "prev_price": cr["prev_price"],
        "direction": cr["direction"],
        "mode": mode,
    }


def scan(ctx: ScanContext, spec, cfg: ScanConfig, *, summary: dict,
         ticks_fn, bar_window_fn=_default_bar_window):
    """接点イベントを yield しつつ ``summary`` を集計する。

    ticks_fn(start, end) -> [(sec, mid), ...]（full_scan 時のみ呼ぶ）。preview（no-full-scan）は
    候補足の確定足 close クロスのみを接点化し、ティックは一切読まない（ticks_scanned==0）。
    """
    n = len(ctx.bar_times)
    warmup = candidate = skipped = scanned = contacts = ticks_scanned = 0
    mode = "full_scan" if cfg.full_scan else "preview"
    closes = ctx.closes

    for i in range(n):
        levels = spec.levels(ctx, i)
        if not levels:
            warmup += 1
            continue
        bar_is_candidate = False
        for level in levels:
            if not spec.straddles(ctx, i, level):
                continue
            bar_is_candidate = True
            if cfg.full_scan:
                start, end = bar_window_fn(ctx.bar_times, i, cfg.timeframe)
                ticks = ticks_fn(start, end)
                ticks_scanned += len(ticks)
                series = spec.tick_values(ctx, i, ticks)
                for cr in detect_crossings(series, level.value):
                    contacts += 1
                    yield _event(cfg, ctx, i, level, cr, mode)
            else:
                # preview: 確定足 close の前足→今足クロスのみ（候補足限定・tick 非読込）。
                # i==0 は前足がなく closes[-1] への負 index ラップを生む。増分1（MovingAverageContact）は
                #   i<=0 で levels 空＝ここへ未到達だが、増分2 で i=0 にレベルを返す将来 spec への防御。
                if i == 0:
                    continue
                close_series = [(ctx.bar_times[i - 1], float(closes[i - 1])),
                                (ctx.bar_times[i], float(closes[i]))]
                for cr in detect_crossings(close_series, level.value):
                    contacts += 1
                    yield _event(cfg, ctx, i, level, cr, mode)
        if bar_is_candidate:
            candidate += 1
            if cfg.full_scan:
                scanned += 1
        else:
            skipped += 1

    summary.update(
        bars_total=n,
        bars_warmup_skipped=warmup,
        candidate_bars=candidate,
        skipped_bars=skipped,
        scanned_bars=scanned,
        contacts=contacts,
        ticks_scanned=ticks_scanned,
    )


def make_summary(cfg: ScanConfig, ctx: ScanContext, counts: dict) -> dict:
    """scan が集計した counts と range/メタを束ねた summary dict（schema=contact.summary.v1）。"""
    bt = ctx.bar_times
    return {
        "schema": "contact.summary.v1",
        "range": {
            "from": bt[0] if bt else None,
            "to": bt[-1] if bt else None,
            "n_bars": len(bt),
        },
        "full_scan": cfg.full_scan,
        "bars_total": counts.get("bars_total", 0),
        "bars_warmup_skipped": counts.get("bars_warmup_skipped", 0),
        "candidate_bars": counts.get("candidate_bars", 0),
        "skipped_bars": counts.get("skipped_bars", 0),
        "scanned_bars": counts.get("scanned_bars", 0),
        "contacts": counts.get("contacts", 0),
        "ticks_scanned": counts.get("ticks_scanned", 0),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
