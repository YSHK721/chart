// 下部タブ帯とペイン器（View・F-7）。
//
// 役割: 移植元 index.html:44-111 の「下部マルチビュー枠」のうち sim が使う分だけを生成し
//   所有する。移植元 6 タブ（比較・明細・ヒートマップ・グラフ・サマリー・用語）のうち
//   **graph / report は流用しない**（YAGNI・doc §流用）。共通 4 タブ（明細・ヒートマップ・
//   比較判定・用語）だけを出す。中身（明細表・ヒートマップ・比較グラフ・用語）は
//   合成根が各ペインへ挿す（本 View はタブの活性切替とペインの可視だけを持つ）。
//
// なぜ .mv-body を必ず生成するか（Phase 4 事故と同型・実測 2026-08-11）:
//   移植元 style.css:86 は `.mv-pane { position:absolute; inset:0 }`。効かせるには
//   `.mv-body { position:relative }`（移植元 index.html:56）という**位置指定された祖先**が
//   要る。祖先が無いと絶対配置はビューポート基準へ落ち、ペインが全面を覆って既存ツール
//   バーのクリックを飲み込む（elementFromPoint が th を返す＝モードから出られない）。
//   よって器の中に .mv-body を必ず出し、ペインをその中へ閉じ込める。
//
// lwc・report.json・CSS には触らない（DOM だけを知る・裁定 B）。

/** 共通 4 タブ（この順で帯へ並ぶ・移植元の並びから graph/report を除いたもの）。 */
export const SIM_TAB_NAMES = Object.freeze(["detail", "heat", "compare", "glossary"]);

/** タブ帯のラベル（移植元 index.html:47-52 の文言）。 */
const TAB_LABELS = Object.freeze({
  detail: "取引明細 (Trade Detail)",
  heat: "ヒートマップ (Heatmap)",
  compare: "⚖ 比較・判定 (IS vs OOS)",
  glossary: "用語説明 (Glossary)",
});

/** 下部タブ帯とペイン器を生成・切替する View を返す。 */
export function createSimTabsView({ doc } = {}) {
  let root = null;
  let host = null;
  const tabs = {};
  const panes = {};

  const el = (tag, props) => {
    const node = doc.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (k === "dataset") Object.assign(node.dataset, v);
      else node[k] = v;
    }
    return node;
  };

  /** name のペインだけを可視にし、name のタブだけを active にする（単一活性）。 */
  function activate(name) {
    for (const [tabName, tab] of Object.entries(tabs)) {
      tab.classList.toggle("active", tabName === name);
    }
    for (const [paneName, pane] of Object.entries(panes)) {
      pane.classList.toggle("hidden", paneName !== name);
    }
  }

  function build() {
    const bottom = el("div", { id: "bottom" });

    const bar = el("div", { className: "mv-tabs", id: "tabs" });
    for (const name of SIM_TAB_NAMES) {
      const tab = el("button", {
        className: "mv-tab", textContent: TAB_LABELS[name], dataset: { tab: name },
      });
      tab.addEventListener("click", () => activate(name));
      tabs[name] = tab;
      bar.appendChild(tab);
    }

    // .mv-body は「位置指定された祖先」。これが無いと .mv-pane の absolute が全面を覆う。
    const body = el("div", { id: "mv-body", className: "mv-body" });
    for (const name of SIM_TAB_NAMES) {
      const pane = el("div", { className: "mv-pane", dataset: { pane: name } });
      pane.classList.add("hidden"); // 初期は全ペイン非可視（activate で 1 枚だけ開く）。
      panes[name] = pane;
      body.appendChild(pane);
    }

    bottom.appendChild(bar);
    bottom.appendChild(body);
    return bottom;
  }

  return {
    /** 各ペイン（合成根が中身を挿す先）。 */
    elements: { panes },

    isMounted() { return root !== null; },

    /** 器を `target` の下へ挿す（二重 mount は無視・同じ root を返す）。 */
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
      root = host = null;
      for (const k of Object.keys(tabs)) delete tabs[k];
      for (const k of Object.keys(panes)) delete panes[k];
    },

    /** name のタブ/ペインをコード側から活性化する（初期表示・タブ切替の単一経路）。 */
    activate,
  };
}
