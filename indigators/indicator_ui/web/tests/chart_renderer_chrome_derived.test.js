// chart_renderer_chrome_derived.test.js — 派生クロムとローソク復元色がテーマに追従すること
//   （基本設計_指標カラーテーマ.md FR-C13・§4.2 #12/#13/#18/#19/#20・§7.6 受入基準 6・§7.8）。
//
// 検定境界を「applier が sink へ渡したか」ではなく **実際の利用点** に置く。段階 3 のレビューで
//   露呈した欠陥は「20 点を受け取ったが 11 点しか読んでいない」であり、配信の検定だけでは
//   受け取った値が捨てられていることを検出できなかった（dimCandle / analysisTint /
//   backgroundFallback / candleUpRestore / candleDownRestore / replayBoundaryDim の 6 点）。
//
// よって本ファイルは「applyChromeColors の後に各利用点が実際に書き込む色」を固定する:
//   - dimCandlesOutsidePair の per-bar 色      = slots.dimCandle
//   - setCandleTransparency(false) の復元色    = slots.candleUpRestore / candleDownRestore
//   - setAnalysisTint(true/false) の背景色     = slots.analysisTint / slots.backgroundFallback
//   いずれもテーマ未配信・恒等テーマでは現行リテラルと**文字列一致**する（D-11 恒等テーマ）。
//
// 構造: Arrange-Act-Assert（AAA）。

import test from 'node:test';
import assert from 'node:assert/strict';

import { ChartRenderer } from '../js/adapter/front/chart_renderer.js';
import { resolveAllChrome } from '../js/usecase/color_resolver.js';
import { CHROME_CURRENT } from '../js/usecase/chrome_tokens.js';

const theme = (roleColors) => ({ themeId: 'thm#1', name: 't', roleColors, tfModifier: null });

// 面（surface）を白へ振ったテーマ。派生 3 点は §4.2 の実測差分で追随する。
const WHITE_SURFACE = theme({ surface: '#ffffff' });

function newRenderer({ backgroundOptions = { type: 'solid', color: CHROME_CURRENT.layoutBackground } } = {}) {
  const chartCalls = [];
  const seriesCalls = [];
  const setDataCalls = [];
  const chart = {
    addSeries: () => mainSeries,
    addPane: () => ({}),
    panes: () => [],
    applyOptions: (o) => chartCalls.push(o),
    options: () => ({ layout: { background: { ...backgroundOptions } } }),
    subscribeCrosshairMove() {},
    timeScale: () => timeScale,
  };
  // setCandles が触る時間軸の最小 fake（本ファイルの検定対象は色であり、幅・位置は問わない）。
  const timeScale = {
    fitContent() {}, scrollToRealTime() {}, applyOptions() {},
    options: () => ({ barSpacing: 8, rightOffset: 0 }),
    getVisibleLogicalRange: () => null,
    width: () => 1000,
  };
  const mainSeries = {
    setData: (d) => setDataCalls.push(d),
    applyOptions: (o) => seriesCalls.push(o),
    priceScale: () => ({ applyOptions() {} }),
  };
  const renderer = new ChartRenderer({
    chart, mainSeries, lwc: { LineSeries: 'L', HistogramSeries: 'H', CandlestickSeries: 'C' },
  });
  return {
    renderer, chartCalls, seriesCalls, setDataCalls,
  };
}

const BARS = [
  { time: 100, open: 1, high: 2, low: 0, close: 1 },
  { time: 200, open: 1, high: 2, low: 0, close: 1 },
  { time: 300, open: 1, high: 2, low: 0, close: 1 },
];

// =========================================================================
// #18 減光ローソク（dimCandle）
// =========================================================================

test('FR-C13: テーマ適用後の減光ローソク色は slots.dimCandle と一致する', () => {
  // Arrange
  const { renderer, setDataCalls } = newRenderer();
  renderer.setCandles(BARS);
  const { slots } = resolveAllChrome(WHITE_SURFACE);
  renderer.applyChromeColors(slots);
  setDataCalls.length = 0;
  // Act
  renderer.dimCandlesOutsidePair({ from: 300, to: 300 });
  // Assert
  const dimmed = setDataCalls.at(-1);
  assert.equal(dimmed[0].color, slots.dimCandle);
  assert.equal(dimmed[0].borderColor, slots.dimCandle);
  assert.equal(dimmed[0].wickColor, slots.dimCandle);
  assert.notEqual(slots.dimCandle, CHROME_CURRENT.dimCandle, '前提: テーマで値が動くこと');
});

test('D-11: テーマ未配信／恒等テーマの減光ローソク色は現行リテラルと文字列一致', () => {
  // Arrange: 未配信（起動直後）
  const a = newRenderer();
  a.renderer.setCandles(BARS);
  a.setDataCalls.length = 0;
  // Act
  a.renderer.dimCandlesOutsidePair({ from: 300, to: 300 });
  // Assert
  assert.equal(a.setDataCalls.at(-1)[0].color, CHROME_CURRENT.dimCandle);

  // Arrange: 恒等テーマ配信後
  const b = newRenderer();
  b.renderer.setCandles(BARS);
  b.renderer.applyChromeColors(resolveAllChrome(null).slots);
  b.setDataCalls.length = 0;
  // Act
  b.renderer.dimCandlesOutsidePair({ from: 300, to: 300 });
  // Assert
  assert.equal(b.setDataCalls.at(-1)[0].color, CHROME_CURRENT.dimCandle);
});

test('R-6 と同旨: テーマ A → テーマ B（surface 未宣言）で派生色が現行リテラルへ戻る（履歴非依存）', () => {
  // Arrange
  const { renderer, setDataCalls } = newRenderer();
  renderer.setCandles(BARS);
  renderer.applyChromeColors(resolveAllChrome(WHITE_SURFACE).slots);
  // Act: surface を宣言しないテーマへ切り替える
  renderer.applyChromeColors(resolveAllChrome(theme({ primary: '#010203' })).slots);
  setDataCalls.length = 0;
  renderer.dimCandlesOutsidePair({ from: 300, to: 300 });
  // Assert
  assert.equal(setDataCalls.at(-1)[0].color, CHROME_CURRENT.dimCandle, '前のテーマの値が残らない');
});

test('§3.4: applyChromeColors 自身は再描画（setData）を起こさない', () => {
  // Arrange
  const { renderer, setDataCalls } = newRenderer();
  renderer.setCandles(BARS);
  setDataCalls.length = 0;
  // Act
  renderer.applyChromeColors(resolveAllChrome(WHITE_SURFACE).slots);
  // Assert
  assert.deepEqual(setDataCalls, [], '色の保持と再描画を混ぜない');
});

// =========================================================================
// #12 / #13 ローソク透明化からの復元色（candleUpRestore / candleDownRestore）
// =========================================================================

test('FR-C11: テーマ適用後の透明化復元色は slots.candleUpRestore / candleDownRestore と一致する', () => {
  // Arrange
  const { renderer, seriesCalls } = newRenderer();
  const { slots } = resolveAllChrome(theme({ bullish: '#00ff00', bearish: '#ff00ff' }));
  renderer.applyChromeColors(slots);
  seriesCalls.length = 0;
  // Act
  renderer.setCandleTransparency(true);
  renderer.setCandleTransparency(false);
  // Assert
  const restored = seriesCalls.at(-1);
  for (const k of ['upColor', 'borderUpColor', 'wickUpColor']) {
    assert.equal(restored[k], slots.candleUpRestore, k);
  }
  for (const k of ['downColor', 'borderDownColor', 'wickDownColor']) {
    assert.equal(restored[k], slots.candleDownRestore, k);
  }
});

test('D-11: テーマ未配信の透明化復元色は現行リテラルと文字列一致', () => {
  // Arrange
  const { renderer, seriesCalls } = newRenderer();
  // Act
  renderer.setCandleTransparency(true);
  renderer.setCandleTransparency(false);
  // Assert
  const restored = seriesCalls.at(-1);
  assert.equal(restored.upColor, CHROME_CURRENT.candleUpRestore);
  assert.equal(restored.downColor, CHROME_CURRENT.candleDownRestore);
});

// =========================================================================
// #19 / #2 分析モード背景 tint（analysisTint）と背景フォールバック（backgroundFallback）
// =========================================================================

test('FR-C13: テーマ適用後の分析 tint は slots.analysisTint と一致する', () => {
  // Arrange
  const { renderer, chartCalls } = newRenderer();
  const { slots } = resolveAllChrome(WHITE_SURFACE);
  renderer.applyChromeColors(slots);
  chartCalls.length = 0;
  // Act
  renderer.setAnalysisTint(true);
  // Assert
  assert.equal(chartCalls.at(-1).layout.background.color, slots.analysisTint);
  assert.notEqual(slots.analysisTint, CHROME_CURRENT.analysisTint, '前提: テーマで値が動くこと');
});

test('FR-C13: 分析 tint 解除の復元色は slots.backgroundFallback と一致する（旧背景に戻らない）', () => {
  // Arrange: テーマ適用**前**に tint 基準を捕捉させる（実運用の順序: 起動 → 分析 → テーマ適用）。
  const { renderer, chartCalls } = newRenderer();
  renderer.setAnalysisTint(false);
  const { slots } = resolveAllChrome(WHITE_SURFACE);
  renderer.applyChromeColors(slots);
  chartCalls.length = 0;
  // Act
  renderer.setAnalysisTint(true);
  renderer.setAnalysisTint(false);
  // Assert
  assert.equal(chartCalls.at(-1).layout.background.color, slots.backgroundFallback);
  assert.equal(chartCalls.at(-1).layout.background.type, 'solid', 'type は捕捉した既定を保つ');
});

test('D-11: テーマ未配信の分析 tint と復元は現行リテラルと文字列一致', () => {
  // Arrange
  const { renderer, chartCalls } = newRenderer();
  // Act
  renderer.setAnalysisTint(true);
  const tinted = chartCalls.at(-1).layout.background.color;
  renderer.setAnalysisTint(false);
  const restored = chartCalls.at(-1).layout.background.color;
  // Assert
  assert.equal(tinted, CHROME_CURRENT.analysisTint);
  assert.equal(restored, CHROME_CURRENT.backgroundFallback);
});

// =========================================================================
// 保持値の購読（#20 リプレイ減光境界の従属先・§7.8 の「書き手 1 箇所」）
// =========================================================================

test('§7.8: クロム色の購読者には登録直後に現在の保持値が 1 回届く（購読順序に依存しない）', () => {
  // Arrange
  const { renderer } = newRenderer();
  const seen = [];
  // Act
  renderer.addChromeObserver((slots) => seen.push(slots.replayBoundaryDim));
  // Assert
  assert.deepEqual(seen, [CHROME_CURRENT.replayBoundaryDim], '未配信でも現行リテラルが届く');
});

test('§7.8: applyChromeColors のたびに購読者へ保持値が届く（派生も追随する）', () => {
  // Arrange
  const { renderer } = newRenderer();
  const seen = [];
  renderer.addChromeObserver((slots) => seen.push(slots.replayBoundaryDim));
  const { slots } = resolveAllChrome(WHITE_SURFACE);
  // Act
  renderer.applyChromeColors(slots);
  // Assert
  assert.deepEqual(seen, [CHROME_CURRENT.replayBoundaryDim, slots.replayBoundaryDim]);
});

test('購読解除できる（解除後は届かない）', () => {
  // Arrange
  const { renderer } = newRenderer();
  const seen = [];
  const off = renderer.addChromeObserver((slots) => seen.push(slots.replayBoundaryDim));
  // Act
  off();
  renderer.applyChromeColors(resolveAllChrome(WHITE_SURFACE).slots);
  // Assert
  assert.equal(seen.length, 1, '登録直後の 1 回だけ');
});

test('非関数の購読要求は無視する（全域的・例外を投げない）', () => {
  const { renderer } = newRenderer();
  assert.doesNotThrow(() => renderer.addChromeObserver(null));
  assert.doesNotThrow(() => renderer.applyChromeColors(resolveAllChrome(null).slots));
});

// =========================================================================
// 表示モード × テーマ の組み合わせ（§7.8「クロムの色の書き手は 1 箇所」）
//
// 病因（段階 3 の再レビューで露呈）: 「今の見え方」を決める入力は 2 系統（配信済みクロム色と
//   表示モード＝透明化 / ペア外減光 / 分析 tint）あるのに、出力を書く場所が 4 メソッドに分かれ、
//   互いの状態を知らなかった。よって片方の入力を変えると他方の入力が無かったことにされた
//   （テーマ適用で透明が不透明へ戻る・tint が消える・減光ローソクだけ旧色で残る）。
//   検定は**状態の組み合わせ**で固定する（単一状態の検定は上の各節が担う）。
// =========================================================================

// 分析 tint と透明化を同時に扱うため、そのときの最終出力を 1 か所で読む小道具。
const lastCandleColors = (seriesCalls) => {
  const o = seriesCalls.at(-1) ?? {};
  return [o.upColor, o.borderUpColor, o.wickUpColor, o.downColor, o.borderDownColor, o.wickDownColor];
};
const lastBackground = (chartCalls) => chartCalls.at(-1).layout.background.color;
const TRANSPARENT = 'rgba(0,0,0,0)';
// 面を中間色へ振ったテーマ。白（WHITE_SURFACE）だと派生 3 点が 255 で飽和して従属先と同値になり、
//   「tint が消えたのか保たれたのか」を区別できない（値が動くことを前提に置く検定に使う）。
const GRAY_SURFACE = theme({ surface: '#202020' });

test('🔴-A: 透明化 ON のままテーマを適用してもローソクは透明を保つ（受入基準 16）', () => {
  // Arrange: MP sessions / tf-period 列 ON 相当（ローソク透明）。
  const { renderer, seriesCalls } = newRenderer();
  renderer.setCandleTransparency(true);
  const { slots } = resolveAllChrome(theme({ bullish: '#00ff00', bearish: '#ff00ff' }));
  // Act
  renderer.applyChromeColors(slots);
  // Assert
  assert.deepEqual(lastCandleColors(seriesCalls), Array(6).fill(TRANSPARENT), 'テーマ適用で不透明へ戻さない');
});

test('🔴-A: 透明化 ON × テーマ適用のあと OFF にすると新しいテーマ色で復元する', () => {
  // Arrange
  const { renderer, seriesCalls } = newRenderer();
  renderer.setCandleTransparency(true);
  const { slots } = resolveAllChrome(theme({ bullish: '#00ff00', bearish: '#ff00ff' }));
  renderer.applyChromeColors(slots);
  // Act
  renderer.setCandleTransparency(false);
  // Assert
  assert.deepEqual(
    lastCandleColors(seriesCalls),
    [slots.candleUpRestore, slots.candleUpRestore, slots.candleUpRestore,
      slots.candleDownRestore, slots.candleDownRestore, slots.candleDownRestore],
  );
});

test('🔴-B: 分析 tint ON のままテーマを適用しても背景は tint 色を保つ', () => {
  // Arrange: 分析モード ON（背景 tint 表示中）。
  const { renderer, chartCalls } = newRenderer();
  renderer.setAnalysisTint(true);
  const { slots } = resolveAllChrome(GRAY_SURFACE);
  // Act
  renderer.applyChromeColors(slots);
  // Assert
  assert.equal(lastBackground(chartCalls), slots.analysisTint, 'モードは ON のまま＝状態表示を誤らせない');
  assert.notEqual(slots.analysisTint, slots.layoutBackground, '前提: tint 色と地の色が区別できること');
});

test('🔴-B: 分析 tint ON × テーマ適用のあと OFF にすると新しい背景色へ戻る', () => {
  // Arrange
  const { renderer, chartCalls } = newRenderer();
  renderer.setAnalysisTint(true);
  const { slots } = resolveAllChrome(GRAY_SURFACE);
  renderer.applyChromeColors(slots);
  // Act
  renderer.setAnalysisTint(false);
  // Assert
  assert.equal(lastBackground(chartCalls), slots.backgroundFallback);
});

test('F-7: ペア減光中にテーマを適用するとペア外の減光色も新しい値へ更新される', () => {
  // Arrange: ペア hover 中（ペア外を減光済み）。
  const { renderer, setDataCalls } = newRenderer();
  renderer.setCandles(BARS);
  renderer.dimCandlesOutsidePair({ from: 300, to: 300 });
  const { slots } = resolveAllChrome(WHITE_SURFACE);
  // Act
  renderer.applyChromeColors(slots);
  // Assert
  const bars = setDataCalls.at(-1);
  assert.equal(bars[0].color, slots.dimCandle, 'ペア外だけ旧色で取り残さない');
  assert.equal(bars[2].color, undefined, 'ペア内は原色のまま（色を上書きしない）');
});

test('F-7: 減光していないときテーマ適用はローソクデータへ触れない（トリムを復元しない）', () => {
  // Arrange: リプレイ/スナップショットのトリム中（データの所有者は setCandleTrim）。
  const { renderer, setDataCalls } = newRenderer();
  renderer.setCandles(BARS);
  renderer.setCandleTrim(200);
  setDataCalls.length = 0;
  // Act
  renderer.applyChromeColors(resolveAllChrome(WHITE_SURFACE).slots);
  // Assert
  assert.deepEqual(setDataCalls, [], '減光オーバーレイが無効なら書き手は名乗り出ない');
});

test('F-7: 減光 × トリムの同時成立で、テーマ適用が所有バー集合を変えない', () => {
  // Arrange: トリム中（T=200 まで＝2 本を所有）に、さらにペア hover の減光が乗っている状態。
  //   §3.4 v0.3.1 が許すのは「自分が所有する per-bar 色の塗り直し」だけで、どのバーが存在するかの
  //   変更は含まれない。全件（_baseCandles）を書き戻すと T より後のバーが再表示される。
  const { renderer, setDataCalls } = newRenderer();
  renderer.setCandles(BARS);
  renderer.setCandleTrim(200);
  renderer.dimCandlesOutsidePair({ from: 200, to: 200 });
  renderer.setCandleTrim(200); // 減光でトリムが解けた分を復帰させる（所有は再び 2 本）。
  setDataCalls.length = 0;

  // Act: 減光が有効なままテーマを適用する。
  const slots = resolveAllChrome(WHITE_SURFACE).slots;
  renderer.applyChromeColors(slots);

  // Assert: 塗り直しは起きるが、本数（＝所有集合）は増えない。
  assert.equal(setDataCalls.length, 1, '減光中は per-bar 色を塗り直す');
  const bars = setDataCalls.at(-1);
  assert.equal(bars.length, 2, `テーマ適用がトリムを解除している（本数 ${bars.length}）`);
  assert.deepEqual(bars.map((b) => b.time), [100, 200]);
  assert.equal(bars[0].color, slots.dimCandle, 'ペア外は新しい減光色へ追随する');
  assert.equal(bars[1].color, undefined, 'ペア内は原色のまま');
});

test('D-11 恒等: モード・テーマとも未設定なら 3 出力すべてが現行リテラルと文字列一致', () => {
  // Arrange
  const { renderer, chartCalls, seriesCalls, setDataCalls } = newRenderer();
  renderer.setCandles(BARS);
  // Act
  renderer.applyChromeColors(resolveAllChrome(null).slots);
  renderer.dimCandlesOutsidePair({ from: 300, to: 300 });
  // Assert
  assert.equal(lastBackground(chartCalls), CHROME_CURRENT.layoutBackground);
  assert.deepEqual(lastCandleColors(seriesCalls), [
    CHROME_CURRENT.candleUp, CHROME_CURRENT.candleUp, CHROME_CURRENT.candleUp,
    CHROME_CURRENT.candleDown, CHROME_CURRENT.candleDown, CHROME_CURRENT.candleDown,
  ]);
  assert.equal(seriesCalls.at(-1).priceLineColor, CHROME_CURRENT.priceLine);
  assert.equal(setDataCalls.at(-1)[0].color, CHROME_CURRENT.dimCandle);
});

test('適用順序は結果に影響しない（テーマ → モード と モード → テーマ で同一出力）', () => {
  // Arrange
  const { slots } = resolveAllChrome(theme({ surface: '#202020', bullish: '#00ff00' }));
  const a = newRenderer(); // テーマ → モード
  const b = newRenderer(); // モード → テーマ
  a.renderer.setCandles(BARS);
  b.renderer.setCandles(BARS);
  // Act
  a.renderer.applyChromeColors(slots);
  a.renderer.setCandleTransparency(true);
  a.renderer.setAnalysisTint(true);
  a.renderer.dimCandlesOutsidePair({ from: 300, to: 300 });

  b.renderer.setCandleTransparency(true);
  b.renderer.setAnalysisTint(true);
  b.renderer.dimCandlesOutsidePair({ from: 300, to: 300 });
  b.renderer.applyChromeColors(slots);
  // Assert
  assert.deepEqual(lastCandleColors(a.seriesCalls), lastCandleColors(b.seriesCalls));
  assert.equal(lastBackground(a.chartCalls), lastBackground(b.chartCalls));
  assert.equal(a.setDataCalls.at(-1)[0].color, b.setDataCalls.at(-1)[0].color);
  assert.equal(lastBackground(a.chartCalls), slots.analysisTint, '前提: tint ON の出力を見ている');
  assert.deepEqual(lastCandleColors(a.seriesCalls), Array(6).fill(TRANSPARENT), '前提: 透明の出力を見ている');
});
