// result_chart_model.js の単体検証（パネルの並び・whitespace・足の照合・読み込み範囲・計算量）。
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  candleRequestOf,
  candlesMatchRunBars,
  RESULT_CHART_COLORS,
  TRADE_CLOSE_LINE_WIDTH,
  resultChartInstances,
  resultChartPanes,
  toPoints,
} from '../js/usecase/result_chart_model.js';
import { tradeCloseCurves } from '../../../report_ui/web/js/chart.js';

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

/** 取引終了時の残高・DD（シミュレーション結果と同じ関数で作る）。 */
function close(times = T, curve = [{ time: 220, value: 1010 }]) {
  return tradeCloseCurves({ meta: { initial_deposit: 1000 }, agg: { balance_curve: curve } }, times);
}

test('null と非有限は whitespace（time だけ）になり、0 を置かない', () => {
  assert.deepEqual(toPoints(T, [1, null, Number.NaN]), [{ time: 100, value: 1 }, { time: 160 }, { time: 220 }]);
});

test('価格上の指標はパネル 0、別窓の指標は自分のパネル、口座系 4 枚が後ろに続く', () => {
  const panes = resultChartPanes(overlay([
    { series: 'sma', placement: 'price', time: T, value: [null, 1, 2] },
    { series: 'madiff', placement: 'pane', time: T, value: [1, 2, 3] },
  ]), close());
  assert.deepEqual(panes.map((p) => p.title), ['価格', 'madiff', '残高・有効証拠金', 'DD', '損益', '証拠金維持率(%)']);
  assert.deepEqual(panes[0].series.map((s) => s.name), ['sma']);
});

test('DD は下向きに描く・維持率は保有の無い足で値なし', () => {
  const panes = resultChartPanes(overlay(), close());
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
    const counts = resultChartPanes(o, close(times, [])).flatMap((p) => p.series.map((s) => s.points.length));
    assert.deepEqual([...new Set(counts)], [n]);
  }
});

test('instance: 価格パネルは系列ごと・他のパネルは 1 枚 1 つ（ChartRenderer の payload の形）', () => {
  const insts = resultChartInstances(overlay([
    { series: 'sma', placement: 'price', time: T, value: [null, 1, 2] },
    { series: 'madiff', placement: 'pane', time: T, value: [1, 2, 3] },
  ]), close());
  assert.deepEqual(insts.map((i) => [i.label, i.pane]), [
    ['sma', false], ['madiff', true], ['残高・有効証拠金', true], ['DD', true], ['損益', true], ['証拠金維持率(%)', true],
  ]);
  assert.equal(new Set(insts.map((i) => i.instanceId)).size, insts.length);
  const acct = insts.find((i) => i.label === '残高・有効証拠金');
  assert.deepEqual(acct.payloads.map((p) => p.name), ['残高（足ごと）', '有効証拠金（足ごと）', '残高（取引終了時）']);
  assert.deepEqual(Object.keys(acct.payloads[0]).sort(), ['color', 'data', 'name', 'style', 'width']);
});

test('計算量: instance の点はパネルの点をそのまま渡す（作り直さない）', () => {
  const o = overlay([{ series: 'sma', placement: 'price', time: T, value: [null, 1, 2] }]);
  const fromPanes = resultChartPanes(o, close()).flatMap((p) => p.series.map((s) => s.points.length));
  const fromInsts = resultChartInstances(o, close()).flatMap((i) => i.payloads.map((p) => p.data.length));
  assert.deepEqual(fromInsts, fromPanes);
});

test('取引終了時のレイヤー: 残高・DD の 2 枚に、シミュレーション結果と同じ系列を足ごとのレイヤーへ重ねる', () => {
  const tc = close();
  const panes = resultChartPanes(overlay(), tc);
  const bal = panes.find((p) => p.title === '残高・有効証拠金').series;
  const dd = panes.find((p) => p.title === 'DD').series;
  assert.deepEqual(bal.map((s) => s.name), ['残高（足ごと）', '有効証拠金（足ごと）', '残高（取引終了時）']);
  assert.deepEqual(dd.map((s) => s.name), ['DD（足ごと）', 'DD（取引終了時）']);
  assert.equal(bal[2].points, tc.balData);
  assert.equal(dd[1].points, tc.ddData);
  // 他のパネルには重ねない（参照実装の資産曲線・ドローダウンは残高と DD の 2 つだけ）。
  for (const title of ['損益', '証拠金維持率(%)']) {
    assert.ok(panes.find((p) => p.title === title).series.every((s) => !s.name.includes('取引終了時')));
  }
});

test('視認性: 取引終了時は不透明で太く、足ごとは不透明度を下げて細い', () => {
  const alpha = (c) => Number(/rgba\([^)]*,\s*([\d.]+)\)/.exec(c)[1]);
  const insts = resultChartInstances(overlay(), close());
  for (const label of ['残高・有効証拠金', 'DD']) {
    const payloads = insts.find((i) => i.label === label).payloads;
    const forest = payloads.filter((p) => p.name.includes('取引終了時'));
    const trees = payloads.filter((p) => p.name.includes('足ごと'));
    assert.equal(forest.length, 1);
    assert.ok(trees.length >= 1);
    for (const t of trees) {
      assert.ok(alpha(t.color) < alpha(forest[0].color), `${t.name} が ${forest[0].name} より濃い`);
      assert.ok(t.width < forest[0].width);
    }
  }
  assert.equal(RESULT_CHART_COLORS.balanceClose, 'rgba(59,130,246,0.9)');
  assert.equal(TRADE_CLOSE_LINE_WIDTH, 2);
});

test('計算量: 取引終了時のレイヤーの点は足の数に一致し、取引の数を増やしても増えない', () => {
  for (const trades of [1, 500]) {
    const times = Array.from({ length: 1000 }, (_, i) => 60 * (i + 1));
    const curve = Array.from({ length: trades }, (_, k) => ({ time: times[(k * 2) % times.length], value: 1000 + k }));
    const tc = close(times, curve);
    // 発行した点 − 足の数 = 0（取引ごとに点を作って捨てない）
    assert.equal(tc.balData.length - times.length, 0);
    assert.equal(tc.ddData.length - times.length, 0);
  }
});
