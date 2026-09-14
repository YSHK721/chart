// forming_plan.test.js — 足内一括計算の純ロジック（ISSUE-232）。
//
// 固定する契約:
//   - sampleIndices: ローソクが動く全点（間引かない・ISSUE-233）。昇順ユニーク・末尾 n-1 を含む
//     （末尾＝バー確定値。欠けると確定に往復が要る）
//   - formingStatesAt: animateForming と同一の畳み方（open 固定・hi/lo 累積・close=当該ティック）
//   - planSignature: 指標構成・variant・params・窓のいずれかが変われば必ず変わる（誤描画の遮断）

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  sampleIndices, formingStatesAt, planSignature,
} from '../js/replay/forming_plan.js';

test('sampleIndices: 全ティックを対象にする（間引かない）', () => {
  assert.deepEqual(sampleIndices(5), [0, 1, 2, 3, 4]);
});

test('sampleIndices: 指標の更新回数はローソクの更新回数と一致する（ISSUE-233）', () => {
  // 上限を設けない＝「点間でローソクだけが動く」区間を作らない。上限を戻すと粒度が
  // 人手の定数で決まり、指標を足すほど黙って落ちる構造（応急処置）へ退行する。
  for (const n of [1, 32, 201, 1000, 30000]) {
    const idx = sampleIndices(n);
    assert.equal(idx.length, n, `n=${n} で全点が対象になっていない`);
    assert.equal(idx[0], 0);
    assert.equal(idx[idx.length - 1], n - 1, '末尾を含まないとバー確定値に往復が必要になる');
  }
});

test('sampleIndices: 昇順ユニーク', () => {
  const idx = sampleIndices(1000);
  for (let i = 1; i < idx.length; i++) {
    assert.ok(idx[i] > idx[i - 1], '昇順ユニークでない');
  }
});

test('sampleIndices: 空・不正は空配列', () => {
  assert.deepEqual(sampleIndices(0), []);
  assert.deepEqual(sampleIndices(-1), []);
  assert.deepEqual(sampleIndices(NaN), []);
});

test('formingStatesAt: open 固定・high/low 累積・close=当該ティック（animateForming と同一規則）', () => {
  const cd = { time: 100 };
  const prices = [10, 12, 9, 11];
  const states = formingStatesAt(cd, prices, [0, 1, 2, 3]);
  assert.deepEqual(states, [
    { time: 100, open: 10, high: 10, low: 10, close: 10 },
    { time: 100, open: 10, high: 12, low: 10, close: 12 },
    { time: 100, open: 10, high: 12, low: 9, close: 9 },
    { time: 100, open: 10, high: 12, low: 9, close: 11 },
  ]);
});

test('formingStatesAt: 間引いた添字でも high/low は全ティックの累積（飛ばした極値を落とさない）', () => {
  const states = formingStatesAt({ time: 1 }, [10, 99, 5, 11], [3]);
  assert.deepEqual(states, [{ time: 1, open: 10, high: 99, low: 5, close: 11 }]);
});

test('formingStatesAt: 空入力は空配列', () => {
  assert.deepEqual(formingStatesAt(null, [1], [0]), []);
  assert.deepEqual(formingStatesAt({ time: 1 }, [], [0]), []);
});

test('planSignature: params / variant / 窓が変われば署名も変わる', () => {
  const base = {
    targets: [{ instanceId: 'ma#1', indicatorId: 'moving_averages', variant: 'default', params: { length: 20 } }],
    timeframe: '5m', limit: 100, untilTime: 999,
  };
  const sig = planSignature(base);
  assert.equal(planSignature({ ...base }), sig, '同一入力で署名が揺れる');
  assert.notEqual(planSignature({ ...base, timeframe: '1h' }), sig);
  assert.notEqual(planSignature({ ...base, limit: 101 }), sig);
  assert.notEqual(planSignature({ ...base, untilTime: 1000 }), sig);
  assert.notEqual(planSignature({
    ...base,
    targets: [{ ...base.targets[0], params: { length: 21 } }],
  }), sig, 'params 変更が署名に出ない（陳腐化した値で描画してしまう）');
  assert.notEqual(planSignature({
    ...base,
    targets: [{ ...base.targets[0], variant: 'other' }],
  }), sig);
});

// --- ISSUE-238: リプレイ現在時刻 `to` の付与 --------------------------------- //
// 形成中バーに volume が無いと `forming_bar.apply`（存在するキーのみ更新）の規約により
// 確定足の完成 tick 数が残り、足の先頭から未来の値を表示してしまう。`to` を添えることで
// サーバが「その時点までに到来した実 tick 数」を数えて volume にする。

test('formingStatesAt: secs があれば各時点へリプレイ現在時刻 to を添える', () => {
  const cd = { time: 1000 };
  const out = formingStatesAt(cd, [10, 12, 11], [0, 1, 2], [1000, 1100, 1200]);
  assert.deepEqual(out.map((s) => s.to), [1000, 1100, 1200]);
  // OHLC の畳み方は不変（open 固定・hi/lo 累積・close=当該ティック）。
  assert.deepEqual(out.map((s) => s.close), [10, 12, 11]);
  assert.deepEqual(out.map((s) => s.high), [10, 12, 12]);
});

test('formingStatesAt: to は整数秒へ丸める（合成 secs は小数になりうる）', () => {
  const out = formingStatesAt({ time: 0 }, [1, 2], [0, 1], [10.9, 20.2]);
  assert.deepEqual(out.map((s) => s.to), [10, 20]);
});

test('formingStatesAt: secs 未提供なら to を付けない（従来 payload と同一）', () => {
  const out = formingStatesAt({ time: 0 }, [1, 2], [0, 1]);
  assert.ok(out.every((s) => !('to' in s)), 'to を付けてはいけない');
  assert.deepEqual(Object.keys(out[0]).sort(), ['close', 'high', 'low', 'open', 'time']);
});

test('formingStatesAt: secs が部分的に欠けている点だけ to を付けない', () => {
  const out = formingStatesAt({ time: 0 }, [1, 2, 3], [0, 1, 2], [10, null, 30]);
  assert.equal(out[0].to, 10);
  assert.ok(!('to' in out[1]));
  assert.equal(out[2].to, 30);
});

test('formingStatesAt: volume はフロントで作らない（点数≠実 tick 数のため）', () => {
  // 合成モードの点数は実 tick 数と一致しない（1分OHLC は 4 点/分）。数えるのはサーバの責務。
  const out = formingStatesAt({ time: 0 }, [1, 2, 3], [0, 1, 2], [10, 20, 30]);
  assert.ok(out.every((s) => !('volume' in s)));
});
