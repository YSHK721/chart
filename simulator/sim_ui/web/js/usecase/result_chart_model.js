// result_chart_model.js — 売買履歴チャートの「何をどのパネルへ描くか」（純ロジック）。
//
// 入力はジョブの足の成果物（ISSUE-552/554 段階 2-2: 画面が持つのは表示する範囲だけ）:
//   宣言 … `GET /sim/chart-bars/{job}/extent`（列・指標と列の対応）。
//   列   … `GET /sim/chart-bars/{job}/rows/{start}/{end}` の `columns`（列名 → 並び。位置の区間ぶん。
//          値なしは null）。足（time・open・high・low・close）・口座・指標が同じ行に並ぶ。
//   返った列の長さが行数と違う・区間が問うた区間と違うなら描かない（`rowsMismatch`）。
// DOM・lwc に触れない。描画はライブチャートの ChartRenderer が行い、View は本モジュールの
// 出力（`resultChartInstances`）をそのまま渡す。系列は線か面で、どちらで描くかは系列の `kind`
// （'line' / 'baseline'）が名乗る（ChartRenderer の renderLine / renderBaseline）。
//
// パネルの並び（依頼者指示 2026-09-26）:
//   0     … 価格（ローソク足・売買マーク・置き場 "price" のトリガー指標）
//   1..k  … 置き場 "pane" のトリガー指標（1 系列 1 パネル）
//   続く  … 口座（残高・有効証拠金）／DD／損益（初期資金比）／証拠金維持率
// 口座系はすべて**足ごと**の値（保有中も更新）。値の無い足（未成立の指標・保有の無い足の
// 維持率）は lwc の whitespace（time だけの点）にする——0 を置くと偽の値を描く。
// 残高・DD の 2 枚には**取引終了時**のレイヤー（シミュレーション結果の資産曲線 balChart・
// ドローダウン ddChart と同じ系列）も重ねる（依頼者指示 2026-09-27）。
// 基準つきの面（依頼者指示 2026-10-02「スタート残高を基準に損益を分かりやすくプロットしてほしい」
// 「残高グラフも同じ仕様にしろ」「棒グラフではなく、面グラフでグラデーションで表現しろ」「DDも同じく」
// 「証拠金維持率も同じく」「面グラフのラインは1px」）。
// 基準を境に、上は陽線・下は陰線の色の、グラデーションの面（lwc の BaselineSeries・kind 'baseline'）。
// 色は View が売買履歴チャートのローソク足から借りて渡す。縁の線は既定の太さ（DEFAULT_LINE_WIDTH）。
//   面と線の規則（依頼者裁定 2026-10-02）: 面＝足ごとの有効証拠金ベース・重ねる線＝確定（残高ベース）。
//     宣言は SERIES_RULE・SOURCE_BASIS・ACCOUNT_PANES の 1 か所。
//   残高・有効証拠金 … 足ごとの有効証拠金の面。基準は初期資金の高さ（目盛りは金額のまま）。
//                      確定の線（残高（取引終了時））を上に重ねる。確定の線は全ペインで同じ見た目・1 本（SERIES_RULE）。
//   DD               … 足ごとの DD（下向き・≤0）の面。基準は 0。DD（取引終了時）の線を上に重ねる。
//   損益（初期資金比）… 足ごとの 有効証拠金 − 初期資金 の面。基準は 0（＝スタート残高）。
//                      確定損益（累計）の線を上に重ねる。含み損益の線は描かない
//                      （面の縁と確定損益の線の差が含み損益）。
//   証拠金維持率(%)  … 足ごとの維持率の面。基準は run が使ったストップアウト水準（足の成果物の宣言
//                      `stop_out_level`）。宣言が水準を名乗らないジョブ（宣言に書く前に実行した run）は
//                      基準が分からないので面にせず線で描き、凡例名「証拠金維持率（水準の宣言なし）」で示す。

/** 描画色（系列ごとに固定）。 */
export const RESULT_CHART_COLORS = Object.freeze({
  indicator: ['#f5c542', '#42a5f5', '#ab47bc', '#26c6da'],
  // 確定の線の色（全ペインで 1 色・依頼者裁定 2026-10-02「DDの配色が、確定と足ごとが同じ色で視認性が
  //   悪すぎる」「統一しろ」）。色相はシミュレーション結果の資産曲線 balChart の線色（`lwc5_chart_renderer.js`
  //   の balSeries）で、面の上下の色（陽線・陰線）とは別の色相。不透明度は 0.75。
  settled: 'rgba(59,130,246,0.75)',
  marginLevel: '#ab47bc',
});

/** 線の既定の太さ（lwc の既定と同じ 1）。指標の線（価格パネル・別窓）はこの太さのまま。 */
export const DEFAULT_LINE_WIDTH = 1;
/**
 * 口座のペインの主役の線を太くする量（依頼者指示 2026-10-02「全体的に線が見にくい +2px 程度太くしたい」
 * 「残高グラフなどの4ペインのラインのことだけで... チャートの移動平均線は通常でよい」）。面に替わった後は、
 * 面の代わりに描く線（`ACCOUNT_LINE_WIDTH`）だけに効く。
 */
export const LINE_WIDTH_GAIN = 2;
/**
 * 口座のペインで面の代わりに主役になる線の太さ。今は、宣言が水準を名乗らないジョブの証拠金維持率の線だけ
 * （面に重ねる線は控えめにする・依頼者指示 2026-10-02）。
 */
export const ACCOUNT_LINE_WIDTH = DEFAULT_LINE_WIDTH + LINE_WIDTH_GAIN;
/** 確定の線の太さ（全ペインで 1 つ。面の縁＝既定より 1 太い）。 */
export const SETTLED_LINE_WIDTH = DEFAULT_LINE_WIDTH + 1;

/** 値の列を lwc の点列へ（null・非有限は whitespace）。 */
export function toPoints(times, values) {
  const out = new Array(times.length);
  for (let i = 0; i < times.length; i += 1) {
    const v = values[i];
    out[i] = (v === null || v === undefined || !Number.isFinite(v))
      ? { time: times[i] }
      : { time: times[i], value: v };
  }
  return out;
}

/**
 * 面の塗りの不透明度。edge＝縁の線の側（濃い）、base＝基準の側（薄い）。値は vendor の lightweight-charts
 * v5 の BaselineSeries の既定（topFillColor1 0.28 → topFillColor2 0.05・bottomFillColor1 0.05 →
 * bottomFillColor2 0.28）と同じ。
 */
export const AREA_FILL_ALPHA = Object.freeze({ edge: 0.28, base: 0.05 });

/**
 * 面の点を作る口。面の点はすべてここを通る（観測の境界として宣言する注入点:
 * `resultChartPanes` / `resultChartInstances` の `seams.areaPoint`。計算量の検定が数える）。
 * @param {number} time
 * @param {number|null} value null は値なし（whitespace＝time だけの点）
 */
export function areaPoint(time, value) {
  return value === null ? { time } : { time, value };
}

/**
 * 面の点: 足ごとの値 v を toValue(v) にする。値なし（null・非有限）の足は whitespace（0 を置かない）。
 * @param {Array<number>} times
 * @param {Array<number|null>} values
 * @param {function} toValue (v) => number
 * @param {function} point 面の点を作る口（既定 `areaPoint`）
 */
export function areaPoints(times, values, toValue, point = areaPoint) {
  const out = new Array(times.length);
  for (let i = 0; i < times.length; i += 1) {
    const v = values[i];
    out[i] = (v === null || v === undefined || !Number.isFinite(v)) ? point(times[i], null) : point(times[i], toValue(v));
  }
  return out;
}

/**
 * 面の 6 色（lwc の BaselineSeries のオプション名）。基準より上は upColor・下は downColor。
 * 線は不透明、面は縁の線の側が濃く（AREA_FILL_ALPHA.edge）基準の側が薄い（AREA_FILL_ALPHA.base）。
 * @param {{upColor: string, downColor: string, withAlpha: function}} baseline
 */
export function areaFill({ upColor, downColor, withAlpha }) {
  return {
    topLineColor: upColor,
    topFillColor1: withAlpha(upColor, AREA_FILL_ALPHA.edge),
    topFillColor2: withAlpha(upColor, AREA_FILL_ALPHA.base),
    bottomLineColor: downColor,
    bottomFillColor1: withAlpha(downColor, AREA_FILL_ALPHA.base),
    bottomFillColor2: withAlpha(downColor, AREA_FILL_ALPHA.edge),
  };
}

/** 足の列（成果物の列名。ローソク足 1 本を作る 5 列）。 */
const CANDLE_COLUMNS = Object.freeze(['time', 'open', 'high', 'low', 'close']);

/** 描く口座の列（`resultChartPanes` が読む列。長さの照合もこの列すべてに行う）。 */
const ACCOUNT_COLUMNS = Object.freeze(
  ['equity', 'drawdown', 'realized_pnl', 'margin_level'],
);

function indicatorsOf(declared) {
  return Array.isArray(declared && declared.indicators) ? declared.indicators : [];
}

/**
 * 描画に使う列の名前（位置の列・足・描く口座の列・宣言された指標の列）。
 * 画面が持ち続けるのはこの列だけ（成果物には描かない列も在る）。
 * @param {object} declared 足の成果物の宣言（extent の応答）
 * @returns {Array<string>}
 */
export function drawnColumns(declared) {
  return [
    declared.index_column, ...CANDLE_COLUMNS, ...ACCOUNT_COLUMNS,
    ...indicatorsOf(declared).map((ind) => ind.column),
  ];
}

/**
 * 返った区間が問うた区間そのもので、描く列がすべて行数と同じ長さか。一致すれば null、
 * しなければ最初に食い違った点の説明。長さや位置が違う列を描くと、別の足の上に値を置くことになる
 * （黙ってずらさない）。欠けた列は長さ 0。
 * @param {object} declared 足の成果物の宣言（extent の応答）
 * @param {{start: number, end: number, rows: number, columns: object}} answer rows の応答
 * @param {{start: number, end: number}} asked 問うた区間
 * @returns {string|null}
 */
export function rowsMismatch(declared, answer, asked) {
  if (!answer || answer.start !== asked.start || answer.end !== asked.end) {
    return `返った区間 [${answer && answer.start}, ${answer && answer.end}) が問うた区間 [${asked.start}, ${asked.end}) と一致しません`;
  }
  const expected = asked.end - asked.start;
  if (answer.rows !== expected) {
    return `返った行数 ${answer.rows} が区間の本数 ${expected} と一致しません`;
  }
  const columns = answer.columns || {};
  const seriesOf = new Map(indicatorsOf(declared).map((ind) => [ind.column, ind.series]));
  for (const name of drawnColumns(declared)) {
    const n = Array.isArray(columns[name]) ? columns[name].length : 0;
    if (n !== answer.rows) {
      const label = seriesOf.has(name) ? `${name}（${seriesOf.get(name)}）` : name;
      return `列 ${label} の長さ ${n} が行数 ${answer.rows} と一致しません`;
    }
  }
  if (answer.rows > 0 && columns[declared.index_column][0] !== asked.start) {
    return `返った先頭の位置 ${columns[declared.index_column][0]} が問うた位置 ${asked.start} と一致しません`;
  }
  return null;
}

/** 列からローソク足の列を作る（ChartRenderer へ渡す形）。 */
export function candlesOf(columns) {
  const { time, open, high, low, close } = columns;
  const out = new Array(time.length);
  for (let i = 0; i < time.length; i += 1) {
    out[i] = { time: time[i], open: open[i], high: high[i], low: low[i], close: close[i] };
  }
  return out;
}

/**
 * 足ごとの量の出所（成果物の列・取引終了時の系列）が、有効証拠金ベース（含み損益込み）か確定（残高ベース）か。
 *   drawdown・margin_level は有効証拠金から作る列（書き手 chart_overlay_writer の drawdown(account.equity)・
 *   口座記録の維持率＝有効証拠金 ÷ 必要証拠金）。取引終了時の系列は残高ベース（report_ui の tradeCloseCurves）。
 */
export const SOURCE_BASIS = Object.freeze({
  equity: 'equity',
  drawdown: 'equity',
  margin_level: 'equity',
  realized_pnl: 'settled',
  balanceClose: 'settled',
  drawdownClose: 'settled',
});

/**
 * 面と線の規則（依頼者裁定 2026-10-02「面グラフのルールはどうなっているのか? … 統一しろ」）:
 *   面（role 'area'）＝足ごとの有効証拠金ベース（含み損益込み）／重ねる線（role 'line'）＝確定（残高ベース）。
 * 確定の線（依頼者裁定 2026-10-02「確定と足ごとが同じ色で視認性が悪すぎる」「太さが違う。統一しろ」）:
 *   全ペインで同じ見た目（settledLine の色・太さ）で、1 ペイン高々 1 本。
 */
export const SERIES_RULE = Object.freeze({
  area: 'equity',
  line: 'settled',
  settledLine: Object.freeze({ color: RESULT_CHART_COLORS.settled, width: SETTLED_LINE_WIDTH }),
});

/** 取引終了時の系列（点の列のまま重ねる。出所の名前 → tradeClose の鍵）。 */
const TRADE_CLOSE_SOURCES = Object.freeze({ balanceClose: 'balData', drawdownClose: 'ddData' });

/**
 * 口座のペイン（並び順）。各ペインは面 1 つ（role 'area'）と、その上に重ねる線（role 'line'）を宣言する。
 *   area.base(ctx)    … 面の基準（ctx = { deposit, stopOutLevel }）。null なら基準が分からない
 *                       → 面にせず area.fallback の名前の線で描く（ペインの主役なので ACCOUNT_LINE_WIDTH）。
 *   area.toValue(v, ctx) … 列の値 → 描く値。
 *   lines             … 確定の線（高々 1 本・見た目は SERIES_RULE.settledLine）。
 */
const ACCOUNT_PANES = Object.freeze([
  {
    title: '残高・有効証拠金',
    // 基準は初期資金の高さ（目盛りは金額のまま）。
    area: { name: '有効証拠金（足ごと）', source: 'equity', base: (ctx) => ctx.deposit, toValue: (v) => v },
    // 残高は決済でしか変わらないので、確定の線は取引終了時の 1 本。
    lines: [{ name: '残高（取引終了時）', source: 'balanceClose' }],
  },
  {
    title: 'DD',
    // 下落を下向きに見せる（金額は正で持っているので符号を反転して描く）。基準は 0。
    area: { name: 'DD（足ごと）', source: 'drawdown', base: () => 0, toValue: (v) => -v },
    // 残高ベースの DD（≤0）。シミュレーション結果のドローダウン ddChart と同じ系列。
    lines: [{ name: 'DD（取引終了時）', source: 'drawdownClose' }],
  },
  {
    title: '損益（初期資金比）',
    // 基準は 0（＝スタート残高）。面の縁と確定損益の線の差が含み損益。
    area: { name: '損益（初期資金比）', source: 'equity', base: () => 0, toValue: (v, ctx) => v - ctx.deposit },
    lines: [{ name: '確定損益（累計）', source: 'realized_pnl' }],
  },
  {
    title: '証拠金維持率(%)',
    // 基準は run が使ったストップアウト水準（足の成果物の宣言 stop_out_level）。
    area: {
      name: '証拠金維持率', source: 'margin_level', base: (ctx) => ctx.stopOutLevel, toValue: (v) => v,
      fallback: { name: '証拠金維持率（水準の宣言なし）', color: 'marginLevel' },
    },
    lines: [],
  },
]);

/** 宣言 1 枚ぶんの系列（面を先に＝下に置き、線を重ねる）。 */
function accountSeries(pane, { declared, columns, tradeClose, baseline, point }) {
  const t = columns.time;
  const ctx = {
    deposit: baseline.deposit,
    stopOutLevel: Number.isFinite(declared.stop_out_level) ? declared.stop_out_level : null,
  };
  const { area } = pane;
  const base = area.base(ctx);
  const out = [];
  if (base === null) {
    out.push({
      name: area.fallback.name, role: 'area', source: area.source, width: ACCOUNT_LINE_WIDTH,
      color: RESULT_CHART_COLORS[area.fallback.color], points: toPoints(t, columns[area.source]),
    });
  } else {
    out.push({
      name: area.name, role: 'area', source: area.source, kind: 'baseline', color: baseline.upColor,
      width: DEFAULT_LINE_WIDTH, base, fill: areaFill(baseline),
      points: areaPoints(t, columns[area.source], (v) => area.toValue(v, ctx), point),
    });
  }
  for (const line of pane.lines) {
    const closeKey = TRADE_CLOSE_SOURCES[line.source];
    out.push({
      name: line.name, role: 'line', source: line.source,
      color: SERIES_RULE.settledLine.color, width: SERIES_RULE.settledLine.width,
      points: closeKey ? tradeClose[closeKey] : toPoints(t, columns[line.source]),
    });
  }
  return out;
}

/**
 * パネルの並びと各パネルの系列を返す。
 * @param {object} declared   足の成果物の宣言（extent の応答。指標と列の対応を持つ）
 * @param {object} columns    持っている区間の列（列名 → 並び。時刻は列 time）
 * @param {{balData: Array, ddData: Array}} tradeClose 取引終了時の残高・DD（`windowTradeClose` の出力。
 *                            各足の時刻で持つ）。残高・DD のパネルに足ごとのレイヤーと重ねて描く。
 * @param {{deposit: number, upColor: string, downColor: string, withAlpha: function}} baseline 面の基準
 *                            （report.json の meta.initial_deposit）と塗り分けの色（ローソク足の陽線・陰線）・
 *                            色に不透明度を付ける関数（report_ui の _withAlpha）。
 * @param {{areaPoint?: function}} [seams] 観測の境界（面の点を作る口。既定 `areaPoint`）
 * @returns {Array<{title: string, series: Array<{name: string, kind: string, color: string, width?: number,
 *   base?: number, fill?: object, points: Array}>}>}
 *   配列の添字がパネル番号（0 は価格パネル・ローソク足は View が別に置く）。
 */
export function resultChartPanes(declared, columns, tradeClose, baseline, { areaPoint: point = areaPoint } = {}) {
  const times = columns.time;
  const panes = [{ title: '価格', series: [] }];
  indicatorsOf(declared).forEach((ind, i) => {
    const series = {
      name: ind.series,
      color: RESULT_CHART_COLORS.indicator[i % RESULT_CHART_COLORS.indicator.length],
      points: toPoints(times, columns[ind.column]),
    };
    if (ind.placement === 'price') {
      panes[0].series.push(series);
    } else {
      panes.push({ title: ind.series, series: [series] });
    }
  });
  for (const pane of ACCOUNT_PANES) {
    panes.push({ title: pane.title, series: accountSeries(pane, { declared, columns, tradeClose, baseline, point }) });
  }
  // kind を名乗らない系列は線（点の列は作り直さずそのまま渡す）。
  return panes.map((pane) => ({ ...pane, series: pane.series.map((s) => ({ kind: 'line', ...s })) }));
}

/**
 * ChartRenderer へ渡す単位（instance）の列。ライブチャートの指標 1 つ＝instance 1 つと同じ扱いで、
 * ペイン別凡例の行もこの単位で出る。
 *   価格パネルの系列 … 系列 1 つが instance 1 つ（凡例で 1 本ずつ表示/非表示を切り替えられる）
 *   それ以外のパネル … パネル 1 枚が instance 1 つ（パネルの系列を束ねる）
 * @returns {Array<{instanceId: string, label: string, pane: boolean,
 *   payloads: Array<{name: string, kind: string, color: string, width: number, style: string, data: Array,
 *     base?: number, baseline?: object}>}>}
 */
export function resultChartInstances(declared, columns, tradeClose, baseline, seams = {}) {
  const payload = (s) => ({
    name: s.name, kind: s.kind, color: s.color, width: s.width || DEFAULT_LINE_WIDTH, style: 'solid', data: s.points,
    // 面の基準と 6 色（ChartRenderer の renderBaseline が lwc の baseValue・面の色へ渡す）。面だけが持つ。
    ...(s.kind === 'baseline' ? { base: s.base, baseline: s.fill } : {}),
  });
  const out = [];
  resultChartPanes(declared, columns, tradeClose, baseline, seams).forEach((pane, paneIndex) => {
    if (paneIndex === 0) {
      for (const s of pane.series) {
        out.push({ instanceId: `price:${s.name}`, label: s.name, pane: false, payloads: [payload(s)] });
      }
      return;
    }
    out.push({
      instanceId: `pane:${pane.title}`, label: pane.title, pane: true, payloads: pane.series.map(payload),
    });
  });
  return out;
}

/**
 * balance_curve の時刻（昇順・重複なし）。区間ごとの前置きに使う。1 ジョブにつき 1 回作る。
 * @param {object|null} segment report.json の先頭の区間
 * @returns {Array<number>}
 */
export function balanceCurveTimes(segment) {
  const curve = (segment && segment.agg && segment.agg.balance_curve) || [];
  return [...new Set(curve.map((p) => p.time))].sort((x, y) => x - y);
}

/** 昇順の列 sorted で、value 未満の要素の数（二分探索）。 */
function countBelow(sorted, value) {
  let lo = 0;
  let hi = sorted.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (sorted[mid] < value) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/**
 * 持っている区間の足の時刻で、取引終了時の残高・DD を作る。**全期間の足で作った値の同じ区間と一致する**。
 *
 * なぜ前置きするか: 作る関数（report_ui の tradeCloseCurves・単一ソース）は、渡した足の先頭で
 *   最高値を初期資金へ戻す。区間の足だけを渡すと、区間より前に付けた最高値を知らないまま DD を出す。
 *   区間より前の balance_curve の時刻を足の時刻の前に置いて計算し、前置き分を切り落とす。
 * 前提（実測 2026-09-30・実ジョブ）: balance_curve の時刻 ⊆ 足の時刻。全期間の計算が残高の更新を
 *   見るのは balance_curve の時刻の足だけなので、その時刻を前に置けば最高値の推移が同じになる。
 *
 * @param {function} tradeCloseCurves (segment, barTimes, deposit) => {balData, ddData}
 * @param {object}   segment     report.json の先頭の区間
 * @param {number}   deposit     payload の初期証拠金
 * @param {Array<number>} curveTimes `balanceCurveTimes(segment)`
 * @param {Array<number>} times  持っている区間の足の時刻
 * @returns {{balData: Array, ddData: Array}}
 */
export function windowTradeClose({ tradeCloseCurves, segment, deposit, curveTimes, times }) {
  const before = times.length > 0 ? countBelow(curveTimes, times[0]) : 0;
  if (before === 0) return tradeCloseCurves(segment, times, deposit);
  const whole = tradeCloseCurves(segment, curveTimes.slice(0, before).concat(times), deposit);
  return { balData: whole.balData.slice(before), ddData: whole.ddData.slice(before) };
}
