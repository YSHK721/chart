// sim 表示層の合成根（F-1）。**2 つの入口**を持つ。
//
//   統合ページ側: setupSimDisplay({doc, host, jobId})  → 器（#sim-display）と iframe を出す
//   子文書側:     mountSimReportView({doc, lwc, host}) → 3 窓・取引明細を実際に組み立てる
//
// なぜ 2 段か（実 UI 実測 2026-08-11 → 裁定 B）: 移植元 style.css は body そのものを
//   全画面レイアウトへ作り替えるため、統合ページへ link すると既存 UI の見た目が変わる
//   （実測: body 背景・font・文字色）。CSS を書き換えるのは「見た目の複製」なので採らない。
//   同じ CSS をそのまま使い波及だけを止める構造 ＝ 別文書（iframe）。
//   親は器だけを持ち、表示の中身は子文書 `/sim/report_view.html` が持つ。
//
// 依存の向き（DIP / ISP）:
//   - 移植元 report_ui の実体は **/sim/report-js/ から import** する。sim 側に写しは 1 つも
//     置かない（import_source.test.js が機械強制する）。
//   - lwc に触るのは F-3（v5 アダプタ）だけ。View も本モジュールも lwc API を書かない。
//   - linkage は**ここで生成して注入**する（chart→linkage の直接 import を作らない・移植元規約）。
//
// 規定:
//   - job_id は `?job=<id>` からのみ得る。一覧から自動で選ばない（ビュー自動介入の禁止）。
//     親は受け取った id を子の URL へ載せ、子は自分で読む（読み方を二重化しない）。
//   - 取れない・未生成なら**何も描かず**理由を掲示する（部分描画しない・fail-stop）。

import {
  balanceForwardFill, byTimeResolve,
  buildTradeMarkers, buildDimBars, mergeDimBarsForTrade,
  visibleTradesInRange, chartBadgeText,
  DIM_ALPHA, MARKER_CAP, DEFAULT_DEPOSIT,
} from "/sim/report-js/chart.js";
import { fmtMoney } from "/sim/report-js/format.js";
import { createLinkage } from "/sim/report-js/linkage.js";
import { buildTradeTable } from "/sim/report-js/table.js";

import { createLwc5ChartRenderer } from "./lwc5_chart_renderer.js";
import { createReportSourceClient, firstSegment, readJobId } from "./report_source_client.js";
import { createSimDisplayView } from "./sim_display_view.js";
import { createSimFrameView } from "./sim_frame_view.js";

/** 移植元 chart.js が所有する表示規則。v5 アダプタへはこの束を注入する。 */
const CHART_LOGIC = {
  balanceForwardFill, byTimeResolve,
  buildTradeMarkers, buildDimBars, mergeDimBarsForTrade,
  visibleTradesInRange, chartBadgeText,
  DIM_ALPHA, MARKER_CAP, DEFAULT_DEPOSIT,
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
export async function setupSimDisplay({ doc, host, jobId, search } = {}) {
  const frame = createSimFrameView({ doc });
  const targetJobId = resolveJobId({ jobId, search });
  // 器は `.chart-wrap` と同じ版面（#app の flex 子）へ置く。承認 H-C により統合ページは
  //   chart API を持たないモードで `.chart-wrap` を畳むので、空いた領域をそのまま受け取る。
  //   #app が無いページ（子文書・E2E fixture）では渡された host にそのまま置く。
  const mountPoint = (host && host.querySelector && host.querySelector("#app")) || host;
  let enabled = false;

  return {
    /** sim モードへ入るときに呼ばれる。器と子文書を出す。 */
    async enable() {
      if (enabled) return;
      enabled = true;
      frame.mount(mountPoint, targetJobId);
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
 * 子文書側の入口。3 窓・取引明細を実際に組み立てる（report_view.html から呼ばれる）。
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

  let renderer = null;

  // hover 購読は**この 1 回だけ**登録する。描画のたびに足すと、モード往復（sim→live→sim）で
  // 購読が積み上がり、1 回の hover が描画 N 回になる。移植元 table.js（:81-100）が同じ理由で
  // 結線を 1 度に制限している（区間切替で累積させない）。renderer は描画ごとに作り直されるため
  // 購読側はそのときの実体を都度読む。
  // 効果: マーカー強調（点 S3）＋ 連動ラベル（点16）＋ ローソク減光（点 S4）。
  linkage.subscribe((id) => {
    if (renderer) {
      renderer.renderMarkers(renderer.currentRows(), { hoverId: id, filter: linkage.activeFilter });
    }
    updateSelectionLabel(id);
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

  function draw(segment, payload) {
    renderer = createLwc5ChartRenderer({
      lwc,
      hosts: {
        chart: view.elements.chart, bal: view.elements.bal,
        dd: view.elements.dd, badge: view.elements.badge,
      },
      logic: CHART_LOGIC,
    });

    // chart→linkage は**コールバック注入**（直接 import を作らない・移植元規約）。
    //   購読は renderer と同じ寿命なので、ここで登録してよい（積み上がらない）。
    renderer.onMarkerHover((id) => linkage.setHover(id, "chart"));

    renderer.render(segment, {
      initialDeposit: payload && payload.meta && payload.meta.initial_deposit,
    });

    // 行クリック → 選択確定（減光/強調）＋ 該当時刻へズーム（移植元 main.js と同じ結線）。
    const onRowFocus = (id, time) => { linkage.setHover(id, "table"); renderer.focusTime(time); };
    buildTradeTable(view.elements.table, segment, linkage, onRowFocus);

    // E2E フック（双方向結線の実測点）。移植元 main.js:182-183 と同じものを合成根が配る
    // （chart 実体は F-3 が `__simPriceChart` / `__simCandleSeries` で出す）。
    if (typeof window !== "undefined") {
      window.__simLinkage = linkage;
      // disable 後は renderer が畳まれる。掴んだままの参照で落とさない（呼び出し順非依存）。
      window.__simEmitMarkerHover = (id) => { if (renderer) renderer.emitMarkerHover(id); };
    }
  }

  view.mount(host);
  try {
    const payload = await source.load(targetJobId);
    const segment = firstSegment(payload);
    if (!segment) {
      view.showMessage("結果未生成（表示できる区間がありません）");
    } else {
      view.clearMessage();
      draw(segment, payload);
    }
  } catch (e) {
    // 部分描画しない。何が起きたかだけを掲示する（fail-stop）。
    view.showMessage(e && e.message ? e.message : "結果を表示できません");
  }

  return {
    /** 子文書を畳む（親が iframe ごと捨てるため通常は使わない）。 */
    destroy() {
      if (renderer) { renderer.destroy(); renderer = null; }
      view.unmount();
    },

    /** 現在のジョブ（診断・E2E 用）。 */
    jobId() { return targetJobId; },
  };
}
