// sim_result_chart_view.js — ジョブ結果を売買履歴チャートへ描く View（2026-09-26 依頼者指示）。
//
// 何を描くか（「何をどのパネルへ」は usecase/result_chart_model.js が決め、ここは描くだけ）:
//   価格パネル … ジョブ自身の足（report.json の先頭の区間の足＝run が実行した Bar 列）・売買マーク・トリガー指標
//   別パネル  … 別窓指標・残高/有効証拠金・DD・損益・証拠金維持率（いずれも足ごと）
//
// 組み立てはライブチャートと**同じ関数**で行う（`chartKit`＝live core の公開面 `live_chart_kit_api.js`
//   を統合層が注入する。sim は live core の URL を知らない）。2026-09-27 依頼者指示「チャートの
//   操作性もライブモードと同期しろ」: 読み取り欄・ペイン別凡例・価格軸ホイール・ダブルクリック復帰・
//   縦ドラッグ・最新足ボタン・右クリックのメニューは、ライブチャートの組み立て関数が付ける。
//   ここで部品を new し直すと、ライブチャートの修正が売買履歴チャートへ届かない第 2 実装になる。
//   本ファイルは lwc の API を直接呼ばない（系列の追加・表示範囲はすべて ChartRenderer 経由）。
//
// 足の出所（ISSUE-552/554 段階 1）: 足はライブの `/candles` からではなく、ジョブ自身の成果物 report.json
//   から描く。ライブは 1 分足を末尾 5 万本しか持たないため、それを超える run では足を読めなかった。
//   report.json は取引終了時の残高・DD の材料でもあり、**1 ジョブにつき 1 回だけ**取得して両方に使う。
//
// 描かない条件（黙ってずらさない）:
//   - report.json に足が無い
//   - chart_overlay.json の値の列の長さが足の本数と違う
//   どちらも理由を host に出して終わる。

import {
  overlayLengthMismatch,
  resultChartInstances,
  wholeRunMinBarSpacing,
} from '../../usecase/result_chart_model.js';
import { firstSegment } from './report_source_client.js';

/** 成果物が 409（ジョブ未完了）のときの掲示（chart_overlay.json・report.json で共通・ISSUE-540）。 */
const NOT_READY_MESSAGE = "ジョブが完了していないため、売買履歴チャートはまだ表示できません。完了すると表示します。";

/** チャートを生成する要素（器 host の中に置く。読み取り欄・凡例は host 直下に並ぶ）。 */
const CANVAS_CLASS = 'sim-result-chart-canvas';

/**
 * @param {object}   deps
 * @param {Document} deps.doc
 * @param {Element}  deps.host          描画先（売買履歴チャートの器）
 * @param {object}   deps.lwc           lightweight-charts v5
 * @param {object}   deps.chartKit      live core の公開面（composeChartViewer・installPaneGeometry・
 *                                      installChartOperations・ChartToastView・TradeMarkersRenderer）
 * @param {function} deps.fetchJson     (url) => Promise<object>（sim の成果物を読む）
 * @param {function} deps.loadReport    (jobId) => Promise<object>（report.json の取得。1 ジョブにつき 1 回呼ぶ）
 * @param {function} deps.tradeCloseCurves (segment, barTimes, payloadDeposit) => {balData, ddData}
 *                                      （取引終了時の残高・DD。シミュレーション結果の資産曲線 balChart・
 *                                      ドローダウン ddChart と同じ系列を作る report_ui の単一ソース）
 * @param {function} [deps.fetchImpl]   売買マークの読み込みに使う fetch
 */
export function createSimResultChartView({
  doc, host, lwc, chartKit, fetchJson, loadReport, tradeCloseCurves, fetchImpl,
}) {
  let built = null;
  let message = null;
  let shownJob = null;
  // 取引明細・価格ローソク足priceChart と共有する hover の状態（子文書の linkage・ISSUE-538）。
  let linkage = null;

  // 売買マークを linkage と双方向に結ぶ: グリフ hover → linkage、linkage → 強調（同じ取引番号）。
  function wireMarkers(markers) {
    if (!linkage) return;
    markers.onHighlightChange((id) => linkage.setHover(id, "chart"));
    markers.highlightTrade(linkage.hoverTradeId);
  }

  function clear() {
    if (built) {
      built.dispose();
      built = null;
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

  /** ライブチャートと同じ関数でチャートと操作性を組み、描いたものを片付ける関数を返す。 */
  function build(overlay, candles, times, tradeClose) {
    const container = doc.createElement('div');
    container.className = CANVAS_CLASS;
    host.appendChild(container);
    const viewer = chartKit.composeChartViewer({
      lwc, container, doc, anchor: host, datasetRef: overlay.dataset_ref,
    });
    const { renderer } = viewer;
    const geometry = chartKit.installPaneGeometry({ container, chart: viewer.chart, renderer });

    const instances = resultChartInstances(overlay, times, tradeClose);
    const visible = new Map(instances.map((inst) => [inst.instanceId, true]));
    const labels = new Map(instances.map((inst) => [inst.instanceId, inst.label]));
    const operations = chartKit.installChartOperations({
      container, renderer, doc, anchor: host,
      updatePaneHeight: geometry.updatePaneHeight,
      toast: new chartKit.ChartToastView({ document: doc, anchor: host }),
      getMenuContext: () => ({
        symbol: viewer.symbolSpec ? viewer.symbolSpec.symbol : overlay.dataset_ref,
        timeframe: overlay.timeframe,
        labels,
        priceDigits: viewer.symbolSpec ? viewer.symbolSpec.digits : null,
      }),
    });

    renderer.setCandles(candles);
    for (const inst of instances) {
      renderer.renderLine(inst.instanceId, inst.payloads, { pane: inst.pane });
    }
    // ペイン別凡例の行（ライブチャートの行と同じ形）。売買履歴チャートの行が持つ操作は表示/非表示だけ。
    const legendRows = () => instances.map((inst) => ({
      instanceId: inst.instanceId,
      label: inst.label,
      visible: visible.get(inst.instanceId),
      onEye: () => {
        visible.set(inst.instanceId, !visible.get(inst.instanceId));
        renderer.setVisible(inst.instanceId, visible.get(inst.instanceId));
        viewer.paneLegendView.setInstances(legendRows());
      },
    }));
    viewer.paneLegendView.setInstances(legendRows());
    viewer.currentPriceView.render(renderer.lastClose());
    // run の全期間を見せる（ライブチャートは最新の足から見せるが、ジョブの結果は期間全体が対象）。
    //   足 1 本の最小幅（既定 0.5px）が「描画幅 ÷ 本数」より大きいと全期間が入らず、指定が最後の
    //   描画幅 ÷ 0.5 本で止まる（実測 25,498 本中 3,027 本）。本数に合わせて最小幅を下げてから指定する。
    //
    //   初期表示の定義: **利用者が売買履歴チャートを操作するまでは、表示＝run の全期間**。
    //   描画幅は組み立て直後に 23ms で 1600→1540→1514px と確定していく（価格目盛りが付き、文字に
    //   合わせて広がる・実測）。最初の幅で 1 回だけ指定すると、その後の縮みで左端 430 本が押し出された。
    //   幅が確定していくたびに（幅の変更の通知）最小幅を決め直し、利用者の操作（押す・ホイール・キー）
    //   より前なら全期間を指定し直す。操作の後は見えている範囲に触れない（ビュー自動介入の禁止）。
    const timeScale = viewer.chart.timeScale();
    let userTookOver = false;
    const takeOver = () => { userTookOver = true; };
    const USER_EVENTS = ['pointerdown', 'wheel', 'keydown', 'touchstart'];
    for (const type of USER_EVENTS) container.addEventListener(type, takeOver, { capture: true, passive: true });
    const fitWholeRun = (plotWidth) => {
      if (!(plotWidth > 0)) return;
      viewer.chart.applyOptions({
        timeScale: { minBarSpacing: wholeRunMinBarSpacing(plotWidth, candles.length) },
      });
      if (userTookOver) return;
      renderer.focusTimeRange(candles[0].time, candles[candles.length - 1].time);
    };
    timeScale.subscribeSizeChange(fitWholeRun);
    fitWholeRun(timeScale.width());

    return {
      viewer,
      dispose() {
        timeScale.unsubscribeSizeChange(fitWholeRun);
        for (const type of USER_EVENTS) container.removeEventListener(type, takeOver, { capture: true });
        operations.dispose();
        geometry.dispose();
        viewer.chart.remove();
        // 読み取り欄・ペイン別凡例・メニュー・告知の器も host の中にある。器ごと空にする。
        while (host.firstChild) host.removeChild(host.firstChild);
      },
    };
  }

  return {
    /** 表示中のジョブ（診断・E2E 用）。 */
    shownJob() { return shownJob; },

    /** ジョブの結果を描く（同じジョブなら描き直さない）。@returns {Promise<boolean>} 描いたか */
    async render(jobId) {
      if (jobId === shownJob) return built !== null;
      clear();
      shownJob = jobId;
      let overlay;
      try {
        overlay = await fetchJson(`/sim/data/${encodeURIComponent(jobId)}/chart_overlay.json`);
      } catch (err) {
        // 読めなかったら覚えない＝次の load イベント（子文書は完了で読み直される）で再試行できる。
        //   覚えたままだと「同じジョブなら描き直さない」の早期 return が再試行を塞ぐ（ISSUE-540）。
        shownJob = null;
        showMessage(err && err.status === 409
          ? NOT_READY_MESSAGE
          : `売買履歴チャートの材料を読めません（${err && err.message ? err.message : err}）。`);
        return false;
      }
      let payload;
      try {
        payload = await loadReport(jobId);
      } catch (err) {
        shownJob = null;   // 次の load イベントで再試行できるように（上の 409 と同じ理由）
        showMessage(err && err.status === 409
          ? NOT_READY_MESSAGE
          : `ジョブの足と取引終了時の残高・DD（report.json）を読めないため表示しません（${err && err.message ? err.message : err}）。`);
        return false;
      }
      const segment = firstSegment(payload);
      const candles = segment && Array.isArray(segment.bars) ? segment.bars : [];
      if (candles.length === 0) {
        showMessage('ジョブの成果物（report.json）に足が無いため、売買履歴チャートには表示できません。');
        return false;
      }
      const mismatch = overlayLengthMismatch(overlay, candles.length);
      if (mismatch) {
        showMessage(`売買履歴チャートの値がジョブの足と一致しないため表示しません（${mismatch}）。`);
        return false;
      }
      const times = candles.map((bar) => bar.time);
      const tradeClose = tradeCloseCurves(segment, times, payload.meta && payload.meta.initial_deposit);
      built = build(overlay, candles, times, tradeClose);
      const { viewer } = built;
      const markers = new chartKit.TradeMarkersRenderer({
        lwc, mainSeries: viewer.mainSeries, chart: viewer.chart, chartRenderer: viewer.renderer,
        document: doc, container: host,
      });
      markers.setCurrentTimeframe(overlay.timeframe);
      await markers.load(`/sim/data/${encodeURIComponent(jobId)}/trade_markers.json`, fetchImpl);
      built.markers = markers;
      wireMarkers(markers);
      return true;
    },

    /**
     * 取引明細・priceChart と hover の状態を共有する（ISSUE-538）。子文書が作り直されると linkage も
     * 新しくなるので、渡されるたびに結び直す（同じ linkage なら何もしない）。
     * @param {{hoverTradeId: (number|null), setHover: function, subscribe: function}} next
     */
    bindLinkage(next) {
      if (!next || next === linkage) return;
      linkage = next;
      linkage.subscribe((id) => {
        if (built && built.markers) built.markers.highlightTrade(id);
      });
      if (built && built.markers) wireMarkers(built.markers);
    },

    /** 描いたものを片付ける（器へ何も残さない）。 */
    clear,
  };
}
