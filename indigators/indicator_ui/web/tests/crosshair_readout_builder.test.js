// crosshair_readout_builder.test.js — 読み取りロール（ISSUE-479 Wave2 J-2c）の抽出を固定する。
//
// 何を固定するか:
//   R1 構造: クロスヘア読み取り・足の情報・スナップ候補の**本体**は chart_renderer.js に無い
//       （協働子への 1 行委譲）。読み取りロールの状態（当日 MP・ホバー座標ハンドラ・読み取り欄の
//       コールバック）も ChartRenderer は持たない（ISSUE-181「状態も一緒に移す」規律）。
//   C  計算量: 足の情報／スナップ候補を 1 回引くとき、系列データの取り出し発行数が
//       **出力に載った値の数と一致**する（作って捨てる取り出しが無い）。系列数を変えた 2 点で
//       比例係数を固定し、回数そのものは焼き込まない（絶対命令 2026-08-28）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { ChartRenderer } from '../js/adapter/front/chart_renderer.js';

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const FRONT = join(WEB, 'js', 'adapter', 'front');
const RENDERER_SRC = readFileSync(join(FRONT, 'chart_renderer.js'), 'utf8');

// 協働子へ本体を移した読み取りメソッド（ChartRenderer には委譲ラッパだけが残る）。
const READOUT_METHODS = [
  '_onCrosshairMove', 'setTfPeriodHoverHandler', '_emitReadout', '_buildReadoutDto',
  '_crosshairValue', '_slotValues', 'barInfoAt', 'snapCandidatesAt',
  '_timeAtCoordinate', 'setSessionMP',
];

// 協働子が所有する読み取りロールの状態（ChartRenderer 側に代入が残っていないこと）。
const READOUT_STATE = ['_sessionMP', '_onTfPeriodHover', '_onCrosshairReadout'];

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

test('R1: 読み取りの本体は chart_renderer.js に無い（協働子への委譲だけが残る）', () => {
  const offenders = [];
  for (const name of READOUT_METHODS) {
    const body = methodBody(name);
    if (body.length !== 1 || !body[0].includes('this._readout.')) {
      offenders.push(`${name}: ${body.length} 行 / ${body.join(' ')}`.slice(0, 160));
    }
  }
  assert.deepEqual(offenders, [],
    `読み取りの本体が chart_renderer.js に残っています:\n  ${offenders.join('\n  ')}`);
});

test('R1: 読み取りロールの状態は ChartRenderer が持たない（状態も一緒に移す）', () => {
  const offenders = READOUT_STATE.filter(
    (f) => new RegExp(`this\\.${f}\\s*=`).test(RENDERER_SRC),
  );
  assert.deepEqual(offenders, [],
    `読み取りロールの状態が ChartRenderer に残っています: ${offenders.join(', ')}`);
});

// ---------------------------------------------------------------------------
// 計算量ゲート（Test Spy＝系列データの取り出し発行回数を数える）
// ---------------------------------------------------------------------------

const BAR_TIME = 20;

function fakeSeries(spy, points) {
  return {
    _data: points ?? [],
    setData(p) { this._data = p ?? []; },
    data() { spy.seriesData += 1; return this._data; },
    update() {},
    applyOptions() {},
    createPriceLine(opt) { return { opt }; },
    removePriceLine() {},
  };
}

// 価格ペインへ overlay 系列 seriesCount 本を載せた構成を作る（すべて BAR_TIME に値を持つ）。
function build(seriesCount) {
  const spy = { seriesData: 0 };
  const panesArr = [];
  const makePane = () => {
    const pane = {
      paneIndex() { return panesArr.indexOf(pane); },
      getHeight() { return 400; },
      setStretchFactor() {}, setPreserveEmptyPane() {},
      addSeries() { return fakeSeries(spy); },
    };
    return pane;
  };
  panesArr.push(makePane());
  const chart = {
    panes() { return panesArr; },
    addPane() { const p = makePane(); panesArr.push(p); return p; },
    removePane() {},
    addSeries() { return fakeSeries(spy); },
    removeSeries() {}, applyOptions() {},
    timeScale() {
      return {
        width: () => 800, height: () => 28, fitContent() {}, applyOptions() {},
        coordinateToTime: () => BAR_TIME,
      };
    },
    subscribeCrosshairMove(fn) { chart._crosshair = fn; },
  };
  const main = fakeSeries(spy, []);
  main.getPane = () => panesArr[0];
  const renderer = new ChartRenderer({ chart, mainSeries: main, lwc: { LineSeries: { kind: 'Line' } } });
  renderer.setCandles([{
    time: BAR_TIME, open: 1, high: 2, low: 0.5, close: 1.5,
  }]);
  // overlay（価格ペイン重ね描き）として系列を載せる＝スナップ候補・足の情報の両方に載る。
  renderer.renderLine('ind#1', Array.from({ length: seriesCount }, (_, i) => ({
    name: `s${i}`, kind: 'line', style: 'solid', width: 1, color: '#0f0',
    data: [{ time: BAR_TIME, value: 10 + i }],
  })));
  return { renderer, spy, seriesCount };
}

function measure(rig, call) {
  const before = rig.spy.seriesData;
  const out = call(rig.renderer);
  return { seriesData: rig.spy.seriesData - before, out };
}

test('C: 足の情報を 1 回引くと、系列データの取り出しは出力に載った値の数と一致する', () => {
  // Arrange
  const rig = build(3);
  // Act
  const m = measure(rig, (r) => r.barInfoAt(100));
  // Assert: 出力に載った値の総数を数え、取り出しの発行数と突き合わせる（発行 − 使用 = 0）。
  const used = (m.out.indicators ?? []).reduce((a, g) => a + (g.values ?? []).length, 0);
  assert.equal(used, rig.seriesCount, '出力に系列値が載っていない（測定の前提が崩れている）');
  assert.equal(m.seriesData - used, 0,
    `系列データの取り出しが出力より多い（発行 ${m.seriesData} / 使用 ${used}）`);
});

test('C: 系列を増やしても 1 系列あたりの取り出しは変わらない（オーダーの表明）', () => {
  // Arrange: 系列数だけが違う 2 点。
  const few = build(2);
  const many = build(8);
  // Act
  const a = measure(few, (r) => r.barInfoAt(100));
  const b = measure(many, (r) => r.barInfoAt(100));
  // Assert
  assert.equal(
    b.seriesData / many.seriesCount, a.seriesData / few.seriesCount,
    `1 系列あたりの取り出しが系列数で変わっている（${a.seriesData}/${few.seriesCount} vs ${b.seriesData}/${many.seriesCount}）`,
  );
});

test('C: スナップ候補も同じ規律で引く（発行 − 出力に載った系列値 = 0）', () => {
  // Arrange
  const rig = build(4);
  // Act
  const m = measure(rig, (r) => r.snapCandidatesAt(100));
  // Assert: 候補のうち系列由来のものだけが取り出しの結果（水準線・OHLC は data() を使わない）。
  const used = (m.out ?? []).filter((c) => c.kind === 'series').length;
  assert.equal(used, rig.seriesCount, '出力に系列候補が載っていない（測定の前提が崩れている）');
  assert.equal(m.seriesData - used, 0,
    `系列データの取り出しが出力より多い（発行 ${m.seriesData} / 使用 ${used}）`);
});

test('C5: 系列ごとに二度引く変異を入れると計算量ゲートが赤になる（検出力の実測）', () => {
  // Arrange: 「1 系列につきもう 1 回データを引く」浪費を注入する（負の対照）。
  const rig = build(3);
  const readout = rig.renderer._readout;
  const inner = readout._slotValues.bind(readout);
  readout._slotValues = (slot, pick) => {
    for (const [key, series] of slot.lines) {
      pick(series, key);   // 結果を捨てる取り出し＝出力には載らない。
    }
    return inner(slot, pick);
  };
  // Act
  const m = measure(rig, (r) => r.barInfoAt(100));
  const used = (m.out.indicators ?? []).reduce((a, g) => a + (g.values ?? []).length, 0);
  // Assert: 発行 − 使用 が 0 でなくなる＝上の assert が落ちる条件。
  assert.notEqual(m.seriesData - used, 0,
    '浪費を注入しても計算量ゲートが同じ値を返している（ゲートが空振りしている）');
});
