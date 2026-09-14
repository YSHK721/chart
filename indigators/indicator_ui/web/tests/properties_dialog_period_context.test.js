// PropertiesDialog の期間プリセット基準（periodContext）解決の検証（node:test / node:assert）。
//
// 対象: adapter/front/properties_dialog.js の _periodContext / _timeframeLabel / _controlCtx。
// 設計入力: 基本設計_期間プリセット.md v0.1.0 §6.5 実効計算時間足 / §8.2 供給面。
// 構造: Arrange-Act-Assert（AAA）。最小 DOM スタブのみ（jsdom 非依存・C-2）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { PropertiesDialog } from '../js/adapter/front/properties_dialog.js';
import { get } from '../js/usecase/catalog.js';

function fakeDoc() {
  return {
    createElement() {
      return { className: '', dataset: {}, textContent: '' };
    },
  };
}

const MA = get('moving_averages');

function makeDialog(context) {
  return new PropertiesDialog({
    document: fakeDoc(), def: MA, instance: null, mode: 'b', context,
  });
}

test('_periodContext: context 未供給なら null（プリセット非提示へ退化・F-P2/F-P3）', () => {
  assert.equal(makeDialog({})._periodContext(), null);
  assert.equal(makeDialog({ timeframe: '1D' })._periodContext(), null);
  assert.equal(makeDialog({ datasetRef: 'jp225_tick' })._periodContext(), null);
});

test('_periodContext: timeframe override が既定 chart ならチャート足を基準にする', () => {
  const d = makeDialog({ datasetRef: 'jp225_tick', timeframe: '1D' });
  // moving_averages の timeframe 既定は 'chart'。
  assert.equal(d._values.timeframe, 'chart');
  assert.deepEqual(d._periodContext(), {
    datasetRef: 'jp225_tick', timeframe: '1D', timeframeLabel: '日足',
  });
});

test('_periodContext: 指標の計算時間足 override を基準にする（MTF・§6.5）', () => {
  const d = makeDialog({ datasetRef: 'jp225_tick', timeframe: '1D' });
  // Act: ダイアログ内で計算時間足を 1h へ変更した状態を再現する。
  d._values.timeframe = '1h';
  // Assert: 遅延アクセサなので次の呼び出しから新しい実効足になる。
  assert.deepEqual(d._periodContext(), {
    datasetRef: 'jp225_tick', timeframe: '1h', timeframeLabel: '1時間足',
  });
});

test('_periodContext: timeframe パラメータを持たない指標はチャート足に追従する', () => {
  const d = new PropertiesDialog({
    document: fakeDoc(), def: get('ma_marod'), instance: null, mode: 'b',
    context: { datasetRef: 'jp225_tick', timeframe: '4h' },
  });
  assert.equal(d._periodContext().timeframe, '4h');
});

test('_timeframeLabel: 時間足ラベルの単一情報源（timeframe_menu）を使う', () => {
  const d = makeDialog({ datasetRef: 'jp225_tick', timeframe: '1D' });
  assert.equal(d._timeframeLabel('1m'), '1分足');
  assert.equal(d._timeframeLabel('1h'), '1時間足');
  assert.equal(d._timeframeLabel('1W'), '週足');
  // 未知足はコードをそのまま返す（F-P2 でプリセット自体は出ない）。
  assert.equal(d._timeframeLabel('2h'), '2h');
});

test('_controlCtx.periodContext は _periodContext へ委譲する（コントロールへの供給面）', () => {
  const d = makeDialog({ datasetRef: 'jp225_tick', timeframe: '5m' });
  assert.equal(typeof d._controlCtx.periodContext, 'function');
  assert.deepEqual(d._controlCtx.periodContext(), d._periodContext());
  assert.equal(d._controlCtx.periodContext().timeframe, '5m');
});
