// result_chart_model.js の単体検証（パネルの並び・whitespace・返った列と行数の照合・取引終了時の残高・DD・計算量）。
//
// 入力はジョブの足の成果物（ISSUE-552/554 段階 2-2）: 宣言（`/sim/chart-bars/{job}/extent`）と、
//   位置の区間の列（`/sim/chart-bars/{job}/rows/{start}/{end}`）。持っている区間だけを渡す。
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  RESULT_CHART_COLORS,
  TRADE_CLOSE_LINE_WIDTH,
  balanceCurveTimes,
  barPoint,
  candlesOf,
  drawnColumns,
  resultChartInstances,
  resultChartPanes,
  rowsMismatch,
  toPoints,
  windowTradeClose,
} from '../js/usecase/result_chart_model.js';
import { tradeCloseCurves } from '../../../report_ui/web/js/chart.js';

const T = [100, 160, 220];

/**
 * 初期資金基準（損益（初期資金比）と残高の棒の基準＝初期資金）と塗り分けの色。色は売買履歴チャートのローソク足の陽線・陰線から
 * View が借りて渡す。ここでは本番と違う色を名乗る——model が色を書き写していれば落ちる。
 */
const BASELINE = Object.freeze({ deposit: 1000, upColor: '#00aa00', downColor: '#aa0000' });

/** 足の成果物の宣言（extent の応答のうち model が読む部分）。指標の列名は位置から付く。 */
function declared(indicators = []) {
  const entries = indicators.map((ind, i) => ({ series: ind.series, placement: ind.placement, column: `indicator_${i}` }));
  return {
    index_column: 'bar_index',
    columns: [
      'bar_index', 'time', 'open', 'high', 'low', 'close',
      'balance', 'equity', 'drawdown', 'drawdown_pct', 'realized_pnl', 'floating_pnl', 'margin', 'margin_level',
      ...entries.map((e) => e.column),
    ],
    indicators: entries,
  };
}

/** 位置の区間の列（rows の応答の columns）。 */
function columns(indicators = [], start = 0) {
  const out = {
    bar_index: T.map((_, i) => start + i),
    time: T,
    open: [1, 1, 1], high: [2, 2, 2], low: [0.5, 0.5, 0.5], close: [1.5, 1.5, 1.5],
    balance: [1000, 1000, 1010],
    equity: [1000, 995, 1010],
    drawdown: [0, 5, 0],
    drawdown_pct: [0, 0.5, 0],
    realized_pnl: [0, 0, 10],
    floating_pnl: [0, -5, 0],
    margin: [0, 1, 0],
    margin_level: [null, 500, null],
  };
  indicators.forEach((ind, i) => { out[`indicator_${i}`] = ind.value; });
  return out;
}

/** 旧来の呼び方の置き換え: 指標の並びから（宣言, 列）を作る。 */
function material(indicators = []) {
  return [declared(indicators), columns(indicators)];
}

/** 取引終了時の残高・DD（シミュレーション結果と同じ関数で作る）。 */
function close(times = T, curve = [{ time: 220, value: 1010 }]) {
  return tradeCloseCurves({ meta: { initial_deposit: 1000 }, agg: { balance_curve: curve } }, times);
}

test('null と非有限は whitespace（time だけ）になり、0 を置かない', () => {
  assert.deepEqual(toPoints(T, [1, null, Number.NaN]), [{ time: 100, value: 1 }, { time: 160 }, { time: 220 }]);
});

test('価格上の指標はパネル 0、別窓の指標は自分のパネル、口座系 4 枚が後ろに続く', () => {
  const panes = resultChartPanes(...material([
    { series: 'sma', placement: 'price', value: [null, 1, 2] },
    { series: 'madiff', placement: 'pane', value: [1, 2, 3] },
  ]), close(), BASELINE);
  assert.deepEqual(panes.map((p) => p.title), ['価格', 'madiff', '残高・有効証拠金', 'DD', '損益（初期資金比）', '証拠金維持率(%)']);
  assert.deepEqual(panes[0].series.map((s) => s.name), ['sma']);
});

test('DD は下向きに描く・維持率は保有の無い足で値なし', () => {
  const panes = resultChartPanes(...material(), close(), BASELINE);
  const dd = panes.find((p) => p.title === 'DD').series[0].points;
  assert.deepEqual(dd.map((p) => p.value), [-0, -5, -0]);
  const ml = panes.find((p) => p.title === '証拠金維持率(%)').series[0].points;
  assert.deepEqual(ml, [{ time: 100 }, { time: 160, value: 500 }, { time: 220 }]);
});

test('点の時刻は区間の足の時刻（列 time）', () => {
  const panes = resultChartPanes(...material([{ series: 'sma', placement: 'price', value: [null, 1, 2] }]), close(), BASELINE);
  for (const s of panes.flatMap((p) => p.series)) {
    assert.deepEqual(s.points.map((q) => q.time), T, s.name);
  }
});

test('返った列がすべて行数と同じ長さで、先頭の位置が問うた位置なら null', () => {
  const inds = [{ series: 'sma', placement: 'price', value: [1, 2, 3] }];
  const answer = { start: 7, end: 10, rows: 3, columns: columns(inds, 7) };
  assert.equal(rowsMismatch(declared(inds), answer, { start: 7, end: 10 }), null);
});

test('描く列の長さが行数と違えば、どの列か・長さ・行数を返す（黙ってずらさない）', () => {
  const cols = columns();
  cols.margin_level = [null, 500];
  assert.equal(
    rowsMismatch(declared(), { start: 0, end: 3, rows: 3, columns: cols }, { start: 0, end: 3 }),
    '列 margin_level の長さ 2 が行数 3 と一致しません',
  );
});

test('指標の列の長さが行数と違えば、系列名つきで返す', () => {
  const inds = [{ series: 'sma', placement: 'price', value: [1, 2, 3, 4] }];
  assert.equal(
    rowsMismatch(declared(inds), { start: 0, end: 3, rows: 3, columns: columns(inds) }, { start: 0, end: 3 }),
    '列 indicator_0（sma） の長さ 4 が行数 3 と一致しません',
  );
});

test('描く列が欠けていれば長さ 0 として一致しない（欠けた列を描かない）', () => {
  const cols = columns();
  delete cols.equity;
  assert.equal(
    rowsMismatch(declared(), { start: 0, end: 3, rows: 3, columns: cols }, { start: 0, end: 3 }),
    '列 equity の長さ 0 が行数 3 と一致しません',
  );
});

test('返った区間・行数・先頭の位置が問うた区間と違えば理由を返す（別の足の上へ置かない）', () => {
  const ask = { start: 7, end: 10 };
  assert.match(rowsMismatch(declared(), { start: 6, end: 10, rows: 3, columns: columns([], 7) }, ask), /区間/);
  assert.match(rowsMismatch(declared(), { start: 7, end: 10, rows: 2, columns: columns([], 7) }, ask), /行数 2/);
  assert.match(rowsMismatch(declared(), { start: 7, end: 10, rows: 3, columns: columns([], 8) }, ask), /先頭の位置 8/);
});

test('足は列から作る（時刻・始値・高値・安値・終値）', () => {
  assert.deepEqual(candlesOf(columns())[1], { time: 160, open: 1, high: 2, low: 0.5, close: 1.5 });
  assert.equal(candlesOf(columns()).length, 3);
});

test('描画に使う列は、位置・足・口座の描く列・宣言された指標の列（使わない列を持たない）', () => {
  const names = drawnColumns(declared([{ series: 'sma', placement: 'price', value: [] }]));
  assert.deepEqual(names, [
    'bar_index', 'time', 'open', 'high', 'low', 'close',
    'balance', 'equity', 'drawdown', 'realized_pnl', 'margin_level', 'indicator_0',
  ]);
});

test('値なし（null）の DD は 0 を描かず whitespace にする', () => {
  const cols = columns();
  cols.drawdown = [0, null, 5];
  const dd = resultChartPanes(declared(), cols, close(), BASELINE).find((p) => p.title === 'DD').series[0].points;
  assert.deepEqual(dd, [{ time: 100, value: -0 }, { time: 160 }, { time: 220, value: -5 }]);
});

test('計算量: 点の数は足の数に一致し、系列数を増やしても 1 系列あたりの点は増えない', () => {
  for (const n of [3, 3000]) {
    const times = Array.from({ length: n }, (_, i) => 60 * i);
    const values = times.map(() => 1);
    const inds = [{ series: 'a', placement: 'price', value: values }];
    const cols = Object.fromEntries(declared(inds).columns.map((k) => [k, values]));
    cols.time = times;
    const all = resultChartPanes(declared(inds), cols, close(times, []), BASELINE).flatMap((p) => p.series);
    assert.ok(all.some((s) => s.kind === 'histogram'), '棒（損益（初期資金比））が検定に入っていない（検定が空虚）');
    // 発行した点 − 足の数 = 0（棒も線も・足の本数 2 点）。
    assert.deepEqual([...new Set(all.map((s) => s.points.length - n))], [0]);
  }
});

test('instance: 価格パネルは系列ごと・他のパネルは 1 枚 1 つ（ChartRenderer の payload の形）', () => {
  const insts = resultChartInstances(...material([
    { series: 'sma', placement: 'price', value: [null, 1, 2] },
    { series: 'madiff', placement: 'pane', value: [1, 2, 3] },
  ]), close(), BASELINE);
  assert.deepEqual(insts.map((i) => [i.label, i.pane]), [
    ['sma', false], ['madiff', true], ['残高・有効証拠金', true], ['DD', true], ['損益（初期資金比）', true], ['証拠金維持率(%)', true],
  ]);
  assert.equal(new Set(insts.map((i) => i.instanceId)).size, insts.length);
  const acct = insts.find((i) => i.label === '残高・有効証拠金');
  assert.deepEqual(acct.payloads.map((p) => p.name), ['残高（足ごと）', '有効証拠金（足ごと）', '残高（取引終了時）']);
  assert.deepEqual(Object.keys(acct.payloads[1]).sort(), ['color', 'data', 'kind', 'name', 'style', 'width']);
  // 棒は基準（base）も名乗る。
  assert.deepEqual(Object.keys(acct.payloads[0]).sort(), ['base', 'color', 'data', 'kind', 'name', 'style', 'width']);
  // 描き方（ChartRenderer の renderHistogram / renderLine のどちらで描くか）は payload が名乗る。
  assert.deepEqual(
    insts.find((i) => i.label === '損益（初期資金比）').payloads.map((p) => [p.name, p.kind]),
    [['損益（初期資金比）', 'histogram'], ['確定損益（累計）', 'line']],
  );
  assert.deepEqual(
    acct.payloads.map((p) => [p.name, p.kind]),
    [['残高（足ごと）', 'histogram'], ['有効証拠金（足ごと）', 'line'], ['残高（取引終了時）', 'line']],
  );
  assert.ok(insts.filter((i) => !['損益（初期資金比）', '残高・有効証拠金'].includes(i.label))
    .every((i) => i.payloads.every((p) => p.kind === 'line')));
});

// ---- 残高: 初期資金の高さを基準にした棒（目盛りは金額のまま） ----

test('残高（足ごと）は初期資金を基準（base）にした棒で、値は残高の金額のまま。有効証拠金・取引終了時の線を上に重ねる', () => {
  const cols = columns();
  cols.balance = [1000, 990, 1012.5];
  const insts = resultChartInstances(declared(), cols, close(), BASELINE);
  const acct = insts.find((i) => i.label === '残高・有効証拠金');
  const [bars, equity, closeLine] = acct.payloads;
  assert.equal(bars.base, BASELINE.deposit);
  assert.deepEqual(bars.data, [
    { time: 100, value: 1000, color: BASELINE.upColor },     // 基準ちょうどは陽線の色
    { time: 160, value: 990, color: BASELINE.downColor },
    { time: 220, value: 1012.5, color: BASELINE.upColor },
  ]);
  // 線の色・太さは今のまま。
  assert.deepEqual([equity.color, equity.width], [RESULT_CHART_COLORS.equity, 1]);
  assert.deepEqual([closeLine.color, closeLine.width], [RESULT_CHART_COLORS.balanceClose, TRADE_CLOSE_LINE_WIDTH]);
  // 別の初期資金なら基準も色も変わる（値は金額のまま）。
  const other = resultChartInstances(declared(), cols, close(), { ...BASELINE, deposit: 995 })
    .find((i) => i.label === '残高・有効証拠金').payloads[0];
  assert.equal(other.base, 995);
  assert.deepEqual(other.data.map((q) => [q.value, q.color]), [[1000, BASELINE.upColor], [990, BASELINE.downColor], [1012.5, BASELINE.upColor]]);
});

test('残高（足ごと）: 値なし（null）の足は 0 を描かず whitespace', () => {
  const cols = columns();
  cols.balance = [null, 1000, Number.NaN];
  const bars = resultChartInstances(declared(), cols, close(), BASELINE)
    .find((i) => i.label === '残高・有効証拠金').payloads[0].data;
  assert.deepEqual(bars, [{ time: 100 }, { time: 160, value: 1000, color: BASELINE.upColor }, { time: 220 }]);
});

test('計算量: 棒の点は 発行した点 − 描画へ渡した点 = 0（残高と損益の棒・足の本数 2 点）', () => {
  // 観測の境界: 棒の点を作る口 barPoint（resultChartInstances の注入点として宣言）。
  for (const n of [3, 3000]) {
    const times = Array.from({ length: n }, (_, i) => 60 * (i + 1));
    const cols = Object.fromEntries(declared().columns.map((k) => [k, times.map((_, i) => 990 + (i % 21))]));
    cols.time = times;
    // 値なしの足（null・NaN）を混ぜ、whitespace の分岐も同じ表明で見張る。
    for (const k of ['balance', 'equity']) {
      cols[k] = cols[k].map((v, i) => (i % 3 === 1 ? null : (i % 3 === 2 && i % 2 === 0 ? Number.NaN : v)));
    }
    const issued = [];
    const spy = (...args) => { const q = barPoint(...args); issued.push(q); return q; };
    const insts = resultChartInstances(declared(), cols, close(times, []), BASELINE, { barPoint: spy });
    const bars = insts.flatMap((i) => i.payloads).filter((p) => p.kind === 'histogram');
    assert.deepEqual(bars.map((p) => p.name).sort(), ['損益（初期資金比）', '残高（足ごと）'].sort(), '棒が検定に入っていない');
    const drawn = new Set(bars.flatMap((p) => p.data));
    assert.ok(issued.length > 0, '注入点を通っていない（検定が空虚）');
    assert.ok([...drawn].some((q) => !('value' in q)) && [...drawn].some((q) => 'value' in q), '値なしと値ありの両方の足が無い（検定が空虚）');
    assert.equal(issued.filter((q) => !drawn.has(q)).length, 0, '作って描かない棒の点がある');
    assert.equal(issued.length - drawn.size, 0);
    // 1 本の棒あたりの点 − 足の数 = 0。
    assert.deepEqual([...new Set(bars.map((p) => p.data.length - n))], [0]);
  }
});

// ---- 損益（初期資金比）: 0（＝スタート残高）を基準にした棒と、確定損益（累計）の線 ----

test('損益（初期資金比）の棒は 足ごとの有効証拠金 − 初期資金、確定損益（累計）の線を上に重ね、含み損益の線は無い', () => {
  const cols = columns();
  cols.equity = [1000, 995, 1012.5];
  const pane = resultChartPanes(declared(), cols, close(), BASELINE).find((p) => p.title === '損益（初期資金比）');
  assert.deepEqual(pane.series.map((s) => [s.name, s.kind]), [['損益（初期資金比）', 'histogram'], ['確定損益（累計）', 'line']]);
  assert.deepEqual(pane.series[0].points.map((q) => [q.time, q.value]), [[100, 0], [160, -5], [220, 12.5]]);
  assert.deepEqual(pane.series[1].points.map((q) => q.value), [0, 0, 10]);
  const all = resultChartPanes(declared(), cols, close(), BASELINE).flatMap((p) => p.series.map((s) => s.name));
  assert.ok(!all.includes('含み損益'), '含み損益の線が残っている');
});

test('損益（初期資金比）の棒の色: 0 以上は陽線の色・0 未満は陰線の色（0 ちょうどは陽線の色）', () => {
  const cols = columns();
  cols.equity = [1000, 999.99, 1000.01];
  const bars = resultChartPanes(declared(), cols, close(), BASELINE)
    .find((p) => p.title === '損益（初期資金比）').series[0].points;
  assert.deepEqual(bars.map((q) => q.color), [BASELINE.upColor, BASELINE.downColor, BASELINE.upColor]);
});

test('損益（初期資金比）: 有効証拠金が値なし（null）の足は 0 を描かず whitespace（色も付けない）', () => {
  const cols = columns();
  cols.equity = [null, 990, Number.NaN];
  const bars = resultChartPanes(declared(), cols, close(), BASELINE)
    .find((p) => p.title === '損益（初期資金比）').series[0].points;
  assert.deepEqual(bars, [{ time: 100 }, { time: 160, value: -10, color: BASELINE.downColor }, { time: 220 }]);
});

test('損益（初期資金比）: 基準は渡された初期資金（別の初期資金なら同じ有効証拠金でも値が変わる）', () => {
  const cols = columns();
  cols.equity = [1000, 1000, 1000];
  const bars = resultChartPanes(declared(), cols, close(), { ...BASELINE, deposit: 1200 })
    .find((p) => p.title === '損益（初期資金比）').series[0].points;
  assert.deepEqual(bars.map((q) => [q.value, q.color]), [[-200, BASELINE.downColor], [-200, BASELINE.downColor], [-200, BASELINE.downColor]]);
});

test('計算量: instance の点はパネルの点をそのまま渡す（作り直さない）', () => {
  const m = material([{ series: 'sma', placement: 'price', value: [null, 1, 2] }]);
  const fromPanes = resultChartPanes(...m, close(), BASELINE).flatMap((p) => p.series.map((s) => s.points.length));
  const fromInsts = resultChartInstances(...m, close(), BASELINE).flatMap((i) => i.payloads.map((p) => p.data.length));
  assert.deepEqual(fromInsts, fromPanes);
});

test('取引終了時のレイヤー: 残高・DD の 2 枚に、シミュレーション結果と同じ系列を足ごとのレイヤーへ重ねる', () => {
  const tc = close();
  const panes = resultChartPanes(...material(), tc, BASELINE);
  const bal = panes.find((p) => p.title === '残高・有効証拠金').series;
  const dd = panes.find((p) => p.title === 'DD').series;
  assert.deepEqual(bal.map((s) => s.name), ['残高（足ごと）', '有効証拠金（足ごと）', '残高（取引終了時）']);
  assert.deepEqual(dd.map((s) => s.name), ['DD（足ごと）', 'DD（取引終了時）']);
  assert.equal(bal[2].points, tc.balData);
  assert.equal(dd[1].points, tc.ddData);
  // 他のパネルには重ねない（参照実装の資産曲線・ドローダウンは残高と DD の 2 つだけ）。
  for (const title of ['損益（初期資金比）', '証拠金維持率(%)']) {
    assert.ok(panes.find((p) => p.title === title).series.every((s) => !s.name.includes('取引終了時')));
  }
});

test('視認性: 取引終了時は不透明で太く、足ごとは不透明度を下げて細い', () => {
  const alpha = (c) => Number(/rgba\([^)]*,\s*([\d.]+)\)/.exec(c)[1]);
  const insts = resultChartInstances(...material(), close(), BASELINE);
  for (const label of ['残高・有効証拠金', 'DD']) {
    const payloads = insts.find((i) => i.label === label).payloads;
    const forest = payloads.filter((p) => p.name.includes('取引終了時'));
    // 足ごとの線（残高（足ごと）は初期資金基準の棒なので線の比較から外す）。
    const trees = payloads.filter((p) => p.name.includes('足ごと') && p.kind === 'line');
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

// ---- 取引終了時の残高・DD を区間で作る（全期間で作った値と一致させる） ----
//
// report_ui の balanceForwardFill は、渡した足の先頭で最高値を初期資金へ戻す。区間の足だけを渡すと、
//   区間より前に付けた最高値を知らないまま DD を出す。区間より前の balance_curve の時刻を前置きして
//   計算し、前置き分を切り落とす。前提（実測 2026-09-30・実ジョブ 66b108e1…: balance_curve 1,016 点の
//   時刻がすべて足 2,152,183 本の時刻に含まれる）: balance_curve の時刻 ⊆ 足の時刻。

/** run 全体の足の時刻（60 秒刻み）と、その上の balance_curve（時刻は足の時刻の部分集合・重複あり）。 */
function run(bars = 4000, trades = 120) {
  const times = Array.from({ length: bars }, (_, i) => 1000 + i * 60);
  const curve = [];
  let value = 1000;
  for (let k = 0; k < trades; k += 1) {
    // 上げ下げを混ぜ、途中に最高値を作る（区間より前の最高値が効く形）。
    value += (k % 7 === 0 ? 90 : -20) * (k < trades / 2 ? 1 : -1);
    curve.push({ time: times[(k * 31 + 5) % bars], value });
  }
  curve.push({ time: curve[3].time, value: curve[3].value + 1 });   // 同じ時刻の重複（後勝ち）
  const segment = { meta: { initial_deposit: 1000 }, agg: { balance_curve: curve } };
  return { times, segment };
}

test('区間で作った取引終了時の残高・DD は、全期間で作った値の同じ区間と一致する（どの区間でも）', () => {
  const { times, segment } = run();
  const whole = tradeCloseCurves(segment, times, 1000);
  const curveTimes = balanceCurveTimes(segment);
  for (const [start, end] of [[0, 4000], [0, 300], [1700, 2300], [3700, 4000], [2000, 2001]]) {
    const part = windowTradeClose({
      tradeCloseCurves, segment, deposit: 1000, curveTimes, times: times.slice(start, end),
    });
    assert.deepEqual(part.balData, whole.balData.slice(start, end), `残高 [${start}, ${end})`);
    assert.deepEqual(part.ddData, whole.ddData.slice(start, end), `DD [${start}, ${end})`);
  }
});

test('前置きをしないと DD が全期間の値と違う区間がある（この検定が前置きを見ていることの確認）', () => {
  const { times, segment } = run();
  const whole = tradeCloseCurves(segment, times, 1000);
  const naive = tradeCloseCurves(segment, times.slice(3700, 4000), 1000);
  assert.notDeepEqual(naive.ddData, whole.ddData.slice(3700, 4000));
});

test('balance_curve の時刻は昇順・重複なしで 1 回だけ作る（区間ごとに並べ直さない）', () => {
  const { segment } = run();
  const t = balanceCurveTimes(segment);
  assert.deepEqual(t, [...new Set(t)].sort((a, b) => a - b));
  assert.deepEqual(balanceCurveTimes({ agg: {} }), []);
  assert.deepEqual(balanceCurveTimes(null), []);
});

test('計算量: 区間の計算へ渡す時刻 − （区間の足 + 区間より前の balance_curve の時刻）= 0（足の本数 2 点）', () => {
  for (const bars of [4000, 40000]) {
    const { times, segment } = run(bars);
    const curveTimes = balanceCurveTimes(segment);
    const window = times.slice(bars - 300, bars);
    const before = curveTimes.filter((t) => t < window[0]).length;
    let passed = null;
    const spy = (seg, barTimes, deposit) => { passed = barTimes.length; return tradeCloseCurves(seg, barTimes, deposit); };
    const part = windowTradeClose({ tradeCloseCurves: spy, segment, deposit: 1000, curveTimes, times: window });
    assert.equal(passed - (window.length + before), 0);
    // 出した点 − 区間の足 = 0（前置き分を出力へ残さない）。
    assert.equal(part.balData.length - window.length, 0);
    assert.equal(part.ddData.length - window.length, 0);
  }
});
