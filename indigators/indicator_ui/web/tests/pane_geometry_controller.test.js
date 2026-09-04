// pane_geometry_controller.test.js — ペイン幾何ロール（ISSUE-479 Wave2 J-2）の抽出を固定する。
//
// 何を固定するか:
//   R1 構造: 幾何メソッドの**本体**は chart_renderer.js に無い（協働子への 1 行委譲だけ）。
//       幾何ロールの状態（ペイン採番・目標配分・版面総高・幾何指紋・再確認予約）も
//       ChartRenderer は持たない（ISSUE-181「状態も一緒に移す」規律）。
//   C  計算量: クロスヘア 1 イベントで発行する upstream 幾何問い合わせが、**ペイン数の
//       一定倍**に留まる（系列数では増えない）。回数そのものは焼き込まない——固定するのは
//       「無駄の不在」であって実装詳細ではない（絶対命令 2026-08-28）。
//
// 抽出は挙動不変（byte 等価）の作業であり、本ファイルの計算量ゲートは抽出の前後で同じ値を
//   指す回帰錨として置く。緩んでいないことは C5（負の対照）で実測する。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { ChartRenderer } from '../js/adapter/front/chart_renderer.js';

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const FRONT = join(WEB, 'js', 'adapter', 'front');
const RENDERER_SRC = readFileSync(join(FRONT, 'chart_renderer.js'), 'utf8');

// 協働子へ本体を移した幾何メソッド（ChartRenderer には委譲ラッパだけが残る）。
const GEOMETRY_METHODS = [
  '_emitPaneLegend', '_scheduleGeometryRecheck', 'movePane', 'setPaneOrderObserver',
  'paneLegendModel', '_paneKeysOrdered', '_paneHeights', '_paneAreaHeight',
  'setPaneAreaHeightProvider', '_paneSeparatorPx', '_paneGeometrySignature',
  '_applyGoalRatios', 'syncPaneGeometry', '_notePaneGeometry',
  'refreshPaneLegendIfGeometryChanged', '_paneTops', 'paneOrderInstanceIds',
  '_slotPaneIndex', '_pricePaneIndex', '_isPaneMovable', 'paneIndexAtCoordinate',
];

// 協働子が所有する幾何ロールの状態（ChartRenderer 側に代入が残っていないこと）。
const GEOMETRY_STATE = [
  '_paneKeys', '_paneKeySeq', '_onPaneOrder', '_paneAreaHeightProvider',
  '_lastPaneGeometrySig', '_geometryRecheckPending', '_paneGoal', '_lastPaneArea',
  '_appliedPaneHeights',
];

// chart_renderer.js 内の当該メソッド本体（コメント・空行を除いた実行行）を返す。
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

test('R1: 幾何メソッドの本体は chart_renderer.js に無い（協働子への委譲だけが残る）', () => {
  const offenders = [];
  for (const name of GEOMETRY_METHODS) {
    const body = methodBody(name);
    if (body.length !== 1 || !body[0].includes('this._paneGeom.')) {
      offenders.push(`${name}: ${body.length} 行 / ${body.join(' ')}`.slice(0, 160));
    }
  }
  assert.deepEqual(offenders, [],
    `幾何の本体が chart_renderer.js に残っています:\n  ${offenders.join('\n  ')}`);
});

test('R1: 幾何ロールの状態は ChartRenderer が持たない（状態も一緒に移す）', () => {
  const offenders = GEOMETRY_STATE.filter(
    (f) => new RegExp(`this\\.${f}\\s*=`).test(RENDERER_SRC),
  );
  assert.deepEqual(offenders, [],
    `幾何ロールの状態が ChartRenderer に残っています: ${offenders.join(', ')}`);
});

// ---------------------------------------------------------------------------
// 計算量ゲート（Test Spy＝upstream 幾何 API の発行回数を数える）
// ---------------------------------------------------------------------------

function fakeSeries() {
  return {
    _data: [], _options: {}, _priceLines: [],
    setData(points) { this._data = points ?? []; },
    data() { return this._data ?? []; },
    update() {},
    applyOptions(o) { Object.assign(this._options, o); },
    createPriceLine(opt) { const pl = { opt }; this._priceLines.push(pl); return pl; },
    removePriceLine(pl) { this._priceLines = this._priceLines.filter((x) => x !== pl); },
  };
}

// ペイン数 paneCount（価格ペイン 1 + 指標ペイン paneCount-1）の構成を作り、
//   upstream 幾何 API（panes / getHeight）の発行回数を数える spy を仕込む。
function build(paneCount) {
  const spy = { panes: 0, getHeight: 0 };
  const panesArr = [];
  const heights = [];
  const makePane = () => {
    const pane = {
      _series: [],
      paneIndex() { return panesArr.indexOf(pane); },
      getHeight() { spy.getHeight += 1; return heights[panesArr.indexOf(pane)] ?? 0; },
      stretch: 1,
      setStretchFactor(v) { pane.stretch = v; },
      setPreserveEmptyPane() {},
      addSeries() { const s = fakeSeries(); s._pane = pane; pane._series.push(s); return s; },
    };
    return pane;
  };
  panesArr.push(makePane()); heights.push(400);
  const chart = {
    _crosshair: () => {},
    panes() { spy.panes += 1; return panesArr; },
    addPane() { const p = makePane(); panesArr.push(p); heights.push(100); return p; },
    removePane(i) { panesArr.splice(i, 1); },
    addSeries() { return panesArr[0].addSeries(); },
    removeSeries() {}, applyOptions() {},
    timeScale() {
      return { width: () => 800, height: () => 28, fitContent() {}, coordinateToTime: () => null };
    },
    subscribeCrosshairMove(fn) { chart._crosshair = fn; },
  };
  const main = fakeSeries();
  main.getPane = () => panesArr[0];
  const emitted = [];
  const renderer = new ChartRenderer({
    chart, mainSeries: main, lwc: { LineSeries: { kind: 'Line' } },
    onPaneLegend: (m) => emitted.push(m),
  });
  for (let i = 1; i < paneCount; i += 1) {
    renderer.renderLine(`osc#${i}`, [{
      name: 'osc', kind: 'line', style: 'solid', width: 1, color: '#0f0',
      data: [{ time: 20, value: 55 }],
    }], { pane: true });
  }
  renderer.setPaneHeight(500);
  return {
    renderer, chart, spy, emitted, paneCount, seriesCount: paneCount - 1,
  };
}

// クロスヘア 1 イベントぶんの発行数（＋掲載ペイン数）を測る。
function measureOneCrosshair(rig) {
  const before = { ...rig.spy };
  rig.emitted.length = 0;
  rig.chart._crosshair({ time: 20, point: { x: 1, y: 2 }, seriesData: new Map() });
  const model = rig.emitted[rig.emitted.length - 1];
  return {
    panes: rig.spy.panes - before.panes,
    getHeight: rig.spy.getHeight - before.getHeight,
    groups: model ? model.groups.length : 0,
    emits: rig.emitted.length,
  };
}

test('C1/C2: クロスヘア 1 イベントの幾何問い合わせはペイン数の一定倍（系列数では増えない）', () => {
  // Arrange: ペイン 2 面と 6 面の 2 点（オーダーの表明は 2 点以上で固定する）。
  const small = build(2);
  const large = build(6);
  // Act
  const a = measureOneCrosshair(small);
  const b = measureOneCrosshair(large);
  // Assert
  assert.equal(a.emits, 1, 'クロスヘア 1 イベントで凡例 DTO は 1 回だけ発行する');
  assert.equal(b.emits, 1, 'クロスヘア 1 イベントで凡例 DTO は 1 回だけ発行する');
  // panes() は「今あるペインの一覧」を引くだけ＝ペインが増えても発行は増えない（定数）。
  assert.equal(b.panes, a.panes, 'ペインを増やすと panes() の発行が増えている（オーダーが崩れた）');
  // getHeight() は各ペインを 1 回ずつ測る仕事なのでペイン数に比例する。比例係数（1 ペインあたりの
  //   問い合わせ回数）を **2 点で一致させる**ことで、系列数や項目数に引きずられた過剰発行を落とす。
  //   係数そのものは期待値に焼き込まない（焼き込むと現在の実装詳細が仕様へ昇格する）。
  assert.equal(a.getHeight % small.paneCount, 0, 'ペイン単位で測っていない');
  assert.equal(
    b.getHeight / large.paneCount, a.getHeight / small.paneCount,
    `1 ペインあたりの高さ問い合わせがペイン数で変わっている（${a.getHeight}/${small.paneCount} vs ${b.getHeight}/${large.paneCount}）`,
  );
  // 出力（DTO 掲載ペイン数）は系列の在席で決まる＝測った仕事が捨てられていない。
  assert.equal(a.groups, small.seriesCount);
  assert.equal(b.groups, large.seriesCount);
});

test('C3: ホバーを何度動かしても幾何の再確認予約は 1 本しか作らない（多重予約を作らない）', () => {
  // Arrange: 予約先（rAF）を捕まえるだけで実行しない＝予約の本数だけを見る。
  const scheduled = [];
  // 予約先を差し替える前に組み立てる（組み立て時の発行は本検定の対象ではない）。
  const rig = build(3);
  const original = globalThis.requestAnimationFrame;
  globalThis.requestAnimationFrame = (fn) => { scheduled.push(fn); return scheduled.length; };
  try {
    // Act: 8 回ホバーを動かす。
    for (let i = 0; i < 8; i += 1) {
      rig.chart._crosshair({ time: 20 + i, point: { x: i, y: 2 }, seriesData: new Map() });
    }
    // Assert
    assert.equal(scheduled.length, 1, 'ホバーのたびに再確認を予約している（予約が積み上がる）');
  } finally {
    globalThis.requestAnimationFrame = original;
  }
});

test('C5: 系列ごとに幾何を測り直す変異を入れると C1/C2 が赤になる（検出力の実測）', () => {
  // Arrange: 「系列 1 本につき 1 回、ペイン高を測り直す」浪費を注入する（負の対照）。
  const inject = (rig) => {
    const geom = rig.renderer._paneGeom;
    const inner = geom.paneLegendModel.bind(geom);
    geom.paneLegendModel = (...args) => {
      for (let i = 0; i < rig.seriesCount; i += 1) {
        geom._paneHeights();
      }
      return inner(...args);
    };
    return rig;
  };
  const small = inject(build(2));
  const large = inject(build(6));
  // Act
  const a = measureOneCrosshair(small);
  const b = measureOneCrosshair(large);
  // Assert: 比例係数が 2 点で食い違う＝C1/C2 の assert が落ちる条件。
  assert.notEqual(
    b.getHeight / large.paneCount, a.getHeight / small.paneCount,
    '浪費を注入しても計算量ゲートが同じ値を返している（ゲートが空振りしている）',
  );
});
