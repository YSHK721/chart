// trade_markers_theme.test.js — 取引マーカーの描画物をテーマ配線点へ接続する（段階 5-E）。
//
// 何を守るか:
//   1. 恒等（通過条件 1）: テーマ未設定のとき、ポップアップが解決する色が接続前と厳密一致。
//   2. リテラル 0 件（通過条件 2）: 適用側に素の色リテラルが残らない。
//   3. 意味の分離（通過条件 5）: 同一リテラル `#26a69a` が担っていた 2 つの意味
//      （:138 profit＝成果 / :146 side＝方向）が、サイト単位で別の配線点に割れている。
//
// 機構は CSS（`var(--ct-<slotId>, <現行値>)`）を採る。ポップアップは document.body 配下の
//   要素なので `:root` のカスタムプロパティが継承される＝既に 5-D が配っている変数がそのまま
//   効く。JS で色を解決して文字列を組むより配線が 1 本少なく、テーマ切替時に再描画も要らない。
//
// fallback は **CHROME_CURRENT から生成する**（リテラルを手書きしない）。CSS ファイル側は
//   fallback を逐語で書かざるを得ないが、JS 側は単一情報源を参照できるため、二重定義が
//   構造的に発生しない。

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { TradeMarkersRenderer } from '../js/adapter/front/trade_markers_renderer.js';
import { CHROME_CURRENT, THEME_EXEMPT_LITERALS } from '../js/usecase/chrome_tokens.js';
import { resolveAllChrome } from '../js/usecase/color_resolver.js';

const SRC = readFileSync(
  fileURLToPath(new URL('../js/adapter/front/trade_markers_renderer.js', import.meta.url)), 'utf8',
);

function fakeLwc() {
  return { createSeriesMarkers: () => ({ setMarkers() {} }) };
}

function makePair(overrides = {}) {
  return {
    i: 0, side: 'buy', win: true, profit: 50, volume: 0.1,
    entry: { time: 1781568840, price: 100.5 },
    exit: { time: 1781568900, price: 110.5 },
    ...overrides,
  };
}

// コメントを落としてから走査する（値ではなく**コードに書かれているか**を見る）。
function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1');
}

// var(--ct-X, fallback) を [slotId, fallback] で列挙する。
const VAR_RE = /var\(--ct-([A-Za-z]+),\s*((?:[^()]|\([^()]*\))*)\)/g;

test('TC-TM-T01 恒等: ポップアップが読む var() の fallback は現行リテラルと文字列一致する', () => {
  // Arrange
  const r = new TradeMarkersRenderer({ lwc: fakeLwc(), mainSeries: {} });
  // Act: 生成されるすべての色指定（枠 + 明細 3 系統）を集める。
  const rendered = [
    r._popupHtml(makePair({ profit: 10 })),
    r._popupHtml(makePair({ profit: -10 })),
    r._popupHtml(makePair({ profit: 0, side: 'sell' })),
  ].join('\n');
  // Assert: 各 var() の fallback が台帳の現行リテラルと逐語一致。
  const seen = [...rendered.matchAll(new RegExp(VAR_RE.source, 'g'))];
  assert.ok(seen.length > 0, 'ポップアップが --ct-* を 1 つも読んでいない');
  for (const [, slotId, fallback] of seen) {
    assert.equal(fallback.trim(), CHROME_CURRENT[slotId],
      `--ct-${slotId}: fallback ${fallback} が現行リテラル ${CHROME_CURRENT[slotId]} と違う`);
  }
});

test('TC-TM-T02 恒等: fallback はテーマ未設定時の解決値とも一致する（JS 動作時も同一）', () => {
  // fallback だけ合わせて解決値を放置すると、JS が :root へ書いた瞬間に色が変わる。
  // Arrange
  const { cssSlots } = resolveAllChrome(null);
  const r = new TradeMarkersRenderer({ lwc: fakeLwc(), mainSeries: {} });
  // Act
  const rendered = r._popupHtml(makePair({ profit: 10 }));
  // Assert: 先に「読んでいる変数が 1 つ以上ある」ことを固定する。これが無いと、var() が
  //   1 つも無い（＝未接続の）状態でループが空回りして vacuous に通ってしまう。
  const seen = [...rendered.matchAll(new RegExp(VAR_RE.source, 'g'))];
  assert.ok(seen.length > 0, 'ポップアップが --ct-* を 1 つも読んでいない');
  for (const [, slotId, fallback] of seen) {
    assert.equal(cssSlots[slotId], fallback.trim(),
      `--ct-${slotId}: 解決値 ${cssSlots[slotId]} と fallback ${fallback} が別の色`);
  }
});

test('TC-TM-T03 通過条件 2: trade_markers_renderer.js に素の色リテラルが残っていない', () => {
  // Arrange: var() の fallback は許す（JS 不動作時に現行と一致するための値）。潰してから探す。
  const code = stripComments(SRC).replace(new RegExp(VAR_RE.source, 'g'), 'VAR');
  const exempt = new Set(THEME_EXEMPT_LITERALS.map((e) => e.literal));
  // Act
  const found = [...code.matchAll(/#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)|hsla?\([^)]*\)/g)].map((m) => m[0]);
  // Assert: テンプレート展開（`rgba(${r}, ${g}, ${b}, ${alpha})`）は**色の値ではなく組み立て式**
  //   なので除外する。値が式であることは `${` の存在で判定でき、色リテラルと取り違えようがない。
  const leaked = found.filter((v) => !exempt.has(v) && !v.includes('${'));
  assert.deepEqual(leaked, [], `リテラルが残っている: ${leaked.join(' / ')}`);
});

test('TC-TM-T04 通過条件 5: 成果（profit/loss）と方向（side）が別の配線点に割れている', () => {
  // 実測（接続前）: :138 と :146 はどちらも '#26a69a' / '#ef5350' を書いていた。リテラルが
  //   同じでも意味が違う以上、別の slot でなければ「利益は緑・買いは青」が指定できない。
  // Arrange
  const r = new TradeMarkersRenderer({ lwc: fakeLwc(), mainSeries: {} });
  // Act
  const buyProfit = r._popupHtml(makePair({ profit: 10, side: 'buy' }));
  const sellLoss = r._popupHtml(makePair({ profit: -10, side: 'sell' }));
  // Assert: 成果側と方向側が別の変数名を読む。
  assert.ok(buyProfit.includes('--ct-tradeProfit'), '利益 > 0 が tradeProfit を読まない');
  assert.ok(buyProfit.includes('--ct-tradeSideBuy'), 'side=buy が tradeSideBuy を読まない');
  assert.ok(sellLoss.includes('--ct-tradeLoss'), '利益 < 0 が tradeLoss を読まない');
  assert.ok(sellLoss.includes('--ct-tradeSideSell'), 'side=sell が tradeSideSell を読まない');
  // 成果の変数と方向の変数が同一名に潰れていないこと（分離の実体）。
  assert.notEqual('tradeProfit', 'tradeSideBuy');
  assert.equal(buyProfit.includes('--ct-tradeLoss'), false, '利益 > 0 で損失色を読んでいる');
});

test('TC-TM-T05 通過条件 5: 成果が 0 / 非数値のときは方向色へ倒さず文字色を読む', () => {
  // 境界値（0）と異常系（非数値）。接続前は '#d1d4dc'（＝uiText の現行値）だった。
  // Arrange
  const r = new TradeMarkersRenderer({ lwc: fakeLwc(), mainSeries: {} });
  // Act
  const zero = r._popupHtml(makePair({ profit: 0 }));
  const nan = r._popupHtml(makePair({ profit: null }));
  // Assert
  for (const html of [zero, nan]) {
    assert.equal(html.includes('--ct-tradeProfit'), false, '成果 0 / 非数値で利益色を読んでいる');
    assert.equal(html.includes('--ct-tradeLoss'), false, '成果 0 / 非数値で損失色を読んでいる');
    assert.ok(html.includes('--ct-uiText'), '文字色（uiText）を読んでいない');
  }
});

test('TC-TM-T06 通過条件 4: 取引マーカーの 4 配線点はすべて実際に読まれる（死語でない）', () => {
  // 台帳に在るだけで誰も読まなければ「配ったが受け取り手が無い」＝死んだ配線点。
  // Arrange
  const r = new TradeMarkersRenderer({ lwc: fakeLwc(), mainSeries: {} });
  const rendered = [
    r._popupHtml(makePair({ profit: 10, side: 'buy' })),
    r._popupHtml(makePair({ profit: -10, side: 'sell' })),
  ].join('\n');
  // Act / Assert
  for (const id of ['tradeProfit', 'tradeLoss', 'tradeSideBuy', 'tradeSideSell']) {
    assert.ok(rendered.includes(`--ct-${id}`), `${id}: どこからも読まれていない（死語）`);
  }
});

// ── 配信の結線（受け口だけ作っても、配る側が繋がっていなければ無言で死ぬ）────────
//
// ISSUE-291 の教訓: サーバ側に分岐を作っても front が送らなければ何も起きない。ここでも
//   PairLinesPrimitive に setChromeColors を生やしただけでは、誰も呼ばなければ色は永久に既定の
//   ままである。よって「購読して転送しているか」を端から端まで固定する。

function fakeChartRendererWithChrome() {
  const observers = [];
  return {
    _observers: observers,
    _slots: { pairLineWin: '#aaaaaa', pairLineLoss: '#bbbbbb' },
    addChromeObserver(fn) {
      observers.push(fn);
      fn(this._slots); // 実装と同契約: 登録直後に現在値を 1 回配る。
      return () => {};
    },
    push(slots) { for (const fn of observers) fn(slots); },
  };
}

function fakeSeriesWithPrimitive() {
  const attached = [];
  return { attached, attachPrimitive(p) { attached.push(p); } };
}

test('TC-TM-T08 結線: chartRenderer のクロム購読口へ登録する（配る側と繋がっている）', () => {
  // Arrange
  const cr = fakeChartRendererWithChrome();
  // Act
  new TradeMarkersRenderer({ lwc: fakeLwc(), mainSeries: {}, chartRenderer: cr });
  // Assert
  assert.equal(cr._observers.length, 1, 'クロム購読へ登録していない（配信が届かない）');
});

test('TC-TM-T09 結線: 生成済みペア線 primitive へ配信色が転送される', () => {
  // Arrange
  const cr = fakeChartRendererWithChrome();
  const series = fakeSeriesWithPrimitive();
  const r = new TradeMarkersRenderer({ lwc: fakeLwc(), mainSeries: series, chartRenderer: cr });
  // Act: pairs が来て primitive が生成された後に、テーマが配信される。
  r._attachPairLines([makePair()]);
  cr.push({ pairLineWin: '#cccccc', pairLineLoss: '#dddddd' });
  // Assert
  const primitive = series.attached[0];
  assert.ok(primitive, 'PairLinesPrimitive が装着されていない');
  assert.equal(primitive._win, '#cccccc');
  assert.equal(primitive._loss, '#dddddd');
});

test('TC-TM-T10 結線: 配信が先・生成が後でも色が古いまま残らない（順序非依存）', () => {
  // 起動時にテーマを配ってから、後で trade markers を load する実際の順序。
  // Arrange
  const cr = fakeChartRendererWithChrome();
  const series = fakeSeriesWithPrimitive();
  const r = new TradeMarkersRenderer({ lwc: fakeLwc(), mainSeries: series, chartRenderer: cr });
  // Act: 購読時点（構築時）に配られた色を、後から生まれた primitive が引き継ぐ。
  r._attachPairLines([makePair()]);
  // Assert
  const primitive = series.attached[0];
  assert.equal(primitive._win, '#aaaaaa', '生成前に配信された色が反映されていない');
  assert.equal(primitive._loss, '#bbbbbb');
});

test('TC-TM-T11 結線: chartRenderer 未注入・購読口なしでも例外を投げない（後方互換）', () => {
  // Arrange / Act / Assert
  assert.doesNotThrow(() => new TradeMarkersRenderer({ lwc: fakeLwc(), mainSeries: {} }));
  assert.doesNotThrow(() => new TradeMarkersRenderer({
    lwc: fakeLwc(), mainSeries: {}, chartRenderer: {},
  }));
});

test('TC-TM-T07 枠（cssText）も配線点を読む（地・文字・境界がリテラルのまま残らない）', () => {
  // ポップアップの地だけリテラルで、明細だけテーマ色という判読不能な組み合わせを作らない。
  // Arrange
  const created = [];
  const el = {
    id: '', style: { _css: '', get cssText() { return this._css; }, set cssText(v) { this._css = v; } },
    children: [], appendChild(n) { this.children.push(n); return n; },
  };
  const doc = {
    body: { children: [], appendChild(n) { this.children.push(n); return n; } },
    createElement() { created.push(el); return el; },
    getElementById() { return null; },
  };
  const r = new TradeMarkersRenderer({ lwc: fakeLwc(), mainSeries: {}, document: doc });
  // Act
  r._ensurePopup();
  // Assert
  const css = el.style.cssText;
  for (const id of ['uiPanel', 'uiText', 'uiBorder']) {
    assert.ok(css.includes(`--ct-${id}`), `枠が ${id} を読んでいない: ${css}`);
  }
});
