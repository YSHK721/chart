// chrome_color_controller.test.js — クロム色ロール（ISSUE-479 Wave2 J-2b）の抽出を固定する。
//
// 何を固定するか:
//   R1 構造: クロム色の導出・押し出しの**本体**は chart_renderer.js に無い（協働子への 1 行委譲）。
//       クロム色ロールの状態（保持色・表示モード 3 種・購読者・背景 type の捕捉）も
//       ChartRenderer は持たない（ISSUE-181「状態も一緒に移す」規律）。
//   C4 計算量: applyChromeColors 1 回で upstream へ押し出す回数が「影響対象の数」と一致し、
//       背景プリミティブや購読者の数を増やしても増えない。回数は焼き込まない
//       （固定するのは無駄の不在であって実装詳細ではない・絶対命令 2026-08-28）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { ChartRenderer } from '../js/adapter/front/chart_renderer.js';

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const FRONT = join(WEB, 'js', 'adapter', 'front');
const RENDERER_SRC = readFileSync(join(FRONT, 'chart_renderer.js'), 'utf8');

// 協働子へ本体を移したクロム色メソッド（ChartRenderer には委譲ラッパだけが残る）。
const CHROME_METHODS = [
  'applyChromeColors', '_deriveCandleOptions', '_deriveChartOptions', '_deriveBackground',
  '_deriveDimmedCandles', '_pushChartOptions', '_pushCandleOptions', '_pushDimmedCandles',
  'addChromeObserver', '_notifyChromeObservers', '_pushChromeToBackgroundPrimitive',
  'setCandleTransparency', 'setAnalysisTint', 'dimCandlesOutsidePair', 'restoreCandles',
];

// 協働子が所有するクロム色ロールの状態（ChartRenderer 側に代入が残っていないこと）。
const CHROME_STATE = [
  '_chromeSlots', '_candlesTransparent', '_analysisTintOn', '_dimRange',
  '_chromeObservers', '_analysisTintBase',
];

function methodBody(name) {
  const lines = RENDERER_SRC.split('\n');
  const start = lines.findIndex((l) => new RegExp(`^  ${name}\\(`).test(l));
  assert.notEqual(start, -1, `${name} が chart_renderer.js に見つからない（公開面が消えている）`);
  const body = [];
  for (let i = start + 1; i < lines.length; i += 1) {
    if (lines[i] === '  }') break;
    const code = lines[i].trim();
    if (code === '' || code.startsWith('//') || code.startsWith('*') || code.startsWith('/*')) continue;
    body.push(code);
  }
  return body;
}

test('R1: クロム色の本体は chart_renderer.js に無い（協働子への委譲だけが残る）', () => {
  const offenders = [];
  for (const name of CHROME_METHODS) {
    const body = methodBody(name);
    if (body.length !== 1 || !body[0].includes('this._chrome.')) {
      offenders.push(`${name}: ${body.length} 行 / ${body.join(' ')}`.slice(0, 160));
    }
  }
  assert.deepEqual(offenders, [],
    `クロム色の本体が chart_renderer.js に残っています:\n  ${offenders.join('\n  ')}`);
});

test('R1: クロム色ロールの状態は ChartRenderer が持たない（状態も一緒に移す）', () => {
  const offenders = CHROME_STATE.filter(
    (f) => new RegExp(`this\\.${f}\\s*=`).test(RENDERER_SRC),
  );
  assert.deepEqual(offenders, [],
    `クロム色ロールの状態が ChartRenderer に残っています: ${offenders.join(', ')}`);
});

// ---------------------------------------------------------------------------
// 計算量ゲート（Test Spy＝upstream 押し出しの発行回数を数える）
// ---------------------------------------------------------------------------

// 背景プリミティブ数 primitiveCount・購読者数 observerCount の構成を作る。
function build({ primitiveCount = 0, observerCount = 0 } = {}) {
  const spy = {
    chartApplyOptions: 0, seriesApplyOptions: 0, seriesSetData: 0,
    primitivePush: 0, observerCalls: 0,
  };
  const chart = {
    applyOptions() { spy.chartApplyOptions += 1; },
    options() { return { layout: { background: { type: 'solid', color: '#000' } } }; },
    panes() { return []; },
    addSeries() { return null; },
    removeSeries() {}, addPane() { return null; }, removePane() {},
    timeScale() { return { width: () => 800, height: () => 28, applyOptions() {} }; },
    subscribeCrosshairMove() {},
  };
  const main = {
    _data: [],
    applyOptions() { spy.seriesApplyOptions += 1; },
    setData(points) { spy.seriesSetData += 1; this._data = points ?? []; },
    data() { return this._data; },
    attachPrimitive() {},
    getPane() { return null; },
  };
  const renderer = new ChartRenderer({ chart, mainSeries: main, lwc: {} });
  for (let i = 0; i < primitiveCount; i += 1) {
    renderer.attachBackgroundPrimitive(`bg#${i}`, () => ({
      setChromeColors() { spy.primitivePush += 1; },
    }));
  }
  for (let i = 0; i < observerCount; i += 1) {
    renderer.addChromeObserver(() => { spy.observerCalls += 1; });
  }
  return { renderer, spy, primitiveCount, observerCount };
}

// applyChromeColors 1 回ぶんの発行数を測る。
function measureApply(rig) {
  const before = { ...rig.spy };
  rig.renderer.applyChromeColors({ layoutBackground: '#101010' });
  return {
    chartApplyOptions: rig.spy.chartApplyOptions - before.chartApplyOptions,
    seriesApplyOptions: rig.spy.seriesApplyOptions - before.seriesApplyOptions,
    seriesSetData: rig.spy.seriesSetData - before.seriesSetData,
    primitivePush: rig.spy.primitivePush - before.primitivePush,
    observerCalls: rig.spy.observerCalls - before.observerCalls,
  };
}

test('C4: applyChromeColors 1 回の押し出しは影響対象の数と一致する（余分な発行が無い）', () => {
  // Arrange: 背景プリミティブ 1 本・購読者 1 人。
  const rig = build({ primitiveCount: 1, observerCount: 1 });
  // Act
  const d = measureApply(rig);
  // Assert: 出力は「チャート options」「ローソク options」の 2 つ。減光レンジが無いので
  //   per-bar 色の書き戻し（setData）は**発行しない**＝作って捨てる計算が無い。
  assert.equal(d.chartApplyOptions, 1, 'チャート options の押し出しが 1 回でない');
  assert.equal(d.seriesApplyOptions, 1, 'ローソク options の押し出しが 1 回でない');
  assert.equal(d.seriesSetData, 0, '減光していないのに per-bar データを書き戻している');
  // 配信先はそれぞれちょうど 1 回（取りこぼしも二重配信もしない）。
  assert.equal(d.primitivePush, rig.primitiveCount);
  assert.equal(d.observerCalls, rig.observerCount);
});

test('C4: 配信先を増やしても upstream への押し出しは増えない（オーダーの表明）', () => {
  // Arrange: 配信先の数だけが違う 2 点。
  const few = build({ primitiveCount: 1, observerCount: 1 });
  const many = build({ primitiveCount: 4, observerCount: 5 });
  // Act
  const a = measureApply(few);
  const b = measureApply(many);
  // Assert: options の押し出しは配信先の数に依らない（配信は配信、押し出しは押し出し）。
  assert.equal(b.chartApplyOptions, a.chartApplyOptions);
  assert.equal(b.seriesApplyOptions, a.seriesApplyOptions);
  assert.equal(b.seriesSetData, a.seriesSetData);
  // 配信先へはそれぞれちょうど 1 回ずつ（数に比例し、比例係数は 1）。
  assert.equal(b.primitivePush, many.primitiveCount);
  assert.equal(b.observerCalls, many.observerCount);
});

test('C5: 配信先ごとに options を押し出す変異を入れると C4 が赤になる（検出力の実測）', () => {
  // Arrange: 「背景プリミティブ 1 本につき 1 回、ローソク options を押し直す」浪費を注入する。
  const inject = (rig) => {
    const chrome = rig.renderer._chrome;
    const inner = chrome._pushChromeToBackgroundPrimitive.bind(chrome);
    chrome._pushChromeToBackgroundPrimitive = (primitive) => {
      chrome._pushCandleOptions();
      return inner(primitive);
    };
    return rig;
  };
  const few = inject(build({ primitiveCount: 1 }));
  const many = inject(build({ primitiveCount: 4 }));
  // Act
  const a = measureApply(few);
  const b = measureApply(many);
  // Assert: 配信先の数で押し出し回数が動く＝C4 の assert が落ちる条件。
  assert.notEqual(b.seriesApplyOptions, a.seriesApplyOptions,
    '浪費を注入しても計算量ゲートが同じ値を返している（ゲートが空振りしている）');
});
