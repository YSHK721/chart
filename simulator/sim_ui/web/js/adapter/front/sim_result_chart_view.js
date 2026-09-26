// sim_result_chart_view.js — ジョブ結果を上のチャート領域へ描く View（2026-09-26 依頼者指示）。
//
// 何を描くか（「何をどのパネルへ」は usecase/result_chart_model.js が決め、ここは描くだけ）:
//   価格パネル … run が読んだ系列と同じ足（`/candles` の範囲読み）・売買マーク・トリガー指標
//   別パネル  … 別窓指標・残高/有効証拠金・DD・損益・証拠金維持率（いずれも足ごと）
//
// 部品はライブチャートと同じもの（チャート生成・売買マーク描画）を**注入で**受け取る
//   （`chartKit`＝live core の公開面 `live_chart_kit_api.js`。sim は live core の URL を知らない）。
//
// 描かない条件（黙ってずらさない）:
//   - 系列が台帳外（`/candles` で同じ足を読めない）
//   - 読んだ足の時刻の並びが run の足と 1 本でも違う
//   どちらも理由を host に出して終わる。

import {
  candleRequestOf,
  candlesMatchRunBars,
  resultChartPanes,
} from '../../usecase/result_chart_model.js';
import { buildResultChart } from './lwc5_chart_renderer.js';

/**
 * @param {object}   deps
 * @param {Document} deps.doc
 * @param {Element}  deps.host          描画先（上のチャート領域の器）
 * @param {object}   deps.lwc           lightweight-charts v5
 * @param {object}   deps.chartKit      { createChartWithMainSeries, TradeMarkersRenderer }
 * @param {function} deps.fetchJson     (url) => Promise<object>（sim の成果物を読む）
 * @param {function} deps.fetchCandles  ({datasetRef, timeframe, from, to}) => Promise<Array>
 * @param {function} [deps.fetchImpl]   売買マークの読み込みに使う fetch
 */
export function createSimResultChartView({ doc, host, lwc, chartKit, fetchJson, fetchCandles, fetchImpl }) {
  let chart = null;
  let message = null;
  let shownJob = null;

  function clear() {
    if (chart) {
      chart.remove();
      chart = null;
    }
    if (message) {
      host.removeChild(message);
      message = null;
    }
    shownJob = null;
  }

  function showMessage(text) {
    message = doc.createElement('div');
    message.className = 'sim-result-chart-message';
    message.textContent = text;
    host.appendChild(message);
  }

  return {
    /** 表示中のジョブ（診断・E2E 用）。 */
    shownJob() { return shownJob; },

    /** ジョブの結果を描く（同じジョブなら描き直さない）。@returns {Promise<boolean>} 描いたか */
    async render(jobId) {
      if (jobId === shownJob) return chart !== null;
      clear();
      shownJob = jobId;
      const overlay = await fetchJson(`/sim/data/${encodeURIComponent(jobId)}/chart_overlay.json`);
      const request = candleRequestOf(overlay);
      if (!request) {
        showMessage('このジョブの価格系列はデータ台帳に無いため、上のチャートには表示できません。');
        return false;
      }
      const candles = await fetchCandles(request);
      if (!candlesMatchRunBars(candles, overlay.account.time)) {
        showMessage('上のチャートの足がジョブの足と一致しないため表示しません。');
        return false;
      }
      const built = buildResultChart({
        lwc, chartKit, host, candles, panes: resultChartPanes(overlay),
      });
      chart = built.chart;
      const markers = new chartKit.TradeMarkersRenderer({
        lwc, mainSeries: built.mainSeries, chart, document: doc, container: host,
      });
      markers.setCurrentTimeframe(overlay.timeframe);
      await markers.load(`/sim/data/${encodeURIComponent(jobId)}/trade_markers.json`, fetchImpl);
      chart.timeScale().fitContent();
      return true;
    },

    /** 描いたものを片付ける（器へ何も残さない）。 */
    clear,
  };
}
