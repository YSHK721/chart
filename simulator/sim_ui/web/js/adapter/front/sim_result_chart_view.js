// sim_result_chart_view.js — ジョブ結果を売買履歴チャートへ描く View（2026-09-26 依頼者指示）。
//
// 何を描くか（「何をどのパネルへ」は usecase/result_chart_model.js が決め、ここは描くだけ）:
//   価格パネル … ジョブ自身の足（run が実行した Bar 列）・売買マーク・トリガー指標
//   別パネル  … 別窓指標・残高/有効証拠金・DD・損益（初期資金比）・証拠金維持率（いずれも足ごと）
//
// 組み立てはライブチャートと**同じ関数**で行う（`chartKit`＝live core の公開面 `live_chart_kit_api.js`
//   を統合層が注入する。sim は live core の URL を知らない）。2026-09-27 依頼者指示「チャートの
//   操作性もライブモードと同期しろ」: 読み取り欄・ペイン別凡例・価格軸ホイール・ダブルクリック復帰・
//   縦ドラッグ・最新足ボタン・右クリックのメニューは、ライブチャートの組み立て関数が付ける。
//   ここで部品を new し直すと、ライブチャートの修正が売買履歴チャートへ届かない第 2 実装になる。
//   本ファイルは lwc の API を直接呼ばない（系列の追加・表示範囲はすべて ChartRenderer 経由）。
//
// 持つのは表示する範囲だけ（ISSUE-552/554 段階 2-2）:
//   1 分足の全履歴 run は 215 万本あり、足と値を丸ごと持つとタブが落ちた（実測 2026-09-30）。
//   足・口座・指標はジョブの足の成果物から**位置の区間**で読む（`loadExtent`・`fetchRows`）。
//   - 初期表示: run の末尾から、ライブチャートが最初に読むのと同じ本数（`chartKit.RECENT_BARS`）。
//     描く口もライブチャートと同じ（ChartRenderer.setCandles＝全体を収めて末尾へ）。
//   - 読み足し: 利用者の操作の後に来た表示範囲の変化が止まったら、見えている端が持っている端へ
//     近づいた側の区間を 1 回読む。読んだ結果では表示範囲を動かさない（ChartRenderer の
//     表示を保つ差し替えの口を使う・ビュー自動介入の禁止）。
//   - 持つ総量には上限がある。超える分は、読む側と反対の端の、見えている範囲とその余白の外の足を
//     捨て、戻ったら読み直す。捨てられる足が無ければ読まない（読んでから捨てない・見えている足を
//     捨てない）。
//   どこを読み、どこを捨てるかは usecase/chart_window.js が一緒に決める（`planReads`）。
//
// report.json は足を持たない。取引終了時の残高・DD の材料・損益（初期資金比）の基準（初期資金）・
//   銘柄名のために**1 ジョブにつき 1 回だけ**取得する。
//
// 描かない条件（黙ってずらさない）。どれも理由を host に出して終わる:
//   - ジョブが未完了（409）・足の成果物が無い（404＝本変更より前に実行したジョブ）・足が 0 本
//   - 返った列の長さが行数と違う・返った区間が問うた区間と違う

import {
  balanceCurveTimes,
  candlesOf,
  drawnColumns,
  resultChartInstances,
  rowsMismatch,
  windowTradeClose,
} from '../../usecase/result_chart_model.js';
import {
  heldRowsCap,
  mergeColumns,
  planReads,
  readRowsOf,
  tailWindow,
} from '../../usecase/chart_window.js';
import { firstSegment } from './report_source_client.js';

/** 成果物が 409（ジョブ未完了）のときの掲示（足の成果物・report.json で共通・ISSUE-540）。 */
const NOT_READY_MESSAGE = "ジョブが完了していないため、売買履歴チャートはまだ表示できません。完了すると表示します。";

/** 足の成果物が無い（404）ときの掲示。足の成果物を書くようになる前に実行したジョブが該当する。 */
const NO_BARS_ARTEFACT_MESSAGE = "このジョブは変更前に実行されたため売買履歴チャートを表示できません。再実行すると表示できます。";

/**
 * 読み足しの「変化が止まった」とみなす待ち（ms）。ライブチャートの先例（日別プロファイルの
 * ローリング取得 `TFP_ROLL_DEBOUNCE_MS`＝150ms・indicator_ui の合成根）と同じ形・同じ長さ。
 * 変化のたびに読むと、ドラッグ中に読みが連発する。
 */
const READ_DEBOUNCE_MS = 150;

/**
 * 系列の `kind` → 描く ChartRenderer のメソッド。**配列の順が z 順**（先に描いたものが下）。
 * 損益（初期資金比）は棒の上に確定損益（累計）の線を重ねるため、棒を先に描く
 * （ライブチャートの描画経路の台帳 RENDER_ROUTES も histogram → line の順）。
 */
const RENDER_METHODS = Object.freeze([
  Object.freeze({ kind: 'histogram', method: 'renderHistogram' }),
  Object.freeze({ kind: 'line', method: 'renderLine' }),
]);

/** instance の系列を kind ごとに ChartRenderer へ渡す（同じ instance・同じペイン）。 */
function renderInstance(renderer, inst) {
  for (const { kind, method } of RENDER_METHODS) {
    const payloads = inst.payloads.filter((p) => p.kind === kind);
    if (payloads.length > 0) renderer[method](inst.instanceId, payloads, { pane: inst.pane });
  }
}

/** report.json の meta.initial_deposit（有限の数でなければ null）。損益（初期資金比）の基準。 */
function initialDepositOf(payload) {
  const deposit = payload && payload.meta ? payload.meta.initial_deposit : null;
  return typeof deposit === 'number' && Number.isFinite(deposit) ? deposit : null;
}

/**
 * ChartRenderer が持つローソク足の陽線・陰線の色（初期資金基準の棒の塗り分けに借りる）。
 * 購読口は登録直後に今の色を 1 回配る。その 1 回だけ受け取り、購読は残さない。
 */
function candleColorsOf(renderer) {
  let colors = null;
  const unsubscribe = renderer.addChromeObserver((slots) => {
    colors = { upColor: slots.candleUp, downColor: slots.candleDown };
  });
  unsubscribe();
  return colors;
}

/** 利用者の操作とみなす事象（これの後に来た表示範囲の変化だけが読み足しの起点になる）。 */
const USER_EVENTS = ['pointerdown', 'wheel', 'keydown', 'touchstart'];

/** report.json の meta.symbol（空・非文字列は無い扱い＝null）。 */
function reportSymbolOf(payload) {
  const symbol = payload && payload.meta ? payload.meta.symbol : null;
  return typeof symbol === 'string' && symbol !== '' ? symbol : null;
}

function reasonOf(err) {
  return err && err.message ? err.message : err;
}

/** チャートを生成する要素（器 host の中に置く。読み取り欄・凡例は host 直下に並ぶ）。 */
const CANVAS_CLASS = 'sim-result-chart-canvas';

/**
 * @param {object}   deps
 * @param {Document} deps.doc
 * @param {Element}  deps.host          描画先（売買履歴チャートの器）
 * @param {object}   deps.lwc           lightweight-charts v5
 * @param {object}   deps.chartKit      live core の公開面（composeChartViewer・installPaneGeometry・
 *                                      installChartOperations・ChartToastView・TradeMarkersRenderer・
 *                                      RECENT_BARS＝ライブチャートが最初に読む足の本数）
 * @param {function} deps.loadExtent    (jobId) => Promise<object>（足の成果物の宣言。1 ジョブにつき 1 回呼ぶ）
 * @param {function} deps.fetchRows     (jobId, start, end) => Promise<object>（位置の半開区間の列）
 * @param {function} deps.loadReport    (jobId) => Promise<object>（report.json の取得。1 ジョブにつき 1 回呼ぶ）
 * @param {function} deps.tradeCloseCurves (segment, barTimes, payloadDeposit) => {balData, ddData}
 *                                      （取引終了時の残高・DD。シミュレーション結果の資産曲線 balChart・
 *                                      ドローダウン ddChart と同じ系列を作る report_ui の単一ソース）
 * @param {function} [deps.fetchImpl]   売買マークの読み込みに使う fetch
 * @param {function} [deps.setTimer]    (fn, ms) => handle（読み足しの待ち。既定 setTimeout）
 * @param {function} [deps.clearTimer]  (handle) => void（既定 clearTimeout）
 */
export function createSimResultChartView({
  doc, host, lwc, chartKit, loadExtent, fetchRows, loadReport, tradeCloseCurves, fetchImpl,
  setTimer = (fn, ms) => setTimeout(fn, ms),
  clearTimer = (handle) => clearTimeout(handle),
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

  /** 読めなかった・描けなかった理由を出し、覚えない（次の load イベントで再試行できる・ISSUE-540）。 */
  function refuse(text) {
    shownJob = null;
    showMessage(text);
    return false;
  }

  /**
   * ライブチャートと同じ関数でチャートと操作性を組み、最初の区間を描く。
   * 以後の読み足し・片付けもここが持つ（持っている区間はこの関数の中だけに在る）。
   *
   * @param {string} jobId
   * @param {object} declared     足の成果物の宣言（extent の応答）
   * @param {object} payload      report.json
   * @param {number} readRows     1 回の本数
   * @param {{start: number, end: number}} first 最初に持つ区間
   * @param {object} firstColumns 最初の区間の列（rows の応答の columns）
   */
  function build({ jobId, declared, payload, readRows, first, firstColumns }) {
    const container = doc.createElement('div');
    container.className = CANVAS_CLASS;
    host.appendChild(container);
    const viewer = chartKit.composeChartViewer({
      lwc, container, doc, anchor: host, datasetRef: declared.dataset_ref,
    });
    const { renderer } = viewer;
    const geometry = chartKit.installPaneGeometry({ container, chart: viewer.chart, renderer });
    const toast = new chartKit.ChartToastView({ document: doc, anchor: host });

    const totalRows = declared.rows;
    const cap = heldRowsCap({ readRows, maxReturnedRows: declared.max_returned_rows });
    const names = drawnColumns(declared);
    const segment = firstSegment(payload);
    const deposit = initialDepositOf(payload);
    // 初期資金基準の棒（残高・損益（初期資金比））の基準と塗り分けの色（ローソク足と同じ色）。
    //   1 ジョブにつき 1 回だけ決める。
    const baseline = { deposit, ...candleColorsOf(renderer) };
    // balance_curve の時刻は 1 ジョブにつき 1 回だけ並べる（区間ごとに並べ直さない）。
    const curveTimes = balanceCurveTimes(segment);
    const reportSymbol = reportSymbolOf(payload);

    // 持っている区間と、その列（描画に使う列だけ。成果物には描かない列も在る）。
    let held = first;
    let columns = Object.fromEntries(names.map((name) => [name, firstColumns[name]]));
    let alive = true;

    /** 持っている区間から、描画へ渡す足と instance を作る。 */
    const drawing = () => {
      const tradeClose = windowTradeClose({
        tradeCloseCurves, segment, deposit, curveTimes, times: columns.time,
      });
      return {
        candles: candlesOf(columns), instances: resultChartInstances(declared, columns, tradeClose, baseline),
      };
    };

    // ---- 読み足し・「最新足」の読み（発行は 1 本ずつ・順に） ----
    let queue = Promise.resolve();
    const enqueue = (work) => {
      queue = queue.then(() => (alive ? work() : undefined)).catch((err) => {
        if (!alive) return;
        // 持っている足はそのまま（差し替えていない）。理由は告知する（黙って諦めない）。
        toast.show(`売買履歴チャートの足を読めません（${reasonOf(err)}）。`);
        console.warn('[sim-result-chart] 足の読みに失敗しました', err);
      });
    };

    /** 位置の区間を読む。返った列が問うた区間の列でなければ例外（別の足の上へ置かない）。 */
    async function read(window) {
      const answer = await fetchRows(jobId, window.start, window.end);
      const mismatch = rowsMismatch(declared, answer, window);
      if (mismatch) throw new Error(`返った列が足と一致しない: ${mismatch}`);
      return answer.columns;
    }

    /** 見えている端が持っている端へ近づいた側を読み足す（前と後それぞれ高々 1 回）。 */
    async function readMore() {
      let sides = ['before', 'after'];
      while (sides.length > 0) {
        // 1 回読むたびに、その時点の持っている区間と見えている範囲で決め直す。
        //   読む区間と、つないだ後に持つ区間（`plan.next`）は planReads が一緒に決める。
        //   つないだ後に残らない区間は発行されない（読んでから捨てない）。
        const [plan] = planReads({
          held, totalRows, readRows, cap, visible: renderer.visibleLogicalRange(), sides,
        });
        if (!plan) return;
        sides = sides.filter((side) => side !== plan.side);
        const readColumns = await read(plan);
        if (!alive) return;
        columns = mergeColumns({ names, held, heldColumns: columns, read: plan, readColumns, next: plan.next });
        held = plan.next;
        const { candles, instances } = drawing();
        // 表示を保つ差し替え。読んだ結果で表示範囲を動かさない。
        renderer.replaceDataKeepingView(candles, instances);
      }
    }

    /** run の末尾へ移動する（「最新足」ボタン）。末尾を持っていなければ読んでから移動する。 */
    async function showRunTail(options) {
      if (held.end < totalRows) {
        const tail = tailWindow({ totalRows, readRows });
        const tailColumns = await read(tail);
        if (!alive) return;
        held = tail;
        columns = Object.fromEntries(names.map((name) => [name, tailColumns[name]]));
        const { candles, instances } = drawing();
        renderer.replaceDataKeepingView(candles, instances);
      }
      renderer.scrollToRealTime(options);
    }

    // 操作性へ渡す描画の口。ChartRenderer のメソッドをそのまま通し、「最新足」の 2 つだけを
    //   run の末尾の意味にする（ChartRenderer が知る「末尾」は持っている区間の末尾であって、
    //   run の末尾を捨てた後は run の末尾ではない）。
    const operated = new Proxy(renderer, {
      get(target, property) {
        if (property === 'isLatestBarVisible') {
          return () => held.end >= totalRows && target.isLatestBarVisible();
        }
        if (property === 'scrollToRealTime') {
          return (options) => enqueue(() => showRunTail(options));
        }
        const value = target[property];
        return typeof value === 'function' ? value.bind(target) : value;
      },
    });

    // ---- 最初の区間を描く（ライブチャートと同じ口: 全体を収めて末尾へ） ----
    const initial = drawing();
    const instances = initial.instances;
    const visible = new Map(instances.map((inst) => [inst.instanceId, true]));
    const labels = new Map(instances.map((inst) => [inst.instanceId, inst.label]));
    const operations = chartKit.installChartOperations({
      container, renderer: operated, doc, anchor: host,
      updatePaneHeight: geometry.updatePaneHeight,
      toast,
      getMenuContext: () => ({
        // 台帳の銘柄仕様を優先し、引けなければジョブ自身の report.json の銘柄名（台帳外の系列でも在る）。
        symbol: viewer.symbolSpec ? viewer.symbolSpec.symbol : reportSymbol,
        timeframe: declared.timeframe,
        labels,
        priceDigits: viewer.symbolSpec ? viewer.symbolSpec.digits : null,
      }),
    });

    renderer.setCandles(initial.candles);
    for (const inst of instances) renderInstance(renderer, inst);
    // ペイン別凡例の行（ライブチャートの行と同じ形）。売買履歴チャートの行が持つ操作は表示/非表示だけ。
    //   表示/非表示は ChartRenderer が instance ごとに持つ。読み足しは同じ instance の系列へ
    //   入れるだけなので、目のボタンの状態は読み足しの後も残る。
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
    // 現在値の表示は run の最後の足の終値（最初に持つのは run の末尾）。読み足しでは書き換えない。
    viewer.currentPriceView.render(renderer.lastClose());

    // ---- 読み足しの起点: 利用者の操作の後に来た表示範囲の変化だけ ----
    //   表示範囲の変化は利用者の操作以外でも来る（幅の確定・差し替えの直後と次の描画・実測
    //   2026-09-30）。それで読むと、読んだ結果がまた読みを呼ぶ。操作 1 回が許す読みは 1 回
    //   （変化が止まったときに 1 度だけ確かめる）。
    let licensed = false;
    let timer = null;
    const onUserEvent = () => { licensed = true; };
    for (const type of USER_EVENTS) {
      container.addEventListener(type, onUserEvent, { capture: true, passive: true });
    }
    const unsubscribeRange = renderer.subscribeVisibleLogicalRange(() => {
      if (!licensed) return;
      if (timer !== null) clearTimer(timer);
      timer = setTimer(() => {
        timer = null;
        licensed = false;
        enqueue(readMore);
      }, READ_DEBOUNCE_MS);
    });

    return {
      viewer,
      dispose() {
        alive = false;
        if (timer !== null) { clearTimer(timer); timer = null; }
        unsubscribeRange();
        for (const type of USER_EVENTS) container.removeEventListener(type, onUserEvent, { capture: true });
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
      // 読めなかったら覚えない＝次の load イベント（子文書は完了で読み直される）で再試行できる。
      //   覚えたままだと「同じジョブなら描き直さない」の早期 return が再試行を塞ぐ（ISSUE-540）。
      //   待っている間に別のジョブへ替わった・片付けられたら、届いた結果（失敗も）を使わない
      //   （`stale`。今のジョブの状態と掲示に触れない）。
      const stale = () => shownJob !== jobId;
      let declared;
      try {
        declared = await loadExtent(jobId);
      } catch (err) {
        if (stale()) return false;
        if (err && err.status === 409) return refuse(NOT_READY_MESSAGE);
        if (err && err.status === 404) return refuse(NO_BARS_ARTEFACT_MESSAGE);
        return refuse(`売買履歴チャートの材料を読めません（${reasonOf(err)}）。`);
      }
      let payload;
      try {
        payload = await loadReport(jobId);
      } catch (err) {
        if (stale()) return false;
        return refuse(err && err.status === 409
          ? NOT_READY_MESSAGE
          : `取引終了時の残高・DD（report.json）を読めないため表示しません（${reasonOf(err)}）。`);
      }
      if (stale()) return false;
      if (!(declared.rows > 0)) {
        showMessage('ジョブの足の成果物に足が無いため、売買履歴チャートには表示できません。');
        return false;
      }
      const recentBars = chartKit.RECENT_BARS;
      if (!(Number.isInteger(recentBars) && recentBars > 0)) {
        showMessage('ライブチャートの公開面が最初に読む足の本数を名乗っていないため、売買履歴チャートを表示できません。');
        return false;
      }
      if (!(Number.isInteger(declared.max_returned_rows) && declared.max_returned_rows > 0)) {
        showMessage('足の成果物の宣言が 1 回に返す上限を名乗っていないため、売買履歴チャートを表示できません。');
        return false;
      }
      if (initialDepositOf(payload) === null) {
        showMessage('report.json が初期資金（meta.initial_deposit）を名乗っていないため、損益（初期資金比）の基準が無く、売買履歴チャートを表示できません。');
        return false;
      }
      const readRows = readRowsOf({ recentBars, maxReturnedRows: declared.max_returned_rows });
      const first = tailWindow({ totalRows: declared.rows, readRows });
      let answer;
      try {
        answer = await fetchRows(jobId, first.start, first.end);
      } catch (err) {
        if (stale()) return false;
        if (err && err.status === 409) return refuse(NOT_READY_MESSAGE);
        return refuse(`売買履歴チャートの足を読めません（${reasonOf(err)}）。`);
      }
      if (stale()) return false;
      const mismatch = rowsMismatch(declared, answer, first);
      if (mismatch) {
        showMessage(`売買履歴チャートの値がジョブの足と一致しないため表示しません（${mismatch}）。`);
        return false;
      }
      const mine = build({ jobId, declared, payload, readRows, first, firstColumns: answer.columns });
      built = mine;
      const { viewer } = mine;
      const markers = new chartKit.TradeMarkersRenderer({
        lwc, mainSeries: viewer.mainSeries, chart: viewer.chart, chartRenderer: viewer.renderer,
        document: doc, container: host,
      });
      markers.setCurrentTimeframe(declared.timeframe);
      await markers.load(`/sim/data/${encodeURIComponent(jobId)}/trade_markers.json`, fetchImpl);
      // 売買マークを待つ間に片付けられていたら、別のジョブのチャートへ結ばない。
      if (built !== mine) return false;
      mine.markers = markers;
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
