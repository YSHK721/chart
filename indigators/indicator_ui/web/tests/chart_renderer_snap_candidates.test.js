// ChartRenderer.snapCandidatesAt / paneIndexAtCoordinate（ISSUE-368 スライス 8-b）の仕様検証。
//
// 設計入力（唯一の仕様源）: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md
//   「ピッカー経路の実測検証」2:
//     - `snapCandidatesAt(x)`: 価格ペイン（pane 0）の系列値＋水準線（hlinePayloads）＋当該足 OHLC を
//       **プレーンデータ**で列挙する（barInfoAt は paneIndex を捨て水準線を含まない＝不足 2 点）。
//     - `paneIndexAtCoordinate(y)`: **必須**。vendor 実測で `coordinateToPrice` はクランプ無しの
//       線形外挿＝オシレーターペインのクリックが異常価格を返すため、価格ペイン判定が要る。
//   「1」: `barInfoAt` は 1 byte も変えない（既存 deepEqual 検定を壊さないため）＝本 2 面は追加のみ。
//
// 固定する規約:
//   - series 実体・lwc 型は返さない（隔離維持。返すのは {kind,label,price} のプレーンデータ）。
//   - 値の出所は凡例と同じ経路（_slotValues + その足の値）＝可視規約も凡例と同一。
//   - **その足に値が無い指標は載せない**（barInfoAt と同じく最新値へ落とさない）。
//   - 列挙順は固定（系列 → 水準線 → OHLC）。スナップ解決器の同距離規則が「先頭優先」であるため、
//     並びが揺れると同じ操作が別の価格を入れる。
// 構造: Arrange-Act-Assert。Fake chart/series を注入（DOM 非依存・chart_renderer_bar_info と同作法）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ChartRenderer } from '../js/adapter/front/chart_renderer.js';

const LineSeries = { kind: 'Line' };

function fakeSeries() {
  return {
    _data: [], _options: {}, _createOpts: null, _priceLines: [],
    setData(points) { this._data = points ?? []; },
    data() { return this._data ?? []; },
    update() {},
    applyOptions(o) { Object.assign(this._options, o); },
    createPriceLine(opt) { const pl = { opt }; this._priceLines.push(pl); return pl; },
    removePriceLine(pl) { this._priceLines = this._priceLines.filter((x) => x !== pl); },
  };
}

// time→座標の対応は「x/10 の時刻」を返す最小 fake（実 lwc は座標→バー index→time）。
//   ペイン高は heights で与える（実 lwc の pane.getHeight() 相当）。
function fakeChart(times, heights = [300, 100]) {
  const panesArr = [];
  const makePane = () => {
    const pane = {
      _series: [],
      paneIndex() { return panesArr.indexOf(pane); },
      getHeight() { return heights[panesArr.indexOf(pane)] ?? 0; },
      setStretchFactor() {}, setPreserveEmptyPane() {},
      addSeries(def, opts) {
        const s = fakeSeries(); s._createOpts = opts; s._pane = pane;
        pane._series.push(s); return s;
      },
    };
    return pane;
  };
  panesArr.push(makePane());
  return {
    panes() { return panesArr; },
    addPane() { const p = makePane(); panesArr.push(p); return p; },
    removePane(i) { panesArr.splice(i, 1); },
    addSeries(def, opts) { return panesArr[0].addSeries(def, opts); },
    removeSeries() {},
    applyOptions() {},
    timeScale() {
      return {
        fitContent() {},
        coordinateToTime(x) {
          const t = times[Math.round(x / 10)];
          return t === undefined ? null : t;
        },
      };
    },
    subscribeCrosshairMove() {},
  };
}

function newRenderer(times = [10, 20, 30], heights = [300, 100]) {
  const chart = fakeChart(times, heights);
  const main = fakeSeries();
  main.getPane = () => chart.panes()[0];
  const renderer = new ChartRenderer({ chart, mainSeries: main, lwc: { LineSeries } });
  return { renderer, chart, main };
}

const CANDLES = [
  { time: 10, open: 1, high: 2, low: 0.5, close: 1.5 },
  { time: 20, open: 1.5, high: 2.5, low: 1.4, close: 2.0 },
  { time: 30, open: 2.0, high: 2.2, low: 1.8, close: 1.9 },
];

test('TC-SC01 足の無い座標（データ範囲外）は null（候補を作らない）', () => {
  // Arrange
  const { renderer } = newRenderer();
  renderer.setCandles(CANDLES);
  // Act / Assert
  assert.equal(renderer.snapCandidatesAt(999), null);
});

test('TC-SC02 timeScale 非提供（Fake/SSR）は null（例外にしない）', () => {
  // Arrange
  const main = fakeSeries();
  const chart = { addSeries: () => main, subscribeCrosshairMove() {} };
  const renderer = new ChartRenderer({ chart, mainSeries: main, lwc: { LineSeries } });
  // Act / Assert
  assert.equal(renderer.snapCandidatesAt(10), null);
});

test('TC-SC03 当該足の OHLC 4 値が候補に載る（R-P2「ローソク OHLC（当該足）」）', () => {
  // Arrange: 3 本のローソク。
  const { renderer } = newRenderer();
  renderer.setCandles(CANDLES);
  // Act: 2 本目（x=10 → time=20）。
  const got = renderer.snapCandidatesAt(10);
  // Assert: 別の足（1・3 本目）の値を混ぜない。
  assert.deepEqual(got.filter((c) => c.kind === 'ohlc'), [
    { kind: 'ohlc', label: 'open', price: 1.5 },
    { kind: 'ohlc', label: 'high', price: 2.5 },
    { kind: 'ohlc', label: 'low', price: 1.4 },
    { kind: 'ohlc', label: 'close', price: 2.0 },
  ]);
});

test('TC-SC04 価格ペインの指標系列は当該足の値で候補に載る（R-P2「表示中の全指標系列の値」）', () => {
  // Arrange: overlay（価格ペイン）の移動平均 1 本。
  const { renderer } = newRenderer();
  renderer.setCandles(CANDLES);
  renderer.renderLine('sma#1', [{
    name: 'sma20', kind: 'line', style: 'solid', width: 1, color: '#fff',
    data: [{ time: 10, value: 1.2 }, { time: 20, value: 1.7 }, { time: 30, value: 1.9 }],
  }]);
  // Act
  const got = renderer.snapCandidatesAt(10);
  // Assert
  assert.deepEqual(got.filter((c) => c.kind === 'series'), [
    { kind: 'series', label: 'sma20', price: 1.7 },
  ]);
});

test('TC-SC05 その足に値が無い指標は候補にしない（最新値へ落とさない＝barInfoAt と同じ規約）', () => {
  // Arrange: 指標は 3 本目にしか値が無い（計算 warmup 明け）。
  const { renderer } = newRenderer();
  renderer.setCandles(CANDLES);
  renderer.renderLine('sma#1', [{
    name: 'sma20', kind: 'line', style: 'solid', width: 1, color: '#fff',
    data: [{ time: 30, value: 1.9 }],
  }]);
  // Act: 1 本目（time=10）を指す。
  const got = renderer.snapCandidatesAt(0);
  // Assert: 1.9（最新値）を候補に混ぜない＝別の足の値へ吸い付かない。
  assert.deepEqual(got.filter((c) => c.kind === 'series'), []);
});

test('TC-SC06 オシレーターペインの系列は候補にしない（価格ペインの値ではない）', () => {
  // Arrange: 専用ペイン（pane=true）の RSI と、価格ペインの移動平均。
  const { renderer } = newRenderer();
  renderer.setCandles(CANDLES);
  renderer.renderLine('rsi#1', [{
    name: 'rsi', kind: 'line', style: 'solid', width: 1, color: '#0f0',
    data: [{ time: 20, value: 55 }],
  }], { pane: true });
  renderer.renderLine('sma#1', [{
    name: 'sma20', kind: 'line', style: 'solid', width: 1, color: '#fff',
    data: [{ time: 20, value: 1.7 }],
  }]);
  // Act
  const got = renderer.snapCandidatesAt(10);
  // Assert: 55（RSI 値）を価格として候補に入れると、桁違いの価格が入力される。
  assert.deepEqual(got.filter((c) => c.kind === 'series').map((c) => c.label), ['sma20']);
});

test('TC-SC07 非表示の指標は候補にしない（凡例と同じ可視規約）', () => {
  // Arrange
  const { renderer } = newRenderer();
  renderer.setCandles(CANDLES);
  renderer.renderLine('sma#1', [{
    name: 'sma20', kind: 'line', style: 'solid', width: 1, color: '#fff',
    data: [{ time: 20, value: 1.7 }],
  }]);
  renderer.setVisible('sma#1', false);
  // Act
  const got = renderer.snapCandidatesAt(10);
  // Assert
  assert.deepEqual(got.filter((c) => c.kind === 'series'), []);
});

test('TC-SC08 価格ペインの水準線（hlinePayloads）が候補に載る（barInfoAt に無い不足分）', () => {
  // Arrange: 価格バンド系の水準線 2 本（overlay＝価格ペインの priceLine）。
  const { renderer } = newRenderer();
  renderer.setCandles(CANDLES);
  renderer.renderHorizontal('mp#1', [
    { name: 'poc', price: 1.9, text: 'POC', width: 1, style: 'solid', color: '#ff0' },
    { name: 'vah', price: 2.4, text: 'VAH', width: 1, style: 'dashed', color: '#ff0' },
  ]);
  // Act
  const got = renderer.snapCandidatesAt(10);
  // Assert
  assert.deepEqual(got.filter((c) => c.kind === 'level'), [
    { kind: 'level', label: 'POC', price: 1.9 },
    { kind: 'level', label: 'VAH', price: 2.4 },
  ]);
});

test('TC-SC09 オシレーターペインの水準線は候補にしない（σ 水準は価格ではない）', () => {
  // Arrange: 専用ペインの指標に水準線（σ ライン）を付ける。
  const { renderer } = newRenderer();
  renderer.setCandles(CANDLES);
  renderer.renderLine('rsi#1', [{
    name: 'rsi', kind: 'line', style: 'solid', width: 1, color: '#0f0',
    data: [{ time: 20, value: 55 }],
  }], { pane: true });
  renderer.renderHorizontal('rsi#1', [
    { name: 'ub', price: 70, text: '70', width: 1, style: 'dashed', color: '#888' },
  ]);
  // Act
  const got = renderer.snapCandidatesAt(10);
  // Assert
  assert.deepEqual(got.filter((c) => c.kind === 'level'), []);
});

test('TC-SC10 非表示インスタンスの水準線は候補にしない（描画と候補を一致させる）', () => {
  // Arrange
  const { renderer } = newRenderer();
  renderer.setCandles(CANDLES);
  renderer.renderHorizontal('mp#1', [
    { name: 'poc', price: 1.9, text: 'POC', width: 1, style: 'solid', color: '#ff0' },
  ]);
  renderer.setVisible('mp#1', false);
  // Act / Assert
  assert.deepEqual(renderer.snapCandidatesAt(10).filter((c) => c.kind === 'level'), []);
});

test('TC-SC11 列挙順は 系列 → 水準線 → OHLC で固定（同距離は先頭優先＝解決が毎回同じ）', () => {
  // Arrange: 同じ価格 1.5 に 系列・水準線・OHLC(open) の 3 候補が重なる。
  const { renderer } = newRenderer();
  renderer.setCandles(CANDLES);
  renderer.renderLine('sma#1', [{
    name: 'sma20', kind: 'line', style: 'solid', width: 1, color: '#fff',
    data: [{ time: 20, value: 1.5 }],
  }]);
  renderer.renderHorizontal('mp#1', [
    { name: 'poc', price: 1.5, text: 'POC', width: 1, style: 'solid', color: '#ff0' },
  ]);
  // Act
  const kinds = renderer.snapCandidatesAt(10).map((c) => c.kind);
  // Assert: 種別の初出順が固定されている（スナップ解決器の先頭優先規則の入力）。
  assert.deepEqual([...new Set(kinds)], ['series', 'level', 'ohlc']);
});

// ---------------------------------------------------------------------------
// paneIndexAtCoordinate（設計書「ピッカー経路の実測検証」2・**必須**）
//   vendor 実測で coordinateToPrice はクランプ無しの線形外挿＝オシレーターペインのクリックが
//   異常価格を返す。価格ペインの外を「価格」として受け取らないためのガード。
// ---------------------------------------------------------------------------

// 価格ペイン（高 300）＋オシレーターペイン（高 100）の 2 段構成を作る。
function twoPaneRenderer() {
  const { renderer } = newRenderer([10, 20, 30], [300, 100]);
  renderer.setCandles(CANDLES);
  renderer.renderLine('rsi#1', [{
    name: 'rsi', kind: 'line', style: 'solid', width: 1, color: '#0f0',
    data: [{ time: 20, value: 55 }],
  }], { pane: true });
  return renderer;
}

test('TC-SC12 価格ペインの y はそのペイン番号を返す（上端・下端の境界を含む）', () => {
  // Arrange: ペイン高 [300, 100]（価格ペイン 0・オシレーター 1）。
  const renderer = twoPaneRenderer();
  // Act / Assert
  assert.equal(renderer.paneIndexAtCoordinate(0), 0, '上端（0px）は価格ペイン');
  assert.equal(renderer.paneIndexAtCoordinate(150), 0);
  assert.equal(renderer.paneIndexAtCoordinate(299), 0, '下端の直前は価格ペイン');
});

test('TC-SC13 オシレーターペインの y は非 0 を返す（価格として受けてはならない領域）', () => {
  // Arrange
  const renderer = twoPaneRenderer();
  // Act / Assert
  assert.equal(renderer.paneIndexAtCoordinate(300), 1, '境界（300px）は 2 番目のペインの先頭');
  assert.equal(renderer.paneIndexAtCoordinate(399), 1);
});

test('TC-SC14 ペイン領域の外（時間軸・負の y・非有限）は null', () => {
  // Arrange
  const renderer = twoPaneRenderer();
  // Act / Assert
  assert.equal(renderer.paneIndexAtCoordinate(400), null, 'ペイン合計高より下（時間軸）は所属なし');
  assert.equal(renderer.paneIndexAtCoordinate(-1), null);
  assert.equal(renderer.paneIndexAtCoordinate(NaN), null);
});

test('TC-SC15 panes 非提供（Fake/SSR）は null（例外にしない・非対応環境の縮退）', () => {
  // Arrange
  const main = fakeSeries();
  const chart = { addSeries: () => main, subscribeCrosshairMove() {} };
  const renderer = new ChartRenderer({ chart, mainSeries: main, lwc: { LineSeries } });
  // Act / Assert
  assert.equal(renderer.paneIndexAtCoordinate(10), null);
});

// ---------------------------------------------------------------------------
// ペイン幾何の単一ソース（SOLID リファクタリング 2026-08-20）
//
//   凡例のチップ位置（paneLegendModel の group.top）と、座標→ペイン判定
//   （paneIndexAtCoordinate）は同じ幾何を見なければならない。累積の式が 2 か所にあると、
//   区切り高の扱いが片方だけ変わったとき「凡例は正しいのにクリック判定だけずれる」
//   ＝下段ペインのクリックを価格として受け取る（裁定 2026-08-20 の違反）状態になる。
//   実装は `_paneTops` 1 か所に寄せた。ここでは**外から見える対応**を固定する。
// ---------------------------------------------------------------------------

test('TC-SC16 凡例の group.top と paneIndexAtCoordinate の境界が一致する（幾何の単一ソース）', () => {
  // Arrange: 価格ペイン＋オシレーター 2 枚の 3 段構成。
  const { renderer } = newRenderer([10, 20, 30], [300, 100, 80]);
  renderer.setCandles(CANDLES);
  renderer.renderLine('rsi#1', [{
    name: 'rsi', kind: 'line', style: 'solid', width: 1, color: '#0f0',
    data: [{ time: 20, value: 55 }],
  }], { pane: true });
  renderer.renderLine('macd#1', [{
    name: 'macd', kind: 'line', style: 'solid', width: 1, color: '#00f',
    data: [{ time: 20, value: 1 }],
  }], { pane: true });
  // Act
  const { groups } = renderer.paneLegendModel();
  // Assert: 凡例が言う上端と高さの範囲が、そのままペイン判定の範囲になっている。
  assert.equal(groups.length >= 2, true, '前提: 複数ペインが在る');
  for (const group of groups) {
    assert.equal(
      renderer.paneIndexAtCoordinate(group.top),
      group.paneIndex,
      `凡例の上端 ${group.top}px がペイン ${group.paneIndex} と判定されない（幾何が 2 本に割れている）`,
    );
    assert.equal(
      renderer.paneIndexAtCoordinate(group.top + group.height - 1),
      group.paneIndex,
      `凡例の下端直前がペイン ${group.paneIndex} と判定されない（幾何が 2 本に割れている）`,
    );
  }
});
