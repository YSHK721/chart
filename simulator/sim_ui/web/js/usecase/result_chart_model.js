// result_chart_model.js — 売買履歴チャートの「何をどのパネルへ描くか」（純ロジック）。
//
// 入力はジョブの足の成果物（ISSUE-552/554 段階 2-2: 画面が持つのは表示する範囲だけ）:
//   宣言 … `GET /sim/chart-bars/{job}/extent`（列・指標と列の対応）。
//   列   … `GET /sim/chart-bars/{job}/rows/{start}/{end}` の `columns`（列名 → 並び。位置の区間ぶん。
//          値なしは null）。足（time・open・high・low・close）・口座・指標が同じ行に並ぶ。
//   返った列の長さが行数と違う・区間が問うた区間と違うなら描かない（`rowsMismatch`）。
// DOM・lwc に触れない。描画はライブチャートの ChartRenderer が行い、View は本モジュールの
// 出力（`resultChartInstances`）をそのまま渡す。系列は線か棒で、どちらで描くかは系列の `kind`
// （'line' / 'histogram'）が名乗る（ChartRenderer の renderLine / renderHistogram）。
//
// パネルの並び（依頼者指示 2026-09-26）:
//   0     … 価格（ローソク足・売買マーク・置き場 "price" のトリガー指標）
//   1..k  … 置き場 "pane" のトリガー指標（1 系列 1 パネル）
//   続く  … 口座（残高・有効証拠金）／DD／損益（初期資金比）／証拠金維持率
// 口座系はすべて**足ごと**の値（保有中も更新）。値の無い足（未成立の指標・保有の無い足の
// 維持率）は lwc の whitespace（time だけの点）にする——0 を置くと偽の値を描く。
// 残高・DD の 2 枚には**取引終了時**のレイヤー（シミュレーション結果の資産曲線 balChart・
// ドローダウン ddChart と同じ系列）も重ねる（依頼者指示 2026-09-27）。
// 損益（初期資金比）のパネル（依頼者指示 2026-10-02「スタート残高を基準に損益を分かりやすく
// プロットしてほしい」）: 足ごとの 有効証拠金 − 初期資金 を 0（＝スタート残高）を基準にした棒で描き、
// 0 以上は陽線・0 未満は陰線の色で塗る。確定損益（累計）の線を上に重ねる。含み損益の線は描かない
// （棒の先端と確定損益の線の差が含み損益）。色は View が売買履歴チャートのローソク足から借りて渡す。

/** 描画色（系列ごとに固定）。 */
export const RESULT_CHART_COLORS = Object.freeze({
  indicator: ['#f5c542', '#42a5f5', '#ab47bc', '#26c6da'],
  // 残高・DD の 2 枚は、取引終了時のレイヤーを足ごとのレイヤーに重ねる（2026-09-27 依頼者指示:
  //   足ごとは「木」、取引終了時は「森」）。森を読めるよう、足ごとは不透明度を下げて細く、
  //   取引終了時はシミュレーション結果の資産曲線 balChart・ドローダウン ddChart と同じ色と太さで描く
  //   （`lwc5_chart_renderer.js` の balSeries / ddSeries の線色・lineWidth 2）。
  balance: 'rgba(41,98,255,0.35)',
  equity: 'rgba(38,166,154,0.35)',
  drawdown: 'rgba(239,83,80,0.35)',
  balanceClose: 'rgba(59,130,246,0.9)',
  drawdownClose: 'rgba(239,83,80,0.9)',
  realized: '#2962ff',
  marginLevel: '#ab47bc',
});

/** 取引終了時のレイヤーの線の太さ（足ごとのレイヤーは 1）。 */
export const TRADE_CLOSE_LINE_WIDTH = 2;

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
 * 損益（初期資金比）の棒の点: 足ごとの 有効証拠金 − 初期資金。0 以上は upColor・0 未満は downColor
 * （0 ちょうどは upColor）。有効証拠金が値なし（null・非有限）の足は whitespace（0 を置かない）。
 * @param {Array<number>} times
 * @param {Array<number|null>} equity
 * @param {{deposit: number, upColor: string, downColor: string}} basis
 */
export function pnlBars(times, equity, { deposit, upColor, downColor }) {
  const out = new Array(times.length);
  for (let i = 0; i < times.length; i += 1) {
    const e = equity[i];
    if (e === null || e === undefined || !Number.isFinite(e)) {
      out[i] = { time: times[i] };
      continue;
    }
    const value = e - deposit;
    out[i] = { time: times[i], value, color: value >= 0 ? upColor : downColor };
  }
  return out;
}

/** 足の列（成果物の列名。ローソク足 1 本を作る 5 列）。 */
const CANDLE_COLUMNS = Object.freeze(['time', 'open', 'high', 'low', 'close']);

/** 描く口座の列（`resultChartPanes` が読む列。長さの照合もこの列すべてに行う）。 */
const ACCOUNT_COLUMNS = Object.freeze(
  ['balance', 'equity', 'drawdown', 'realized_pnl', 'margin_level'],
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
 * パネルの並びと各パネルの系列を返す。
 * @param {object} declared   足の成果物の宣言（extent の応答。指標と列の対応を持つ）
 * @param {object} columns    持っている区間の列（列名 → 並び。時刻は列 time）
 * @param {{balData: Array, ddData: Array}} tradeClose 取引終了時の残高・DD（`windowTradeClose` の出力。
 *                            各足の時刻で持つ）。残高・DD のパネルに足ごとのレイヤーと重ねて描く。
 * @param {{deposit: number, upColor: string, downColor: string}} pnlBasis 損益（初期資金比）の基準
 *                            （report.json の meta.initial_deposit）と塗り分けの色（ローソク足の陽線・陰線）。
 * @returns {Array<{title: string, series: Array<{name: string, kind: string, color: string, width?: number, points: Array}>}>}
 *   配列の添字がパネル番号（0 は価格パネル・ローソク足は View が別に置く）。
 */
export function resultChartPanes(declared, columns, tradeClose, pnlBasis) {
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
  const a = columns;
  const t = times;
  panes.push({
    title: '残高・有効証拠金',
    series: [
      { name: '残高（足ごと）', color: RESULT_CHART_COLORS.balance, points: toPoints(t, a.balance) },
      { name: '有効証拠金（足ごと）', color: RESULT_CHART_COLORS.equity, points: toPoints(t, a.equity) },
      { name: '残高（取引終了時）', color: RESULT_CHART_COLORS.balanceClose, width: TRADE_CLOSE_LINE_WIDTH,
        points: tradeClose.balData },
    ],
  });
  panes.push({
    title: 'DD',
    series: [
      // 下落を下向きに見せる（金額は正で持っているので符号を反転して描く）。
      { name: 'DD（足ごと）', color: RESULT_CHART_COLORS.drawdown,
        points: toPoints(t, a.drawdown.map((v) => (v === null ? null : -v))) },
      // 残高ベースの DD（≤0）。シミュレーション結果のドローダウン ddChart と同じ系列。
      { name: 'DD（取引終了時）', color: RESULT_CHART_COLORS.drawdownClose, width: TRADE_CLOSE_LINE_WIDTH,
        points: tradeClose.ddData },
    ],
  });
  panes.push({
    title: '損益（初期資金比）',
    series: [
      // 棒を先に置く（先に描いたものが下になる）。凡例の色は 0 以上の色。
      { name: '損益（初期資金比）', kind: 'histogram', color: pnlBasis.upColor,
        points: pnlBars(t, a.equity, pnlBasis) },
      { name: '確定損益（累計）', color: RESULT_CHART_COLORS.realized, points: toPoints(t, a.realized_pnl) },
    ],
  });
  panes.push({
    title: '証拠金維持率(%)',
    series: [
      { name: '証拠金維持率', color: RESULT_CHART_COLORS.marginLevel, points: toPoints(t, a.margin_level) },
    ],
  });
  // kind を名乗らない系列は線（点の列は作り直さずそのまま渡す）。
  return panes.map((pane) => ({ ...pane, series: pane.series.map((s) => ({ kind: 'line', ...s })) }));
}

/**
 * ChartRenderer へ渡す単位（instance）の列。ライブチャートの指標 1 つ＝instance 1 つと同じ扱いで、
 * ペイン別凡例の行もこの単位で出る。
 *   価格パネルの系列 … 系列 1 つが instance 1 つ（凡例で 1 本ずつ表示/非表示を切り替えられる）
 *   それ以外のパネル … パネル 1 枚が instance 1 つ（パネルの系列を束ねる）
 * @returns {Array<{instanceId: string, label: string, pane: boolean,
 *   payloads: Array<{name: string, kind: string, color: string, width: number, style: string, data: Array}>}>}
 */
export function resultChartInstances(declared, columns, tradeClose, pnlBasis) {
  const payload = (s) => ({
    name: s.name, kind: s.kind, color: s.color, width: s.width || 1, style: 'solid', data: s.points,
  });
  const out = [];
  resultChartPanes(declared, columns, tradeClose, pnlBasis).forEach((pane, paneIndex) => {
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
