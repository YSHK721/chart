// コピーした足情報と画面（読み取り欄）の OHLC が食い違わないこと（工程 5 是正 A・ISSUE-368 B-1）。
//
// 設計入力（唯一の仕様源）: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md
//   「追加裁定」B-1（**現在値＋バー情報 OHLC を `digits` に合わせる。指標レジェンドの値は対象外**）、
//   「工程 5 レビュー結果」§6-1 段階 2（`symbolSpec` を `composeChartShell` の戻り値に足し、
//    両 root が `installSharedUi` へ**明示転送**する＝`themeState` と同型の既存先例）。
//
// 是正前の実測（node・本ファイル追加時点）:
//   コピー(bar_info_text) : "O 65,800.949\tH 65,801.077\tL 65,695.924\tC 65,706.025"
//   画面(読み取り欄)      : "O 65,801 H 65,801 L 65,696 C 65,706"
//   `format.js:17-18` は単一ソース化の根拠として「コピーした文字列と画面表示が食い違わない」と
//   明記し、`bar_info_text.js:13` も「読み取り欄と同じラベルと整形」と述べる。B-1 で読み取り欄
//   だけを `digits` へ寄せた結果、**この不変条件が割れていた**。
//
// **対象外（巻き込んではならない）**:
//   - 指標の値（`RSI (length=14)\trsi 55` 等）。下段ペインには価格でない系列がある。
//   - 当日 MP の POC/VA。読み取り欄が `fmtValue` で出しているため、こちらも従来のまま
//     （「読み取り欄と同じ整形」を守るとはそういう意味であり、片方だけ寄せると再び割れる）。
//
// 桁の権威は Python 台帳（`marketdata/symbol_spec.py` → 生成物）ただ 1 つ。front の解決点は
//   `chart_app_wiring` の既存 2 か所だけで、本是正は**新しい解決点を作らず値を転送するだけ**。
//
// 構造: Arrange-Act-Assert（AAA）。実 DOM 非依存（fake document）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { formatBarInfoText } from '../js/adapter/front/bar_info_text.js';
import { fmtValue, fmtPrice } from '../js/adapter/front/format.js';
import { CrosshairReadoutView } from '../js/adapter/front/crosshair_readout_view.js';
import { composeChartShell, installSharedUi } from '../js/adapter/front/chart_app_wiring.js';
import { lookupSymbolSpec } from '../js/adapter/front/symbol_spec_catalog.js';

// 実 UI で観測された生値（symptom そのもの・readout_price_digits.test.js と同一）。
const RAW_OHLC = Object.freeze({
  open: 65800.949, high: 65801.077, low: 65695.924, close: 65706.025,
});
const INFO = Object.freeze({
  time: 1786320000,
  ohlc: RAW_OHLC,
  sessionMP: { poc: 65700.125, val: 65600.5, vah: 65800.875 },
  indicators: [{ instanceId: 'rsi#1', values: [{ name: 'rsi', value: 55.4321 }] }],
});

// 従来（本是正の前）の出力＝`fmtValue` の規則。規則を書き写すのではなく「変えていない」基準。
const legacy = (v) => Number(v).toLocaleString(undefined, { maximumFractionDigits: 3 });

// コピー文字列から OHLC 行だけを取り出す。
const copyOhlcLine = (text) => text.split('\n').find((l) => /^O /.test(l));

// ---------------------------------------------------------------------------
// 純関数: コピー整形が桁を受け取り、読み取り欄と同じ結果を出す
// ---------------------------------------------------------------------------

class El {
  constructor() {
    this.children = [];
    this.style = {};
    this.dataset = {};
    this.textContent = '';
    this.innerHTML = '';
    this.className = '';
    this.value = '';
    this.id = '';
    this.parentElement = null;
    this._handlers = {};
    this.classList = {
      add: () => {}, remove: () => {}, contains: () => false, toggle: () => {},
    };
  }

  appendChild(k) { k.parentElement = this; this.children.push(k); return k; }

  removeChild(k) { this.children = this.children.filter((c) => c !== k); return k; }

  insertBefore(k) { k.parentElement = this; this.children.unshift(k); return k; }

  append(...kids) { for (const k of kids) { this.appendChild(k); } return undefined; }

  setAttribute() {}

  removeAttribute() {}

  getAttribute() { return null; }

  select() {}

  focus() {}

  remove() {}

  closest() { return null; }

  querySelector() { return null; }

  querySelectorAll() { return []; }

  getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 100 }; }

  addEventListener(type, fn) { (this._handlers[type] ||= []).push(fn); }

  removeEventListener() {}

  fire(type, ev = {}) { (this._handlers[type] || []).forEach((fn) => fn(ev)); }
}

// 読み取り欄が実際に描く OHLC セル（画面側の唯一の正解）。
function readoutOhlcCells(priceDigits) {
  const created = [];
  const doc = {
    createElement: () => { const n = new El(); created.push(n); return n; },
    querySelector: () => { const n = new El(); created.push(n); return n; },
    getElementById: () => null,
  };
  const view = new CrosshairReadoutView({ document: doc, elementId: 'crosshair-readout', priceDigits });
  view.render({ time: INFO.time, ohlc: RAW_OHLC, overlays: [] });
  const root = created.find((n) => n.id === 'crosshair-readout');
  return root.children
    .flatMap((r) => r.children.map((c) => c.textContent))
    .filter((t) => /^[OHLC] /.test(t));
}

test('TC-CD01 digits=0 のとき、コピーの OHLC と読み取り欄の OHLC は同一文字列', () => {
  // Arrange: 桁は台帳から引く（front が桁を自称しない・検定に数値を書かない）。
  const digits = lookupSymbolSpec('jp225_tick').digits;
  assert.equal(digits, 0, '前提（JP225 は整数表示）が崩れている');
  // Act
  const copied = copyOhlcLine(formatBarInfoText(INFO, { priceDigits: digits }));
  const onScreen = readoutOhlcCells(digits);
  // Assert: 区切り（タブ／スパン）以外は 1 文字も違わない。
  assert.deepEqual(copied.split('\t'), onScreen, 'コピーと画面の OHLC が食い違っている');
});

test('TC-CD02 桁が解決できない構成ではコピーは従来と完全同一（maximumFractionDigits:3）', () => {
  // Arrange: 未指定・null・非整数・負・文字列＝いずれも「解決できていない」。
  const expected = ['O', 'H', 'L', 'C']
    .map((l, i) => `${l} ${legacy(Object.values(RAW_OHLC)[i])}`).join('\t');
  // Act / Assert
  assert.equal(copyOhlcLine(formatBarInfoText(INFO, {})), expected, '未指定で従来と食い違う');
  for (const priceDigits of [null, undefined, Number.NaN, 1.5, -1, '2']) {
    assert.equal(
      copyOhlcLine(formatBarInfoText(INFO, { priceDigits })), expected,
      `priceDigits=${String(priceDigits)} で従来と食い違う`,
    );
  }
});

test('TC-CD03 指標値は巻き込まない（桁を渡しても指標行は従来のまま）', () => {
  // Arrange / Act
  const line = formatBarInfoText(INFO, { priceDigits: 0 })
    .split('\n').find((l) => l.startsWith('rsi#1'));
  // Assert
  assert.equal(line, `rsi#1\trsi ${fmtValue(55.4321)}`, '指標値に価格の桁が漏れている');
  assert.equal(fmtValue(55.4321), legacy(55.4321), '指標値の書式そのものが変わっている');
});

test('TC-CD04 当日 MP（POC/VA）も読み取り欄と同じ整形のまま（片方だけ寄せて再び割らない）', () => {
  // Arrange: 読み取り欄は POC/VA を fmtValue で出す（crosshair_readout_view.js）。
  const mp = INFO.sessionMP;
  // Act
  const line = formatBarInfoText(INFO, { priceDigits: 0 })
    .split('\n').find((l) => l.startsWith('POC '));
  // Assert
  assert.equal(line, `POC ${fmtValue(mp.poc)}\tVA ${fmtValue(mp.val)}–${fmtValue(mp.vah)}`);
});

// ---------------------------------------------------------------------------
// 結線: composeChartShell が解決結果を返し、installSharedUi が受けてコピー項目へ配る
// ---------------------------------------------------------------------------

function shellEnv() {
  const created = [];
  const series = { applyOptions() {}, setData() {}, priceScale: () => ({ applyOptions() {} }) };
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
    createElement: () => { const n = new El(); created.push(n); return n; },
    getElementById: () => null,
    querySelector: (sel) => {
      if (!anchors.has(sel)) { anchors.set(sel, new El()); }
      return anchors.get(sel);
    },
    querySelectorAll: () => [],
    addEventListener() {},
    removeEventListener() {},
    body: new El(),
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
  const env = shellEnv();
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

test('TC-CD05 composeChartShell が銘柄仕様を戻り値で配る（themeState と同型・新しい解決点を作らない）', async () => {
  // Arrange / Act
  const jp = await bootShell('jp225_tick');
  const unknown = await bootShell('unknown_dataset_ref');
  // Assert: 値は台帳の解決結果そのもの（既定値でも front の自称でもない）。
  //   同一性ではなく等価性で見る: `lookupSymbolSpec` は呼ぶたび新しいオブジェクトを返す契約
  //   （`symbol_spec_catalog.js` の JSDoc「返すのは毎回新しいオブジェクト」）。
  assert.deepEqual(jp.shell.symbolSpec, lookupSymbolSpec('jp225_tick'), '台帳の解決結果を返していない');
  assert.equal(unknown.shell.symbolSpec, null, '未知 ref でフェイルセーフになっていない');
});

// 右クリック「情報をコピーする」で実際にクリップボードへ渡った文字列を捕まえる版面。
function uiEnv() {
  const wrap = new El();
  const app = new El();
  const copied = [];
  const body = new El();
  const doc = {
    createElement: () => new El(),
    querySelector: (sel) => {
      if (sel === '.chart-wrap') return wrap;
      if (sel === '#app') return app;
      return null;
    },
    getElementById: () => null,
    execCommand: () => { copied.push(body.children[body.children.length - 1].value); return true; },
    addEventListener: () => {},
    removeEventListener: () => {},
    body,
  };
  return { doc, wrap, copied };
}

function fakeRendererWithBar() {
  return {
    panPriceByPixels() {},
    handlePriceWheel: () => false,
    isOverPriceAxis: () => false,
    resetPriceZoom() {},
    setPaneHeight() {},
    isLatestBarVisible: () => true,
    scrollToLatest() {},
    barInfoAt: () => INFO,
  };
}

// 本番の `getContext()` は **controller が在る**分岐を通る（配信 3 ページはいずれも controller を
//   生成する）。`getController: () => null` だけで検定すると、縮退分岐しか踏まないため
//   **本番経路の取り残しを検出できない**（実測: 本番分岐から桁を落としても 2362 件が緑のままだった）。
//   よって既定を「controller 在り」にし、縮退分岐は別ケースで見る。
function fakeController() {
  return {
    _timeframe: '1D',
    legendRows: () => [{ instanceId: 'rsi#1', label: 'RSI', params: { length: 14 } }],
  };
}

async function copyViaMenu(symbolSpec, controller = fakeController()) {
  const { doc, wrap, copied } = uiEnv();
  const container = new El();
  installSharedUi({
    container,
    renderer: fakeRendererWithBar(),
    doc,
    getController: () => controller,
    updatePaneHeight: () => {},
    symbolSpec,
  });
  container.fire('contextmenu', { clientX: 10, clientY: 20, preventDefault() {} });
  const host = wrap.children[wrap.children.length - 1];
  await host.children[0].fire('click');
  await Promise.resolve();
  await Promise.resolve();
  return copied;
}

test('TC-CD06 結線: コピー項目は配られた銘柄仕様の桁で書き、画面と同一文字列になる', async () => {
  // Arrange
  const { shell } = await bootShell('jp225_tick');
  const digits = lookupSymbolSpec('jp225_tick').digits;
  // Act: 右クリック → 「情報をコピーする」→ クリップボードへ渡った文字列。
  const copied = await copyViaMenu(shell.symbolSpec);
  // Assert
  assert.equal(copied.length, 1, 'クリップボードへ書かれていない（結線が届いていない）');
  assert.deepEqual(
    copyOhlcLine(copied[0]).split('\t'), readoutOhlcCells(digits),
    'コピーした OHLC が画面の OHLC と食い違う',
  );
  // 期待値そのものも台帳から導く（front が桁を自称しない）。
  assert.equal(
    copyOhlcLine(copied[0]),
    ['O', 'H', 'L', 'C'].map((l, i) => `${l} ${fmtPrice(Object.values(RAW_OHLC)[i], digits)}`).join('\t'),
  );
});

test('TC-CD06b 結線: controller 未生成の縮退分岐でも同じ桁で書く（両分岐を塞ぐ）', async () => {
  // Arrange
  const { shell } = await bootShell('jp225_tick');
  const digits = lookupSymbolSpec('jp225_tick').digits;
  // Act: 配線途中（controller 未生成）＝縮退分岐。
  const copied = await copyViaMenu(shell.symbolSpec, null);
  // Assert
  assert.equal(copied.length, 1, 'クリップボードへ書かれていない');
  assert.deepEqual(copyOhlcLine(copied[0]).split('\t'), readoutOhlcCells(digits));
});

test('TC-CD06c 結線: 本番分岐は指標の見出しも出す（縮退分岐と取り違えていない）', async () => {
  // Arrange: この行が出ていることが「controller 在りの分岐を通った」証拠になる。
  const { shell } = await bootShell('jp225_tick');
  // Act
  const copied = await copyViaMenu(shell.symbolSpec);
  // Assert
  assert.match(copied[0], /^RSI \(length=14\)\trsi /m, '本番分岐（controller 在り）を通っていない');
});

test('TC-CD07 結線: 銘柄仕様が未注入なら従来どおり（既存呼び出しは 1 バイトも変わらない）', async () => {
  // Arrange / Act: symbolSpec を渡さない＝既存の呼び出し形。
  const { doc, wrap, copied } = uiEnv();
  const container = new El();
  installSharedUi({
    container,
    renderer: fakeRendererWithBar(),
    doc,
    getController: () => fakeController(),
    updatePaneHeight: () => {},
  });
  container.fire('contextmenu', { clientX: 10, clientY: 20, preventDefault() {} });
  await wrap.children[wrap.children.length - 1].children[0].fire('click');
  await Promise.resolve();
  await Promise.resolve();
  // Assert
  assert.equal(
    copyOhlcLine(copied[0]),
    ['O', 'H', 'L', 'C'].map((l, i) => `${l} ${legacy(Object.values(RAW_OHLC)[i])}`).join('\t'),
  );
});

// ---------------------------------------------------------------------------
// 構造: 両 root が対称に転送する（片側だけの是正を機械的に禁止する）
// ---------------------------------------------------------------------------

const ROOTS = Object.freeze({
  live: '../js/adapter/front/composition_root_front.js',
  replay: '../../../../simulator/replay_ui/web/js/adapter/front/composition_root_front.js',
});

const readRoot = (rel) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8');

// `const { … } = await composeChartShell(` の分配ブロック。
function shellDestructuring(src) {
  const end = src.indexOf('} = await composeChartShell(');
  assert.notEqual(end, -1, 'composeChartShell の分配が見つからない');
  return src.slice(src.lastIndexOf('const {', end), end);
}

// `installSharedUi({ … })` の実引数ブロック（対応する括弧まで）。
function installArgs(src) {
  const start = src.indexOf('installSharedUi({');
  assert.notEqual(start, -1, 'installSharedUi の呼び出しが見つからない');
  let depth = 0;
  for (let i = src.indexOf('{', start); i < src.length; i += 1) {
    if (src[i] === '{') { depth += 1; }
    if (src[i] === '}') { depth -= 1; if (depth === 0) { return src.slice(start, i + 1); } }
  }
  throw new Error('installSharedUi の実引数が閉じていない');
}

test('TC-CD08 両 root が composeChartShell から symbolSpec を受け取る（対称）', () => {
  // Arrange / Act / Assert
  for (const [name, rel] of Object.entries(ROOTS)) {
    assert.match(
      shellDestructuring(readRoot(rel)), /\bsymbolSpec\b/,
      `${name} root が composeChartShell から symbolSpec を受け取っていない`,
    );
  }
});

test('TC-CD09 両 root が installSharedUi へ symbolSpec を明示転送する（対称）', () => {
  // Arrange / Act / Assert
  for (const [name, rel] of Object.entries(ROOTS)) {
    assert.match(
      installArgs(readRoot(rel)), /\bsymbolSpec\b/,
      `${name} root が installSharedUi へ symbolSpec を転送していない`,
    );
  }
});

test('TC-CD10 root は台帳を自分で引かない（解決点を増やさない）', () => {
  // Arrange / Act / Assert
  for (const [name, rel] of Object.entries(ROOTS)) {
    assert.equal(
      /lookupSymbolSpec|symbol_spec_catalog|symbol_spec_generated/.test(readRoot(rel)), false,
      `${name} root が台帳を直接引いている（解決点が増えている）`,
    );
  }
});
