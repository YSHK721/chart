// 比較・判定ペイン（View・F-7 FR-17）。
//
// 役割: 移植元 index.html:58-85 の #pane-compare の受け皿を生成し、区間数で描き分ける。
//   - 2 区間以上（report_ui 形の払い出し）: buildCompare(payload)（7 指標カード・劣化比較表・
//     5 グラフ）を呼ぶ。受け皿（cmpVerdict/cmpBasic/cmpTable/5 canvas）を先に出す。
//   - 単一区間（sim 実ジョブ＝single）: renderVerdictBanner(payload) **だけ**を呼ぶ。比較
//     グラフ（canvas）も指標カードも作らない（P9 縮退）。判定文言は payload が単一ソース——
//     View は payload を渡すだけで、判定語を自前に持たない。
//
// buildCompare / renderVerdictBanner は**注入**で受ける。/sim/report-js/ を直接 import する
//   のは合成根だけ（複製 0・import_source.test.js が機械強制）。両関数は移植元 compare.js の
//   実体で、子文書内の global document から自分の id を引く（View はその id を出すだけ）。
//
// lwc・report.json には触らない。DOM 生成は注入された doc だけを使う（global document 非依存）。

/** 比較グラフ（5 canvas）の id（移植元 index.html:71-82・window.__cmpCharts の突合点）。 */
const CMP_CANVAS_IDS = Object.freeze(["cmpEquity", "cmpPnl", "cmpDD", "cmpRadar", "cmpDeg"]);

/** 比較・判定ペインを描き分ける View を返す。 */
export function createSimCompareView({ doc, buildCompare, renderVerdictBanner } = {}) {
  const el = (tag, props) => {
    const node = doc.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (k === "dataset") Object.assign(node.dataset, v);
      else node[k] = v;
    }
    return node;
  };

  /** 判定バナーの受け皿（両モードで要る）。 */
  function verdictHost() {
    return el("div", { id: "cmpVerdict", className: "cmp-verdict" });
  }

  /** 比較グラフ 1 枚分のカード（canvas を内包）。 */
  function chartCard(canvasId) {
    const card = el("div", { className: "cmp-card" });
    const cv = el("div", { className: "cmp-cv" });
    cv.appendChild(el("canvas", { id: canvasId }));
    card.appendChild(cv);
    return card;
  }

  function buildFull(host, payload) {
    // 受け皿（移植元 index.html:58-85 の要素を出す。中身は buildCompare が埋める）。
    host.appendChild(verdictHost());
    const basicWrap = el("div", { className: "cmp-card cmp-basic-wrap" });
    basicWrap.appendChild(el("div", { id: "cmpBasic", className: "cmp-basic basic-grid" }));
    host.appendChild(basicWrap);

    const split = el("div", { className: "cmp-split" });
    const left = el("div", { className: "cmp-left" });
    const tableCard = el("div", { className: "cmp-card" });
    const table = el("table", { id: "cmpTable", className: "cmp" });
    table.appendChild(el("thead", {}));
    table.appendChild(el("tbody", {}));
    tableCard.appendChild(table);
    left.appendChild(tableCard);
    const right = el("div", { className: "cmp-right" });
    for (const id of CMP_CANVAS_IDS) right.appendChild(chartCard(id));
    split.appendChild(left);
    split.appendChild(right);
    host.appendChild(split);

    buildCompare(payload);
  }

  function buildDegenerate(host, payload) {
    // 縮退: 判定バナーだけ。比較グラフ（canvas）も指標カードも作らない（P9）。
    host.appendChild(verdictHost());
    renderVerdictBanner(payload);
  }

  return {
    /** 比較ペインを host（tabs の compare ペイン）へ描く。 */
    render({ host, segKeys, payload } = {}) {
      const keys = segKeys || [];
      if (keys.length >= 2) buildFull(host, payload);
      else buildDegenerate(host, payload);
    },
  };
}
