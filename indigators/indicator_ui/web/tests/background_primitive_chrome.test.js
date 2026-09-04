// background_primitive_chrome.test.js — 背景プリミティブへのクロム配信結線（段階 5-E）。
//
// ISSUE-291 の教訓（受け口だけでは無言で死ぬ）: TickvolBandsPrimitive に setChromeColors を
//   生やしても、配る側が呼ばなければ帯の色は永久に既定のままで、テーマを変えても何も起きない。
//   配信の所有者は ChartRenderer（保持と配布の単一点）なので、背景プリミティブの装着口が
//   そのまま配信の登録口も兼ねる形にする（装着したのに配られない、という状態を作らない）。
//
// なぜ装着口に閉じるか: 装着と配信登録を別々の呼び出しにすると、片方だけ呼ぶ経路が必ず生まれる
//   （実測: replay 側は装着だけを行う独自経路を持っている）。1 つの操作にまとめれば、装着した
//   ものは必ず配信を受ける。

import test from 'node:test';
import assert from 'node:assert/strict';

import { ChartRenderer } from '../js/adapter/front/chart_renderer.js';
import { TickvolBandsPrimitive } from '../js/adapter/front/tickvol_bands_primitive.js';
import { CHROME_CURRENT } from '../js/usecase/chrome_tokens.js';

function makeRenderer() {
  const attached = [];
  const mainSeries = {
    attachPrimitive(p) { attached.push(p); },
    applyOptions() {},
    setData() {},
  };
  const chart = { applyOptions() {} };
  return { renderer: new ChartRenderer({ chart, mainSeries, lwc: {} }), attached };
}

test('TC-BP-T01 装着した背景プリミティブへ現在のクロム色が即時に配られる', () => {
  // Arrange
  const { renderer } = makeRenderer();
  // Act
  const p = renderer.attachBackgroundPrimitive('tvb', () => new TickvolBandsPrimitive());
  // Assert: 未配信でも「現在の保持値」＝現行リテラルが届く（届いた結果が現行と同じ＝恒等）。
  assert.equal(p._fill, CHROME_CURRENT.tickvolBand);
});

test('TC-BP-T02 装着後にテーマが適用されると背景プリミティブの色が追随する', () => {
  // Arrange
  const { renderer } = makeRenderer();
  const p = renderer.attachBackgroundPrimitive('tvb', () => new TickvolBandsPrimitive());
  // Act
  renderer.applyChromeColors({ tickvolBand: 'rgba(1, 2, 3, 0.07)' });
  // Assert
  assert.equal(p._fill, 'rgba(1, 2, 3, 0.07)', 'テーマ適用が背景プリミティブへ届いていない');
});

test('TC-BP-T03 テーマ適用が先・装着が後でも色が古いまま残らない（順序非依存）', () => {
  // Arrange
  const { renderer } = makeRenderer();
  // Act: 起動時にテーマを配ってから、後で帯を有効化する実際の順序。
  renderer.applyChromeColors({ tickvolBand: 'rgba(9, 9, 9, 0.07)' });
  const p = renderer.attachBackgroundPrimitive('tvb', () => new TickvolBandsPrimitive());
  // Assert
  assert.equal(p._fill, 'rgba(9, 9, 9, 0.07)', '装着前に適用された色が反映されていない');
});

test('TC-BP-T04 setChromeColors を持たないプリミティブでも装着は壊れない（後方互換）', () => {
  // 既存の背景プリミティブ（受け口を持たないもの）を装着しても例外にしない。
  // Arrange
  const { renderer, attached } = makeRenderer();
  const plain = { paneViews: () => [] };
  // Act / Assert
  assert.doesNotThrow(() => renderer.attachBackgroundPrimitive('plain', () => plain));
  assert.equal(attached.length, 1);
  assert.doesNotThrow(() => renderer.applyChromeColors({ tickvolBand: 'rgba(1, 1, 1, 0.07)' }));
});

test('TC-BP-T05 同一 key の再装着は既存を返し、二重登録しない（配信が多重にならない）', () => {
  // Arrange
  const { renderer, attached } = makeRenderer();
  // Act
  const first = renderer.attachBackgroundPrimitive('tvb', () => new TickvolBandsPrimitive());
  const second = renderer.attachBackgroundPrimitive('tvb', () => new TickvolBandsPrimitive());
  // Assert
  assert.equal(first, second, '同一 key で別インスタンスが生まれている');
  assert.equal(attached.length, 1, 'lwc へ二重装着している');
});
