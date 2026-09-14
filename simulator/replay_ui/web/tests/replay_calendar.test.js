// replay_calendar.test.js — リプレイバーのカレンダー純ロジック（UTC 固定・AAA）。
//   日は必ず UTC で切る（足の time が UNIX 秒＝UTC のため、ローカル TZ で切ると所属日がズレる）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { dayKey, dayStartUnix, shiftMonth, monthCells, latestMonth } from '../js/replay/calendar.js';

test('dayKey は UNIX 秒をその日（UTC）のキーへ落とす', () => {
  assert.equal(dayKey(1578268800), '2020-01-06');           // 00:00:00 UTC ちょうど
  assert.equal(dayKey(1578268800 + 86399), '2020-01-06');   // 同日 23:59:59 UTC
  assert.equal(dayKey(1578268800 + 86400), '2020-01-07');   // 翌日
});

test('dayStartUnix は日キーをその日の 00:00:00 UTC へ戻す（dayKey の逆）', () => {
  assert.equal(dayStartUnix('2020-01-06'), 1578268800);
  assert.equal(dayKey(dayStartUnix('2026-02-17')), '2026-02-17');
});

test('shiftMonth は年境界をまたいで月を送る', () => {
  assert.deepEqual(shiftMonth({ year: 2026, month: 1 }, -1), { year: 2025, month: 12 });
  assert.deepEqual(shiftMonth({ year: 2026, month: 12 }, 1), { year: 2027, month: 1 });
  assert.deepEqual(shiftMonth({ year: 2026, month: 2 }, 0), { year: 2026, month: 2 });
});

test('monthCells は日曜始まり 6 週 = 42 セルを返し、当月セルだけ inMonth=true', () => {
  const cells = monthCells({ year: 2026, month: 2 }); // 2026-02-01 は日曜
  assert.equal(cells.length, 42);
  assert.equal(cells[0].key, '2026-02-01');
  assert.equal(cells[0].inMonth, true);
  const inMonth = cells.filter((c) => c.inMonth);
  assert.equal(inMonth.length, 28);                    // 2026 年 2 月は 28 日
  assert.equal(inMonth[inMonth.length - 1].key, '2026-02-28');
  assert.equal(cells[28].inMonth, false);              // 3 月分は当月外
});

test('monthCells は月初が日曜でない月でも直前の日曜から並べる', () => {
  const cells = monthCells({ year: 2026, month: 3 }); // 2026-03-01 は日曜でない月の確認
  assert.equal(cells.filter((c) => c.inMonth).length, 31);
  assert.equal(cells.find((c) => c.inMonth).key, '2026-03-01');
});

test('latestMonth は選択可能日の末尾が属する月（空なら null）', () => {
  assert.deepEqual(latestMonth(['2020-01-03', '2026-02-17']), { year: 2026, month: 2 });
  assert.equal(latestMonth([]), null);
  assert.equal(latestMonth(null), null);
});
