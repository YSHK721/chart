// session_day.js（frontend セッション日境界・ISSUE-078）の検証。backend test_session_day.py と同じ
//   実測ケース（夏冬境界・日曜帰属・DST 23h/25h・往復・冪等）を固定する（両実装の規則一致を担保）。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  sessionDayStart, nextSessionDayStart, sessionDateLabel, sessionBarTime, sessionLabelToStart,
} from '../js/domain/session_day.js';

const utc = (y, m, d, hh = 0, mm = 0, ss = 0) => Date.UTC(y, m - 1, d, hh, mm, ss) / 1000;

test('夏境界 21:00 UTC（境界ちょうどは新セッション）', () => {
  const b = utc(2026, 7, 13, 21);
  assert.equal(sessionDayStart(b), b);
  assert.equal(sessionDayStart(b - 1), b - 86400);
});

test('冬境界 22:00 UTC', () => {
  const b = utc(2026, 1, 13, 22);
  assert.equal(sessionDayStart(b), b);
  assert.equal(sessionDayStart(b - 1), b - 86400);
});

test('日曜夜 UTC は月曜セッションへ帰属（夏・実測オープン 22:03）', () => {
  const t = utc(2026, 7, 12, 22, 3, 44);
  assert.equal(sessionDayStart(t), utc(2026, 7, 12, 21));
  assert.equal(sessionDateLabel(t), '2026-07-13');
  assert.equal(sessionBarTime(t), utc(2026, 7, 13));
});

test('日曜夜 UTC は月曜セッションへ帰属（冬・実測オープン 23:00）', () => {
  const t = utc(2026, 1, 11, 23, 0, 37);
  assert.equal(sessionDayStart(t), utc(2026, 1, 11, 22));
  assert.equal(sessionDateLabel(t), '2026-01-12');
});

test('DST 切替日は 23h / 25h セッション', () => {
  const spring = sessionDayStart(utc(2026, 3, 8, 0));
  assert.equal(spring, utc(2026, 3, 7, 22));
  assert.equal(nextSessionDayStart(spring) - spring, 23 * 3600);
  const fall = sessionDayStart(utc(2026, 11, 1, 0));
  assert.equal(fall, utc(2026, 10, 31, 21));
  assert.equal(nextSessionDayStart(fall) - fall, 25 * 3600);
});

test('ラベル往復と冪等性', () => {
  assert.equal(sessionLabelToStart('2026-07-13'), utc(2026, 7, 12, 21));
  assert.equal(sessionLabelToStart('2026-01-12'), utc(2026, 1, 11, 22));
  assert.ok(Number.isNaN(sessionLabelToStart('bogus')));
  for (const t of [utc(2026, 7, 12, 22, 3), utc(2026, 1, 11, 23, 1), utc(2026, 3, 8, 12)]) {
    const s = sessionDayStart(t);
    assert.equal(sessionDayStart(s), s);
  }
});

test('backend との規則一致（python 実装と同一値のスポット照合）', () => {
  // marketdata/tests/test_session_day.py の既知値。
  assert.equal(sessionDayStart(1783893824), 1783890000); // 2026-07-12 22:03:44 → 21:00。
  assert.equal(sessionBarTime(1783890000), 1783900800);  // → 2026-07-13 00:00。
});
