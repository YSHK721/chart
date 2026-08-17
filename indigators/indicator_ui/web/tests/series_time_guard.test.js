// ISSUE-383: lightweight-charts（vendor production ビルド）は系列データの「厳密増加 time」契約を
//   検証しない。契約違反（同 time / 後退 time）の配列を setData に通すと内部二分探索が破綻し、
//   以後クロスヘア/ペイントのたびに `Value is null` を throw し続ける回復不能状態になる
//   （最小再現で実測・逆行 1 点で恒久再発）。SeriesTimeGuard は lwc へ渡す直前の防壁として
//   厳密増加を保証する（ISSUE-167 のローソク防壁 dedupeCandlesByTime の全系列一般化）。
// 構造: Arrange-Act-Assert（AAA）。DOM 非依存。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  findTimeOrderViolation,
  enforceAscendingTimes,
  setSeriesTimeGuardNotifier,
} from '../js/adapter/front/series_time_guard.js';

// console.error を一時捕捉する（フィンガープリントログの検証・透過性の検証）。
function withCapturedError(fn) {
  const orig = console.error;
  const calls = [];
  console.error = (...args) => { calls.push(args); };
  try {
    fn(calls);
  } finally {
    console.error = orig;
  }
  return calls;
}

test('清浄（厳密増加）な配列は同一参照を返しログも出ない', () => {
  const pts = [{ time: 10, value: 1 }, { time: 20, value: 2 }, { time: 30, value: 3 }];
  const calls = withCapturedError(() => {
    const out = enforceAscendingTimes(pts, 'k');
    assert.equal(out, pts); // 同一参照＝挙動 byte 不変
  });
  assert.equal(calls.length, 0);
});

test('空配列・1 点は清浄扱い（同一参照）', () => {
  const empty = [];
  const one = [{ time: 5, value: 0 }];
  assert.equal(enforceAscendingTimes(empty, 'k'), empty);
  assert.equal(enforceAscendingTimes(one, 'k'), one);
});

test('findTimeOrderViolation は最初の違反 index を返す（清浄は -1）', () => {
  assert.equal(findTimeOrderViolation([{ time: 1 }, { time: 2 }]), -1);
  assert.equal(findTimeOrderViolation([{ time: 1 }, { time: 1 }]), 1);
  assert.equal(findTimeOrderViolation([{ time: 1 }, { time: 2 }, { time: 0 }]), 2);
  assert.equal(findTimeOrderViolation(null), -1);
});

test('同 time は後勝ち（keep-last）で 1 点へ畳む', () => {
  const pts = [{ time: 10, value: 1 }, { time: 10, value: 9 }, { time: 20, value: 2 }];
  withCapturedError(() => {
    const out = enforceAscendingTimes(pts, 'k');
    assert.deepEqual(out, [{ time: 10, value: 9 }, { time: 20, value: 2 }]);
  });
});

test('後退 time は捨てて厳密増加を維持する', () => {
  const pts = [{ time: 10, value: 1 }, { time: 20, value: 2 }, { time: 15, value: 9 }];
  withCapturedError(() => {
    const out = enforceAscendingTimes(pts, 'k');
    assert.deepEqual(out, [{ time: 10, value: 1 }, { time: 20, value: 2 }]);
  });
});

test('違反時はフィンガープリント（ラベル・位置・前後 time・点数）を console.error する', () => {
  const pts = [{ time: 10, value: 1 }, { time: 5, value: 2 }];
  const calls = withCapturedError(() => {
    enforceAscendingTimes(pts, 'ma_marod#2::mean');
  });
  assert.equal(calls.length, 1);
  assert.match(String(calls[0][0]), /series-time-guard/);
  assert.match(String(calls[0][0]), /ma_marod#2::mean/);
  assert.deepEqual(calls[0][1], {
    firstViolationIndex: 1, prevTime: 10, time: 5, before: 2, after: 1,
  });
});

// ================= 能動通知 seam（ISSUE-383・ユーザー裁定 2026-08-17） =================

test('違反時は登録済み notifier がラベル付きで呼ばれる（清浄時は呼ばれない）', () => {
  const seen = [];
  setSeriesTimeGuardNotifier((label) => seen.push(label));
  try {
    withCapturedError(() => {
      enforceAscendingTimes([{ time: 1, value: 0 }, { time: 2, value: 1 }], 'clean::s'); // 清浄
      enforceAscendingTimes([{ time: 2, value: 0 }, { time: 1, value: 1 }], 'bad::s');   // 違反
    });
    assert.deepEqual(seen, ['bad::s']);
  } finally {
    setSeriesTimeGuardNotifier(null);
  }
});

test('notifier の例外は握られ、畳み込み結果とログは通常どおり返る', () => {
  setSeriesTimeGuardNotifier(() => { throw new Error('boom'); });
  try {
    let out;
    const calls = withCapturedError(() => {
      out = enforceAscendingTimes([{ time: 2, value: 0 }, { time: 1, value: 1 }], 'k');
    });
    assert.deepEqual(out, [{ time: 2, value: 0 }]);
    assert.equal(calls.length, 1); // フィンガープリントは失われない
  } finally {
    setSeriesTimeGuardNotifier(null);
  }
});

test('setSeriesTimeGuardNotifier は関数以外で解除される（null 登録＝通知なし）', () => {
  const seen = [];
  setSeriesTimeGuardNotifier((label) => seen.push(label));
  setSeriesTimeGuardNotifier(null);
  withCapturedError(() => {
    enforceAscendingTimes([{ time: 2, value: 0 }, { time: 1, value: 1 }], 'k');
  });
  assert.deepEqual(seen, []);
});

test('非数値 time（business day 等）はローソク防壁と同様に対象外＝素通し', () => {
  const pts = [{ time: '2026-08-13', value: 1 }, { time: '2026-08-12', value: 2 }];
  const calls = withCapturedError(() => {
    const out = enforceAscendingTimes(pts, 'k');
    assert.equal(out, pts);
  });
  assert.equal(calls.length, 0);
});
