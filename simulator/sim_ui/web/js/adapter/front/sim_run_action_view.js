// 実行指示面（View・Phase 9 S5 M3。`sim_execution_panel_view.js` を改名）。
//
// 役割: 実行を開始させることと、出来上がった結果への導線を出すこと——この 2 つだけである。
//   何を投入するか（本文の組み立て）は M5 sim_submission_builder が、どこへ遷移するか
//   （URL の作り方）は合成根が持つ。この面は本文も HTTP も URL も知らない。
//
// なぜ結果導線の DOM をここが持つか（SRP）: S4 までは合成根が `doc.createElement` で直接
//   ボタンを生やしていた。合成根が DOM を作り始めると器の骨格が 2 箇所へ散り、CSS の選択子も
//   パネル id の外へはみ出す（`#execViewResult` が body 直下に生えていた）。DOM を作るのは
//   View だけにする。
//
// **自動遷移しない**（ビュー自動介入の禁止・裁定 2026-07-23）: 投入が通っても画面は切り替え
//   ない。導線を出すだけで、遷移するのは利用者がそれを押したときに限る。
//
// fake DOM 前提: querySelector は使わず、要素参照を JS 側で保持する。

export function createSimRunActionView({ doc } = {}) {
  let root = null;
  let startBtn = null;
  let resultLink = null;
  let startCb = null;
  let viewResultCb = null;
  let currentJobId = null;

  const el = (tag, props) => {
    const node = doc.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (k === "dataset") Object.assign(node.dataset, v);
      else node[k] = v;
    }
    return node;
  };

  return {
    elements: {},

    mount(host) {
      root = el("div", { id: "simRunActionPanel", className: "run-action-panel" });
      startBtn = el("button", {
        id: "runStart", className: "run-start", type: "button", textContent: "スタート",
        dataset: { mt5: "action:start" },
      });
      startBtn.addEventListener("click", () => { if (startCb) startCb(); });
      root.appendChild(startBtn);
      host.appendChild(root);
      this.elements = { root, startBtn };
      return root;
    },

    /** 実行開始の購読口（この面は何を投入するかを知らない）。 */
    onStart(cb) { startCb = cb; },

    /** 結果導線の購読口（遷移先の決定は外＝合成根の reportViewUrl）。 */
    onViewResult(cb) { viewResultCb = cb; },

    /**
     * 出来た job への導線を出す（既に出ていれば指す job を差し替える）。
     * 押すたびにボタンが増えないよう、実体は 1 つだけ持つ。
     */
    showResultLink(jobId) {
      currentJobId = jobId;
      if (!resultLink) {
        resultLink = el("button", {
          id: "execViewResult", className: "exec-view-result", type: "button",
          textContent: "結果を見る", dataset: { mt5: "ui:view-result" },
        });
        resultLink.addEventListener("click", () => {
          if (viewResultCb && currentJobId) viewResultCb(currentJobId);
        });
        root.appendChild(resultLink);
        this.elements.resultLink = resultLink;
      }
      return resultLink;
    },
  };
}
