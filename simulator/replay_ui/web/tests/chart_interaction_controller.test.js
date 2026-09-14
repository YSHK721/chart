// ChartInteractionController（adapter/front/chart_interaction_controller.js）の replay 側検証。
//
// ISSUE-123（値渡し是正）: 旧・独立コピーを廃止し、present と同一実体（symlink 単一ソース）を参照する。
//   旧コピー固有だった「MP リプレイモード中は縦パンを開始しない」ゲートは isVerticalPanBlocked
//   オプション注入（replay composition root が供給）で維持する。本テストは replay 配線視点の観点
//   （wheel/dblclick/縦パン＋ゲート注入）を固定する。ISSUE-108（常時縦パン）は symlink 化により
//   replay へも自動伝播する（旧コピーの「ズーム中のみ縦パン」は撤去済み仕様）。
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
    // ISSUE-440: 幾何が動いたら凡例を引き直す面（ChartInteractionController が pointermove /
    //   pointerup で呼ぶ）。ダブルも契約を満たす（部分実装を通さない＝fail-close）。
    syncPaneGeometry: () => false,
    resetPriceZoom: () => { calls.resetPriceZoom = (calls.resetPriceZoom || 0) + 1; },
    setPaneHeight: (h) => { calls.setPaneHeight.push(h); },
  };
  return Object.assign(r, overrides);
}

// getController Fake（リプレイ ON/OFF を切替可能）。controller._marketProfile.isReplay() を遅延参照する。
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
    // composition root と同型のゲート注入（controller._marketProfile.isReplay() を遅延参照）。
    isVerticalPanBlocked: () => !!(ctrl && ctrl._marketProfile
      && typeof ctrl._marketProfile.isReplay === 'function' && ctrl._marketProfile.isReplay()),
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
  let prevented = false; let stopped = false;
  container.fire('wheel', { clientX: 100, clientY: 180, deltaY: -100, preventDefault() { prevented = true; }, stopPropagation() { stopped = true; } });
  assert.equal(prevented, false);
  assert.equal(stopped, false, '本体領域では stopPropagation しない（本体ホイール既存挙動へ非干渉）');
});

test('dblclick: 価格軸上のみ resetPriceZoom（本体領域は無反応）', () => {
  const renderer = fakeRenderer({ isOverPriceAxis: (x) => x >= 600 });
  const { container } = build({ renderer });
  container.fire('dblclick', { clientX: 610, clientY: 100 });
  assert.equal(renderer.calls.resetPriceZoom, 1);
  container.fire('dblclick', { clientX: 100, clientY: 100 });
  assert.equal(renderer.calls.resetPriceZoom, 1);
});

test('本体ドラッグ縦パン: 非リプレイは全体表示（未ズーム）でも常時 panPriceByPixels（ISSUE-108 伝播）', () => {
  // ISSUE-123 の symlink 化により present の ISSUE-108（常時縦パン）が replay へも自動伝播する。
  const renderer = fakeRenderer({ isPriceZoomed: () => false });
  const { container } = build({ renderer, replay: false });
  container.fire('pointerdown', { button: 0, clientX: 100, clientY: 100 });
  container.fire('pointermove', { buttons: 1, clientX: 100, clientY: 140 }); // dy=40
  assert.deepEqual(renderer.calls.panPriceByPixels, [40], '全体表示（自動スケール）でも縦パンする');
  renderer.isPriceZoomed = () => true;
  container.fire('pointerdown', { button: 0, clientX: 100, clientY: 100 });
  container.fire('pointermove', { buttons: 1, clientX: 100, clientY: 130 }); // dy=30
  container.fire('pointermove', { buttons: 1, clientX: 100, clientY: 150 }); // dy=20
  assert.deepEqual(renderer.calls.panPriceByPixels, [40, 30, 20], 'ズーム中も従来どおり縦パンする');
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

test('本体ドラッグ縦パン: リプレイ中は開始しない（isVerticalPanBlocked ゲート注入・ISSUE-123）', () => {
  const renderer = fakeRenderer({ isPriceZoomed: () => true });
  const { container } = build({ renderer, replay: true });
  container.fire('pointerdown', { button: 0, clientX: 100, clientY: 100 });
  container.fire('pointermove', { buttons: 1, clientX: 100, clientY: 130 });
  assert.deepEqual(renderer.calls.panPriceByPixels, [], 'リプレイ中は本体縦パンを開始しない');
});

test('container 不在/addEventListener 非対応でも install は例外を投げない（SSR/テスト防御）', () => {
  const { getController } = makeGetController(false);
  const ctl = new ChartInteractionController({
    container: {}, renderer: fakeRenderer(), getController, updatePaneHeight: () => {},
  });
  assert.doesNotThrow(() => ctl.install());
});
