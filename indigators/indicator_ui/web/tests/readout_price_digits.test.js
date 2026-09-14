// 現在値・バー情報（OHLC）の表示桁を銘柄仕様の digits に合わせる（ISSUE-368 A-3 の未達分）。
//
// 設計入力（唯一の仕様源）: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md
//   「承認事項」A-3（**価格軸・現在値・クロスヘアの表示桁**を `digits` に合わせる。値だけ丸めると
//    「軸は 2 桁・入る値は整数」の乖離が残る）／「承認結果」A-3。
//
// 実 UI 実測（2026-08-20・:8000 live と /replay/ の両方）: 価格軸は整数になったが
//   `#current-price` は `65,721.051`・同じオーバーレイのバー情報は
//   `O 65,800.949 H 65,801.077 L 65,695.924 C 65,706.025`＝A-3 の「現在値」が未達だった。
//   原因は `adapter/front/format.js` の `fmtValue` が `maximumFractionDigits: 3` 固定で、
//   価格（銘柄の桁が効くもの）と指標値（効かないもの）が同じ関数を共有していたこと。
//
// **対象外（巻き込んではならない）**: 指標の値（ペインレジェンド・読み取り欄の overlay 行）。
//   下段ペインには価格でない系列があり、価格の桁を強制すると誤りになる。よって
//   「価格の書式」と「指標値の書式」を別の関数として分ける。
//
// 桁の権威は台帳（`marketdata/symbol_spec.py` → 生成物）ただ 1 つで、front の解決点は
//   `chart_app_wiring` の既存 1 か所だけ（新しい解決点を作らない）。よって本検定は桁の数値を
//   結線側の期待に書かず、`lookupSymbolSpec` から引く。
//
// 構造: Arrange-Act-Assert（AAA）。実 DOM 非依存（fake document）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { fmtValue, fmtPrice } from '../js/adapter/front/format.js';
import { CurrentPriceView } from '../js/adapter/front/current_price_view.js';
import { CrosshairReadoutView } from '../js/adapter/front/crosshair_readout_view.js';
import { composeChartShell } from '../js/adapter/front/chart_app_wiring.js';
import { lookupSymbolSpec } from '../js/adapter/front/symbol_spec_catalog.js';

// 実 UI で観測された生値（symptom そのもの）。
const RAW_PRICE = 65721.051;
const RAW_OHLC = Object.freeze({
  open: 65800.949, high: 65801.077, low: 65695.924, close: 65706.025,
});

// 従来（本是正の前）の出力＝`fmtValue` の規則。ここに規則を書き写すのではなく、
//   「変えていない」ことを測るための基準として明示する。
const legacy = (v) => Number(v).toLocaleString(undefined, { maximumFractionDigits: 3 });

// ---------------------------------------------------------------------------
// 単体: 価格の書式（fmtPrice）— 指標値の書式（fmtValue）とは別の関数
// ---------------------------------------------------------------------------

test('TC-RD01 digits=0 の価格は整数で出る（実 UI の 65,721.051 が 65,721 になる）', () => {
  // Arrange / Act / Assert
  assert.equal(fmtPrice(RAW_PRICE, 0), '65,721');
});

test('TC-RD02 digits=2 の価格は小数 2 桁で出る（front が桁を自称せず台帳に従う）', () => {
  // Arrange / Act / Assert
  assert.equal(fmtPrice(RAW_PRICE, 2), '65,721.05');
});

test('TC-RD03 桁が解決できないときは従来（fmtValue）と完全同一＝無音で誤った桁に固定しない', () => {
  // Arrange: 桁の解決に失敗する 3 状態と、境界・非有限。
  const values = [RAW_PRICE, 0, -0.5, -1234.5675, 1e21, 0.0004];
  // Act / Assert
  for (const v of values) {
    for (const digits of [undefined, null, Number.NaN, 1.5, -1, '2']) {
      assert.equal(fmtPrice(v, digits), legacy(v), `digits=${String(digits)} / v=${v} で従来と食い違う`);
    }
  }
  for (const v of [null, undefined, Number.NaN, Infinity, -Infinity]) {
    assert.equal(fmtPrice(v, 0), '', `非有限で空文字を返していない: ${String(v)}`);
    assert.equal(fmtPrice(v), fmtValue(v), `非有限で従来と食い違う: ${String(v)}`);
  }
});

test('TC-RD04 指標値の書式（fmtValue）は 1 バイトも変わっていない（対象外を巻き込まない）', () => {
  // Arrange: ペインレジェンド・読み取り欄の overlay 行が出していた実測値。
  const indicatorValues = [65668.637, 56.462, 0.101, RAW_PRICE, -0.0005, 0];
  // Act / Assert
  for (const v of indicatorValues) {
    assert.equal(fmtValue(v), legacy(v), `指標値の書式が変わっている: ${v}`);
  }
});

// ---------------------------------------------------------------------------
// View: 現在値（#current-price）
// ---------------------------------------------------------------------------

function fakeSlotDoc() {
  const created = [];
  const make = () => {
    const node = {
      id: '', className: '', textContent: '', innerHTML: '', style: {}, children: [],
      appendChild(c) { node.children.push(c); return c; },
      append(...cs) { node.children.push(...cs); return undefined; },
      querySelector: () => null,
    };
    created.push(node);
    return node;
  };
  return {
    created,
    createElement: make,
    querySelector: () => make(),
    getElementById: () => null,
  };
}

const slotOf = (doc, id) => doc.created.find((n) => n.id === id);

test('TC-RD05 現在値は注入された桁で描画する（未注入なら従来どおり）', () => {
  // Arrange
  const withDigits = fakeSlotDoc();
  const without = fakeSlotDoc();
  // Act
  new CurrentPriceView({ document: withDigits, elementId: 'current-price', priceDigits: 0 })
    .render(RAW_PRICE);
  new CurrentPriceView({ document: without, elementId: 'current-price' }).render(RAW_PRICE);
  // Assert
  assert.equal(slotOf(withDigits, 'current-price').textContent, '65,721');
  assert.equal(slotOf(without, 'current-price').textContent, legacy(RAW_PRICE));
});

// ---------------------------------------------------------------------------
// View: バー情報（読み取り欄の OHLC）と、その隣にある指標値（対象外）
// ---------------------------------------------------------------------------

function readoutTexts(doc) {
  const root = slotOf(doc, 'crosshair-readout');
  const rows = root.children;
  return rows.flatMap((r) => (r.children.length > 0
    ? r.children.map((c) => c.textContent) : [r.textContent]));
}

test('TC-RD06 バー情報の OHLC は注入された桁で描画する（同一銘柄の価格）', () => {
  // Arrange
  const doc = fakeSlotDoc();
  const view = new CrosshairReadoutView({
    document: doc, elementId: 'crosshair-readout', priceDigits: 0,
  });
  // Act
  view.render({ time: 0, ohlc: RAW_OHLC, overlays: [] });
  // Assert
  const texts = readoutTexts(doc);
  assert.deepEqual(
    texts.filter((t) => /^[OHLC] /.test(t)),
    ['O 65,801', 'H 65,801', 'L 65,696', 'C 65,706'],
  );
});

test('TC-RD07 読み取り欄の指標行は桁を注入しても従来のまま（価格の桁を指標へ強制しない）', () => {
  // Arrange: 下段ペインには価格でない系列がある（RSI・ma_marod）。
  const doc = fakeSlotDoc();
  const view = new CrosshairReadoutView({
    document: doc, elementId: 'crosshair-readout', priceDigits: 0,
  });
  const overlays = [
    { name: 'RSI', value: 56.462, color: null },
    { name: 'ma_marod', value: 0.101, color: null },
    { name: 'MA', value: 65668.637, color: null },
  ];
  // Act
  view.render({ time: 0, ohlc: null, overlays });
  // Assert
  assert.deepEqual(readoutTexts(doc), [
    `RSI: ${legacy(56.462)}`, `ma_marod: ${legacy(0.101)}`, `MA: ${legacy(65668.637)}`,
  ]);
});

test('TC-RD08 桁が未注入なら OHLC も従来どおり（無音で桁を決めない）', () => {
  // Arrange
  const doc = fakeSlotDoc();
  const view = new CrosshairReadoutView({ document: doc, elementId: 'crosshair-readout' });
  // Act
  view.render({ time: 0, ohlc: RAW_OHLC, overlays: [] });
  // Assert
  assert.deepEqual(
    readoutTexts(doc).filter((t) => /^[OHLC] /.test(t)),
    ['O', 'H', 'L', 'C'].map((l, i) => `${l} ${legacy(Object.values(RAW_OHLC)[i])}`),
  );
});

// ---------------------------------------------------------------------------
// 結線: chart_app_wiring が既存の解決結果を両 View へ配る（新しい解決点を作らない）
// ---------------------------------------------------------------------------

function el(created) {
  const node = {
    id: '',
    style: {},
    dataset: {},
    children: [],
    textContent: '',
    innerHTML: '',
    classList: {
      add() {}, remove() {}, toggle() {}, contains: () => false,
    },
    appendChild(c) { node.children.push(c); return c; },
    append(...cs) { node.children.push(...cs); return undefined; },
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
  if (created) {
    created.push(node);
  }
  return node;
}

function makeEnv() {
  const created = [];
  const series = {
    applyOptions() {}, setData() {}, priceScale: () => ({ applyOptions() {} }),
  };
  const chart = {
    applyOptions() {},
    addSeries: () => series,
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
    createElement: () => el(created),
    getElementById: () => null,
    querySelector: (sel) => {
      if (!anchors.has(sel)) { anchors.set(sel, el(created)); }
      return anchors.get(sel);
    },
    querySelectorAll: () => [],
    addEventListener() {},
    removeEventListener() {},
    body: el(created),
  };
  return {
    lwc,
    doc,
    created,
    container: { clientHeight: 400 },
    storage: { getItem: () => null, setItem() {}, removeItem() {} },
    fetch: async () => ({ ok: true, status: 200, json: async () => ({}) }),
  };
}

async function bootShell(datasetRef) {
  const env = makeEnv();
  const shell = await composeChartShell({
    lwc: env.lwc,
    container: env.container,
    doc: env.doc,
    storage: env.storage,
    fetch: env.fetch,
    datasetRef,
    recentBars: 300,
  });
  return { env, shell };
}

const slotIn = (env, id) => env.created.find((n) => n.id === id);

test('TC-RD09 結線: 現在値の桁は台帳から届く（JP225=0 桁／TSLA=2 桁）', async () => {
  // Arrange
  const jp = await bootShell('jp225_tick');
  const tsla = await bootShell('sample');
  assert.notEqual(
    lookupSymbolSpec('jp225_tick').digits, lookupSymbolSpec('sample').digits,
    '2 銘柄の桁が同じでは検定にならない（台帳の前提が変わっている）',
  );
  // Act
  jp.shell.currentPriceView.render(RAW_PRICE);
  tsla.shell.currentPriceView.render(RAW_PRICE);
  // Assert: 期待値も台帳から引く（front が桁を自称しない・本検定に数値を書かない）。
  assert.equal(
    slotIn(jp.env, 'current-price').textContent,
    fmtPrice(RAW_PRICE, lookupSymbolSpec('jp225_tick').digits),
    '現在値が台帳の桁で描かれていない（A-3 未達）',
  );
  assert.equal(
    slotIn(tsla.env, 'current-price').textContent,
    fmtPrice(RAW_PRICE, lookupSymbolSpec('sample').digits),
  );
});

test('TC-RD10 結線: バー情報の OHLC も同じ解決結果に従う（片方だけ取り残さない）', async () => {
  // Arrange
  const { env, shell } = await bootShell('jp225_tick');
  const digits = lookupSymbolSpec('jp225_tick').digits;
  // Act
  shell.readoutView.render({ time: 0, ohlc: RAW_OHLC, overlays: [] });
  // Assert
  const root = slotIn(env, 'crosshair-readout');
  const texts = root.children.flatMap((r) => r.children.map((c) => c.textContent));
  assert.deepEqual(
    texts.filter((t) => /^[OHLC] /.test(t)),
    ['O', 'H', 'L', 'C'].map((l, i) => `${l} ${fmtPrice(Object.values(RAW_OHLC)[i], digits)}`),
  );
});

test('TC-RD11 結線: 台帳に無い ref では従来どおり（フェイルセーフ＝桁を決めない）', async () => {
  // Arrange
  assert.equal(lookupSymbolSpec('unknown_dataset_ref'), null, '前提が崩れている');
  const { env, shell } = await bootShell('unknown_dataset_ref');
  // Act
  shell.currentPriceView.render(RAW_PRICE);
  // Assert
  assert.equal(slotIn(env, 'current-price').textContent, legacy(RAW_PRICE));
});

// ---------------------------------------------------------------------------
// 構造: 書式の第 2 実装を作らない・解決点を増やさない
// ---------------------------------------------------------------------------

const FRONT_DIR = fileURLToPath(new URL('../js/adapter/front/', import.meta.url));
const read = (name) => readFileSync(join(FRONT_DIR, name), 'utf8');

test('TC-RD12 価格の書式は front 配下 1 ファイルだけが定義する（第 2 実装の禁止）', () => {
  // Arrange / Act
  const definers = readdirSync(FRONT_DIR)
    .filter((n) => n.endsWith('.js'))
    .filter((n) => /export function fmtPrice/.test(read(n)));
  // Assert
  assert.deepEqual(definers, ['format.js'], `価格書式の定義が複数ある: ${definers.join(', ')}`);
  // 丸め＋桁区切りの実装そのものは price_format.js が持ち、fmtPrice は呼ぶだけ
  //   （同じ規則を 2 か所に書かない＝原因 β の再発防止）。
  assert.match(read('format.js'), /from '\.\/price_format\.js'/, '書式を単一ソースから取っていない');
});

test('TC-RD13 View は桁を自分で解決しない（台帳を引く口は増えない）', () => {
  // Arrange / Act / Assert
  for (const name of ['current_price_view.js', 'crosshair_readout_view.js', 'format.js']) {
    assert.equal(
      /symbol_spec_catalog|symbol_spec_generated/.test(read(name)), false,
      `${name} が台帳を直接引いている（解決点が増えている）`,
    );
  }
});

test('TC-RD14 指標値を出す面は価格の書式を使わない（対象外を構造でも守る）', () => {
  // Arrange: ペインレジェンドは下段ペイン（価格でない系列）も描く。
  // Act / Assert
  assert.equal(
    /fmtPrice/.test(read('pane_legend_view.js')), false,
    'ペインレジェンド（指標値）が価格の書式を使っている',
  );
});
