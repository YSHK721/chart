// 実行指示パネル（View・Phase 6 F-8・TBD-11）。
//
// 役割: ea_name（指標セット選択）・SL/TP 点数・entry_long/entry_short の条件行を組み、
//   投入本文 {backtest, strategy} を作る。条件は TBD-11（比較演算 {">","<"} ＋ AND 連鎖 ＋
//   履歴参照 [bar-shift]）に限る。OR/グループ化/>=,<=,==/インジケーター駆動 SL/TP は**出さない**。
//
// 責務（SRP）: DOM の生成と本文の組み立てだけ。fetch はしない（指標候補は
//   setIndicatorCandidates で**注入**する＝合成根が job_submit_client.loadEaSeries から供給）。
//   ea_name（指標セット）変更は onEaChange で外へ通知し、合成根が選択 EA の系列を取り直す。
//   投入自体も onSubmit コールバックで外へ渡す（このパネルは HTTP を知らない）。
//
// rhs について: パネル UI は rhs を**定数**に限定する（数値入力 1 つ）。spec スキーマ／loader は
//   rhs={indicator,shift}（指標同士の比較）も受けるが、UI では露出しない（YAGNI・認知負荷）。
//   指標同士の比較が必要になった時点で行を拡張する（backend は既に対応済み）。
//
// fake DOM 前提: querySelector は使わず、行ごとにフィールド要素の参照を JS 側で保持する
//   （fake DOM の querySelector は null を返すため・_fakes.js 実測）。

const OPS = Object.freeze([">", "<"]);
// トレーリング評価粒度（バー確定毎 / tick 毎）。spec スキーマと 1:1（backend loader の
// Literal["bar","tick"] と同集合）。UI からはこの 2 値のみを露出する（TBD-01 確定）。
const TRAIL_GRANS = Object.freeze(["bar", "tick"]);

export function createSimExecutionPanelView({ doc } = {}) {
  let root = null;
  let longHost = null;
  let shortHost = null;
  let eaSel = null;
  let slInput = null;
  let tpInput = null;
  let submitBtn = null;
  let datasetSel = null;
  let maPeriodInput = null;
  let maMethodInput = null;
  let depositInput = null;
  let lotInput = null;
  // 建玉変更（トレーリング FR-07 / 部分決済 FR-08・Phase 7）。既定 OFF＝トグル未チェックで
  // spec に載せない（Phase 6 本文と byte 等価）。
  let trailingOn = null;
  let trailGranSel = null;
  let trailTriggerInput = null;
  let trailDistanceInput = null;
  let trailStepInput = null;
  let partialOn = null;
  let partialTriggerInput = null;
  let partialFractionInput = null;
  let candidates = [];
  let eaCandidates = [];
  let profiles = [];
  let submitCb = null;
  let eaChangeCb = null;
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
  // 行の内部台帳（side ごと）。各行は {node, indicatorEl, shiftEl, opEl, rhsEl}。
  const rows = { long: [], short: [] };

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

  function makeRow(side) {
    const row = el("div", { className: "exec-cond-row", dataset: { side } });
    const indicatorEl = el("select", { className: "exec-ind" });
    fillOptions(indicatorEl, candidates);
    const shiftEl = el("input", { className: "exec-shift", type: "number", value: "0", min: "0" });
    const opEl = el("select", { className: "exec-op" });
    for (const op of OPS) opEl.appendChild(option(op));
    opEl.value = OPS[0];
    const rhsEl = el("input", { className: "exec-rhs", type: "number", value: "0" });
    const delBtn = el("button", { className: "exec-del", type: "button", textContent: "削除" });

    const entry = { node: row, indicatorEl, shiftEl, opEl, rhsEl };
    delBtn.addEventListener("click", () => removeRow(side, entry));

    row.appendChild(indicatorEl);
    row.appendChild(shiftEl);
    row.appendChild(opEl);
    row.appendChild(rhsEl);
    row.appendChild(delBtn);
    return entry;
  }

  function addCondition(side) {
    const host = side === "short" ? shortHost : longHost;
    const entry = makeRow(side);
    rows[side].push(entry);
    host.appendChild(entry.node);
    return entry.node;
  }

  function removeRow(side, entry) {
    const i = rows[side].indexOf(entry);
    if (i < 0) return;
    rows[side].splice(i, 1);
    // 実 DOM の親参照は parentNode（非標準の .parent は実ブラウザで undefined＝行が消えず
    // 表示と投入内容が乖離する・sim_segment_view.js:39 と同 idiom）。
    if (entry.node.parentNode) entry.node.parentNode.removeChild(entry.node);
  }

  function sideConditions(side) {
    return rows[side].map((r) => ({
      indicator: String(r.indicatorEl.value),
      shift: Math.trunc(Number(r.shiftEl.value)),
      op: String(r.opEl.value),
      rhs: Number(r.rhsEl.value),
    }));
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
    const strategy = {};
    if (rows.long.length) strategy.entry_long = sideConditions("long");
    if (rows.short.length) strategy.entry_short = sideConditions("short");
    // 建玉変更（Phase 7）: トグル ON のときだけ載せる。未チェック（既定 OFF）は不在＝
    // backend の body.get("strategy").get("trailing") が None＝既存挙動と byte 等価。
    if (trailingOn && trailingOn.checked) {
      strategy.trailing = {
        granularity: String(trailGranSel.value),
        trigger_points: Number(trailTriggerInput.value),
        distance_points: Number(trailDistanceInput.value),
        step_points: Number(trailStepInput.value),
      };
    }
    if (partialOn && partialOn.checked) {
      strategy.partial_close = {
        trigger: { profit_points: Number(partialTriggerInput.value) },
        close_fraction: Number(partialFractionInput.value),
      };
    }
    const body = { backtest };
    // 両側とも空なら strategy を丸ごと省く（OFF＝既存 2 キー本文と byte 等価）。
    if (Object.keys(strategy).length) body.strategy = strategy;
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
      // ea_name（指標セット）を変えたら、その EA の系列を候補へ取り直すよう外へ通知する
      // （このパネルは HTTP を知らない＝候補の再取得は合成根が担う）。
      eaSel.addEventListener("change", () => { if (eaChangeCb) eaChangeCb(String(eaSel.value || "")); });
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

      const longBlock = el("div", { className: "exec-side", dataset: { side: "long" } });
      longBlock.appendChild(el("div", { className: "exec-side-title", textContent: "買い条件（AND）" }));
      longHost = el("div", { id: "execLongRows", className: "exec-rows" });
      const addLong = el("button", { id: "execAddLong", className: "exec-add", type: "button", textContent: "条件追加" });
      addLong.addEventListener("click", () => addCondition("long"));
      longBlock.appendChild(longHost);
      longBlock.appendChild(addLong);

      const shortBlock = el("div", { className: "exec-side", dataset: { side: "short" } });
      shortBlock.appendChild(el("div", { className: "exec-side-title", textContent: "売り条件（AND）" }));
      shortHost = el("div", { id: "execShortRows", className: "exec-rows" });
      const addShort = el("button", { id: "execAddShort", className: "exec-add", type: "button", textContent: "条件追加" });
      addShort.addEventListener("click", () => addCondition("short"));
      shortBlock.appendChild(shortHost);
      shortBlock.appendChild(addShort);

      // 建玉変更（トレーリング FR-07 / 部分決済 FR-08・Phase 7）。既定 OFF（トグル未チェック）
      // ＝spec に載せない＝Phase 6 本文と byte 等価。ON のときだけ各フィールドを読む。
      const pcBlock = el("div", { className: "exec-side", dataset: { side: "position-change" } });
      pcBlock.appendChild(el("div", { className: "exec-side-title", textContent: "建玉変更（任意・既定 OFF）" }));

      const trailToggleWrap = el("label", { className: "exec-field", textContent: "トレーリング ON" });
      trailingOn = el("input", { id: "execTrailingOn", className: "exec-trailing-on", type: "checkbox", checked: false });
      trailToggleWrap.appendChild(trailingOn);
      trailGranSel = el("select", { id: "execTrailGran", className: "exec-trail-gran" });
      fillOptions(trailGranSel, TRAIL_GRANS);
      const trailGranWrap = el("label", { className: "exec-field", textContent: "粒度" });
      trailGranWrap.appendChild(trailGranSel);
      trailTriggerInput = el("input", { id: "execTrailTrigger", className: "exec-trail-trigger", type: "number", value: "0", min: "0" });
      const trailTriggerWrap = el("label", { className: "exec-field", textContent: "作動益(点)" });
      trailTriggerWrap.appendChild(trailTriggerInput);
      trailDistanceInput = el("input", { id: "execTrailDistance", className: "exec-trail-distance", type: "number", value: "150", min: "1" });
      const trailDistanceWrap = el("label", { className: "exec-field", textContent: "距離(点)" });
      trailDistanceWrap.appendChild(trailDistanceInput);
      trailStepInput = el("input", { id: "execTrailStep", className: "exec-trail-step", type: "number", value: "0", min: "0" });
      const trailStepWrap = el("label", { className: "exec-field", textContent: "刻み(点)" });
      trailStepWrap.appendChild(trailStepInput);

      const partialToggleWrap = el("label", { className: "exec-field", textContent: "部分決済 ON" });
      partialOn = el("input", { id: "execPartialOn", className: "exec-partial-on", type: "checkbox", checked: false });
      partialToggleWrap.appendChild(partialOn);
      partialTriggerInput = el("input", { id: "execPartialTrigger", className: "exec-partial-trigger", type: "number", value: "0", min: "0" });
      const partialTriggerWrap = el("label", { className: "exec-field", textContent: "作動益(点)" });
      partialTriggerWrap.appendChild(partialTriggerInput);
      partialFractionInput = el("input", { id: "execPartialFraction", className: "exec-partial-fraction", type: "number", value: "0.5", min: "0", max: "1", step: "0.05" });
      const partialFractionWrap = el("label", { className: "exec-field", textContent: "決済割合" });
      partialFractionWrap.appendChild(partialFractionInput);

      pcBlock.appendChild(trailToggleWrap);
      pcBlock.appendChild(trailGranWrap);
      pcBlock.appendChild(trailTriggerWrap);
      pcBlock.appendChild(trailDistanceWrap);
      pcBlock.appendChild(trailStepWrap);
      pcBlock.appendChild(partialToggleWrap);
      pcBlock.appendChild(partialTriggerWrap);
      pcBlock.appendChild(partialFractionWrap);

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
      root.appendChild(longBlock);
      root.appendChild(shortBlock);
      root.appendChild(pcBlock);
      root.appendChild(submitBtn);

      host.appendChild(root);
      this.elements = { root, eaSel, slInput, tpInput, submitBtn };
      return root;
    },

    /** 指標候補（string[]）を注入する。既存行の指標セレクタも作り直す（候補の単一ソース）。 */
    setIndicatorCandidates(list) {
      candidates = Array.isArray(list) ? list.slice() : [];
      for (const side of ["long", "short"]) {
        for (const r of rows[side]) fillOptions(r.indicatorEl, candidates);
      }
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

    addCondition,
    buildSubmission,

    /** 投入ボタン押下時のコールバックを登録する（このパネルは HTTP を知らない）。 */
    onSubmit(cb) { submitCb = cb; },

    /** ea_name（指標セット）変更時のコールバックを登録する（新 ea_name を渡す）。 */
    onEaChange(cb) { eaChangeCb = cb; },
  };
}
