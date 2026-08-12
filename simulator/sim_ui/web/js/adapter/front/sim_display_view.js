// sim 表示層の器（View・F-2）。
//
// 役割: シミュレーション結果の表示に必要な DOM を**自分で生成し所有する**。統合 UI の
//   index.html は 1 バイトも触らない（ISSUE-278 #16 と同じ規約: 器を持つのは View）。
//
// id 体系は移植元 report_ui/web/index.html と同一にする。移植元の table.js は
//   `<table id=tradeTable>` の thead / tbody を querySelector で引き、chart.js 由来の
//   表示規則は「Balance 窓 / Drawdown 窓 / chartBadge」を前提にしている。id を変えると
//   移植元をそのまま import できなくなり、結局こちらへ写す羽目になる（複製禁止に反する）。
//
// 見た目は移植元の style.css をそのまま使う。**link は子文書（report_view.html）が持つ**
//   ので、View は CSS を一切扱わない（裁定 B: style.css を統合ページへ持ち込まない。
//   持ち込むと既存 UI の body 背景・font・文字色が変わることを実 UI で実測した）。
//   CSS を写さない・独自 CSS を書かない（YAGNI・§11.4）。
//
// lwc（lightweight-charts）はここでは触らない。View が知っているのは DOM だけで、
//   チャート描画は F-3（v5 アダプタ）の責務である（ISP/DIP）。

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
});

/** 器の生成・破棄と、描画できないときのメッセージ表示だけを持つ View を返す。 */
export function createSimDisplayView({ doc } = {}) {
  let root = null;
  let host = null;
  const elements = {
    root: null, chart: null, bal: null, dd: null,
    badge: null, table: null, hSel: null, message: null,
  };

  const el = (tag, props) => {
    const node = doc.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) node[k] = v;
    return node;
  };

  function build() {
    const container = el("div", { id: SIM_DISPLAY_IDS.root, className: "sim-display" });

    // ヘッダ: 連動選択ラベル（点16 hSel）。id は移植元と同じ `topbar`——style.css:26-35 は
    //   `#topbar` / `#topbar h1` の **id セレクタ**なので、別名を付けると 1 つも当たらず
    //   見た目が崩れる（実測: display が flex にならず h1 が 24px・hSel が 2 行目へ落ちる）。
    const header = el("header", { id: SIM_DISPLAY_IDS.topbar });
    const title = el("h1", { textContent: "シミュレーション結果" });
    const hSel = el("span", { id: SIM_DISPLAY_IDS.hSel, className: "hsel" });
    header.appendChild(title);
    header.appendChild(hSel);

    // 3 窓（ローソク足 / Balance / Drawdown）＋ 点7 chartBadge。
    const chartWrap = el("div", { id: "chartWrap" });
    const chart = el("div", { id: SIM_DISPLAY_IDS.chart });
    const bal = el("div", { id: SIM_DISPLAY_IDS.bal, className: "subpane" });
    bal.appendChild(el("span", { className: "plabel", textContent: "Balance（資産曲線）" }));
    const dd = el("div", { id: SIM_DISPLAY_IDS.dd, className: "subpane" });
    dd.appendChild(el("span", { className: "plabel", textContent: "Drawdown（残高ベースDD・JPY）" }));
    const badge = el("div", { id: SIM_DISPLAY_IDS.badge, textContent: "—" });
    chartWrap.appendChild(chart);
    chartWrap.appendChild(bal);
    chartWrap.appendChild(dd);
    chartWrap.appendChild(badge);

    // 取引明細（移植元 table.js が thead / tbody を引く）。
    // `.detail-area`（style.css:92 `overflow-x:auto`）だけを借りる。**`.mv-pane` は借りない**
    //   ——移植元では `position:absolute; inset:0`（style.css:86）で、効かせるには
    //   `.mv-body { position: relative }`（index.html:56）という位置指定された祖先が要る。
    //   sim の器はタブを持たない（YAGNI）ので `.mv-body` も無く、絶対配置はビューポート基準へ
    //   落ちる。実 UI 実測（統合 UI :8000・2026-08-11）で明細が全面を覆い、ツールバーの
    //   `#enter-sim` がクリック不能になった（elementFromPoint が明細の th を返した）。
    const bottom = el("div", { id: "bottom" });
    const detail = el("div", { className: "detail-area" });
    const table = el("table", { id: SIM_DISPLAY_IDS.table, className: "trade-table" });
    table.appendChild(el("thead", {}));
    table.appendChild(el("tbody", {}));
    detail.appendChild(table);
    bottom.appendChild(detail);

    // 描画できないときの掲示（部分描画しない・fail-stop の表示面）。
    const message = el("div", { id: SIM_DISPLAY_IDS.message, className: "error" });
    message.classList.add("hidden");

    container.appendChild(header);
    container.appendChild(chartWrap);
    container.appendChild(bottom);
    container.appendChild(message);

    Object.assign(elements, {
      root: container, chart, bal, dd, badge, table, hSel, message,
    });
    return container;
  }

  return {
    elements,

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
