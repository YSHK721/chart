// ChartInteractionController（adapter/front/chart_interaction_controller.js）の単体検証。
//
// 設計入力: ISSUE-040(a)。composition root の DI ルートに混入したチャート操作の振る舞い
//   （縦価格パン・wheel 価格ズーム・dblclick reset）を本コントローラへ抽出し、
//   composition root は new して install() するだけに縮小する（Composition Root は配線専用）。
// ISSUE-082: リプレイモード（swipe スクラブ・replayBar 依存）は present から撤去した。
//   本テストからもリプレイ系ケースを削除し、isReplay=true 相当でも通常パンのままであることを検証する
//   （replay_ui は独立コピーを保持）。
// 観点: install() が container の pointer/wheel/dblclick を配線し、
//   注入依存（renderer / getController / updatePaneHeight）へ既存と同一の呼出を行う。
// 構造: Arrange-Act-Assert。container/renderer は Fake を注入（DOM/実描画非依存）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ChartInteractionController } from '../js/adapter/front/chart_interaction_controller.js';

// addEventListener を記録するチャートコンテナ Fake（handler を type で引ける・fire で発火）。
function fakeContainer() {
  const handlers = {};
  return {
    addEventListener(type, fn, opts) { (handlers[type] ||= []).push({ fn, opts }); },
    getBoundingClientRect() { return { left: 0, top: 0 }; },
    fire(type, ev) { (handlers[type] || []).forEach((h) => h.fn(ev)); },
    optsFor(type) { return (handlers[type] || [])[0]?.opts; },
    has(type) { return !!(handlers[type] && handlers[type].length); },
  };
}

// renderer Fake（呼出を記録するスパイ群）。既定は本体領域・非ズーム。
function fakeRenderer(overrides = {}) {
  const calls = { userInteraction: [], panPriceByPixels: [], handlePriceWheel: [], setPaneHeight: [] };
  const r = {
    calls,
    setUserInteraction: (v) => { calls.userInteraction.push(v); },
    pixelsPerBar: () => 10,
    isPriceZoomed: () => false,
    panPriceByPixels: (dy) => { calls.panPriceByPixels.push(dy); },
    handlePriceWheel: (x, y, dy) => { calls.handlePriceWheel.push([x, y, dy]); return false; },
    isOverPriceAxis: () => false,
    resetPriceZoom: () => { calls.resetPriceZoom = (calls.resetPriceZoom || 0) + 1; },
    setPaneHeight: (h) => { calls.setPaneHeight.push(h); },
  };
  return Object.assign(r, overrides);
}

// getController Fake（旧リプレイ判定の残骸が参照されないことを isReplay スパイで確認できる形）。
function makeGetController(isReplay = false) {
  const ctrl = { _marketProfile: { isReplay: () => isReplay } };
  return { ctrl, getController: () => ctrl };
}

function build({ replay = false, renderer } = {}) {
  const container = fakeContainer();
  const r = renderer || fakeRenderer();
  const { ctrl, getController } = makeGetController(replay);
  const paneCalls = [];
  const updatePaneHeight = () => { paneCalls.push(1); };
  const ctl = new ChartInteractionController({
    container, renderer: r, getController, updatePaneHeight,
  });
  ctl.install();
  return { container, renderer: r, ctrl, paneCalls, ctl };
}

test('install wires a non-passive capturing wheel listener', () => {
  const { container } = build();
  assert.ok(container.has('wheel'));
  assert.equal(container.optsFor('wheel')?.passive, false);
  assert.equal(container.optsFor('wheel')?.capture, true);
});

test('wheel: handlePriceWheel true → preventDefault + stopPropagation（座標=clientXY−rect・updatePaneHeight 先行）', () => {
  const renderer = fakeRenderer({ handlePriceWheel: (x, y, dy) => { renderer.calls.handlePriceWheel.push([x, y, dy]); return true; } });
  const { container, paneCalls } = build({ renderer });
  let prevented = false; let stopped = false;
  container.fire('wheel', { clientX: 610, clientY: 180, deltaY: -100, preventDefault() { prevented = true; }, stopPropagation() { stopped = true; } });
  assert.deepEqual(renderer.calls.handlePriceWheel.at(-1), [610, 180, -100]);
  assert.equal(prevented, true);
  assert.equal(stopped, true);
  assert.ok(paneCalls.length >= 1, 'wheel 前に updatePaneHeight を再計算する');
});

test('wheel: handlePriceWheel false → preventDefault しない（本体ホイールを奪わない）', () => {
  const { container } = build(); // 既定 handlePriceWheel=false
  let prevented = false;
  container.fire('wheel', { clientX: 100, clientY: 180, deltaY: -100, preventDefault() { prevented = true; } });
  assert.equal(prevented, false);
});

test('dblclick: 価格軸上のみ resetPriceZoom（本体領域は無反応）', () => {
  const renderer = fakeRenderer({ isOverPriceAxis: (x) => x >= 600 });
  const { container } = build({ renderer });
  container.fire('dblclick', { clientX: 610, clientY: 100 });
  assert.equal(renderer.calls.resetPriceZoom, 1);
  container.fire('dblclick', { clientX: 100, clientY: 100 });
  assert.equal(renderer.calls.resetPriceZoom, 1);
});

test('本体ドラッグ縦パン: 価格ズーム中のみ panPriceByPixels（未ズームは無効）', () => {
  const renderer = fakeRenderer({ isPriceZoomed: () => false });
  const { container } = build({ renderer });
  container.fire('pointerdown', { button: 0, clientX: 100, clientY: 100 });
  container.fire('pointermove', { buttons: 1, clientX: 100, clientY: 140 });
  assert.deepEqual(renderer.calls.panPriceByPixels, [], '未ズームは縦パンしない');
  renderer.isPriceZoomed = () => true;
  container.fire('pointerdown', { button: 0, clientX: 100, clientY: 100 });
  container.fire('pointermove', { buttons: 1, clientX: 100, clientY: 130 }); // dy=30
  container.fire('pointermove', { buttons: 1, clientX: 100, clientY: 150 }); // dy=20
  assert.deepEqual(renderer.calls.panPriceByPixels, [30, 20]);
});

test('本体ドラッグ縦パン: 左ボタン以外・価格軸上・ボタン解放では開始/継続しない', () => {
  const renderer = fakeRenderer({ isPriceZoomed: () => true, isOverPriceAxis: (x) => x >= 600 });
  const { container } = build({ renderer });
  // 右ボタン → 開始しない
  container.fire('pointerdown', { button: 2, clientX: 100, clientY: 100 });
  container.fire('pointermove', { buttons: 1, clientX: 100, clientY: 130 });
  assert.deepEqual(renderer.calls.panPriceByPixels, [], '右ボタンは縦パンしない');
  // 価格軸上 → 開始しない（lwc ネイティブへ委ねる）
  container.fire('pointerdown', { button: 0, clientX: 610, clientY: 100 });
  container.fire('pointermove', { buttons: 1, clientX: 610, clientY: 130 });
  assert.deepEqual(renderer.calls.panPriceByPixels, [], '価格軸上は縦パンしない');
  // 本体領域・左ボタンで開始後、ボタン解放（buttons&1===0）で継続停止
  container.fire('pointerdown', { button: 0, clientX: 100, clientY: 100 });
  container.fire('pointermove', { buttons: 0, clientX: 100, clientY: 130 });
  assert.deepEqual(renderer.calls.panPriceByPixels, [], 'ボタン解放後は縦パンしない');
});

test('ISSUE-082: isReplay=true 相当でもスワイプ捕捉は発生せず通常の縦パンとして動作する', () => {
  // present からリプレイ配線を撤去した後、actor が isReplay=true を返しても
  //   setUserInteraction(false)（スワイプ捕捉）は呼ばれず、本体ドラッグは通常縦パンのまま。
  const renderer = fakeRenderer({ isPriceZoomed: () => true });
  const { container } = build({ renderer, replay: true });
  container.fire('pointerdown', { button: 0, clientX: 0, clientY: 100 });
  container.fire('pointermove', { buttons: 1, clientX: 40, clientY: 130 }); // dy=30
  container.fire('pointerup', {});
  assert.deepEqual(renderer.calls.userInteraction, [], 'スワイプ捕捉（setUserInteraction）は配線されない');
  assert.deepEqual(renderer.calls.panPriceByPixels, [30], '通常縦パンとして動作する');
});

test('container 不在/addEventListener 非対応でも install は例外を投げない（SSR/テスト防御）', () => {
  const { getController } = makeGetController(false);
  const ctl = new ChartInteractionController({
    container: {}, renderer: fakeRenderer(), getController, updatePaneHeight: () => {},
  });
  assert.doesNotThrow(() => ctl.install());
});
