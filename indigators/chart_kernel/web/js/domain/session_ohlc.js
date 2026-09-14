// session_ohlc.js — セッション日 OHLC 集計（domain 純関数・ISSUE-094 V6 抽出）。
//
// market_profile_actor.js の _buildSessionView に混在していた「当日全バーの OHLC をセッション日で集計」
//   する部分（データ品質/供給とは独立した集計数学）を domain の純関数へ外出しする。
//   セッション日境界（NY17:00 ET 基準・ISSUE-078）は domain/session_day.js を唯一の規則源として使う
//   （日曜夜 UTC の足も月曜セッションへ束ねる）。DOM・fetch・actor 状態に非依存の純関数。

import { sessionDateLabel } from './session_day.js';

// candles を「セッション日ラベル → { tFirst, tLast, open, high, low, close }」へ集計する。
//   candles は time 昇順が契約だが、順序に依存しない min/max 更新で頑健化する（tFirst=最古 open /
//   tLast=最新 close / high=範囲最大 / low=範囲最小）。1D は日=バー 1:1、日中足（1m 等）は当日全バーの
//   集計 OHLC になる（ISSUE-072 の日集計をセッション日へ一般化）。非有限 time のバーは無視する。
export function aggregateSessionOhlc(candles) {
  const byDay = new Map();
  for (const c of (candles || [])) {
    const time = Number(c.time);
    if (!Number.isFinite(time)) {
      continue;
    }
    const d = sessionDateLabel(time); // ISSUE-078: セッション日で束ねる（UTC 暦日でなく）。
    const agg = byDay.get(d);
    if (!agg) {
      byDay.set(d, {
        tFirst: time, tLast: time, open: c.open, high: c.high, low: c.low, close: c.close,
      });
    } else {
      if (time < agg.tFirst) {
        agg.tFirst = time;
        agg.open = c.open;
      }
      if (time > agg.tLast) {
        agg.tLast = time;
        agg.close = c.close;
      }
      if (c.high > agg.high) {
        agg.high = c.high;
      }
      if (c.low < agg.low) {
        agg.low = c.low;
      }
    }
  }
  return byDay;
}
