// session_ohlc.test.js — セッション日 OHLC 集計（domain 純関数）の単体テスト。
//
// 対象: js/domain/session_ohlc.js（ISSUE-094 V6 抽出・market_profile/web/js/domain/ 実体の symlink）。
//   market_profile_actor.js の _buildSessionView に混在していた「当日全バーの OHLC をセッション日で集計」
//   する部分（byDay 構築）を domain 純関数 aggregateSessionOhlc へ外出しした対象。挙動は抽出前と byte 等価。
//   セッション日ラベル規則（NY17:00 ET 基準・ISSUE-078）は domain/session_day.js に一元化されており、
//   本テストは同じ規則源から期待キーを導いて集計の不変条件（open=始端/close=終端/high=max/low=min/
//   tFirst=最古/tLast=最新）を固定する。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { aggregateSessionOhlc } from '../js/domain/session_ohlc.js';
import { sessionDateLabel } from '../js/domain/session_day.js';

test('aggregateSessionOhlc: null/空は空 Map を返す', () => {
  assert.equal(aggregateSessionOhlc(null).size, 0);
  assert.equal(aggregateSessionOhlc([]).size, 0);
});

test('aggregateSessionOhlc: 単一バーは tFirst=tLast=time・OHLC=そのバー', () => {
  const t = Date.UTC(2025, 0, 6, 3, 0, 0) / 1000; // 2025-01-06 03:00 UTC（月曜・アジア時間帯）
  const m = aggregateSessionOhlc([{ time: t, open: 10, high: 12, low: 9, close: 11 }]);
  const agg = m.get(sessionDateLabel(t));
  assert.deepEqual(agg, { tFirst: t, tLast: t, open: 10, high: 12, low: 9, close: 11 });
});

test('aggregateSessionOhlc: 同一セッション日の複数バーは始端 open/終端 close/範囲 high-low へ集計', () => {
  const base = Date.UTC(2025, 0, 6, 3, 0, 0) / 1000;
  const bars = [
    { time: base, open: 10, high: 12, low: 9, close: 11 },
    { time: base + 60, open: 11, high: 15, low: 10, close: 14 },
    { time: base + 120, open: 14, high: 14, low: 8, close: 13 },
  ];
  // 全バーが同一セッション日である前提を明示（規則源で確認）。
  const day = sessionDateLabel(base);
  assert.equal(sessionDateLabel(base + 120), day);
  const agg = aggregateSessionOhlc(bars).get(day);
  assert.equal(agg.open, 10);   // 最古バーの open
  assert.equal(agg.close, 13);  // 最新バーの close
  assert.equal(agg.high, 15);   // 全バー high の最大
  assert.equal(agg.low, 8);     // 全バー low の最小
  assert.equal(agg.tFirst, base);
  assert.equal(agg.tLast, base + 120);
});

test('aggregateSessionOhlc: 入力順に依存しない（逆順でも min/max 更新で頑健）', () => {
  const base = Date.UTC(2025, 0, 6, 3, 0, 0) / 1000;
  const bars = [
    { time: base + 120, open: 14, high: 14, low: 8, close: 13 },
    { time: base, open: 10, high: 12, low: 9, close: 11 },
    { time: base + 60, open: 11, high: 15, low: 10, close: 14 },
  ];
  const agg = aggregateSessionOhlc(bars).get(sessionDateLabel(base));
  assert.equal(agg.open, 10);
  assert.equal(agg.close, 13);
  assert.equal(agg.tFirst, base);
  assert.equal(agg.tLast, base + 120);
});

test('aggregateSessionOhlc: 別セッション日は別エントリへ分離する', () => {
  const d1 = Date.UTC(2025, 0, 6, 3, 0, 0) / 1000;
  const d2 = Date.UTC(2025, 0, 9, 3, 0, 0) / 1000; // 3 日後（別セッション日）
  const m = aggregateSessionOhlc([
    { time: d1, open: 1, high: 2, low: 1, close: 2 },
    { time: d2, open: 5, high: 6, low: 4, close: 5 },
  ]);
  assert.notEqual(sessionDateLabel(d1), sessionDateLabel(d2));
  assert.equal(m.size, 2);
  assert.equal(m.get(sessionDateLabel(d2)).open, 5);
});

test('aggregateSessionOhlc: 非有限 time のバーは無視する', () => {
  const t = Date.UTC(2025, 0, 6, 3, 0, 0) / 1000;
  const m = aggregateSessionOhlc([
    { time: NaN, open: 99, high: 99, low: 99, close: 99 },
    { time: t, open: 1, high: 2, low: 1, close: 2 },
  ]);
  assert.equal(m.size, 1);
  assert.equal(m.get(sessionDateLabel(t)).open, 1);
});
