// ChartInteractionController（adapter/front/chart_interaction_controller.js）の単体検証。
//
// 設計入力: ISSUE-040(a)。composition root の DI ルートに混入したチャート操作の振る舞い
//   （pointer swipe スクラブ・縦価格パン・wheel 価格ズーム・dblclick reset）を本コントローラへ抽出し、
//   composition root は new して install() するだけに縮小する（Composition Root は配線専用）。
// 観点: 抽出後も挙動は byte 不変。install() が container の pointer/wheel/dblclick を配線し、
//   注入依存（renderer / replayBar / getController / updatePaneHeight）へ既存と同一の呼出を行う。
// 構造: Arrange-Act-Assert。container/renderer/replayBar は Fake を注入（DOM/実描画非依存）。

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

// replayBar Fake（currentIndex / scrubToLogical のスパイ）。
function fakeReplayBar(startIdx = 0) {
  const scrubs = [];
  return { scrubs, currentIndex: () => startIdx, scrubToLogical: (i) => { scrubs.push(i); } };
}

// getController Fake（リプレイ ON/OFF を切替可能）。
function makeGetController(isReplay = false) {
  const ctrl = { _marketProfile: { isReplay: () => isReplay } };
  return { ctrl, getController: () => ctrl };
}

function build({ replay = false, renderer, replayBar, startIdx = 0 } = {}) {
  const container = fakeContainer();
  const r = renderer || fakeRenderer();
  const rb = replayBar || fakeReplayBar(startIdx);
  const { ctrl, getController } = makeGetController(replay);
  const paneCalls = [];
  const updatePaneHeight = () => { paneCalls.push(1); };
  const ctl = new ChartInteractionController({
    container, renderer: r, replayBar: rb, getController, updatePaneHeight,
  });
  ctl.install();
  return { container, renderer: r, replayBar: rb, ctrl, paneCalls, ctl };
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

test('本体ドラッグ縦パン: 非リプレイ・価格ズーム中のみ panPriceByPixels（未ズームは無効）', () => {
  const renderer = fakeRenderer({ isPriceZoomed: () => false });
  const { container } = build({ renderer, replay: false });
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
  const { container } = build({ renderer, replay: false });
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

test('リプレイ swipe: pointerdown で開始 index 記録・pointermove で dIdx=round((x−startX)/px) を scrubToLogical（index 変化時のみ）', () => {
  const replayBar = fakeReplayBar(5); // 開始 index=5
  const renderer = fakeRenderer({ pixelsPerBar: () => 10 });
  const { container } = build({ renderer, replayBar, replay: true });
  container.fire('pointerdown', { button: 0, clientX: 0, clientY: 100 });
  assert.deepEqual(renderer.calls.userInteraction, [false], 'スワイプ開始で通常操作停止');
  container.fire('pointermove', { buttons: 1, clientX: 25, clientY: 100 }); // idx=5+round(2.5)=8
  assert.deepEqual(replayBar.scrubs, [8]);
  container.fire('pointermove', { buttons: 1, clientX: 25, clientY: 100 }); // 同 index → 再取得しない
  assert.deepEqual(replayBar.scrubs, [8], '同 index は冗長スクラブしない');
  container.fire('pointerup', {});
  assert.deepEqual(renderer.calls.userInteraction, [false, true], 'スワイプ終了で通常操作復元');
});

test('リプレイ swipe: 縦成分は価格ズーム中のみ panPriceByPixels（横スクラブとは独立）', () => {
  const replayBar = fakeReplayBar(0);
  const renderer = fakeRenderer({ pixelsPerBar: () => 10, isPriceZoomed: () => true });
  const { container } = build({ renderer, replayBar, replay: true });
  container.fire('pointerdown', { button: 0, clientX: 0, clientY: 100 });
  container.fire('pointermove', { buttons: 1, clientX: 0, clientY: 130 }); // 純縦 dy=30・index 不変
  assert.deepEqual(replayBar.scrubs, [], '純縦はスクラブしない');
  assert.deepEqual(renderer.calls.panPriceByPixels, [30], 'ズーム中は縦成分で価格パン');
});

test('リプレイ OFF: swipe は開始しない（左ボタンでも無反応）', () => {
  const replayBar = fakeReplayBar(3);
  const { container, renderer } = build({ replayBar, replay: false });
  container.fire('pointerdown', { button: 0, clientX: 0, clientY: 100 });
  container.fire('pointermove', { buttons: 1, clientX: 50, clientY: 100 });
  assert.deepEqual(replayBar.scrubs, [], 'リプレイ OFF はスクラブしない');
  assert.ok(!renderer.calls.userInteraction.includes(false), 'リプレイ OFF はスワイプ捕捉しない');
});

test('リプレイ swipe: 左ボタン以外では開始しない', () => {
  const replayBar = fakeReplayBar(3);
  const { container, renderer } = build({ replayBar, replay: true });
  container.fire('pointerdown', { button: 2, clientX: 0, clientY: 100 });
  container.fire('pointermove', { buttons: 1, clientX: 50, clientY: 100 });
  assert.deepEqual(replayBar.scrubs, [], '右ボタンはスワイプ開始しない');
  assert.ok(!renderer.calls.userInteraction.includes(false));
});

test('container 不在/addEventListener 非対応でも install は例外を投げない（SSR/テスト防御）', () => {
  const { getController } = makeGetController(false);
  const ctl = new ChartInteractionController({
    container: {}, renderer: fakeRenderer(), replayBar: fakeReplayBar(), getController, updatePaneHeight: () => {},
  });
  assert.doesNotThrow(() => ctl.install());
});
