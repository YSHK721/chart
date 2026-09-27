// result_chart_model.js — 売買履歴チャートの「何をどのパネルへ描くか」（純ロジック）。
//
// 入力はジョブの成果物 `chart_overlay.json`（サーバ `sim_ui/adapter/chart_overlay_writer.py` が書く）。
// DOM・lwc に触れない。描画はライブチャートの ChartRenderer が行い、View は本モジュールの
// 出力（`resultChartInstances`）をそのまま渡す（系列はすべて線・ChartRenderer に面の塗りの種類は無い）。
//
// パネルの並び（依頼者指示 2026-09-26）:
//   0     … 価格（ローソク足・売買マーク・置き場 "price" のトリガー指標）
//   1..k  … 置き場 "pane" のトリガー指標（1 系列 1 パネル）
//   続く  … 口座（残高・有効証拠金）／DD／損益（確定の累計・含み）／証拠金維持率
// 口座系はすべて**足ごと**の値（保有中も更新）。値の無い足（未成立の指標・保有の無い足の
// 維持率）は lwc の whitespace（time だけの点）にする——0 を置くと偽の値を描く。

/** 描画色（系列ごとに固定）。 */
export const RESULT_CHART_COLORS = Object.freeze({
  indicator: ['#f5c542', '#42a5f5', '#ab47bc', '#26c6da'],
  balance: '#2962ff',
  equity: '#26a69a',
  drawdown: '#ef5350',
  realized: '#2962ff',
  floating: '#ff9800',
  marginLevel: '#ab47bc',
});

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
 * パネルの並びと各パネルの系列を返す。
 * @returns {Array<{title: string, series: Array<{name: string, color: string, points: Array}>}>}
 *   配列の添字がパネル番号（0 は価格パネル・ローソク足は View が別に置く）。
 */
export function resultChartPanes(overlay) {
  const panes = [{ title: '価格', series: [] }];
  const indicators = Array.isArray(overlay.indicators) ? overlay.indicators : [];
  indicators.forEach((ind, i) => {
    const series = {
      name: ind.series,
      color: RESULT_CHART_COLORS.indicator[i % RESULT_CHART_COLORS.indicator.length],
      points: toPoints(ind.time, ind.value),
    };
    if (ind.placement === 'price') {
      panes[0].series.push(series);
    } else {
      panes.push({ title: ind.series, series: [series] });
    }
  });
  const a = overlay.account;
  const t = a.time;
  panes.push({
    title: '残高・有効証拠金',
    series: [
      { name: '残高', color: RESULT_CHART_COLORS.balance, points: toPoints(t, a.balance) },
      { name: '有効証拠金', color: RESULT_CHART_COLORS.equity, points: toPoints(t, a.equity) },
    ],
  });
  panes.push({
    title: 'DD',
    series: [
      // 下落を下向きに見せる（金額は正で持っているので符号を反転して描く）。
      { name: 'DD', color: RESULT_CHART_COLORS.drawdown,
        points: toPoints(t, a.drawdown.map((v) => -v)) },
    ],
  });
  panes.push({
    title: '損益',
    series: [
      { name: '確定損益（累計）', color: RESULT_CHART_COLORS.realized, points: toPoints(t, a.realized_pnl) },
      { name: '含み損益', color: RESULT_CHART_COLORS.floating, points: toPoints(t, a.floating_pnl) },
    ],
  });
  panes.push({
    title: '証拠金維持率(%)',
    series: [
      { name: '証拠金維持率', color: RESULT_CHART_COLORS.marginLevel, points: toPoints(t, a.margin_level) },
    ],
  });
  return panes;
}

/**
 * ChartRenderer へ渡す単位（instance）の列。ライブチャートの指標 1 つ＝instance 1 つと同じ扱いで、
 * ペイン別凡例の行もこの単位で出る。
 *   価格パネルの系列 … 系列 1 つが instance 1 つ（凡例で 1 本ずつ表示/非表示を切り替えられる）
 *   それ以外のパネル … パネル 1 枚が instance 1 つ（パネルの系列を束ねる）
 * @returns {Array<{instanceId: string, label: string, pane: boolean,
 *   payloads: Array<{name: string, color: string, width: number, style: string, data: Array}>}>}
 */
export function resultChartInstances(overlay) {
  const payload = (s) => ({ name: s.name, color: s.color, width: 1, style: 'solid', data: s.points });
  const out = [];
  resultChartPanes(overlay).forEach((pane, paneIndex) => {
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
 * 売買履歴チャートが読む足の範囲（`/candles` のクエリ材料）。台帳の系列でなければ null。
 * 範囲は run の足の最初と最後（両端含む・`/candles` の from/to と同じ規約）。
 */
export function candleRequestOf(overlay) {
  const times = overlay.account && overlay.account.time;
  if (!overlay.dataset_ref || !Array.isArray(times) || times.length === 0) {
    return null;
  }
  return {
    datasetRef: overlay.dataset_ref,
    timeframe: overlay.timeframe,
    from: times[0],
    to: times[times.length - 1],
  };
}

/**
 * 読んだ足が run の足と**同じ時刻の並び**か（1 本でも違えば false）。
 * 違う足へ売買マークや指標を重ねると、別の足の上に描くことになる（黙ってずらさない）。
 */
export function candlesMatchRunBars(candles, runTimes) {
  if (!Array.isArray(candles) || candles.length !== runTimes.length) {
    return false;
  }
  for (let i = 0; i < runTimes.length; i += 1) {
    if (candles[i].time !== runTimes[i]) {
      return false;
    }
  }
  return true;
}
