// sim 表示層の合成根（F-1）。**2 つの入口**を持つ。
//
//   統合ページ側: setupSimDisplay({doc, host, jobId})  → 器（#sim-display）と iframe を出す
//   子文書側:     mountSimReportView({doc, host}) → 取引明細・周辺表示を組み立てる
//
// なぜ 2 段か（実 UI 実測 2026-08-11 → 裁定 B）: 移植元 style.css は body そのものを
//   全画面レイアウトへ作り替えるため、統合ページへ link すると既存 UI の見た目が変わる
//   （実測: body 背景・font・文字色）。CSS を書き換えるのは「見た目の複製」なので採らない。
//   同じ CSS をそのまま使い波及だけを止める構造 ＝ 別文書（iframe）。
//   親は器だけを持ち、表示の中身は子文書 `/sim/report_view.html` が持つ。
//
// 依存の向き（DIP / ISP）:
//   - 移植元 report_ui の実体は **/sim/report-js/ から import** する。sim 側に写しは 1 つも
//     置かない（import_source.test.js が機械強制する）。周辺表示（ヒートマップ・比較判定・
//     用語集）も同じく import し、各 View へ**注入**する。
//   - 子文書は lwc に触らない（3 窓チャートは 2026-09-27 依頼者指示で撤去。ジョブの
//     チャート表示は親ページの売買履歴チャートだけが持つ）。
//   - linkage は**ここで生成して注入**する（chart→linkage の直接 import を作らない・移植元規約）。
//
// 規定:
//   - job_id は `?job=<id>` からのみ得る。一覧から自動で選ばない（ビュー自動介入の禁止）。
//   - 取れない・未生成なら**何も描かず**理由を掲示する（部分描画しない・fail-stop）。
//
// 結線順は移植元 main.js:135-190 と同順（tabs は View が持つ → subscribeFilter → compare →
//   glossary+wireTips〔init 1 回・多重 #tip 禁止〕→ segment → selectSegment）。

// tradeCloseCurves は親の売買履歴チャートの残高・DD 系列を作るためだけに使う。
//   子文書側の 3 窓チャートは撤去済み（2026-09-27 依頼者指示）＝チャート描画の表示規則
//   （マーカー・減光・バッジ・接点）はここでは読まない。
import { tradeCloseCurves } from "/sim/report-js/chart.js";
import { fmtMoney } from "/sim/report-js/format.js";
import { createLinkage } from "/sim/report-js/linkage.js";
import { buildTradeTable } from "/sim/report-js/table.js";
import { buildHeatmap } from "/sim/report-js/heatmap.js";
import { buildCompare, renderVerdictBanner } from "/sim/report-js/compare.js";
import { buildGlossary, wireTips } from "/sim/report-js/glossary.js";
import { buildReport } from "/sim/report-js/report.js";
import { buildGraphs } from "/sim/report-js/graphs.js";
import { nextLayoutMode } from "/sim/report-js/layout.js";

import { createReportSourceClient, firstSegment, readJobId } from "./report_source_client.js";
import { createChartBarsClient } from "./chart_bars_client.js";
import { createSimDisplayView } from "./sim_display_view.js";
import { createSimFrameView, waitForContent, whenChildReady } from "./sim_frame_view.js";
import { createJobStatusClient } from "./job_status_client.js";
import { showRunningAndWaitForCompletion } from "./job_completion_wait.js";
import { createSimProgressView } from "./sim_progress_view.js";
import { createSimResultChartView } from "./sim_result_chart_view.js";
import { createSimSegmentView } from "./sim_segment_view.js";
import { createSimCompareView } from "./sim_compare_view.js";
import { createSimFilterPillView } from "./sim_filter_pill_view.js";
// 段階 4（§9.1/§9.2）: 分析タブの結線は別合成根が持つ（node で測れる場所へ置く・
//   `composition_root_execution.js` と同じ理由）。ここは呼ぶだけである。
import { mountTraceAnalysis } from "./composition_root_analysis.js";

/**
 * 子文書（シミュレーション結果）が hover の状態（linkage）を出す window の名前。子が出し、親の
 * 売買履歴チャートが読む（ISSUE-538）。書き手と読み手がこの 1 つの定数を使う。
 */
export const SIM_LINKAGE_GLOBAL = "__simLinkage";

/**
 * 親（統合ページ）が子文書へ渡す「版面の最大化」の口の window 名と、渡したことを知らせる
 * イベント名（2026-09-27・参照 report_ui の ⛶ 明細最大化）。状態遷移は親が 1 つだけ持つ
 * （チャート最大化ボタンは親の版面に在るため）。子のボタンはこの口を呼ぶだけ。
 */
export const SIM_HOST_LAYOUT_GLOBAL = "__simHostLayout";
export const SIM_HOST_LAYOUT_READY = "sim-host-layout-ready";

/** `?job=<id>` を読む（引数優先・注入可能にしてテストと実行を分けない）。 */
function resolveJobId({ jobId, search }) {
  return jobId || readJobId(
    search !== undefined ? search : (typeof location !== "undefined" ? location.search : ""),
  );
}

/**
 * 統合ページ側の入口。器（#sim-display）と子文書の iframe だけを出す。
 *
 * 表示の中身は子文書が持つので、ここは lwc も report.json も触らない
 * （統合ページへ持ち込むのは器の寸法 CSS 1 枚だけ＝style.css 波及の遮断）。
 *
 * @param {Document} doc    統合ページの DOM（統合層が渡す）
 * @param {Element}  host   器を挿す先
 * @param {string}   jobId  表示対象ジョブ。未指定なら `search` から読む
 * @param {string}   search `location.search` 相当
 * @returns {{enable: function, disable: function}}
 */
export async function setupSimDisplay({
  doc, host, jobId, search, onContentHeight, raf,
  lwc, resultChart = null, chartKit = null,
} = {}) {
  const frame = createSimFrameView({ doc });
  // ジョブ結果を売買履歴チャートへ描く（2026-09-26 依頼者指示）。売買履歴チャートの器（`resultChart`）・部品
  //   （`chartKit`）は統合層が注入する。どれかが無い宿主（スタンドアロン等）では描かない＝従来どおり
  //   下の結果ビューアだけ。足・口座・指標はジョブ自身の足の成果物から、表示する範囲だけを
  //   位置の区間で読む（ISSUE-552/554 段階 2-2・ライブの `/candles` は読まない）。
  const reportSource = createReportSourceClient({});
  const chartBars = createChartBarsClient({});
  const resultView = resultChart && chartKit && lwc
    ? createSimResultChartView({
      doc,
      host: resultChart.host(),
      lwc,
      chartKit,
      // 足の成果物の宣言と、位置の区間の列。失敗は状態つき（409＝未完了・404＝足の成果物が無い）。
      loadExtent: (jobId) => chartBars.extent(jobId),
      fetchRows: (jobId, start, end) => chartBars.rows(jobId, start, end),
      // report.json（シミュレーション結果と同じ成果物・足は持たない）。取引終了時の残高・DD の材料と
      //   銘柄名を 1 回の取得から作る。取引終了時の残高・DD は同じ関数（tradeCloseCurves）で作る。
      loadReport: (jobId) => reportSource.load(jobId),
      tradeCloseCurves,
    })
    : null;
  // 版面の最大化（参照 report_ui layout.js の 3 状態・2026-09-27）。状態遷移は参照の純関数
  //   nextLayoutMode（写さない）、表すのは統合層の器（body の状態クラス）。
  let layoutMode = "normal";
  const toggleLayout = (button) => {
    layoutMode = nextLayoutMode(layoutMode, button);
    resultChart.setLayoutMode(layoutMode);
    return layoutMode;
  };
  const resetLayout = () => { layoutMode = "normal"; };
  if (resultView) resultChart.onMaxChart(() => toggleLayout("chart"));
  // 子文書が読み込まれるたびに、それが結果ビューア（`?job=<id>`）かを見て売買履歴チャートを合わせる。
  //   子は投入の完了で自分を `?job=<id>` へ移す（利用者の実行指示が起点＝自動介入ではない）。
  const onFrameLoad = () => {
    if (!resultView) return;
    const win = frame.childWindow();
    const job = win ? readJobId(win.location.search) : null;
    if (!job) {
      resultView.clear();
      resultChart.hide();
      resetLayout();
      return;
    }
    resultChart.show();
    resultView.render(job).catch((err) => {
      console.warn("[sim-result-chart] 描画に失敗しました", err);
    });
    // 取引明細・priceChart と hover を連動させる（ISSUE-538）。子の linkage は子が組み上がってから在る。
    whenChildReady(frame, nextFrame, (child) => {
      resultView.bindLinkage(child[SIM_LINKAGE_GLOBAL]);
      // 版面の最大化の口を子へ渡し、渡したことを知らせる（子はそれまで ⛶ 明細最大化を出さない）。
      child[SIM_HOST_LAYOUT_GLOBAL] = { toggleDetail: () => toggleLayout("detail"), mode: () => layoutMode };
      if (typeof child.dispatchEvent === "function" && typeof child.Event === "function") {
        child.dispatchEvent(new child.Event(SIM_HOST_LAYOUT_READY));
      }
    });
  };
  const targetJobId = resolveJobId({ jobId, search });
  // 器は**渡された host へそのまま**挿す。どこへ置くかは統合層の判断であって sim の契約では
  //   ない（旧実装は host の中から `#app` を探していた＝統合ページの id を sim 側が知っていた）。
  //   統合ページは下部ペイン（#um-bottom-pane）を渡す（裁定 2026-08-21・MT5 と同じ版面分割）。
  const mountPoint = host;
  // 次フレームの予約（注入可能）。既定はブラウザの requestAnimationFrame、無い環境では null＝
  //   高さの通知そのものを行わない（時計を勝手に作らない）。
  const nextFrame = typeof raf === "function"
    ? raf
    : (typeof requestAnimationFrame === "function" ? (fn) => requestAnimationFrame(fn) : null);
  let enabled = false;

  return {
    /** sim モードへ入るときに呼ばれる。器と子文書を出す。 */
    async enable() {
      if (enabled) return;
      enabled = true;
      frame.mount(mountPoint, targetJobId);
      frame.frameElement().addEventListener("load", onFrameLoad);
      // 中身が必要とする高さを**宿主へ伝える**（ISSUE-442）。どう使うか（ペインの既定高さに
      //   するか）は宿主の判断で、sim は測って渡すだけ（DIP）。購読者が居なければ何もしない。
      //
      //   **いつ測るか**が要点である。`load` の時点ではまだ足りない——子文書の面は module script が
      //   組み立てるので、load 直後の高さは組み立て途中の値になる（実測 2026-08-22: 高さ 109px
      //   ＝下限 120 に丸められ、ペインが 123px で開いた）。子は組み立ての完了を
      //   `window.__simReportViewReady` で表明するので、それを待ってから測る。
      if (typeof onContentHeight === "function") {
        waitForContent(frame, nextFrame, (h) => onContentHeight(h));
      }
    },

    /** sim モードから出るときに呼ばれる。器ごと畳む（統合ページへ何も残さない）。 */
    async disable() {
      if (!enabled) return;
      enabled = false;
      if (resultView) {
        resultView.clear();
        resultChart.hide();
        resetLayout();
      }
      frame.unmount();
    },

    /** 子文書の window（同一オリジン直参照・E2E の観測点）。 */
    childWindow() { return frame.childWindow(); },

    /** 現在のジョブ（診断・E2E 用）。 */
    jobId() { return targetJobId; },

    /** 売買履歴チャートに描いているジョブ（診断・E2E 用）。描いていなければ null。 */
    resultChartJob() { return resultView ? resultView.shownJob() : null; },
  };
}

/**
 * 子文書の「⛶ 明細最大化」を親の版面の口へ結ぶ（2026-09-27・参照 report_ui layout.js の点10）。
 * 口（window[SIM_HOST_LAYOUT_GLOBAL]）は親が子の完了後に渡し、渡したことをイベントで知らせる。
 * 表示の追随（ボタンの文言・グラフの充填 gfill）は返った版面の状態だけで決める。
 */
export function wireDetailMaximize({ win, view }) {
  const tabsView = view && typeof view.tabsView === "function" ? view.tabsView() : null;
  const button = tabsView && typeof tabsView.maxDetailButton === "function" ? tabsView.maxDetailButton() : null;
  if (!win || !button) return;
  const reflect = (mode) => {
    button.textContent = mode === "detail" ? "↙ 復元" : "⛶ 明細最大化";
    button.classList.toggle("on", mode === "detail");
    const graphHost = view.elements && view.elements.graphHost;
    if (graphHost) graphHost.classList.toggle("gfill", mode === "detail");
  };
  const attach = () => {
    const api = win[SIM_HOST_LAYOUT_GLOBAL];
    if (!api) return;
    button.hidden = false;
    reflect(typeof api.mode === "function" ? api.mode() : "normal");
  };
  button.addEventListener("click", () => {
    const api = win[SIM_HOST_LAYOUT_GLOBAL];
    if (api) reflect(api.toggleDetail());
  });
  if (typeof win.addEventListener === "function") win.addEventListener(SIM_HOST_LAYOUT_READY, attach);
  attach();   // 口が先に渡っていた場合
}

/**
 * 子文書側の入口。取引明細・周辺表示（区間トグル・ヒートマップ・比較判定・用語集）を
 * 組み立てる（report_view.html から呼ばれる）。
 *
 * 3 窓チャートは撤去済み（2026-09-27 依頼者指示）: ジョブの足・売買マーク・残高/DD は
 * 親ページの売買履歴チャート（sim_result_chart_view）が描く。hover の状態（linkage）は
 * 従来どおり子が出し、親のチャートと取引明細を結ぶ（ISSUE-538）。
 *
 * @param {Document} doc      子文書の DOM
 * @param {Element}  host     器を挿す先（子文書の body）
 * @param {string}   jobId    表示対象ジョブ。未指定なら `search` から読む
 * @param {string}   search   `location.search` 相当
 * @returns {{destroy: function}}
 */
export async function mountSimReportView({
  doc, host, jobId, search, fetch: fetchFn, statusClient = null,
} = {}) {
  const view = createSimDisplayView({ doc });
  const source = createReportSourceClient({ fetch: fetchFn });
  const linkage = createLinkage();
  const targetJobId = resolveJobId({ jobId, search });

  // 周辺 View（DOM は sim_display_view が所有し、これらは結線と描き分けだけを担う）。
  const compareView = createSimCompareView({ doc, buildCompare, renderVerdictBanner });
  const segmentView = createSimSegmentView({ doc });
  const filterPill = createSimFilterPillView();

  // 実行中のジョブを開いたときの完了待ち（ISSUE-540）。監視は同時 1 本・破棄で必ず止める。
  let stopJobWatch = null;
  let payload = null;
  let segKeys = [];
  let curSeg = null;

  // hover 購読は**この 1 回だけ**登録する（区間切替で積み上げない・移植元 table.js:81-100 の
  //   理由と同じ）。チャート側の反映は親の売買履歴チャートが同じ linkage を購読して行う。
  linkage.subscribe((id) => {
    updateSelectionLabel(id);
  });

  // 点18: 抽出フィルタ購読（ピル表示＋件数＋ table 連動）。init で 1 回だけ登録する
  //   （移植元 main.js:151-159）。購読者側で DOM 副作用を持つ（linkage は純状態機械）。
  linkage.subscribeFilter((filter, label) => {
    if (curSeg) buildTradeTable(view.elements.table, curSeg, linkage, onRowFocus); // dim を反映
    filterPill.reflect(filter, label);
  });

  // 点16: 連動選択ラベル（hover 中の trade を 1 行で示す）。行は表示中区間の trades から引く
  //   （撤去前は renderer.currentRows() だったが、その実体も segment.trades そのもの）。
  function updateSelectionLabel(id) {
    const label = view.elements.hSel;
    if (!label) return;
    const rows = (curSeg && curSeg.trades) || [];
    const trade = id == null ? null : rows.find((r) => r.id === id);
    if (!trade) {
      label.textContent = "";
      return;
    }
    label.innerHTML =
      `▶ #${trade.id} ${String(trade.side).toUpperCase()} @${trade.entry_price} → ${trade.exit_price} ` +
      `<b style="color:${trade.profit > 0 ? "#26a69a" : "#ef5350"}">${fmtMoney(trade.profit)} JPY</b>` +
      ` · MFE ${trade.mfe} / MAE ${trade.mae}`;
  }

  // 行クリック → 選択確定（強調は linkage 購読者が反映。親の売買履歴チャートも同じ id を受ける）。
  function onRowFocus(id) { linkage.setHover(id, "table"); }

  // 区間のメタ 1 行（移植元 renderMeta）。segment.meta を素直に読む。
  function renderMeta(segment) {
    const line = view.elements.metaLine;
    const m = (segment && segment.meta) || {};
    if (line) {
      line.textContent =
        `${m.symbol || ""} ${m.timeframe || ""} / ${m.strategy || ""} / bars=${m.bars || 0} / ` +
        `trades=${m.trades || 0} / ${m.period || ""}`;
    }
  }

  // 区間切替（移植元 main.js:65-76 と同順・チャート描画を除く）。単一 run では 1 度だけ呼ばれる。
  function selectSegment(segKey) {
    curSeg = payload.segments[segKey];
    linkage.applyFilter(null, "");        // 区間切替でフィルタ解除
    segmentView.setCurrent(segKey);       // segbtn の .on（縮退時は no-op）
    renderMeta(curSeg);
    buildTradeTable(view.elements.table, curSeg, linkage, onRowFocus);
    buildHeatmap(
      view.elements.heatHost, payload, segKey, linkage,
      // セルクリックの時刻ズーム先（3 窓）は撤去済み＝ズームなし。抽出連動は linkage が担う。
      () => {},
      // 単一区間（segKeys<2）では「IS vs OOS 損益差」ビューを出さない（D-3）。
      { showIsOosDiff: segKeys.length >= 2 },
    );
    // グラフ (Graphs)。区間が 1 つ（sim の単一 run）なら系列も 1 本（IS/OOS 並置の既定では
    //   区間 is/oos を読むため全グラフが 0 になる）。要素クリックの抽出は linkage が担う。
    buildGraphs(
      view.elements.graphHost, payload, segKey, linkage, () => {},
      segKeys.length >= 2 ? {} : { series: [{ seg: segKey, label: "この run" }] },
    );
    // サマリー (Report)。区間別 report を章立て表示（移植元 main.js selectSegment と同順・末尾）。
    buildReport(view.elements.reportGrid, curSeg.report);
  }

  view.mount(host);
  try {
    payload = await source.load(targetJobId);
    const first = firstSegment(payload);
    if (!first) {
      view.showMessage("結果未生成（表示できる区間がありません）");
      return { destroy, jobId() { return targetJobId; } };
    }
    view.clearMessage();
    segKeys = Object.keys(payload.segments);

    // 比較・判定は区間非依存（init で 1 回）。segKeys>=2 は buildCompare、単一は判定バナーのみ。
    compareView.render({ host: view.elements.paneCompare, segKeys, payload });
    // 分析タブ（段階 4・§9.1）は区間非依存（init で 1 回）。**await しない**——分析 API の
    //   往復で既存タブの初期表示が待たされる理由が無く、失敗しても掲示に留まる
    //   （`mountTraceAnalysis` は throw しない）。
    void mountTraceAnalysis({
      doc, pane: view.elements.paneAnalysis, jobId: targetJobId, fetch: fetchFn,
    });
    // 用語集＋hover tip（init で 1 回・多重 #tip 禁止）。
    buildGlossary(view.elements.glossHost);
    wireTips();
    // 区間トグル（segKeys<2 なら生成しない＝縮退の唯一の所有者）。
    segmentView.render({
      host: view.elements.segHost, segKeys, current: segKeys[0], onSelect: selectSegment,
    });
    // 抽出ピルの解除結線（✕ クリック→ applyFilter 解除）。
    filterPill.wire({
      clearFilter: view.elements.clearFilter, detailCount: view.elements.detailCount, linkage,
    });
    // 初期表示は先頭区間（承認 G）。
    selectSegment(segKeys[0]);
    // 初期タブは明細（移植元と同じ・タブ切替は tabs View が単一経路で持つ）。
    view.activate("detail");

    // ⛶ 明細最大化（参照 report_ui #maxDetail）。版面の切り替えは親が持つ口を呼ぶ。明細最大化中は
    //   グラフを 1 画面に収める（参照の gfill）。口が渡るまではボタンを出さない。
    wireDetailMaximize({ win: typeof window !== "undefined" ? window : null, view });

    // hover の状態（linkage）を親文書へ出す。親の売買履歴チャートがこれで取引明細と
    //   連動する（ISSUE-538）。E2E の実測点も兼ねる（移植元 main.js:182 と対称）。
    if (typeof window !== "undefined") {
      window[SIM_LINKAGE_GLOBAL] = linkage;
    }
  } catch (e) {
    if (e && e.code === "not_ready") {
      // 実行中のジョブを開いた（投入直後に「結果を見る」を押すとここへ来る・ISSUE-540 実測 409）。
      //   完了待ちの中身（掲示・監視・読み直し）は job_completion_wait が持ち、ここは結線だけ。
      // 進み具合（バーと％）は掲示の直後に置く（部品は sim_progress_view・2026-09-27）。
      const progress = createSimProgressView({ doc });
      if (view.elements.root) progress.mount(view.elements.root);
      stopJobWatch = showRunningAndWaitForCompletion({
        view,
        progress,
        statusClient: statusClient || createJobStatusClient({ fetch: fetchFn }),
        jobId: targetJobId,
        reload: () => {
          if (doc && doc.location && typeof doc.location.reload === "function") doc.location.reload();
        },
      });
    } else {
      // 部分描画しない。何が起きたかだけを掲示する（fail-stop）。
      view.showMessage(e && e.message ? e.message : "結果を表示できません");
    }
  }

  function destroy() {
    if (stopJobWatch) { stopJobWatch(); stopJobWatch = null; }
    view.unmount();
  }

  return {
    /** 子文書を畳む（親が iframe ごと捨てるため通常は使わない）。 */
    destroy,

    /** 現在のジョブ（診断・E2E 用）。 */
    jobId() { return targetJobId; },
  };
}
