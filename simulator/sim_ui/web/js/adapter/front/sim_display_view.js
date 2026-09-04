// sim 表示層の器（View・F-2 / F-7 拡張）。
//
// 役割: シミュレーション結果の表示に必要な DOM を**自分で生成し所有する**。統合 UI の
//   index.html は 1 バイトも触らない（ISSUE-278 #16 と同じ規約: 器を持つのは View）。
//   Phase 5 で周辺表示（区間トグル・接点トグル・ヒートマップ・比較判定・用語）の受け皿を
//   足す。下部タブ帯とペイン器は sim_tabs_view に委譲し（#bottom を写さない）、明細・
//   ヒートマップ・比較・用語の各受け皿を対応ペインへ挿す。
//
// id 体系は移植元 report_ui/web/index.html と同一にする。移植元の table.js は
//   `<table id=tradeTable>` を、chart.js は Balance/Drawdown/chartBadge を、compare.js は
//   #cmpVerdict 等を、heatmap.js は host を、glossary.js は #glossHost を id で引く。id を
//   変えると移植元をそのまま import できず、結局こちらへ写す羽目になる（複製禁止に反する）。
//
// 見た目は移植元の style.css をそのまま使う（link は子文書 report_view.html が持つ・裁定 B）。
//   View は CSS を一切扱わない。lwc・report.json にも触らない（DOM だけを知る・ISP/DIP）。

import { createSimTabsView, SIM_TAB_NAMES } from "./sim_tabs_view.js";

/** 生成する要素の id（移植元 report_ui/web/index.html と同名）。 */
export const SIM_DISPLAY_IDS = Object.freeze({
  root: "sim-display",
  topbar: "topbar",
  chart: "price-chart",
  bal: "paneBal",
  dd: "paneDD",
  badge: "chartBadge",
  table: "tradeTable",
  hSel: "hSel",
  message: "sim-display-message",
  // --- Phase 5（周辺表示の受け皿）---
  segSel: "segSel",           // 区間トグル（sim_segment_view が生成する要素の id）
  metaLine: "meta-line",      // メタ情報 1 行（区間切替で更新）
  toggleContacts: "toggleContacts", // 接点マーカー表示トグル（chartWrap 内・badge の後）
  heatHost: "heatHost",       // ヒートマップの描画先（buildHeatmap の host）
  paneCompare: "pane-compare", // 比較・判定ペイン（sim_compare_view が中身を挿す）
  paneGlossary: "pane-glossary", // 用語ペイン
  glossHost: "glossHost",     // 用語集の描画先（buildGlossary の host）
  clearFilter: "clearFilter", // 抽出フィルタ解除ピル（点18）
  detailCount: "detailCount", // 抽出件数の readout（点18）
});

/** 器の生成・破棄と、描画できないときのメッセージ表示を持つ View を返す。 */
export function createSimDisplayView({ doc } = {}) {
  let root = null;
  let host = null;
  let tabs = null;
  const elements = {
    root: null, chart: null, bal: null, dd: null,
    badge: null, table: null, hSel: null, message: null,
    // Phase 5:
    segHost: null, metaLine: null, toggleContacts: null,
    heatHost: null, paneCompare: null, paneGlossary: null, glossHost: null,
    clearFilter: null, detailCount: null,
  };

  const el = (tag, props) => {
    const node = doc.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (k === "dataset") Object.assign(node.dataset, v);
      else node[k] = v;
    }
    return node;
  };

  /** ヘッダ: h1・区間トグルの挿し先・メタ 1 行・連動選択ラベル（点16 hSel）。 */
  function buildHeader() {
    // id は移植元と同じ `topbar`——style.css:26-35 は `#topbar` の id セレクタなので、
    //   別名を付けると 1 つも当たらず見た目が崩れる（実測: flex にならず h1 24px）。
    const header = el("header", { id: SIM_DISPLAY_IDS.topbar });
    header.appendChild(el("h1", { textContent: "シミュレーション結果" }));
    // 区間トグルの挿し先（sim_segment_view が #segSel をここへ入れる・h1 の直後＝移植元順）。
    //   区間が 1 つなら segment_view は何も入れない（縮退）。
    const segHost = el("span", { className: "segwrap-host" });
    header.appendChild(segHost);
    const metaLine = el("div", { id: SIM_DISPLAY_IDS.metaLine, className: "meta-line" });
    header.appendChild(metaLine);
    const hSel = el("span", { id: SIM_DISPLAY_IDS.hSel, className: "hsel" });
    header.appendChild(hSel);
    Object.assign(elements, { segHost, metaLine, hSel });
    return header;
  }

  /** 3 窓（ローソク足 / Balance / Drawdown）＋ chartBadge ＋ 接点トグル。 */
  function buildChartWrap() {
    const chartWrap = el("div", { id: "chartWrap" });
    const chart = el("div", { id: SIM_DISPLAY_IDS.chart });
    const bal = el("div", { id: SIM_DISPLAY_IDS.bal, className: "subpane" });
    bal.appendChild(el("span", { className: "plabel", textContent: "Balance（資産曲線）" }));
    const dd = el("div", { id: SIM_DISPLAY_IDS.dd, className: "subpane" });
    dd.appendChild(el("span", { className: "plabel", textContent: "Drawdown（残高ベースDD・JPY）" }));
    const badge = el("div", { id: SIM_DISPLAY_IDS.badge, textContent: "—" });
    // 接点トグルは **badge の後**（移植元 index.html:35-37 の要素順）。sim_contacts_toggle_view
    //   が挙動を結線する（DOM は器が持つ・main.js wireContactsToggle と同じ分業）。
    const toggleContacts = el("button", {
      className: "maxbtn on", title: "価格×EMA の接点マーカー表示/非表示",
      textContent: "◆ 接点", id: SIM_DISPLAY_IDS.toggleContacts,
    });
    chartWrap.appendChild(chart);
    chartWrap.appendChild(bal);
    chartWrap.appendChild(dd);
    chartWrap.appendChild(badge);
    chartWrap.appendChild(toggleContacts);
    Object.assign(elements, { chart, bal, dd, badge, toggleContacts });
    return chartWrap;
  }

  /** 明細ペインの中身（.detail-area）: 抽出ピル/件数のヒント＋取引明細テーブル。 */
  function buildDetailArea() {
    const detail = el("div", { className: "detail-area" });
    const hint = el("div", { className: "hint" });
    hint.appendChild(el("span", {
      textContent: "行 hover → 該当売買ペアだけ明色 / 列見出しクリックでソート。",
    }));
    const detailCount = el("span", { id: SIM_DISPLAY_IDS.detailCount });
    const clearFilter = el("span", {
      className: "pill", textContent: "フィルタ解除 ✕", id: SIM_DISPLAY_IDS.clearFilter,
    });
    clearFilter.style.display = "none"; // 抽出が立つまで非表示（点18）。
    hint.appendChild(detailCount);
    hint.appendChild(clearFilter);
    const table = el("table", { id: SIM_DISPLAY_IDS.table, className: "trade-table" });
    table.appendChild(el("thead", {}));
    table.appendChild(el("tbody", {}));
    detail.appendChild(hint);
    detail.appendChild(table);
    Object.assign(elements, { table, clearFilter, detailCount });
    return detail;
  }

  function build() {
    const container = el("div", { id: SIM_DISPLAY_IDS.root, className: "sim-display" });
    container.appendChild(buildHeader());
    container.appendChild(buildChartWrap());

    // 下部タブ帯とペイン器は sim_tabs_view に委譲する（#bottom を写さない）。生成された
    //   4 ペイン（detail/heat/compare/glossary）へ各受け皿を挿す。.mv-body（position:relative）
    //   があるので .mv-pane の absolute は器の中に収まる（Phase 4 事故の回帰の壁）。
    tabs = createSimTabsView({ doc });
    const bottom = tabs.mount(container);
    const panes = tabs.elements.panes;

    panes.detail.appendChild(buildDetailArea());

    const heatHost = el("div", { id: SIM_DISPLAY_IDS.heatHost, className: "heat-area" });
    panes.heat.appendChild(heatHost);

    // 比較ペインは sim_compare_view が中身を挿す（受け皿として id を与える）。
    panes.compare.id = SIM_DISPLAY_IDS.paneCompare;

    panes.glossary.id = SIM_DISPLAY_IDS.paneGlossary;
    panes.glossary.appendChild(el("div", {
      className: "hint", textContent: "各レポート項目・グラフの役割と見方の説明。",
    }));
    const glossHost = el("div", { id: SIM_DISPLAY_IDS.glossHost, className: "gloss" });
    panes.glossary.appendChild(glossHost);

    Object.assign(elements, {
      heatHost, paneCompare: panes.compare, paneGlossary: panes.glossary, glossHost,
    });

    // 描画できないときの掲示（部分描画しない・fail-stop の表示面）。
    const message = el("div", { id: SIM_DISPLAY_IDS.message, className: "error" });
    message.classList.add("hidden");

    container.appendChild(message);

    Object.assign(elements, { root: container, message });
    void bottom;
    return container;
  }

  return {
    elements,

    /** タブビュー（合成根が初期タブを活性化するため）。未 mount なら null。 */
    tabsView() { return tabs; },

    /** 名前のタブ/ペインを活性化する（初期表示・タブ切替の単一経路）。 */
    activate(name) { if (tabs) tabs.activate(name); },

    /** 共通 4 タブ名（合成根が初期タブを選ぶための面）。 */
    tabNames() { return SIM_TAB_NAMES; },

    isMounted() { return root !== null; },

    /** 器を `target` の下へ挿す（二重 mount は無視）。 */
    mount(target) {
      if (root) return root;
      host = target;
      root = build();
      host.appendChild(root);
      return root;
    },

    /** 器を外す。二重 unmount は無視。 */
    unmount() {
      if (root && host) host.removeChild(root);
      root = null;
      host = null;
      tabs = null;
      for (const key of Object.keys(elements)) elements[key] = null;
    },

    /** 描画できない理由を掲示する（部分描画の代わりに出すもの）。 */
    showMessage(text) {
      const node = elements.message;
      if (!node) return;
      node.textContent = text;
      node.classList.remove("hidden");
    },

    clearMessage() {
      const node = elements.message;
      if (!node) return;
      node.textContent = "";
      node.classList.add("hidden");
    },
  };
}
