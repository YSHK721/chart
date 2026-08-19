// MT5 Tester Settings 準拠の設定パネル（View・Phase 8 スライス 5・基本設計 §18）。
//
// 役割: `[Tester]` の (キー → 生トークン) Mapping を組む。投入本文の第 4 ブロック
//   `settings: {tester, inputs}` はこのパネルだけが作る。
//
// 責務（SRP）: DOM の生成と Mapping の組み立てだけ。fetch はしない（schema は setSchema で
//   **注入**する＝合成根が settings_schema_client から供給）。検証もしない——規則 B〜Q の
//   単一ソースは framework の `tester_settings_from_mapping` であり、front に第 2 実装を
//   作らない（受付は 400 に rule_id つきで理由を返す）。
//
// **候補を内蔵しない**: 時間足ラベル・`Model` の生値・対象接尾辞・非対象の理由文は 1 つも
//   書かない。すべて `GET /sim/settings-schema` の payload（由来は
//   `usecase/tester_settings/enums.py` と検証層・字句層の宣言）から来る。`setSchema` 前の
//   選択肢は 0 件であり、それが「写しが無い」ことの実証である（構造ガードは
//   `tests/import_source.test.js` の語彙リテラル走査）。
//
// フォームが破らない規則は 1 つだけ（規則 E: 期間の排他）。`Dates` プリセットと
//   `FromDate`/`ToDate` カスタムは同時に送れないため、切替 1 つで出し分ける。他の規則
//   （B・F・K・H …）はサーバの Fail-Stop に委ねる——front で判定を写すと規則が 2 実装になる。
//
// EA inputs（`[TesterInputs]`）欄は出さない（裁定 T-2）。束縛表が空であり、1 行でも指定
//   すれば実行段で必ず設定エラーになる。SL/TP/移動平均/ロットは実行仕様（backtest）が権威。
//
// fake DOM 前提: querySelector は使わず、キーごとに要素参照を JS 側で保持する。

/** 対象種別（規則 D）: 本パネルは Expert テストだけを組む。`Indicator` は出さない。 */
const SUBJECT_KEY = "Expert";
const INDICATOR_KEY = "Indicator";
/** 期間（規則 E）: プリセット 1 キー ⇄ カスタム 2 キーの排他。 */
const PRESET_DATE_KEY = "Dates";
const CUSTOM_DATE_KEYS = ["FromDate", "ToDate"];
/** 空欄なら送らないキー（規則 F: `ForwardMode` がカスタム日付のときだけ要る）。 */
const BLANK_MEANS_ABSENT = ["ForwardDate"];
/** 実行対象データセットとの一致が要求されるキー（写像層 `_require_match` の対象・T-3）。 */
const PROFILE_MATCHED_KEYS = ["Symbol", "Period"];
/** `.ini` キー → 既定値を供給する run profile のフィールド名。
 *  値そのものは profile（＝`SymbolSpecCatalog` 由来）が持つ。ここが持つのは対応だけである。 */
const PROFILE_FIELD_OF_KEY = {
  Symbol: "symbol",
  Period: "period",
  Leverage: "leverage",
  Currency: "settlement_currency",
};
/** profile にもschema にも供給源が無いキーの初期値（フォームの初期表示）。
 *  `Deposit` は移設前の初期資金欄と同じ初期値。`ProfitInPips` / `Visual` は「使わない」側。 */
const INITIAL_SCALARS = { Deposit: "10000", ProfitInPips: "0", Visual: "0" };

/** schema を取れていないときの表示（候補 0 のまま黙らせない）。 */
const NO_SCHEMA_TEXT = "設定 schema を取得できていません（この構成では Tester Settings を投入できません）";

/** `.ini` キー（CamelCase）→ 非対象宣言の field 名（snake_case）。 */
function fieldNameOf(key) {
  return String(key).replace(/([a-z0-9])([A-Z])/g, "$1_$2").toLowerCase();
}

export function createSimTesterSettingsPanelView({ doc } = {}) {
  let root = null;
  let fieldsHost = null;
  let warnNode = null;
  let unsupportedHost = null;
  let emptyNote = null;
  let dateCustom = null;
  let schema = null;
  let profile = null;
  let expertCb = null;
  /** `.ini` キー → 入力要素。 */
  const controls = new Map();
  /** `.ini` キー → 既定値（schema / profile が与えた値）。「動かしたか」の判定に使う。 */
  const defaults = new Map();
  /** Expert の生トークン → EA 名の語幹（接尾辞の切り出しを front でやらない）。 */
  const expertLabels = new Map();

  const el = (tag, props) => {
    const node = doc.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (k === "dataset") Object.assign(node.dataset, v);
      else node[k] = v;
    }
    return node;
  };

  const clear = (host) => {
    for (const child of Array.from((host && host.children) || [])) host.removeChild(child);
  };

  /** 選択肢のあるキーなら [{token,label}]、自由入力なら null。判定は schema だけを見る。 */
  function optionsFor(key) {
    if (key === SUBJECT_KEY) return schema.expert_options || [];
    const enumOptions = (schema.enum_options || {})[key];
    if (enumOptions) return enumOptions;
    const spec = (schema.scalar_specs || {})[key] || {};
    if (spec.proven || spec.provisional) return delayOptions(spec);
    return null;
  }

  /** 実行遅延の候補（実証済み ＋ 暫定）。暫定は TBD をラベルに出す（実証済みに見せない）。 */
  function delayOptions(spec) {
    const proven = (spec.proven || []).map((v) => ({ token: String(v), label: String(v) }));
    const provisional = Object.entries(spec.provisional || {}).map(([token, tbd]) => ({
      token: String(token),
      label: `${token}（${tbd}）`,
    }));
    return [...proven, ...provisional];
  }

  function labelFor(key) {
    const required = (schema.required_keys || []).includes(key);
    return required ? `${key} *` : key;
  }

  function onChanged(key) {
    renderWarnings();
    renderUnsupportedActivation();
    if (key === SUBJECT_KEY && expertCb) expertCb(currentEaName());
  }

  function buildControl(key) {
    const options = optionsFor(key);
    let node;
    if (options) {
      node = el("select", { id: `tester${key}`, className: "tester-input", dataset: { key } });
      for (const option of options) {
        node.appendChild(el("option", { value: option.token, textContent: option.label }));
      }
      node.value = options.length ? options[0].token : "";
    } else {
      node = el("input", {
        id: `tester${key}`, className: "tester-input", type: "text",
        value: INITIAL_SCALARS[key] || "", dataset: { key },
      });
    }
    node.addEventListener("change", () => onChanged(key));
    node.addEventListener("input", () => onChanged(key));
    const wrap = el("label", { className: "tester-field", textContent: labelFor(key) });
    wrap.appendChild(node);
    controls.set(key, node);
    fieldsHost.appendChild(wrap);
  }

  /** 期間形式の切替（規則 E をフォームが破らないための唯一の分岐）。 */
  function buildDateModeToggle() {
    const wrap = el("label", { className: "tester-field", textContent: "期間をカスタム指定する" });
    dateCustom = el("input", {
      id: "testerDateCustom", className: "tester-date-mode", type: "checkbox", checked: false,
    });
    dateCustom.addEventListener("change", () => onChanged(PRESET_DATE_KEY));
    wrap.appendChild(dateCustom);
    fieldsHost.appendChild(wrap);
  }

  function applyProfileDefaults() {
    if (!profile) return;
    for (const [key, field] of Object.entries(PROFILE_FIELD_OF_KEY)) {
      const node = controls.get(key);
      const value = profile[field];
      if (!node || value === undefined || value === null) continue;
      node.value = String(value);
    }
  }

  function snapshotDefaults() {
    defaults.clear();
    for (const [key, node] of controls) defaults.set(key, String(node.value == null ? "" : node.value));
  }

  function noticesForKey(key) {
    const field = fieldNameOf(key);
    return (schema.unsupported || []).filter(
      (notice) => notice.field === field || String(notice.field).startsWith(`${field}.`),
    );
  }

  function renderUnsupported() {
    clear(unsupportedHost);
    for (const notice of (schema && schema.unsupported) || []) {
      const tbd = notice.tbd ? `（${notice.tbd}）` : "";
      unsupportedHost.appendChild(el("div", {
        className: "tester-unsupported-line",
        dataset: { unsupportedId: notice.unsupported_id, active: "0" },
        textContent: `${notice.unsupported_id} ${notice.field}: ${notice.reason}${tbd}`,
      }));
    }
  }

  function renderUnsupportedActivation() {
    const active = new Set(activeUnsupported().map((n) => n.unsupported_id));
    for (const line of unsupportedHost.children || []) {
      line.dataset.active = active.has(line.dataset.unsupportedId) ? "1" : "0";
    }
  }

  function renderWarnings() {
    warnNode.textContent = warnings().join(" / ");
  }

  function rebuild() {
    clear(fieldsHost);
    clear(unsupportedHost);
    controls.clear();
    defaults.clear();
    expertLabels.clear();
    dateCustom = null;
    if (!schema) {
      emptyNote.textContent = NO_SCHEMA_TEXT;
      warnNode.textContent = "";
      return;
    }
    emptyNote.textContent = "";
    for (const option of schema.expert_options || []) {
      expertLabels.set(String(option.token), String(option.label));
    }
    for (const key of schema.key_order || []) {
      if (key === INDICATOR_KEY) continue;   // 規則 D: 本パネルは Expert テスト
      if (key === PRESET_DATE_KEY) buildDateModeToggle();
      buildControl(key);
    }
    applyProfileDefaults();
    snapshotDefaults();
    renderUnsupported();
    renderUnsupportedActivation();
    renderWarnings();
  }

  function currentEaName() {
    const node = controls.get(SUBJECT_KEY);
    return node ? expertLabels.get(String(node.value || "")) || "" : "";
  }

  function warnings() {
    if (!schema || !profile) return [];
    const out = [];
    for (const key of PROFILE_MATCHED_KEYS) {
      const node = controls.get(key);
      if (!node) continue;
      const chosen = String(node.value == null ? "" : node.value);
      const field = PROFILE_FIELD_OF_KEY[key];
      const expected = profile[field] === undefined || profile[field] === null
        ? "" : String(profile[field]);
      if (expected !== "" && chosen !== expected) {
        out.push(
          `${key} が実行対象データセットと一致しません: ${chosen} ≠ ${expected}`
          + "（このまま投入すると実行時に失敗します）",
        );
      }
    }
    return out;
  }

  function activeUnsupported() {
    if (!schema) return [];
    const active = new Set();
    for (const [key, node] of controls) {
      if (String(node.value == null ? "" : node.value) === defaults.get(key)) continue;
      for (const notice of noticesForKey(key)) active.add(notice.unsupported_id);
    }
    return (schema.unsupported || []).filter((n) => active.has(n.unsupported_id));
  }

  function buildTesterMapping() {
    if (!schema) return {};
    const custom = !!(dateCustom && dateCustom.checked);
    const mapping = {};
    for (const key of schema.key_order || []) {
      if (key === INDICATOR_KEY) continue;
      if (key === PRESET_DATE_KEY && custom) continue;
      if (CUSTOM_DATE_KEYS.includes(key) && !custom) continue;
      const node = controls.get(key);
      if (!node) continue;
      const value = String(node.value == null ? "" : node.value);
      if (value === "" && BLANK_MEANS_ABSENT.includes(key)) continue;
      mapping[key] = value;
    }
    return mapping;
  }

  return {
    elements: {},

    mount(host) {
      root = el("div", { id: "simTesterPanel", className: "tester-panel" });
      root.appendChild(el("div", { className: "tester-title", textContent: "Tester Settings" }));
      emptyNote = el("div", { id: "simTesterEmpty", className: "tester-empty", textContent: NO_SCHEMA_TEXT });
      fieldsHost = el("div", { id: "simTesterFields", className: "tester-fields" });
      warnNode = el("div", { id: "simTesterWarn", className: "tester-warn", textContent: "" });
      unsupportedHost = el("div", { id: "simTesterUnsupported", className: "tester-unsupported" });
      root.appendChild(emptyNote);
      root.appendChild(fieldsHost);
      root.appendChild(warnNode);
      root.appendChild(unsupportedHost);
      host.appendChild(root);
      this.elements = { root, fieldsHost, warnNode, unsupportedHost };
      return root;
    },

    /** schema（GET /sim/settings-schema の payload）を注入してフォームを組み直す。 */
    setSchema(payload) {
      schema = payload || null;
      rebuild();
    },

    /** 選択中のデータセット profile を注入する（Symbol/Period/Leverage/Currency の既定値）。 */
    setRunProfile(runProfile) {
      profile = runProfile || null;
      if (!schema) return;
      applyProfileDefaults();
      snapshotDefaults();
      renderUnsupportedActivation();
      renderWarnings();
    },

    buildTesterMapping,

    /** 投入本文の第 4 ブロック。`inputs` は常に空（T-2: EA 入力欄を出さない）。 */
    buildSettings() {
      return { tester: buildTesterMapping(), inputs: [] };
    },

    /** `backtest` へ導出する値（T-4: 同一概念の入力欄を 2 つ持たない）。 */
    derivedBacktest() {
      const deposit = controls.get("Deposit");
      return {
        ea_name: currentEaName(),
        initial_deposit: Number(deposit ? deposit.value : NaN),
      };
    },

    warnings,
    activeUnsupported,

    /** Expert（実行対象 EA）変更時のコールバックを登録する（新 EA 名の語幹を渡す）。 */
    onExpertChange(cb) { expertCb = cb; },
  };
}
