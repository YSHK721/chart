// causal_series_ledger.test.js — 確定済みの点を固定する台帳（ISSUE-293）。
//
// 依頼者の要求（2026-08-08）: 「過去のラインが全て最新の結果に置き換わっている事実を確認したいので、
//   過去に確定したラインは更新するな」。上位足計算の指標は毎フレーム全点を再計算するため、
//   そのまま描くと過去のバーの値まで最新値へ塗り替わり、その時点で何が見えていたかを画面で
//   検証できない。台帳は「T より前は最初に描いた値のまま／T のバーだけ更新」を保証する。
// 構造: Arrange-Act-Assert（AAA）。DOM/ネット非依存。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CausalSeriesLedger } from '../js/replay/causal_series_ledger.js';
import { ReplayIndicatorController } from '../js/adapter/front/replay_indicator_controller.js';

const line = (pts) => [{ name: 'MA', kind: 'line', data: pts }];
const values = (series) => series[0].data.map((p) => p.value);

test('T より前の点は最初に描いた値のまま固定される', () => {
  const ledger = new CausalSeriesLedger();

  ledger.apply('mtf#1', line([{ time: 10, value: 1 }, { time: 20, value: 2 }]), 20);
  // 次のバー: サーバは全点を新しい値で返す（進行中期間の値が動いたため）。
  const out = ledger.apply(
    'mtf#1', line([{ time: 10, value: 9 }, { time: 20, value: 9 }, { time: 30, value: 9 }]), 30);

  assert.deepEqual(values(out), [1, 2, 9], '過去 2 点は確定値・現在のバーだけ新しい値');
});

test('同じ T の間は現在のバーの値が動く（足内・再計算）', () => {
  const ledger = new CausalSeriesLedger();

  ledger.apply('mtf#1', line([{ time: 10, value: 1 }, { time: 20, value: 2 }]), 20);
  const out = ledger.apply('mtf#1', line([{ time: 10, value: 1 }, { time: 20, value: 5 }]), 20);

  assert.deepEqual(values(out), [1, 5], '現在のバーは確定していない');
});

test('巻き戻すと、その先の確定は捨てる（再生し直せば再び確定する）', () => {
  const ledger = new CausalSeriesLedger();
  ledger.setTime(20);
  ledger.apply('mtf#1', line([{ time: 10, value: 1 }, { time: 20, value: 2 }]), 20);
  ledger.setTime(30);
  ledger.apply('mtf#1', line([{ time: 10, value: 1 }, { time: 20, value: 2 }, { time: 30, value: 3 }]), 30);

  ledger.setTime(20);   // 巻き戻し
  const out = ledger.apply(
    'mtf#1', line([{ time: 10, value: 1 }, { time: 20, value: 7 }, { time: 30, value: 8 }]), 20);

  assert.deepEqual(values(out), [1, 7, 8], '20 以降は再び動ける');
});

test('params 変更（forget）・時間足切替（clear）で記録を捨てる', () => {
  const ledger = new CausalSeriesLedger();
  ledger.apply('a#1', line([{ time: 10, value: 1 }]), 20);
  ledger.apply('b#1', line([{ time: 10, value: 1 }]), 20);

  ledger.forget('a#1');
  assert.deepEqual(values(ledger.apply('a#1', line([{ time: 10, value: 4 }]), 20)), [4]);
  assert.deepEqual(values(ledger.apply('b#1', line([{ time: 10, value: 4 }]), 20)), [1]);

  ledger.clear();
  assert.deepEqual(values(ledger.apply('b#1', line([{ time: 10, value: 5 }]), 20)), [5]);
});

test('ライブ（untilTime 未設定）は素通し＝記録しない', () => {
  const ledger = new CausalSeriesLedger();
  const series = line([{ time: 10, value: 1 }]);

  assert.equal(ledger.apply('x#1', series, null), series, '同一参照＝一切触らない');
});

// ---- 結線（controller の描画経路に台帳が入っている） ----

function newCtrl(untilTime) {
  const ctrl = Object.create(ReplayIndicatorController.prototype);
  ctrl._untilTime = untilTime;
  ctrl._ledger = new CausalSeriesLedger();
  ctrl._rendered = [];
  ctrl._router = { renderJob: (job) => ctrl._rendered.push(job) };
  return ctrl;
}

test('controller の描画は台帳を通る（過去点が塗り替わらない）', () => {
  const ctrl = newCtrl(20);

  ctrl._renderInstance({ instanceId: 'mtf#1', series: line([{ time: 10, value: 1 }, { time: 20, value: 2 }]) });
  ctrl._untilTime = 30;
  ctrl._renderInstance({
    instanceId: 'mtf#1',
    series: line([{ time: 10, value: 9 }, { time: 20, value: 9 }, { time: 30, value: 9 }]),
  });

  assert.deepEqual(values(ctrl._rendered[1].series), [1, 2, 9]);
});
