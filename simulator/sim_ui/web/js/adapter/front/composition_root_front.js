// sim 表示層の合成根（F-1）。**2 つの入口**を持つ。
//
//   統合ページ側: setupSimDisplay({doc, host, jobId})  → 器（#sim-display）と iframe を出す
//   子文書側:     mountSimReportView({doc, lwc, host}) → 3 窓・取引明細・周辺表示を組み立てる
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
//     用語集・接点純関数）も同じく import し、各 View へ**注入**する。
//   - lwc に触るのは F-3（v5 アダプタ）だけ。View も本モジュールも lwc API を書かない。
//   - linkage は**ここで生成して注入**する（chart→linkage の直接 import を作らない・移植元規約）。
//
// 規定:
//   - job_id は `?job=<id>` からのみ得る。一覧から自動で選ばない（ビュー自動介入の禁止）。
//   - 取れない・未生成なら**何も描かず**理由を掲示する（部分描画しない・fail-stop）。
//
// 結線順は移植元 main.js:135-190 と同順（tabs は View が持つ → subscribeFilter → compare →
//   glossary+wireTips〔init 1 回・多重 #tip 禁止〕→ segment → contacts トグル → selectSegment）。

import {
  balanceForwardFill, byTimeResolve,
  buildTradeMarkers, buildDimBars, mergeDimBarsForTrade,
  visibleTradesInRange, chartBadgeText,
  contactsInRange, contactsToMarkers,
  DIM_ALPHA, MARKER_CAP, DEFAULT_DEPOSIT, CONTACT_MARKER_CAP,
} from "/sim/report-js/chart.js";
import { fmtMoney } from "/sim/report-js/format.js";
import { createLinkage } from "/sim/report-js/linkage.js";
import { buildTradeTable } from "/sim/report-js/table.js";
import { buildHeatmap } from "/sim/report-js/heatmap.js";
import { aggOf } from "/sim/report-js/data.js";
import { buildCompare, renderVerdictBanner } from "/sim/report-js/compare.js";
import { buildGlossary, wireTips } from "/sim/report-js/glossary.js";

import { createLwc5ChartRenderer } from "./lwc5_chart_renderer.js";
import { createReportSourceClient, firstSegment, readJobId } from "./report_source_client.js";
import { createSimDisplayView } from "./sim_display_view.js";
import { createSimFrameView, waitForContent } from "./sim_frame_view.js";
import { createSimSegmentView } from "./sim_segment_view.js";
import { createSimCompareView } from "./sim_compare_view.js";
import { createSimContactsToggleView } from "./sim_contacts_toggle_view.js";
import { createSimFilterPillView } from "./sim_filter_pill_view.js";

/** 移植元 chart.js が所有する表示規則。v5 アダプタへはこの束を注入する。 */
const CHART_LOGIC = {
  balanceForwardFill, byTimeResolve,
  buildTradeMarkers, buildDimBars, mergeDimBarsForTrade,
  visibleTradesInRange, chartBadgeText,
  // 接点（FR-18）の純関数も移植元 chart.js が単一ソース。v5 アダプタへ注入して使う。
  contactsInRange, contactsToMarkers,
  DIM_ALPHA, MARKER_CAP, DEFAULT_DEPOSIT, CONTACT_MARKER_CAP,
};

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
export async function setupSimDisplay({ doc, host, jobId, search, onContentHeight, raf } = {}) {
  const frame = createSimFrameView({ doc });
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
      frame.unmount();
    },

    /** 子文書の window（同一オリジン直参照・E2E の観測点）。 */
    childWindow() { return frame.childWindow(); },

    /** 現在のジョブ（診断・E2E 用）。 */
    jobId() { return targetJobId; },
  };
}

/**
 * 子文書側の入口。3 窓・取引明細・周辺表示（区間トグル・接点・ヒートマップ・比較判定・
 * 用語集）を組み立てる（report_view.html から呼ばれる）。
 *
 * @param {Document} doc      子文書の DOM
 * @param {object}   lwc      グローバル LightweightCharts（v5.2.0）
 * @param {Element}  host     器を挿す先（子文書の body）
 * @param {string}   jobId    表示対象ジョブ。未指定なら `search` から読む
 * @param {string}   search   `location.search` 相当
 * @returns {{destroy: function}}
 */
export async function mountSimReportView({ doc, lwc, host, jobId, search, fetch: fetchFn } = {}) {
  const view = createSimDisplayView({ doc });
  const source = createReportSourceClient({ fetch: fetchFn });
  const linkage = createLinkage();
  const targetJobId = resolveJobId({ jobId, search });

  // 周辺 View（DOM は sim_display_view が所有し、これらは結線と描き分けだけを担う）。
  const compareView = createSimCompareView({ doc, buildCompare, renderVerdictBanner });
  const segmentView = createSimSegmentView({ doc });
  const contactsToggle = createSimContactsToggleView();
  const filterPill = createSimFilterPillView();

  let renderer = null;
  let payload = null;
  let segKeys = [];
  let curSeg = null;

  // hover 購読は**この 1 回だけ**登録する（区間切替で積み上げない・移植元 table.js:81-100 の
  //   理由と同じ）。renderer は寿命を通じて 1 実体なので、そのときの実体を都度読む必要はない。
  linkage.subscribe((id) => {
    if (renderer) {
      renderer.renderMarkers(renderer.currentRows(), { hoverId: id, filter: linkage.activeFilter });
    }
    updateSelectionLabel(id);
  });

  // 点18: 抽出フィルタ購読（ピル表示＋件数＋ chart/table 連動）。init で 1 回だけ登録する
  //   （移植元 main.js:151-159）。購読者側で DOM 副作用を持つ（linkage は純状態機械）。
  linkage.subscribeFilter((filter, label) => {
    if (renderer) {
      renderer.renderMarkers(renderer.currentRows(), { hoverId: linkage.hoverTradeId, filter });
    }
    if (curSeg) buildTradeTable(view.elements.table, curSeg, linkage, onRowFocus); // dim を反映
    filterPill.reflect(filter, label);
  });

  // 点16: 連動選択ラベル（hover 中の trade を 1 行で示す）＋ ペア区間外の減光（点 S4）。
  function updateSelectionLabel(id) {
    const label = view.elements.hSel;
    if (!label) return;
    const rows = renderer ? renderer.currentRows() : [];
    const trade = id == null ? null : rows.find((r) => r.id === id);
    if (!trade) {
      label.textContent = "";
      if (renderer) renderer.restoreCandles();
      return;
    }
    label.innerHTML =
      `▶ #${trade.id} ${String(trade.side).toUpperCase()} @${trade.entry_price} → ${trade.exit_price} ` +
      `<b style="color:${trade.profit > 0 ? "#26a69a" : "#ef5350"}">${fmtMoney(trade.profit)} JPY</b>` +
      ` · MFE ${trade.mfe} / MAE ${trade.mae}`;
    if (renderer) renderer.dimCandlesForTrade(trade);
  }

  // 行クリック → 選択確定（減光/強調）＋ 該当時刻へズーム（移植元 main.js と同じ結線）。
  function onRowFocus(id, time) { linkage.setHover(id, "table"); if (renderer) renderer.focusTime(time); }

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

  // 区間切替（移植元 main.js:65-76 と同順）。単一 run では 1 度だけ呼ばれる。
  function selectSegment(segKey) {
    curSeg = payload.segments[segKey];
    linkage.applyFilter(null, "");        // 区間切替でフィルタ解除
    segmentView.setCurrent(segKey);       // segbtn の .on（縮退時は no-op）
    renderMeta(curSeg);
    renderer.render(curSeg, { initialDeposit: payload.meta && payload.meta.initial_deposit });
    renderer.setContacts(aggOf(payload, segKey).contacts || []); // 接点（FR-18）
    buildTradeTable(view.elements.table, curSeg, linkage, onRowFocus);
    buildHeatmap(
      view.elements.heatHost, payload, segKey, linkage,
      (time) => { if (renderer) renderer.focusTime(time); },
      // 単一区間（segKeys<2）では「IS vs OOS 損益差」ビューを出さない（D-3）。
      { showIsOosDiff: segKeys.length >= 2 },
    );
    setTimeout(() => { if (renderer) renderer.resize(); }, 30);
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

    // renderer は寿命を通じて 1 実体（render(seg) が内部 chart を破棄→再構築する）。接点
    //   トグル state は render 間で保持される（renderer が contactsShown を destroy で戻さない）。
    renderer = createLwc5ChartRenderer({
      lwc,
      hosts: {
        chart: view.elements.chart, bal: view.elements.bal,
        dd: view.elements.dd, badge: view.elements.badge,
      },
      logic: CHART_LOGIC,
    });
    // chart→linkage は**コールバック注入**（直接 import を作らない・移植元規約）。
    renderer.onMarkerHover((id) => linkage.setHover(id, "chart"));

    // 比較・判定は区間非依存（init で 1 回）。segKeys>=2 は buildCompare、単一は判定バナーのみ。
    compareView.render({ host: view.elements.paneCompare, segKeys, payload });
    // 用語集＋hover tip（init で 1 回・多重 #tip 禁止）。
    buildGlossary(view.elements.glossHost);
    wireTips();
    // 区間トグル（segKeys<2 なら生成しない＝縮退の唯一の所有者）。
    segmentView.render({
      host: view.elements.segHost, segKeys, current: segKeys[0], onSelect: selectSegment,
    });
    // 接点トグル（真実源は renderer・render 間で state 保持）。
    contactsToggle.wire({ btn: view.elements.toggleContacts, renderer });
    // 抽出ピルの解除結線（✕ クリック→ applyFilter 解除）。
    filterPill.wire({
      clearFilter: view.elements.clearFilter, detailCount: view.elements.detailCount, linkage,
    });
    // 初期表示は先頭区間（承認 G）。
    selectSegment(segKeys[0]);
    // 初期タブは明細（移植元と同じ・タブ切替は tabs View が単一経路で持つ）。
    view.activate("detail");

    // E2E フック（双方向結線の実測点・移植元 main.js:182-183 と対称）。
    if (typeof window !== "undefined") {
      window.__simLinkage = linkage;
      window.__simEmitMarkerHover = (id) => { if (renderer) renderer.emitMarkerHover(id); };
    }
  } catch (e) {
    // 部分描画しない。何が起きたかだけを掲示する（fail-stop）。
    view.showMessage(e && e.message ? e.message : "結果を表示できません");
  }

  function destroy() {
    if (renderer) { renderer.destroy(); renderer = null; }
    view.unmount();
  }

  return {
    /** 子文書を畳む（親が iframe ごと捨てるため通常は使わない）。 */
    destroy,

    /** 現在のジョブ（診断・E2E 用）。 */
    jobId() { return targetJobId; },
  };
}
