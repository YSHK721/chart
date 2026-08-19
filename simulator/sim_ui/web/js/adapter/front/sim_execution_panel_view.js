// 実行指示パネル（View・Phase 6 F-8 / Phase 9 S1 で UI 出口を絞る）。
//
// 役割: 実行仕様（`backtest`）を組み、投入本文を作る。
//
// 責務（SRP）: DOM の生成だけ。fetch はしない（データセット profile は setRunOptions で
//   **注入**する＝合成根が job_submit_client.loadRunOptions から供給）。投入本文の組み立ては
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
  let datasetSel = null;
  let profiles = [];
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

  const option = (value, label) => el("option", { value, textContent: label != null ? label : value });

  function fillOptions(select, values) {
    // 既存 option を捨てて注入候補で作り直す（候補の単一ソースは呼出側）。
    // `select.children = []` は実ブラウザでは getter 専用プロパティへの代入で TypeError に
    // なる（fake DOM は素の配列なので通ってしまう＝実 UI でのみ露見する）。実 DOM でも
    // fake DOM でも動く removeChild ループで空にする（children の snapshot を取ってから外す）。
    for (const child of Array.from(select.children || [])) select.removeChild(child);
    for (const v of values) select.appendChild(option(v));
    // 既存 value が候補外なら先頭へ寄せる（未選択の空値を残さない）。
    if (values.length && !values.includes(select.value)) select.value = values[0];
  }

  function selectedProfile() {
    if (!profiles.length) return null;
    const key = datasetSel ? String(datasetSel.value || "") : "";
    return profiles.find((p) => p && p.dataset === key) || profiles[0];
  }

  /** 選択 profile を実行対象の供給元へ渡す（既定値の供給元は 1 つ＝run-options の profile）。 */
  function pushProfile() {
    if (subjectSource) subjectSource.setRunProfile(selectedProfile());
  }

  function buildSubmission() {
    // 実行対象は Port ごしに 1 箇所から取る（供給元がどちらの面かをここで見分けない）。
    const derived = subjectSource.derivedBacktest();
    const subject = {
      ea_name: derived.ea_name,
      initial_deposit: derived.initial_deposit,
      settings: subjectSource.buildSettings(),
    };
    return buildBody({ profile: selectedProfile(), subject, inputs: inputs.values() });
  }

  return {
    elements: {},

    mount(host) {
      root = el("div", { id: "simExecPanel", className: "exec-panel" });

      // データセット選択（profile 由来キーの単一ソース・front リテラル 0）。
      const dsWrap = el("label", { className: "exec-field", textContent: "データセット" });
      datasetSel = el("select", { id: "execDataset", className: "exec-dataset" });
      fillOptions(datasetSel, profiles.map((p) => p.dataset));
      // データセットを変えたら Tester パネルの既定値（Symbol/Period/Leverage/Currency）も
      // その profile へ追随させる（既定値の供給元を 1 つに保つ）。
      datasetSel.addEventListener("change", () => { pushProfile(); });
      dsWrap.appendChild(datasetSel);

      submitBtn = el("button", { id: "execSubmit", className: "exec-submit", type: "button", textContent: "投入" });
      submitBtn.addEventListener("click", () => { if (submitCb) submitCb(buildSubmission()); });

      root.appendChild(dsWrap);
      root.appendChild(submitBtn);

      host.appendChild(root);
      this.elements = { root, submitBtn };
      return root;
    },

    /** データセット profile 一覧（{dataset, data_path, symbol, ...11}[]）を注入する。
     *  profile 由来 11 キーの単一ソース。data_path/symbol/period/銘柄仕様を front に持たせない。 */
    setRunOptions(list) {
      profiles = Array.isArray(list) ? list.slice() : [];
      if (datasetSel) fillOptions(datasetSel, profiles.map((p) => p.dataset));
      pushProfile();
    },

    /** 実行対象の供給元（SubjectSource）を結線する（Phase 9 S3）。
     *
     *  合成根が schema の有無で M1 / M4 のどちらか 1 つを渡す。この面は渡された Port を
     *  呼ぶだけで、どちらが来ているかを見分けない（見分けた瞬間に本文の組み立てへ分岐が戻る）。 */
    setSubjectSource(source) {
      subjectSource = source || null;
      pushProfile();
    },

    buildSubmission,

    /** 投入ボタン押下時のコールバックを登録する（このパネルは HTTP を知らない）。 */
    onSubmit(cb) { submitCb = cb; },
  };
}
