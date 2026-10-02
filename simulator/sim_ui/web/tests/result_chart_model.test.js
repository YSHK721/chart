// result_chart_model.js の単体検証（パネルの並び・whitespace・返った列と行数の照合・取引終了時の残高・DD・計算量）。
//
// 入力はジョブの足の成果物（ISSUE-552/554 段階 2-2）: 宣言（`/sim/chart-bars/{job}/extent`）と、
//   位置の区間の列（`/sim/chart-bars/{job}/rows/{start}/{end}`）。持っている区間だけを渡す。
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  RESULT_CHART_COLORS,
  ACCOUNT_LINE_WIDTH,
  DEFAULT_LINE_WIDTH,
  LINE_WIDTH_GAIN,
  TRADE_CLOSE_LINE_WIDTH,
  balanceCurveTimes,
  AREA_FILL_ALPHA,
  SERIES_RULE,
  SOURCE_BASIS,
  areaPoint,
  candlesOf,
  drawnColumns,
  resultChartInstances,
  resultChartPanes,
  rowsMismatch,
  toPoints,
  windowTradeClose,
} from '../js/usecase/result_chart_model.js';
import { tradeCloseCurves, _withAlpha } from '../../../report_ui/web/js/chart.js';

const T = [100, 160, 220];

/**
 * 初期資金基準（損益（初期資金比）と残高の棒の基準＝初期資金）と塗り分けの色。色は売買履歴チャートのローソク足の陽線・陰線から
 * View が借りて渡す。ここでは本番と違う色を名乗る——model が色を書き写していれば落ちる。
 */
const BASELINE = Object.freeze({ deposit: 1000, upColor: '#00aa00', downColor: '#aa0000', withAlpha: _withAlpha });

/** 面の 6 色（基準より上は陽線の色・下は陰線の色。面は縁の線の側が濃く、基準の側が薄い）。 */
function expectedFill(b = BASELINE) {
  return {
    topLineColor: b.upColor,
    topFillColor1: b.withAlpha(b.upColor, AREA_FILL_ALPHA.edge),
    topFillColor2: b.withAlpha(b.upColor, AREA_FILL_ALPHA.base),
    bottomLineColor: b.downColor,
    bottomFillColor1: b.withAlpha(b.downColor, AREA_FILL_ALPHA.base),
    bottomFillColor2: b.withAlpha(b.downColor, AREA_FILL_ALPHA.edge),
  };
}

/** 足の成果物の宣言が名乗る、run が使ったストップアウト水準（台帳の値と違う値を名乗る）。 */
const STOP_OUT_LEVEL = 87.5;

/** 面の系列（残高・DD・損益・証拠金維持率）と、その基準。 */
const AREAS = Object.freeze([
  { pane: '残高・有効証拠金', name: '有効証拠金（足ごと）', base: (b) => b.deposit },
  { pane: 'DD', name: 'DD（足ごと）', base: () => 0 },
  { pane: '損益（初期資金比）', name: '損益（初期資金比）', base: () => 0 },
  { pane: '証拠金維持率(%)', name: '証拠金維持率', base: (b, d) => d.stop_out_level },
]);

/** 足の成果物の宣言（extent の応答のうち model が読む部分）。指標の列名は位置から付く。 */
function declared(indicators = [], { stopOutLevel = STOP_OUT_LEVEL } = {}) {
  const entries = indicators.map((ind, i) => ({ series: ind.series, placement: ind.placement, column: `indicator_${i}` }));
  return {
    index_column: 'bar_index',
    columns: [
      'bar_index', 'time', 'open', 'high', 'low', 'close',
      'balance', 'equity', 'drawdown', 'drawdown_pct', 'realized_pnl', 'floating_pnl', 'margin', 'margin_level',
      ...entries.map((e) => e.column),
    ],
    indicators: entries,
    stop_out_level: stopOutLevel,
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
    assert.ok(all.some((s) => s.kind === 'baseline'), '面が検定に入っていない（検定が空虚）');
    // 発行した点 − 足の数 = 0（面も線も・足の本数 2 点）。
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
  assert.deepEqual(acct.payloads.map((p) => p.name), ['有効証拠金（足ごと）', '残高（足ごと）', '残高（取引終了時）']);
  assert.deepEqual(Object.keys(acct.payloads[1]).sort(), ['color', 'data', 'kind', 'name', 'style', 'width']);
  // 面は基準（base）と面の色（baseline）も名乗る。
  assert.deepEqual(Object.keys(acct.payloads[0]).sort(), ['base', 'baseline', 'color', 'data', 'kind', 'name', 'style', 'width']);
  // 描き方（ChartRenderer の renderBaseline / renderLine のどちらで描くか）は payload が名乗る。
  const kinds = (label) => insts.find((i) => i.label === label).payloads.map((p) => [p.name, p.kind]);
  assert.deepEqual(kinds('残高・有効証拠金'), [['有効証拠金（足ごと）', 'baseline'], ['残高（足ごと）', 'line'], ['残高（取引終了時）', 'line']]);
  assert.deepEqual(kinds('DD'), [['DD（足ごと）', 'baseline'], ['DD（取引終了時）', 'line']]);
  assert.deepEqual(kinds('損益（初期資金比）'), [['損益（初期資金比）', 'baseline'], ['確定損益（累計）', 'line']]);
  assert.ok(insts.filter((i) => !AREAS.some((a) => a.pane === i.label))
    .every((i) => i.payloads.every((p) => p.kind === 'line')));
});

// ---- 面（基準つき・グラデーション）: 残高（基準＝初期資金）・DD（基準＝0）・損益（基準＝0） ----
//   依頼者指示 2026-10-02「棒グラフではなく、面グラフでグラデーションで表現しろ」「DDも同じく」
//   「証拠金維持率も同じく」「面グラフのラインは1px」。

test('面: 4 つとも基準と面の 6 色を名乗り、縁の線は既定の太さ、点は値だけ（色を持たない）', () => {
  const m = material();
  const insts = resultChartInstances(...m, close(), BASELINE);
  for (const a of AREAS) {
    const p = insts.find((i) => i.label === a.pane).payloads.find((q) => q.name === a.name);
    assert.equal(p.kind, 'baseline', a.name);
    assert.equal(p.base, a.base(BASELINE, m[0]), a.name);
    assert.deepEqual(p.baseline, expectedFill(), a.name);
    assert.equal(p.width, DEFAULT_LINE_WIDTH, a.name);
    assert.ok(p.data.every((q) => !('color' in q)), `${a.name} の点が色を持つ`);
  }
});

test('面の色: 線は不透明、面は縁の線の側（edge）が基準の側（base）より濃い', () => {
  assert.ok(AREA_FILL_ALPHA.edge > AREA_FILL_ALPHA.base && AREA_FILL_ALPHA.base > 0 && AREA_FILL_ALPHA.edge < 1);
});

test('有効証拠金（足ごと）の面: 値は有効証拠金の金額のまま、基準は初期資金。残高（足ごと・取引終了時）の線を重ねる', () => {
  const cols = columns();
  cols.equity = [1000, 990, 1012.5];
  cols.balance = [1000, 1000, 1010];
  const acct = resultChartInstances(declared(), cols, close(), BASELINE).find((i) => i.label === '残高・有効証拠金');
  const [area, balance, closeLine] = acct.payloads;
  assert.deepEqual(area.data, [{ time: 100, value: 1000 }, { time: 160, value: 990 }, { time: 220, value: 1012.5 }]);
  assert.deepEqual(balance.data, [{ time: 100, value: 1000 }, { time: 160, value: 1000 }, { time: 220, value: 1010 }]);
  assert.deepEqual([balance.color, balance.width], [RESULT_CHART_COLORS.balance, DEFAULT_LINE_WIDTH]);
  assert.deepEqual([closeLine.color, closeLine.width], [RESULT_CHART_COLORS.balanceClose, TRADE_CLOSE_LINE_WIDTH]);
  // 別の初期資金なら基準が変わる（値は金額のまま）。
  const other = resultChartInstances(declared(), cols, close(), { ...BASELINE, deposit: 995 })
    .find((i) => i.label === '残高・有効証拠金').payloads[0];
  assert.equal(other.base, 995);
  assert.deepEqual(other.data, area.data);
});

test('DD（足ごと）の面: 値は下向き（≤0）、基準 0。上に重ねる DD（取引終了時）の線は今のまま', () => {
  const dd = resultChartInstances(...material(), close(), BASELINE).find((i) => i.label === 'DD');
  assert.deepEqual(dd.payloads[0].data.map((q) => q.value), [-0, -5, -0]);
  assert.deepEqual([dd.payloads[1].color, dd.payloads[1].width], [RESULT_CHART_COLORS.drawdownClose, TRADE_CLOSE_LINE_WIDTH]);
});

test('面: 値なし（null・NaN）の足は 0 を描かず whitespace（3 つとも）', () => {
  const cols = columns();
  cols.balance = [null, 1000, Number.NaN];
  cols.drawdown = [null, 5, Number.NaN];
  cols.equity = [null, 990, Number.NaN];
  cols.margin_level = [null, 120, Number.NaN];
  const insts = resultChartInstances(declared(), cols, close(), BASELINE);
  const data = (a) => insts.find((i) => i.label === a.pane).payloads.find((q) => q.name === a.name).data;
  assert.deepEqual(data(AREAS[0]), [{ time: 100 }, { time: 160, value: 990 }, { time: 220 }]);
  assert.deepEqual(data(AREAS[1]), [{ time: 100 }, { time: 160, value: -5 }, { time: 220 }]);
  assert.deepEqual(data(AREAS[2]), [{ time: 100 }, { time: 160, value: -10 }, { time: 220 }]);
  assert.deepEqual(data(AREAS[3]), [{ time: 100 }, { time: 160, value: 120 }, { time: 220 }]);
});

test('証拠金維持率の面: 基準は宣言が名乗る run のストップアウト水準（値は維持率のまま）', () => {
  const cols = columns();
  cols.margin_level = [null, 80, 130];
  for (const level of [STOP_OUT_LEVEL, 50]) {
    const p = resultChartInstances(declared([], { stopOutLevel: level }), cols, close(), BASELINE)
      .find((i) => i.label === '証拠金維持率(%)').payloads[0];
    assert.deepEqual([p.kind, p.base], ['baseline', level]);
    assert.deepEqual(p.data, [{ time: 100 }, { time: 160, value: 80 }, { time: 220, value: 130 }]);
  }
});

test('証拠金維持率: 宣言が水準を名乗らないジョブは面にせず線で描き、凡例名で分かるようにする', () => {
  const absent = declared();
  delete absent.stop_out_level;
  for (const d of [declared([], { stopOutLevel: null }), absent]) {
    const p = resultChartInstances(d, columns(), close(), BASELINE)
      .find((i) => i.label === '証拠金維持率(%)').payloads;
    assert.deepEqual(p.map((q) => [q.name, q.kind, q.width]), [['証拠金維持率（水準の宣言なし）', 'line', ACCOUNT_LINE_WIDTH]]);
    assert.deepEqual(p[0].data, [{ time: 100 }, { time: 160, value: 500 }, { time: 220 }]);
  }
});

test('計算量: 面の点は 発行した点 − 描画へ渡した点 = 0（4 つの面・null と NaN を含む・足の本数 2 点）', () => {
  // 観測の境界: 面の点を作る口 areaPoint（resultChartInstances の注入点 seams.areaPoint として宣言）。
  for (const n of [3, 3000]) {
    const times = Array.from({ length: n }, (_, i) => 60 * (i + 1));
    const cols = Object.fromEntries(declared().columns.map((k) => [k, times.map((_, i) => 990 + (i % 21))]));
    cols.time = times;
    // 値なしの足（null・NaN）を混ぜ、whitespace の分岐も同じ表明で見張る。
    for (const k of ['balance', 'equity', 'drawdown', 'margin_level']) {
      cols[k] = cols[k].map((v, i) => (i % 3 === 1 ? null : (i % 3 === 2 && i % 2 === 0 ? Number.NaN : v)));
    }
    const issued = [];
    const spy = (...args) => { const q = areaPoint(...args); issued.push(q); return q; };
    const insts = resultChartInstances(declared(), cols, close(times, []), BASELINE, { areaPoint: spy });
    const areas = insts.flatMap((i) => i.payloads).filter((p) => p.kind === 'baseline');
    assert.deepEqual(areas.map((p) => p.name).sort(), AREAS.map((a) => a.name).sort(), '面が検定に入っていない');
    const drawn = new Set(areas.flatMap((p) => p.data));
    assert.ok(issued.length > 0, '注入点を通っていない（検定が空虚）');
    assert.ok([...drawn].some((q) => !('value' in q)) && [...drawn].some((q) => 'value' in q), '値なしと値ありの両方の足が無い（検定が空虚）');
    assert.equal(issued.filter((q) => !drawn.has(q)).length, 0, '作って描かない面の点がある');
    assert.equal(issued.length - drawn.size, 0);
    // 1 つの面あたりの点 − 足の数 = 0。
    assert.deepEqual([...new Set(areas.map((p) => p.data.length - n))], [0]);
  }
});

// ---- 面と線の規則（依頼者裁定 2026-10-02「面グラフのルールはどうなっているのか? … 統一しろ」） ----

test('面と線の規則: どのペインでも、面は有効証拠金ベース・重ねる線は確定（宣言 SERIES_RULE・SOURCE_BASIS から導く）', () => {
  // ペインを足しても書き換えずに済むよう、役割（面 / 線）を名乗る系列を持つペインを出力から拾う。
  const noLevel = declared();
  delete noLevel.stop_out_level;
  for (const d of [declared(), noLevel]) {
    const panes = resultChartPanes(d, columns(), close(), BASELINE).filter((p) => p.series.some((s) => s.role));
    assert.ok(panes.length > 0, '検定の前提: 役割を名乗るペインが無い');
    for (const p of panes) {
      assert.equal(p.series.filter((s) => s.role === 'area').length, 1, `${p.title} の面が 1 つでない`);
      for (const s of p.series) {
        assert.ok(s.role in SERIES_RULE, `${p.title} ${s.name} の役割 ${s.role}`);
        assert.ok(s.source in SOURCE_BASIS, `${p.title} ${s.name} の出所 ${s.source} が宣言に無い`);
        assert.equal(SOURCE_BASIS[s.source], SERIES_RULE[s.role], `${p.title} ${s.name}（${s.source}）`);
      }
    }
  }
});

test('面と線の規則: 系列の点は名乗った出所の列から作る（名乗りと中身が食い違わない）', () => {
  // 出所ごとに別の値を入れ、面・線の点がその列の値（面は描く値への写像の前後で順序が保たれる）であることを見る。
  const cols = columns();
  // 列どうしが平行移動でも符号反転でも重ならない値（取り違えれば差が一定にならない）。
  cols.equity = [1001, 1005, 1002];
  cols.balance = [2001, 2001, 2010];
  cols.drawdown = [3, 7, 1];
  cols.realized_pnl = [4, 4, 9];
  cols.margin_level = [101, 150, 90];
  const panes = resultChartPanes(declared(), cols, close(), BASELINE).filter((p) => p.series.some((s) => s.role));
  for (const p of panes) {
    for (const s of p.series.filter((q) => q.source in cols)) {
      const raw = cols[s.source];
      const drawn = s.points.map((q) => q.value);
      const diffs = drawn.map((v, k) => v - raw[k]);
      const negDiffs = drawn.map((v, k) => v + raw[k]);
      // 描く値は列の値の平行移動（基準からの差）か符号反転（DD）で、別の列の値ではない。
      assert.ok(new Set(diffs).size === 1 || new Set(negDiffs).size === 1, `${p.title} ${s.name} が ${s.source} 以外から作られている`);
    }
  }
});

// ---- 損益（初期資金比）: 0（＝スタート残高）を基準にした面と、確定損益（累計）の線 ----

test('損益（初期資金比）の面は 足ごとの有効証拠金 − 初期資金、確定損益（累計）の線を上に重ね、含み損益の線は無い', () => {
  const cols = columns();
  cols.equity = [1000, 995, 1012.5];
  const pane = resultChartPanes(declared(), cols, close(), BASELINE).find((p) => p.title === '損益（初期資金比）');
  assert.deepEqual(pane.series.map((s) => [s.name, s.kind]), [['損益（初期資金比）', 'baseline'], ['確定損益（累計）', 'line']]);
  assert.deepEqual(pane.series[0].points.map((q) => [q.time, q.value]), [[100, 0], [160, -5], [220, 12.5]]);
  assert.deepEqual(pane.series[1].points.map((q) => q.value), [0, 0, 10]);
  const all = resultChartPanes(declared(), cols, close(), BASELINE).flatMap((p) => p.series.map((s) => s.name));
  assert.ok(!all.includes('含み損益'), '含み損益の線が残っている');
});

test('損益（初期資金比）: 基準は渡された初期資金（別の初期資金なら同じ有効証拠金でも値が変わる）', () => {
  const cols = columns();
  cols.equity = [1000, 1000, 1000];
  const area = resultChartPanes(declared(), cols, close(), { ...BASELINE, deposit: 1200 })
    .find((p) => p.title === '損益（初期資金比）').series[0].points;
  assert.deepEqual(area.map((q) => q.value), [-200, -200, -200]);
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
  assert.deepEqual(bal.map((s) => s.name), ['有効証拠金（足ごと）', '残高（足ごと）', '残高（取引終了時）']);
  assert.deepEqual(dd.map((s) => s.name), ['DD（足ごと）', 'DD（取引終了時）']);
  assert.equal(bal[2].points, tc.balData);
  assert.equal(dd[1].points, tc.ddData);
  // 他のパネルには重ねない（参照実装の資産曲線・ドローダウンは残高と DD の 2 つだけ）。
  for (const title of ['損益（初期資金比）', '証拠金維持率(%)']) {
    assert.ok(panes.find((p) => p.title === title).series.every((s) => !s.name.includes('取引終了時')));
  }
});

test('視認性: 取引終了時は足ごとより濃く太い（森＞木）', () => {
  const insts = resultChartInstances(...material(), close(), BASELINE);
  for (const label of ['残高・有効証拠金', 'DD']) {
    const payloads = insts.find((i) => i.label === label).payloads;
    const forest = payloads.filter((p) => p.name.includes('取引終了時'));
    const trees = payloads.filter((p) => p.name.includes('足ごと'));
    assert.equal(forest.length, 1);
    assert.ok(trees.length >= 1);
    for (const t of trees) {
      // 面（基準つき）は塗りの濃さを面の色で持つ。線の色の濃さは線どうしで比べる。
      if (t.kind === 'line') assert.ok(alpha(t.color) < alpha(forest[0].color), `${t.name} が ${forest[0].name} より濃い`);
      assert.ok(t.width < forest[0].width);
    }
  }
});

/** rgba(...) の不透明度（不透明の色は 1）。 */
function alpha(c) {
  const m = /rgba\([^)]*,\s*([\d.]+)\)/.exec(c);
  return m ? Number(m[1]) : 1;
}

test('面に重ねる線: 太さは面の縁の線より細くなく、足ごとの線は面の縁より太くない', () => {
  // 依頼者指示（2026-10-02）「主張しすぎ」で細くし、「足ごとのラインの視認性が低い」で不透明度を戻した。
  const insts = resultChartInstances(...material(), close(), BASELINE);
  const panesWithArea = insts.filter((i) => i.payloads.some((p) => p.kind === 'baseline'));
  const overlays = panesWithArea.flatMap((i) => i.payloads).filter((p) => p.kind === 'line');
  assert.deepEqual(overlays.map((p) => p.name).sort(),
    ['残高（足ごと）', '残高（取引終了時）', 'DD（取引終了時）', '確定損益（累計）'].sort(), '検定の前提');
  const edge = Math.max(...panesWithArea.flatMap((i) => i.payloads).filter((p) => p.kind === 'baseline').map((p) => p.width));
  for (const p of overlays) {
    assert.ok(p.width >= edge, `${p.name} の太さ ${p.width} が面の縁 ${edge} より細い`);
    if (!p.name.includes('取引終了時')) assert.ok(p.width <= edge, `${p.name} の太さ ${p.width} が面の縁 ${edge} より太い`);
  }
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

test('線の太さ: 指標の線と面の縁は既定、取引終了時は足ごとより太い、水準の宣言の無い維持率の線は口座ペインの太さ', () => {
  const insts = resultChartInstances(...material([
    { series: 'sma', placement: 'price', value: [null, 1, 2] },
    { series: 'madiff', placement: 'pane', value: [1, 2, 3] },
  ]), close(), BASELINE);
  const account = ['残高・有効証拠金', 'DD', '損益（初期資金比）', '証拠金維持率(%)'];
  const all = insts.flatMap((i) => i.payloads.map((p) => ({ ...p, pane: i.label })));
  const indicatorLines = all.filter((p) => !account.includes(p.pane));
  assert.deepEqual(indicatorLines.map((p) => p.name), ['sma', 'madiff'], '検定の前提: 指標の線が無い');
  for (const p of indicatorLines) assert.equal(p.width, DEFAULT_LINE_WIDTH, `${p.name} の太さ ${p.width}`);
  // 面の縁は既定の太さ（依頼者指示 2026-10-02「面グラフのラインは1px」）。
  const areas = all.filter((p) => p.kind === 'baseline');
  assert.equal(areas.length, AREAS.length);
  for (const p of areas) assert.equal(p.width, DEFAULT_LINE_WIDTH, `${p.name} の太さ ${p.width}`);
  // 取引終了時（森）は足ごと（木）より太い。
  const forest = all.filter((p) => p.name.includes('取引終了時'));
  const trees = all.filter((p) => p.name.includes('足ごと'));
  assert.ok(forest.length > 0 && trees.length > 0);
  for (const f of forest) for (const t of trees) assert.ok(f.width > t.width, `${f.name} が ${t.name} より太くない`);
  // 水準の宣言の無い維持率の線（面の代わりのペインの主役）は口座ペインの太さ（既定 + LINE_WIDTH_GAIN）。
  assert.equal(ACCOUNT_LINE_WIDTH, DEFAULT_LINE_WIDTH + LINE_WIDTH_GAIN);
  assert.ok(ACCOUNT_LINE_WIDTH > DEFAULT_LINE_WIDTH);
});
