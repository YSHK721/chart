// 銘柄名を front が自称せず台帳から導出する（ISSUE-368 スライス S-7・A-4）。
//
// 設計入力（唯一の仕様源）: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md
//   「承認結果（2026-08-20・依頼者裁定）」A-4（**旧リテラル → 台帳由来へ**。`CHART_SYMBOL` を
//    削除し導出化する）、同「決定: 案 E」原因 α（価格が「どの銘柄の価格か」を供給側が名乗って
//    いなかったため front が銘柄名の定数を自称するしかなかった）、
//   同 D-3（リテラルの置き換えでは front が銘柄を自称する構造が不変＝同じ食い違いを別の場所に固定する）。
//
// 除去する原因: 銘柄名の**値**を front が持っていること。実データは `datasetRef='jp225_tick'`
//   （台帳では JP225）なのに画面とコピーは別名を名乗っていた。よって front から名前の値を
//   消し、台帳から解決した名前を**注入**する。本検定は銘柄名の文字列を 1 つも書かず、期待値は
//   台帳（`lookupSymbolSpec`）から引く（書けば第 2 定義になり、同じ食い違いが再発する）。
//
// 保持先（単一情報源）: 解決した名前の置き場はツールバーの `.tb-symbol` ただ 1 つで、
//   画面表示とコピー文脈は同じ実体を読む。app_chrome_view の旧 docstring が言う
//   「ここを複製すると『画面と別名』の食い違いが静かに生まれる」を、
//   定数ではなく**実体の一意性**で守る。
//
// 縮退表示: 解決できないときは空文字にしない（無音）／別名を捏造しない。「解決できていない」
//   ことをそのまま出す（`price_pick_resolver` のフェイルセーフ＝値ではなく機能を落とし理由を出す、
//   と同じ流儀）。
//
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import {
  installChartToolbar, setChartSymbol, chartSymbol, UNRESOLVED_CHART_SYMBOL,
} from '../js/adapter/front/app_chrome_view.js';
import { installSharedUi, wireControllerCollaborators } from '../js/adapter/front/chart_app_wiring.js';
import { lookupSymbolSpec } from '../js/adapter/front/symbol_spec_catalog.js';
import { SYMBOL_SPECS } from '../js/domain/symbol_spec_generated.js';

const JP225_REF = 'jp225_tick';
const TSLA_REF = 'sample';
const UNKNOWN_REF = 'unknown_dataset_ref';

// 足 1 本ぶんの情報（コピー文脈の検定用）。値は本件の対象外なので最小。
const INFO = {
  time: 1277769600,
  ohlc: {
    open: 1.2, high: 1.6, low: 1.1, close: 1.5,
  },
  sessionMP: null,
  indicators: [],
};

// 最小 DOM。**ツールバーの markup 文字列から `.tb-symbol` を引ける**ところだけが
//   tests/support/position_sizing_boot.js の El と違う（あちらは innerHTML を捨てる）。
//   実ブラウザは innerHTML を要素へ解釈するため、器と中身の受け渡しは実 DOM でしか観測できない。
class El {
  constructor() {
    this.children = [];
    this.dataset = {};
    this.style = {};
    this.textContent = '';
    this.value = '';
    this.step = '';
    this.className = '';
    this._html = '';
    this._cls = new Set();
    this._handlers = {};
  }

  get innerHTML() { return this._html; }

  set innerHTML(v) { this._html = String(v ?? ''); this.children = []; }

  get classList() {
    const s = this._cls;
    return {
      add: (c) => s.add(c),
      remove: (c) => s.delete(c),
      contains: (c) => s.has(c),
      toggle: (c, on) => { if (on === undefined ? !s.has(c) : on) { s.add(c); } else { s.delete(c); } },
    };
  }

  appendChild(k) { k.parentElement = this; k.parentNode = this; this.children.push(k); return k; }

  append(...kids) { for (const k of kids) { if (k && typeof k === 'object') this.appendChild(k); } }

  insertBefore(k) { k.parentNode = this; this.children.unshift(k); return k; }

  removeChild(k) { this.children = this.children.filter((c) => c !== k); return k; }

  querySelector() { return null; }

  setAttribute() {}

  getBoundingClientRect() { return { left: 0, top: 0 }; }

  addEventListener(t, fn) { (this._handlers[t] ||= []).push(fn); }

  fire(t, ev = {}) { (this._handlers[t] || []).forEach((fn) => fn(ev)); }
}

// ブラウザの「innerHTML の文字列が要素になる」を、`.tb-symbol` の 1 個だけ再現する document。
//   copied: execCommand('copy') で書き込まれた文字列（コピー文脈の観測点）。
function makeDoc() {
  const app = new El();
  const wrap = new El();
  const body = new El();
  const mounts = new Map();
  const created = [];
  const copied = [];
  let symbolEl = null;
  const doc = {
    body,
    createElement: () => { const e = new El(); created.push(e); return e; },
    querySelector: (sel) => {
      if (sel === '#app') return app;
      if (sel === '.chart-wrap') return wrap;
      if (sel === '.tb-symbol') {
        // 器が生成されて初めて引ける（実 DOM と同じ順序。器が無ければ null）。
        if (!symbolEl) {
          const bar = created.find((e) => /class="tb-symbol"/.test(e.innerHTML));
          if (bar) {
            symbolEl = new El();
            symbolEl.className = 'tb-symbol';
            const m = /<span class="tb-symbol">([^<]*)<\/span>/.exec(bar.innerHTML);
            symbolEl.textContent = m ? m[1] : '';
          }
        }
        return symbolEl;
      }
      return null;
    },
    getElementById: (id) => {
      if (!mounts.has(id)) { mounts.set(id, new El()); }
      return mounts.get(id);
    },
    execCommand: (cmd) => {
      if (cmd !== 'copy') return false;
      const ta = body.children[body.children.length - 1];
      copied.push(ta ? ta.value : '');
      return true;
    },
    addEventListener: () => {},
    removeEventListener: () => {},
  };
  return {
    doc, app, wrap, body, mounts, copied,
  };
}

// 実物の共有配線を root と同じ順序で組む（installSharedUi → wireControllerCollaborators）。
//   support/position_sizing_boot.js と同型だが、あちらの最小 DOM では `.tb-symbol` を引けない。
function boot(datasetRef, { barInfo = null } = {}) {
  const env = makeDoc();
  const container = new El();
  const renderer = {
    panPriceByPixels() {},
    handlePriceWheel: () => false,
    isOverPriceAxis: () => false,
    resetPriceZoom() {},
    setPaneHeight() {},
    isLatestBarVisible: () => true,
    scrollToLatest() {},
    barInfoAt: () => barInfo,
    setUserInteraction() {},
    attachBackgroundPrimitive() {},
    setCandleObserver() {},
    setPaneOrderObserver() {},
    suppressInteraction: () => () => {},
    priceAtCoordinate: (y) => 62707.71 - y,
    paneIndexAtCoordinate: () => 0,
    snapCandidatesAt: () => [],
  };
  let positionSizing = null;
  const shared = installSharedUi({
    container,
    renderer,
    doc: env.doc,
    getController: () => null,
    updatePaneHeight: () => {},
    getPositionSizing: () => positionSizing,
  });
  const wired = wireControllerCollaborators({
    controller: {
      setTimeframe() {},
      registerActorController() {},
      setAppliedObserver() {},
      setTimeframeObserver() {},
      applyPaneOrder() {},
      _timeframe: '1m',
    },
    renderer,
    doc: env.doc,
    fetch: async () => ({ ok: false }),
    datasetRef,
    timeframe: '1m',
    recentBars: 100,
    templateStore: {
      loadTemplates: () => [],
      saveTemplates: () => {},
      loadBindings: () => ({}),
      saveBindings: () => {},
      loadTemplateSeq: () => 0,
      saveTemplateSeq: () => {},
    },
    chartTemplateMenu: shared.chartTemplateMenu,
    chartTemplateDialogs: shared.chartTemplateDialogs,
    positionSizingDialog: shared.positionSizingDialog,
    registerVerticalPanBlocker: shared.registerVerticalPanBlocker,
    chartToast: shared.chartToast,
    lwc: {},
    mainSeries: {},
    chart: {},
    container,
    currentPriceView: { render() {} },
  });
  positionSizing = wired.positionSizing ? wired.positionSizing.controller : null;
  return {
    ...env, shared, wired, container, renderer,
  };
}

const toolbarSymbolText = (ctx) => ctx.doc.querySelector('.tb-symbol').textContent;
const copyItem = (ctx) => ctx.shared.chartContextMenu._items[0];

// ---------------------------------------------------------------------------
// View（器と縮退表示）
// ---------------------------------------------------------------------------

test('TC-CS01 ツールバーは銘柄名の器だけを持ち、未解決のうちは「解決できていない」と出す', () => {
  // Arrange
  const app = new El();
  const doc = { createElement: () => new El(), querySelector: (s) => (s === '#app' ? app : null) };
  // Act
  const html = installChartToolbar(doc, {}).innerHTML;
  // Assert: 器（class）は在る。中身は空でも偽名でもない。
  assert.ok(html.includes('class="tb-symbol"'), '銘柄名の器が無い');
  assert.ok(
    html.includes(`<span class="tb-symbol">${UNRESOLVED_CHART_SYMBOL}</span>`),
    `未解決の縮退表示が出ていない: ${html.slice(0, 120)}`,
  );
  assert.notEqual(UNRESOLVED_CHART_SYMBOL, '', '縮退表示が空文字（無音）');
});

test('TC-CS02 app_chrome_view は銘柄名の値を持たない（台帳のどの銘柄名も現れない）', async () => {
  // Arrange
  const path = fileURLToPath(new URL('../js/adapter/front/app_chrome_view.js', import.meta.url));
  const src = readFileSync(path, 'utf8');
  const mod = await import('../js/adapter/front/app_chrome_view.js');
  // Act / Assert: リテラルを別の値へ書き換えただけでは「front が銘柄を自称する構造」が残る（D-3）。
  assert.equal('CHART_SYMBOL' in mod, false, '銘柄名の定数がまだ export されている');
  assert.equal(src.includes('NI225'), false, '旧リテラルが残っている');
  for (const name of Object.keys(SYMBOL_SPECS)) {
    assert.equal(src.includes(name), false, `銘柄名 ${name} を front が持っている`);
  }
});

test('TC-CS03 解決した銘柄名を器へ注入すると、その名前が表示される', () => {
  // Arrange
  const ctx = makeDoc();
  installChartToolbar(ctx.doc, {});
  const spec = lookupSymbolSpec(JP225_REF);
  // Act
  setChartSymbol(ctx.doc, spec.symbol);
  // Assert
  assert.equal(ctx.doc.querySelector('.tb-symbol').textContent, spec.symbol);
  assert.equal(chartSymbol(ctx.doc), spec.symbol, '表示と保持先が食い違っている');
});

test('TC-CS04 解決できない名前（null・空・非文字列）は縮退表示にする（捏造も無音もしない）', () => {
  // Arrange
  const spec = lookupSymbolSpec(JP225_REF);
  for (const bad of [null, undefined, '', 0, {}]) {
    const ctx = makeDoc();
    installChartToolbar(ctx.doc, {});
    setChartSymbol(ctx.doc, spec.symbol);   // 一度は解決できていた状態から
    // Act
    setChartSymbol(ctx.doc, bad);
    // Assert
    assert.equal(
      ctx.doc.querySelector('.tb-symbol').textContent,
      UNRESOLVED_CHART_SYMBOL,
      `解決できない値 ${JSON.stringify(bad)} で縮退していない`,
    );
  }
});

test('TC-CS05 器が無い構成では書き込みも読み出しも例外にしない（縮退の流儀）', () => {
  // Arrange: ツールバーを install していない document（最小構成・SSR）。
  const ctx = makeDoc();
  // Act / Assert
  assert.equal(setChartSymbol(ctx.doc, 'JP225'), null);
  assert.equal(chartSymbol(ctx.doc), null);
  assert.equal(setChartSymbol(null, 'JP225'), null);
  assert.equal(chartSymbol(undefined), null);
});

// ---------------------------------------------------------------------------
// 結線（台帳 → 画面・コピー）
// ---------------------------------------------------------------------------

test('TC-CS06 結線: datasetRef から解決した銘柄名がツールバーに出る', () => {
  // Arrange / Act
  const ctx = boot(JP225_REF);
  // Assert
  assert.equal(toolbarSymbolText(ctx), lookupSymbolSpec(JP225_REF).symbol);
});

test('TC-CS07 結線: 別 ref では別の銘柄名になる（front が名前を自称していない証拠）', () => {
  // Arrange / Act
  const ctx = boot(TSLA_REF);
  // Assert
  const tsla = lookupSymbolSpec(TSLA_REF).symbol;
  assert.equal(toolbarSymbolText(ctx), tsla);
  assert.notEqual(tsla, lookupSymbolSpec(JP225_REF).symbol, '2 銘柄が同名では検定にならない');
});

test('TC-CS08 結線: 台帳に無い ref では縮退表示（無音で空にしない・別名を出さない）', () => {
  // Arrange / Act
  const ctx = boot(UNKNOWN_REF);
  // Assert
  assert.equal(toolbarSymbolText(ctx), UNRESOLVED_CHART_SYMBOL);
});

test('TC-CS09 結線: 「情報をコピーする」の銘柄は画面の表示と同一文字列（食い違いを作らない）', async () => {
  // Arrange
  const ctx = boot(JP225_REF, { barInfo: INFO });
  // Act
  await copyItem(ctx).onSelect({ x: 10, y: 10 });
  // Assert
  assert.equal(ctx.copied.length, 1, 'コピーが実行されていない（観測点が空）');
  assert.equal(
    ctx.copied[0].split('\n')[0].split('\t')[0],
    toolbarSymbolText(ctx),
    '画面の銘柄とコピーの銘柄が食い違っている',
  );
  assert.equal(ctx.copied[0].split('\n')[0].split('\t')[0], lookupSymbolSpec(JP225_REF).symbol);
});

test('TC-CS10 結線: 解決できない ref でもコピーは銘柄欄を無音で消さない（理由がそのまま入る）', async () => {
  // Arrange
  const ctx = boot(UNKNOWN_REF, { barInfo: INFO });
  // Act
  await copyItem(ctx).onSelect({ x: 10, y: 10 });
  // Assert
  assert.equal(ctx.copied[0].split('\n')[0].split('\t')[0], UNRESOLVED_CHART_SYMBOL);
});

// ---------------------------------------------------------------------------
// 構造（銘柄名の値が front のどこにも無い）
// ---------------------------------------------------------------------------

test('TC-CS11 front 配下に旧リテラル NI225 が 1 件も無い', () => {
  // Arrange
  const frontDir = fileURLToPath(new URL('../js/adapter/front/', import.meta.url));
  // Act
  const hits = readdirSync(frontDir)
    .filter((n) => n.endsWith('.js'))
    .filter((n) => readFileSync(`${frontDir}${n}`, 'utf8').includes('NI225'));
  // Assert
  assert.deepEqual(hits, [], `旧リテラルが残っている: ${hits.join(', ')}`);
});
