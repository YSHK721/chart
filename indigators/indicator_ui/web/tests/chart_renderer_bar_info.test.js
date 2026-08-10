// ChartRenderer.barInfoAt（右クリック位置の足の情報・ユーザー指示 2026-08-09）の仕様検証。
//
// 固定する規約:
//   - 座標 → 足の解決は upstream（timeScale().coordinateToTime）に委ね、本 class に閉じる。
//   - 返す材料は情報ウィンドと同じ（四本値・指標値・当日 MP）で、並びは凡例と同じ（ペイン順・適用順）。
//   - **その足に無い指標値は最新値へ落とさない**（凡例は「クロスヘアが無ければ最新値」だが、
//     足を名指しでコピーする場面で最新値を混ぜると別の足の値を配ってしまう）。
//   - 足の無い座標（データ範囲外）は null（＝呼び出し側はコピーしない）。
// 構造: Arrange-Act-Assert。Fake chart/series を注入（DOM 非依存）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ChartRenderer } from '../js/adapter/front/chart_renderer.js';

const LineSeries = { kind: 'Line' };

function fakeSeries(def) {
  return {
    _def: def, _data: [], _options: {}, _createOpts: null, _priceLines: [],
    setData(points) { this._data = points ?? []; },
    data() { return this._data ?? []; },
    update() {},
    applyOptions(o) { Object.assign(this._options, o); },
    createPriceLine(opt) { const pl = { opt }; this._priceLines.push(pl); return pl; },
    removePriceLine(pl) { this._priceLines = this._priceLines.filter((x) => x !== pl); },
  };
}

// time→座標の対応は「x/10 の時刻」を返す最小 fake（実 lwc は座標→バー index→time）。
//   範囲外（RANGE 外）は null を返す＝実 lwc の「index がデータ範囲外なら null」に対応させる。
function fakeChart(times) {
  const panesArr = [];
  const makePane = () => {
    const pane = {
      _series: [],
      paneIndex() { return panesArr.indexOf(pane); },
      getHeight() { return 100; },
      setStretchFactor() {}, setPreserveEmptyPane() {},
      addSeries(def, opts) {
        const s = fakeSeries(def); s._createOpts = opts; s._pane = pane;
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

function newRenderer(times = [10, 20, 30]) {
  const chart = fakeChart(times);
  const main = fakeSeries(undefined);
  const renderer = new ChartRenderer({ chart, mainSeries: main, lwc: { LineSeries } });
  return { renderer, chart, main };
}

const CANDLES = [
  { time: 10, open: 1, high: 2, low: 0.5, close: 1.5 },
  { time: 20, open: 1.5, high: 2.5, low: 1.4, close: 2.0 },
  { time: 30, open: 2.0, high: 2.2, low: 1.8, close: 1.9 },
];

test('barInfoAt: 座標が指す足の四本値と指標値を返す', () => {
  // Arrange: 3 本のローソクと、全足に値を持つ指標 1 件。
  const { renderer } = newRenderer();
  renderer.setCandles(CANDLES);
  renderer.renderLine('rsi#1', [{
    name: 'rsi', kind: 'line', style: 'solid', width: 1, color: '#fff',
    data: [{ time: 10, value: 40 }, { time: 20, value: 55 }, { time: 30, value: 60 }],
  }]);

  // Act: 2 本目（x=10 → time=20）の位置。
  const info = renderer.barInfoAt(10);

  // Assert
  assert.equal(info.time, 20);
  assert.deepEqual(info.ohlc, { open: 1.5, high: 2.5, low: 1.4, close: 2.0 });
  assert.deepEqual(info.indicators, [{ instanceId: 'rsi#1', values: [{ name: 'rsi', value: 55, color: '#fff' }] }]);
});

test('barInfoAt: その足に値が無い指標は最新値へ落ちない（undefined のまま）', () => {
  // Arrange: 指標は 3 本目にしか値が無い（例: 計算 warmup 明け）。
  const { renderer } = newRenderer();
  renderer.setCandles(CANDLES);
  renderer.renderLine('rsi#1', [{
    name: 'rsi', kind: 'line', style: 'solid', width: 1, color: '#fff',
    data: [{ time: 30, value: 60 }],
  }]);

  // Act: 1 本目（time=10）を指す。
  const info = renderer.barInfoAt(0);

  // Assert: 60（最新値）を持ち込まない。
  assert.equal(info.indicators[0].values[0].value, undefined);
});

test('barInfoAt: 凡例（クロスヘア経路）の最新値フォールバックは従来どおり残る', () => {
  // Arrange: 同じ材料でも paneLegendModel（凡例）は最新値へ落とす＝表示規約は不変。
  const { renderer } = newRenderer();
  renderer.setCandles(CANDLES);
  renderer.renderLine('rsi#1', [{
    name: 'rsi', kind: 'line', style: 'solid', width: 1, color: '#fff',
    data: [{ time: 30, value: 60 }],
  }]);

  // Act: クロスヘア無し（param=null）の凡例モデル。
  const model = renderer.paneLegendModel(null);

  // Assert
  assert.equal(model.groups[0].rows[0].values[0].value, 60);
});

test('barInfoAt: 足の無い座標（データ範囲外）は null', () => {
  const { renderer } = newRenderer();
  renderer.setCandles(CANDLES);
  assert.equal(renderer.barInfoAt(999), null);
});

test('barInfoAt: 非表示の指標は出さない（凡例と同じ可視規約）', () => {
  const { renderer } = newRenderer();
  renderer.setCandles(CANDLES);
  renderer.renderLine('rsi#1', [{
    name: 'rsi', kind: 'line', style: 'solid', width: 1, color: '#fff',
    data: [{ time: 20, value: 55 }],
  }]);
  renderer.setVisible('rsi#1', false);
  assert.deepEqual(renderer.barInfoAt(10).indicators, [{ instanceId: 'rsi#1', values: [] }]);
});

test('barInfoAt: 当日 MP（sessions 表示中）は同じ time で引いて載せる', () => {
  const { renderer } = newRenderer();
  renderer.setCandles(CANDLES);
  renderer.setSessionMP(new Map([[20, { poc: 1.9, vah: 2.4, val: 1.5 }]]));
  assert.deepEqual(renderer.barInfoAt(10).sessionMP, { poc: 1.9, vah: 2.4, val: 1.5 });
  assert.equal(renderer.barInfoAt(0).sessionMP, null);
});

test('barInfoAt: timeScale 非提供（Fake/SSR）は null（例外にしない）', () => {
  const main = fakeSeries(undefined);
  const chart = { addSeries: () => main, subscribeCrosshairMove() {} };
  const renderer = new ChartRenderer({ chart, mainSeries: main, lwc: { LineSeries } });
  assert.equal(renderer.barInfoAt(10), null);
});
