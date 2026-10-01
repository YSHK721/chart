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
// 「設定をコピー」（2026-09-28 依頼者指示・デバッグ用）: スタートで投入する内容と同じものを
//   クリップボードへ写す。何を写すか（本文）は合成根が M5 の同じ関数で組み、この面は押された
//   ことを知らせ、渡された文字列をクリップボードへ書き、結果を横に出すだけ。
//
// fake DOM 前提: querySelector は使わず、要素参照を JS 側で保持する。

export function createSimRunActionView({ doc } = {}) {
  let root = null;
  let startBtn = null;
  let resultLink = null;
  let startCb = null;
  let copyCb = null;
  let copyNote = null;
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
      const copyBtn = el("button", {
        id: "runCopySettings", className: "run-copy-settings", type: "button",
        textContent: "設定をコピー", title: "スタートで投入する設定（Tester Settings・EA パラメータ・実行トレース・データセット）を JSON でコピー",
        // 表示側の操作（MT5 に対応物の無い sim の補助・本文に寄与しない＝ui:）。
        dataset: { mt5: "ui:copy-settings" },
      });
      copyBtn.addEventListener("click", () => { if (copyCb) copyCb(); });
      root.appendChild(copyBtn);
      copyNote = el("span", { id: "runCopyNote", className: "run-copy-note", textContent: "" });
      root.appendChild(copyNote);
      host.appendChild(root);
      this.elements = { root, startBtn, copyBtn, copyNote };
      return root;
    },

    /** 「設定をコピー」の購読口（何を写すかはこの面は知らない）。 */
    onCopySettings(cb) { copyCb = cb; },

    /**
     * 文字列をクリップボードへ書く。Clipboard API が使えない文脈（安全でない配信元など）では、
     * 一時的なテキスト欄を選択して copy コマンドで書く。書けなければ例外を投げる（無音にしない）。
     */
    async copyText(text) {
      const nav = doc.defaultView && doc.defaultView.navigator;
      if (nav && nav.clipboard && typeof nav.clipboard.writeText === "function") {
        await nav.clipboard.writeText(text);
        return;
      }
      const area = el("textarea", { value: text });
      area.style.position = "fixed";
      area.style.opacity = "0";
      root.appendChild(area);
      try {
        area.select();
        if (typeof doc.execCommand !== "function" || !doc.execCommand("copy")) {
          throw new Error("このブラウザではクリップボードへ書けません");
        }
      } finally {
        root.removeChild(area);
      }
    },

    /** コピーの結果を横に出す（成功・失敗とも文言は合成根が決める）。 */
    showCopyResult(message) {
      if (copyNote) copyNote.textContent = message;
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
