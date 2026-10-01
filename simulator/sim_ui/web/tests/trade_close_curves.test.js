// report_ui chart.js の tradeCloseCurves — 取引終了時の残高・DD の単一ソース（2026-09-27）。
//
// シミュレーション結果（資産曲線 balChart・ドローダウン ddChart）と売買履歴チャート（取引終了時の
//   レイヤー）が同じ関数で作る。初期証拠金の決め方（payload の meta → 区間の meta → 既定）を
//   2 か所に書かないための検査。
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { balanceForwardFill, tradeCloseCurves, DEFAULT_DEPOSIT } from '../../../report_ui/web/js/chart.js';

const TIMES = [100, 160, 220];
const CURVE = [{ time: 160, value: 900 }];

test('初期証拠金は payload の meta → 区間の meta → 既定の順', () => {
  const seg = { meta: { initial_deposit: 2000 }, agg: { balance_curve: CURVE } };
  assert.equal(tradeCloseCurves(seg, TIMES, 5000).balData[0].value, 5000);
  assert.equal(tradeCloseCurves(seg, TIMES, null).balData[0].value, 2000);
  assert.equal(tradeCloseCurves({ agg: { balance_curve: CURVE } }, TIMES).balData[0].value, DEFAULT_DEPOSIT);
});

test('値は balanceForwardFill と同一（取引終了時に更新・足の時刻で持つ）', () => {
  const seg = { meta: { initial_deposit: 1000 }, agg: { balance_curve: CURVE } };
  assert.deepEqual(tradeCloseCurves(seg, TIMES), balanceForwardFill(TIMES, CURVE, 1000));
  assert.deepEqual(tradeCloseCurves(seg, TIMES).balData.map((p) => p.value), [1000, 900, 900]);
  assert.deepEqual(tradeCloseCurves(seg, TIMES).ddData.map((p) => p.value), [0, -100, -100]);
});

test('計算量: 点は足の数ちょうど（取引の数に依らない）', () => {
  for (const trades of [0, 300]) {
    const times = Array.from({ length: 600 }, (_, i) => i + 1);
    const curve = Array.from({ length: trades }, (_, k) => ({ time: k * 2 + 1, value: 1000 - k }));
    const r = tradeCloseCurves({ agg: { balance_curve: curve } }, times);
    assert.equal(r.balData.length - times.length, 0);
    assert.equal(r.ddData.length - times.length, 0);
  }
});
