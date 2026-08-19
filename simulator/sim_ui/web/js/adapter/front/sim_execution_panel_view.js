// 実行指示パネル（View・Phase 6 F-8 / Phase 9 S1 で UI 出口を絞る）。
//
// 役割: 実行仕様（`backtest`）を組み、投入本文を作る。
//
// 責務（SRP）: DOM の生成だけ。fetch はしない（実行対象データセットの profile は
//   setRunProfile で**注入**する＝合成根が銘柄から resolveProfile で決めて供給）。
//   投入本文の組み立ては
//   sim_submission_builder（純関数・M5）が、EA パラメータは sim_ea_inputs_panel_view（M2）が、
//   実行対象（EA・口座・設定ブロック）は SubjectSource（M1 か M4）が所有する——このパネルは
//   参照するだけで、規則も欄も写さない。
//   投入自体も onSubmit コールバックで外へ渡す（このパネルは HTTP を知らない）。
//
// Phase 9 S1（§19.2）: 買い/売り条件の行組み立てと建玉変更の入力欄は **UI 出口として
//   撤去**した。機能そのものは API 面で存続する（`POST /sim/jobs` の第 2 ブロック）。
//   MT5 の対応物を持たない入力を画面から出さない、という裁定に従う。
//
// fake DOM 前提: querySelector は使わず、フィールド要素の参照を JS 側で保持する
//   （fake DOM の querySelector は null を返すため・_fakes.js 実測）。

import { buildSubmission as buildBody } from "./sim_submission_builder.js";

export function createSimExecutionPanelView({ doc, inputs } = {}) {
  let root = null;
  let submitBtn = null;
  let profile = null;
  let submitCb = null;
  // 実行対象の供給元（SubjectSource）。schema が取れれば M1 Tester Settings 面、取れなければ
  // M4 縮退面が入る。**どちらか 1 つだけ**であり、この面は区別しない（分岐を持たない）。
  let subjectSource = null;

  const el = (tag, props) => {
    const node = doc.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (k === "dataset") Object.assign(node.dataset, v);
      else node[k] = v;
    }
    return node;
  };

  function buildSubmission() {
    // 実行対象は Port ごしに 1 箇所から取る（供給元がどちらの面かをここで見分けない）。
    const derived = subjectSource.derivedBacktest();
    const subject = {
      ea_name: derived.ea_name,
      initial_deposit: derived.initial_deposit,
      settings: subjectSource.buildSettings(),
    };
    return buildBody({ profile, subject, inputs: inputs.values() });
  }

  return {
    elements: {},

    mount(host) {
      root = el("div", { id: "simExecPanel", className: "exec-panel" });

      submitBtn = el("button", { id: "execSubmit", className: "exec-submit", type: "button", textContent: "投入" });
      submitBtn.addEventListener("click", () => { if (submitCb) submitCb(buildSubmission()); });

      root.appendChild(submitBtn);

      host.appendChild(root);
      this.elements = { root, submitBtn };
      return root;
    },

    /** 実行対象データセットの profile を注入する（Phase 9 S4）。
     *  解決は合成根が銘柄から行う（resolveProfile）。この面は受け取った 1 件を使うだけで、
     *  profile 由来 11 キーの値を front に持たない。 */
    setRunProfile(runProfile) {
      profile = runProfile || null;
    },

    /** 実行対象の供給元（SubjectSource）を結線する（Phase 9 S3）。
     *
     *  合成根が schema の有無で M1 / M4 のどちらか 1 つを渡す。この面は渡された Port を
     *  呼ぶだけで、どちらが来ているかを見分けない（見分けた瞬間に本文の組み立てへ分岐が戻る）。 */
    setSubjectSource(source) {
      subjectSource = source || null;
    },

    buildSubmission,

    /** 投入ボタン押下時のコールバックを登録する（このパネルは HTTP を知らない）。 */
    onSubmit(cb) { submitCb = cb; },
  };
}
