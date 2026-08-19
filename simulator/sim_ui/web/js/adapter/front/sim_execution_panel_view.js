// 実行指示パネル（View・Phase 6 F-8 / Phase 9 S1 で UI 出口を絞る）。
//
// 役割: 実行仕様（`backtest`）を組み、投入本文を作る。
//
// 責務（SRP）: DOM の生成と本文の組み立てだけ。fetch はしない（データセット profile は
//   setRunOptions で**注入**する＝合成根が job_submit_client.loadRunOptions から供給）。
//   投入自体も onSubmit コールバックで外へ渡す（このパネルは HTTP を知らない）。
//
// Phase 9 S1（§19.2）: 買い/売り条件の行組み立てと建玉変更の入力欄は **UI 出口として
//   撤去**した。機能そのものは API 面で存続する（`POST /sim/jobs` の第 2 ブロック）。
//   MT5 の対応物を持たない入力を画面から出さない、という裁定に従う。
//
// fake DOM 前提: querySelector は使わず、フィールド要素の参照を JS 側で保持する
//   （fake DOM の querySelector は null を返すため・_fakes.js 実測）。

export function createSimExecutionPanelView({ doc } = {}) {
  let root = null;
  let eaSel = null;
  let slInput = null;
  let tpInput = null;
  let submitBtn = null;
  let datasetSel = null;
  let maPeriodInput = null;
  let maMethodInput = null;
  let depositInput = null;
  let lotInput = null;
  let eaCandidates = [];
  let profiles = [];
  let submitCb = null;
  // Tester Settings パネル（Phase 8・T-4）。結線されている構成では、EA と初期資金の
  // **入力欄はあちらに 1 つだけ**存在し、ここの重複欄は器から外す（同一概念の入力欄は 1 つ）。
  // 未結線（schema を取れない構成）では従来どおりこちらの欄が権威で、本文に settings を
  // 載せない＝旧フォーム投入と byte 等価。
  let testerPanel = null;
  let eaWrap = null;
  let depositWrap = null;

  // profile 由来の 11 キー（build_interactor の銘柄仕様・data_path/symbol/period）。
  // front はこれらのリテラルを持たない（選択 profile からのみ供給）。
  const PROFILE_KEYS = [
    "data_path", "symbol", "period", "contract_size", "digits", "point_size",
    "leverage", "volume_min", "volume_max", "volume_step", "stops_level",
  ];

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

  /** 選択 profile を Tester パネルへ渡す（既定値の供給元は 1 つ＝run-options の profile）。 */
  function pushProfileToTester() {
    if (testerPanel) testerPanel.setRunProfile(selectedProfile());
  }

  function buildSubmission() {
    // EA と初期資金は Tester パネルが結線されていれば **settings 値から導出**する（T-4）。
    // 語幹の切り出し・接尾辞の連結は front で行わない（導出はパネルが schema の label から返す）。
    const derived = testerPanel ? testerPanel.derivedBacktest() : null;
    // フォーム 7 キー（ユーザー入力・既定値は UI フィールド）。
    const backtest = {
      ea_name: derived ? String(derived.ea_name || "") : String(eaSel.value || ""),
      stop_loss_points: Number(slInput.value),
      take_profit_points: Number(tpInput.value),
      ma_period: Math.trunc(Number(maPeriodInput.value)),
      ma_method: String(maMethodInput.value),
      initial_deposit: derived ? Number(derived.initial_deposit) : Number(depositInput.value),
      lot_size: Number(lotInput.value),
    };
    // profile 由来 11 キー（front リテラル 0・選択 profile からのみ）。
    const profile = selectedProfile();
    if (profile) {
      for (const k of PROFILE_KEYS) backtest[k] = profile[k];
      // profile が config_overrides（例 entry_price_basis）を持てば素通しする（front リテラル 0）。
      // データセットの CSV 形式・EA ローダの組合せで既定の建値基準が成立しない場合に profile が
      // 権威供給する（config_overrides は E-5b の任意キー＝build_interactor の同名 param）。
      if (profile.config_overrides) backtest.config_overrides = profile.config_overrides;
    }
    const body = { backtest };
    // Tester Settings（Phase 8 §18 の第 4 ブロック）。未結線なら**キーごと載せない**＝
    // 現行受付・現行実行経路（settings 不在）と同じ本文になる。
    if (testerPanel) body.settings = testerPanel.buildSettings();
    return body;
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
      datasetSel.addEventListener("change", () => { pushProfileToTester(); });
      dsWrap.appendChild(datasetSel);

      eaWrap = el("label", { className: "exec-field", textContent: "指標セット" });
      eaSel = el("select", { id: "execEaName", className: "exec-ea" });
      fillOptions(eaSel, eaCandidates);
      eaWrap.appendChild(eaSel);

      slInput = el("input", { id: "execSl", className: "exec-sl", type: "number", value: "0", min: "0" });
      tpInput = el("input", { id: "execTp", className: "exec-tp", type: "number", value: "0", min: "0" });
      const slWrap = el("label", { className: "exec-field", textContent: "SL(点)" });
      slWrap.appendChild(slInput);
      const tpWrap = el("label", { className: "exec-field", textContent: "TP(点)" });
      tpWrap.appendChild(tpInput);

      // 追加のフォーム項目（ユーザー入力・UI 既定値）。
      maPeriodInput = el("input", { id: "execMaPeriod", className: "exec-maperiod", type: "number", value: "20", min: "1" });
      maMethodInput = el("input", { id: "execMaMethod", className: "exec-mamethod", type: "text", value: "ema" });
      depositInput = el("input", { id: "execDeposit", className: "exec-deposit", type: "number", value: "10000", min: "0" });
      lotInput = el("input", { id: "execLot", className: "exec-lot", type: "number", value: "0.1", min: "0" });
      const maPeriodWrap = el("label", { className: "exec-field", textContent: "MA周期" });
      maPeriodWrap.appendChild(maPeriodInput);
      const maMethodWrap = el("label", { className: "exec-field", textContent: "MA種別" });
      maMethodWrap.appendChild(maMethodInput);
      depositWrap = el("label", { className: "exec-field", textContent: "初期資金" });
      depositWrap.appendChild(depositInput);
      const lotWrap = el("label", { className: "exec-field", textContent: "ロット" });
      lotWrap.appendChild(lotInput);

      submitBtn = el("button", { id: "execSubmit", className: "exec-submit", type: "button", textContent: "投入" });
      submitBtn.addEventListener("click", () => { if (submitCb) submitCb(buildSubmission()); });

      root.appendChild(dsWrap);
      root.appendChild(eaWrap);
      root.appendChild(slWrap);
      root.appendChild(tpWrap);
      root.appendChild(maPeriodWrap);
      root.appendChild(maMethodWrap);
      root.appendChild(depositWrap);
      root.appendChild(lotWrap);
      root.appendChild(submitBtn);

      host.appendChild(root);
      this.elements = { root, eaSel, slInput, tpInput, submitBtn };
      return root;
    },

    /** ea_name（指標セット）候補（string[]）を注入する。 */
    setEaCandidates(list) {
      eaCandidates = Array.isArray(list) ? list.slice() : [];
      if (eaSel) fillOptions(eaSel, eaCandidates);
    },

    /** データセット profile 一覧（{dataset, data_path, symbol, ...11}[]）を注入する。
     *  profile 由来 11 キーの単一ソース。data_path/symbol/period/銘柄仕様を front に持たせない。 */
    setRunOptions(list) {
      profiles = Array.isArray(list) ? list.slice() : [];
      if (datasetSel) fillOptions(datasetSel, profiles.map((p) => p.dataset));
      pushProfileToTester();
    },

    /** Tester Settings パネルを settings の供給元として結線する（Phase 8・T-4）。
     *
     *  結線した時点で、同一概念の重複欄（指標セット＝Expert・初期資金＝Deposit）を器から
     *  外す。残すと「どちらの値で実行されたのか」が画面から判断できなくなる（認知負荷）。
     *  結線しない構成（schema を取れない）では従来の欄がそのまま権威である。 */
    setTesterPanel(panel) {
      testerPanel = panel || null;
      if (!testerPanel) return;
      for (const wrap of [eaWrap, depositWrap]) {
        if (wrap && wrap.parentNode) wrap.parentNode.removeChild(wrap);
      }
      pushProfileToTester();
    },

    buildSubmission,

    /** 投入ボタン押下時のコールバックを登録する（このパネルは HTTP を知らない）。 */
    onSubmit(cb) { submitCb = cb; },
  };
}
