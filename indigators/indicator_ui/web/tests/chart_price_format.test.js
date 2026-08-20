// 価格軸・現在値・クロスヘアの表示桁を銘柄仕様（台帳）に従わせる（ISSUE-368 スライス S-7・A-3）。
//
// 設計入力（唯一の仕様源）: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md
//   「承認結果（2026-08-20・依頼者裁定）」A-3（**価格軸の表示桁を `digits` に合わせる**。
//    lwc `priceFormat.minMove/precision` を `chart_bootstrap` で設定する）、
//   同「承認事項」A-3 の根拠（現在アプリは `priceFormat` 未設定で vendor 既定に委ねている＝実測 0 件。
//    値だけ丸めると「軸は 2 桁・入る値は整数」の乖離が残る）。
//
// 除去する原因: 表示桁を front が自称も既定任せもしている状態。桁と刻みの権威は marketdata 台帳
//   （`marketdata/symbol_spec.py` → 生成物 `domain/symbol_spec_generated.js`）ただ 1 つで、front は
//   解決結果を**受け取って渡すだけ**にする。よって本検定は桁・刻みの数値を 1 つも書かず、
//   期待値は台帳（`lookupSymbolSpec`）から引く。
//
// vendor 既定（実測・v5.2.0 バンドル）: 系列共通既定に
//   `priceFormat:{type:"price",precision:2,minMove:.01}` が 1 か所だけ存在する。よって
//   **未設定＝小数 2 桁**であり、JP225（digits=0）では軸と値がずれる。
//
// 構造: Arrange-Act-Assert（AAA）。fake lwc が addSeries の**引数**を捕まえる
//   （「渡してはいるが繋がっていない」を見逃さないため、結線点 composeChartShell からも通す）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { createChartWithMainSeries } from '../js/adapter/front/chart_bootstrap.js';
import { composeChartShell } from '../js/adapter/front/chart_app_wiring.js';
import { lookupSymbolSpec } from '../js/adapter/front/symbol_spec_catalog.js';

// 台帳から引く（本ファイルに桁・刻みの数値を書かない）。
const JP225_REF = 'jp225_tick';
const TSLA_REF = 'sample';
const UNKNOWN_REF = 'unknown_dataset_ref';

// 最小の要素スタブ（どのセレクタにも要素を返す＝共有配線が触る DOM をすべて吸収する）。
function el() {
  const node = {
    style: {},
    dataset: {},
    children: [],
    textContent: '',
    innerHTML: '',
    classList: {
      add() {}, remove() {}, toggle() {}, contains: () => false,
    },
    appendChild(c) { node.children.push(c); return c; },
    insertBefore(c) { node.children.push(c); return c; },
    addEventListener() {},
    removeEventListener() {},
    setAttribute() {},
    removeAttribute() {},
    getAttribute: () => null,
    remove() {},
    focus() {},
    closest: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
    getBoundingClientRect: () => ({
      top: 0, left: 0, width: 100, height: 100,
    }),
  };
  return node;
}

// addSeries の第 2 引数（系列オプション）を捕まえる fake lwc。
function makeEnv() {
  const seriesArgs = [];
  const series = {
    applyOptions() {}, setData() {}, priceScale: () => ({ applyOptions() {} }),
  };
  const chart = {
    applyOptions() {},
    addSeries: (kind, options) => { seriesArgs.push({ kind, options }); return series; },
    addPane: () => ({ addSeries: () => series, setStretchFactor() {}, setPreserveEmptyPane() {} }),
    panes: () => [],
    timeScale: () => ({ height: () => 20, subscribeVisibleLogicalRangeChange() {} }),
    subscribeCrosshairMove() {},
  };
  const lwc = {
    ColorType: { Solid: 'solid' },
    CrosshairMode: { Normal: 0 },
    CandlestickSeries: 'C',
    LineSeries: 'L',
    HistogramSeries: 'H',
    createChart: () => chart,
  };
  const anchors = new Map();
  const doc = {
    documentElement: { style: { setProperty() {}, removeProperty() {} } },
    createElement: () => el(),
    getElementById: () => null,
    querySelector: (sel) => {
      if (!anchors.has(sel)) { anchors.set(sel, el()); }
      return anchors.get(sel);
    },
    querySelectorAll: () => [],
    addEventListener() {},
    removeEventListener() {},
    body: el(),
  };
  return {
    lwc,
    doc,
    seriesArgs,
    container: { clientHeight: 400 },
    storage: { getItem: () => null, setItem() {}, removeItem() {} },
    fetch: async () => ({ ok: true, status: 200, json: async () => ({}) }),
  };
}

// ---------------------------------------------------------------------------
// 単体（chart_bootstrap: spec を引数で受け、渡された分だけ設定する）
// ---------------------------------------------------------------------------

test('TC-PF01 銘柄仕様を渡すとメイン系列に priceFormat（precision=digits / minMove=tick）が付く', () => {
  // Arrange
  const env = makeEnv();
  const spec = lookupSymbolSpec(JP225_REF);
  // Act
  createChartWithMainSeries({ lwc: env.lwc, container: env.container, symbolSpec: spec });
  // Assert
  assert.equal(env.seriesArgs.length, 1, 'メイン系列が 1 本だけ生成されていない');
  assert.deepEqual(env.seriesArgs[0].options.priceFormat, {
    type: 'price', precision: spec.digits, minMove: spec.tick,
  }, '表示桁・刻みが台帳の値になっていない');
});

test('TC-PF02 銘柄仕様が無いときは priceFormat を設定しない（vendor 既定のまま＝従来の挙動）', () => {
  // Arrange
  const env = makeEnv();
  // Act
  createChartWithMainSeries({ lwc: env.lwc, container: env.container });
  // Assert: 無音で誤った桁に固定しない（「決められない」を「0 桁」と偽らない）。
  assert.equal(
    Object.hasOwn(env.seriesArgs[0].options, 'priceFormat'),
    false,
    '仕様が無いのに桁を決めている',
  );
});

test('TC-PF03 壊れた銘柄仕様（tick<=0・digits が整数でない）でも桁を決めない', () => {
  // Arrange
  const broken = [
    { symbol: 'X', tick: 0, digits: 0 },
    { symbol: 'X', tick: -1, digits: 0 },
    { symbol: 'X', tick: Number.NaN, digits: 0 },
    { symbol: 'X', tick: 1, digits: 1.5 },
    { symbol: 'X', tick: 1, digits: -1 },
  ];
  for (const symbolSpec of broken) {
    const env = makeEnv();
    // Act
    createChartWithMainSeries({ lwc: env.lwc, container: env.container, symbolSpec });
    // Assert
    assert.equal(
      Object.hasOwn(env.seriesArgs[0].options, 'priceFormat'),
      false,
      `壊れた仕様で桁を決めている: ${JSON.stringify(symbolSpec)}`,
    );
  }
});

// ---------------------------------------------------------------------------
// 結線（composeChartShell が datasetRef から解決して渡しているか）
// ---------------------------------------------------------------------------

test('TC-PF04 結線: datasetRef から解決した銘柄仕様が系列の priceFormat になる（JP225）', async () => {
  // Arrange
  const env = makeEnv();
  const spec = lookupSymbolSpec(JP225_REF);
  // Act
  await composeChartShell({
    lwc: env.lwc,
    container: env.container,
    doc: env.doc,
    storage: env.storage,
    fetch: env.fetch,
    datasetRef: JP225_REF,
    recentBars: 300,
  });
  // Assert
  assert.deepEqual(env.seriesArgs[0].options.priceFormat, {
    type: 'price', precision: spec.digits, minMove: spec.tick,
  }, '結線されていない（渡してはいるが繋がっていない）');
});

test('TC-PF05 結線: 別銘柄の ref では台帳どおり別の桁になる（front が桁を自称しない）', async () => {
  // Arrange
  const env = makeEnv();
  const spec = lookupSymbolSpec(TSLA_REF);
  // Act
  await composeChartShell({
    lwc: env.lwc,
    container: env.container,
    doc: env.doc,
    storage: env.storage,
    fetch: env.fetch,
    datasetRef: TSLA_REF,
    recentBars: 300,
  });
  // Assert
  assert.deepEqual(env.seriesArgs[0].options.priceFormat, {
    type: 'price', precision: spec.digits, minMove: spec.tick,
  });
  assert.notEqual(spec.digits, lookupSymbolSpec(JP225_REF).digits, '2 銘柄の桁が同じでは検定にならない');
});

test('TC-PF06 結線: 台帳に無い ref では priceFormat を設定しない（vendor 既定へ落ちる）', async () => {
  // Arrange
  const env = makeEnv();
  assert.equal(lookupSymbolSpec(UNKNOWN_REF), null, '前提が崩れている（この ref は台帳に無い）');
  // Act
  await composeChartShell({
    lwc: env.lwc,
    container: env.container,
    doc: env.doc,
    storage: env.storage,
    fetch: env.fetch,
    datasetRef: UNKNOWN_REF,
    recentBars: 300,
  });
  // Assert
  assert.equal(Object.hasOwn(env.seriesArgs[0].options, 'priceFormat'), false);
});

// ---------------------------------------------------------------------------
// 構造（桁・刻みの値を front が持たない）
// ---------------------------------------------------------------------------

test('TC-PF07 chart_bootstrap は台帳を直接引かない（spec は引数で受ける＝解決点を増やさない）', () => {
  // Arrange
  const src = readFileSync(
    fileURLToPath(new URL('../js/adapter/front/chart_bootstrap.js', import.meta.url)),
    'utf8',
  );
  // Act / Assert
  assert.equal(/symbol_spec_catalog|symbol_spec_generated/.test(src), false, '解決点が増えている');
});
