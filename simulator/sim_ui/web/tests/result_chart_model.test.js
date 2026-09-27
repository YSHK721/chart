// result_chart_model.js の単体検証（パネルの並び・whitespace・足の照合・読み込み範囲・計算量）。
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  candleRequestOf,
  candlesMatchRunBars,
  resultChartInstances,
  resultChartPanes,
  toPoints,
} from '../js/usecase/result_chart_model.js';

const T = [100, 160, 220];

function overlay(indicators = []) {
  return {
    timeframe: '1m',
    dataset_ref: 'jp225_mt5_spread',
    indicators,
    account: {
      time: T,
      balance: [1000, 1000, 1010],
      equity: [1000, 995, 1010],
      drawdown: [0, 5, 0],
      realized_pnl: [0, 0, 10],
      floating_pnl: [0, -5, 0],
      margin_level: [null, 500, null],
    },
  };
}

test('null と非有限は whitespace（time だけ）になり、0 を置かない', () => {
  assert.deepEqual(toPoints(T, [1, null, Number.NaN]), [{ time: 100, value: 1 }, { time: 160 }, { time: 220 }]);
});

test('価格上の指標はパネル 0、別窓の指標は自分のパネル、口座系 4 枚が後ろに続く', () => {
  const panes = resultChartPanes(overlay([
    { series: 'sma', placement: 'price', time: T, value: [null, 1, 2] },
    { series: 'madiff', placement: 'pane', time: T, value: [1, 2, 3] },
  ]));
  assert.deepEqual(panes.map((p) => p.title), ['価格', 'madiff', '残高・有効証拠金', 'DD', '損益', '証拠金維持率(%)']);
  assert.deepEqual(panes[0].series.map((s) => s.name), ['sma']);
});

test('DD は下向きに描く・維持率は保有の無い足で値なし', () => {
  const panes = resultChartPanes(overlay());
  const dd = panes.find((p) => p.title === 'DD').series[0].points;
  assert.deepEqual(dd.map((p) => p.value), [-0, -5, -0]);
  const ml = panes.find((p) => p.title === '証拠金維持率(%)').series[0].points;
  assert.deepEqual(ml, [{ time: 100 }, { time: 160, value: 500 }, { time: 220 }]);
});

test('読み込み範囲は run の足の最初と最後・台帳外は null', () => {
  assert.deepEqual(candleRequestOf(overlay()), { datasetRef: 'jp225_mt5_spread', timeframe: '1m', from: 100, to: 220 });
  assert.equal(candleRequestOf({ ...overlay(), dataset_ref: null }), null);
});

test('足の照合は時刻の並びが完全一致のときだけ真', () => {
  const candles = T.map((time) => ({ time }));
  assert.equal(candlesMatchRunBars(candles, T), true);
  assert.equal(candlesMatchRunBars(candles.slice(1), T), false);
  assert.equal(candlesMatchRunBars([{ time: 100 }, { time: 161 }, { time: 220 }], T), false);
});

test('計算量: 点の数は足の数に一致し、系列数を増やしても 1 系列あたりの点は増えない', () => {
  for (const n of [3, 3000]) {
    const times = Array.from({ length: n }, (_, i) => 60 * i);
    const values = times.map(() => 1);
    const o = {
      ...overlay([{ series: 'a', placement: 'price', time: times, value: values }]),
      account: Object.fromEntries(
        ['balance', 'equity', 'drawdown', 'realized_pnl', 'floating_pnl', 'margin_level']
          .map((k) => [k, values]).concat([['time', times]]),
      ),
    };
    const counts = resultChartPanes(o).flatMap((p) => p.series.map((s) => s.points.length));
    assert.deepEqual([...new Set(counts)], [n]);
  }
});

test('instance: 価格パネルは系列ごと・他のパネルは 1 枚 1 つ（ChartRenderer の payload の形）', () => {
  const insts = resultChartInstances(overlay([
    { series: 'sma', placement: 'price', time: T, value: [null, 1, 2] },
    { series: 'madiff', placement: 'pane', time: T, value: [1, 2, 3] },
  ]));
  assert.deepEqual(insts.map((i) => [i.label, i.pane]), [
    ['sma', false], ['madiff', true], ['残高・有効証拠金', true], ['DD', true], ['損益', true], ['証拠金維持率(%)', true],
  ]);
  assert.equal(new Set(insts.map((i) => i.instanceId)).size, insts.length);
  const acct = insts.find((i) => i.label === '残高・有効証拠金');
  assert.deepEqual(acct.payloads.map((p) => p.name), ['残高', '有効証拠金']);
  assert.deepEqual(Object.keys(acct.payloads[0]).sort(), ['color', 'data', 'name', 'style', 'width']);
});

test('計算量: instance の点はパネルの点をそのまま渡す（作り直さない）', () => {
  const o = overlay([{ series: 'sma', placement: 'price', time: T, value: [null, 1, 2] }]);
  const fromPanes = resultChartPanes(o).flatMap((p) => p.series.map((s) => s.points.length));
  const fromInsts = resultChartInstances(o).flatMap((i) => i.payloads.map((p) => p.data.length));
  assert.deepEqual(fromInsts, fromPanes);
});
