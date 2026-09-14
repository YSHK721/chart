// market_profile_chrome_wiring.test.js — MP へのクロム配信結線（段階 5-E）。
//
// ISSUE-291 の教訓（受け口だけでは無言で死ぬ）: primitive に setChromeColors を生やしても、
//   配る側が呼ばなければ MP の色は永久に既定のままで、テーマを変えても何も起きない。
//   MP の primitive は MpChartLayout が mainSeries.attachPrimitive で装着するため、
//   ChartRenderer.attachBackgroundPrimitive（背景プリミティブの装着＝配信登録）の経路には
//   乗らない。よって MP actor が明示的に購読して primitive へ中継する。

import test from 'node:test';
import assert from 'node:assert/strict';

import { MarketProfileActor } from '../js/adapter/front/market_profile_actor.js';

function fakePrimitive() {
  return {
    received: [],
    setChromeColors(slots) { this.received.push(slots); },
    setProfile() {}, setVisible() {}, setSessions() {},
  };
}

function fakeRenderer(slots) {
  const observers = [];
  return {
    _observers: observers,
    addChromeObserver(fn) {
      observers.push(fn);
      fn(slots); // 実装と同契約: 登録直後に現在値を 1 回配る。
      return () => {};
    },
    push(next) { for (const fn of observers) fn(next); },
  };
}

function makeActor({ primitive, renderer }) {
  return new MarketProfileActor({
    client: { fetchProfile: async () => null },
    primitive,
    mainSeries: null,
    renderer,
  });
}

test('TC-MPW-T01 結線: renderer のクロム購読口へ登録し、現在値が primitive へ届く', () => {
  // Arrange
  const primitive = fakePrimitive();
  const renderer = fakeRenderer({ mpPocLine: '#111111' });
  // Act
  makeActor({ primitive, renderer });
  // Assert
  assert.equal(renderer._observers.length, 1, 'クロム購読へ登録していない（配信が届かない）');
  assert.equal(primitive.received.length, 1, 'primitive へ中継していない');
  assert.equal(primitive.received[0].mpPocLine, '#111111');
});

test('TC-MPW-T02 結線: 後からのテーマ適用も primitive へ届く', () => {
  // Arrange
  const primitive = fakePrimitive();
  const renderer = fakeRenderer({ mpPocLine: '#111111' });
  makeActor({ primitive, renderer });
  // Act
  renderer.push({ mpPocLine: '#222222' });
  // Assert
  assert.equal(primitive.received.at(-1).mpPocLine, '#222222', 'テーマ適用が MP へ届いていない');
});

test('TC-MPW-T03 結線: renderer 未注入・購読口なし・primitive なしでも例外を投げない（後方互換）', () => {
  // Arrange / Act / Assert
  assert.doesNotThrow(() => makeActor({ primitive: fakePrimitive(), renderer: null }));
  assert.doesNotThrow(() => makeActor({ primitive: fakePrimitive(), renderer: {} }));
  assert.doesNotThrow(() => makeActor({ primitive: null, renderer: fakeRenderer({}) }));
});
