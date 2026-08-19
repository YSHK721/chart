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
// 非対象の該当判定は**宣言駆動**（R-9）: どの選択がどの告知に当たるかは schema が配る
//   `keys` × `trigger`（+`tokens`）だけで決める。キー名から宣言側の field 名を正規表現で
//   再導出したり、「既定値から動かしたか」を該当の代理にしたりしない。前者は綴りが一致
//   しない告知（実ティック・期間窓・実行対象 EA）を**静かに 0 件**にし、後者は profile を
//   選び直しただけで既定が振り直され警告が黙って消える（どちらも実測済みの壊れ方）。
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
/** 実行対象の銘柄キー（実行対象データセットの決定に使う・Phase 9 S4）。 */
const SYMBOL_KEY = "Symbol";
/** 実行対象データセットとの一致が要求されるキー（写像層 `_require_match` の対象・T-3）。 */
const PROFILE_MATCHED_KEYS = [SYMBOL_KEY, "Period"];
/** `.ini` キー → 既定値を供給する run profile のフィールド名。
 *  値そのものは profile（＝`SymbolSpecCatalog` 由来）が持つ。ここが持つのは対応だけである。 */
const PROFILE_FIELD_OF_KEY = {
  Symbol: "symbol",
  Period: "period",
  Leverage: "leverage",
  Currency: "settlement_currency",
};
/** profile にも schema にも供給源が無いキーの初期値（フォームの初期表示）。
 *  `Deposit` は移設前の初期資金欄と同じ初期値。`ProfitInPips` / `Visual` は「使わない」側。 */
const INITIAL_SCALARS = { Deposit: "10000", ProfitInPips: "0", Visual: "0" };

/** schema を取れていないときの表示（候補 0 のまま黙らせない）。 */
const NO_SCHEMA_TEXT = "設定 schema を取得できていません（この構成では Tester Settings を投入できません）";

/** 非対象一覧の開閉トグルの表示文（開いているかを字面でも示す）。 */
const UNSUPPORTED_TOGGLE_TEXT = { collapsed: "非対象の詳細を開く", expanded: "非対象の詳細を閉じる" };

/** 群 → `.ini` キーの割当（**表示メタデータ**・スライス 7）。
 *
 *  ここに置くのは「キー名をどの見出しの下に並べるか」だけである。語彙値（時間足ラベル・
 *  `Model` の生値・対象接尾辞）は 1 つも持たない——値の単一ソースは schema のままである。
 *
 *  **割当表に無いキーは既定群へ落とす**（`DEFAULT_GROUP`）。schema が新しいキーを配ったとき、
 *  ここを直し忘れても UI から**消えない**（表を直せば置き場所だけが変わる＝OCP）。 */
const FIELD_GROUPS = [
  { id: "subject", title: "対象", keys: ["Expert", "Symbol", "Period"] },
  { id: "period", title: "期間", keys: ["Dates", "FromDate", "ToDate", "ForwardMode", "ForwardDate"] },
  { id: "run", title: "実行", keys: ["Model", "ExecutionMode", "Optimization", "OptimizationCriterion", "Visual", "ProfitInPips"] },
  { id: "account", title: "口座", keys: ["Deposit", "Currency", "Leverage"] },
];
/** 割当表に無いキーの落とし先（新キーを黙って捨てないための受け皿）。 */
const DEFAULT_GROUP = { id: "other", title: "その他", keys: [] };

/** キーが属する群の定義を返す（無ければ既定群）。 */
function groupDefOf(key) {
  return FIELD_GROUPS.find((g) => g.keys.includes(key)) || DEFAULT_GROUP;
}

/** 非対象の発火条件（サーバ宣言 `UI_TRIGGER_*` と同一語彙）。front は条件を発明しない。 */
const TRIGGER_ON_TOKENS = "on_tokens";
const TRIGGER_EXCEPT_TOKENS = "except_tokens";
const TRIGGER_ON_PRESENCE = "on_presence";
const TRIGGER_OFF_CANDIDATES = "off_candidates";
const TRIGGER_OFF_PROFILE = "off_profile";

export function createSimTesterSettingsPanelView({ doc } = {}) {
  let root = null;
  let fieldsHost = null;
  let warnNode = null;
  let unsupportedHost = null;
  let unsupportedActiveHost = null;
  let unsupportedToggle = null;
  let emptyNote = null;
  let dateCustom = null;
  let schema = null;
  let profile = null;
  let symbolCb = null;
  /** 実行対象データセットが供給する銘柄候補（Phase 9 S4）。空なら自由入力へ縮退する。 */
  let symbolCandidates = [];
  /** 群 id → その群のフィールド置き場（`.tester-group-fields`）。rebuild ごとに作り直す。 */
  const groupHosts = new Map();
  /** `.ini` キー → 入力要素。 */
  const controls = new Map();
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
    // 銘柄は実行対象データセットが供給する（schema の列挙ではない）。候補が 1 つも無い
    // 構成では自由入力へ落とす——候補を出せないことを理由に投入不能にはしない。
    if (key === SYMBOL_KEY) {
      return symbolCandidates.length
        ? symbolCandidates.map((token) => ({ token, label: token }))
        : null;
    }
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

  /** 群の器（見出し＋フィールド置き場）を作って登録し、フィールド置き場を返す。 */
  function createGroup(def) {
    const section = el("div", { className: "tester-group", dataset: { group: def.id } });
    section.appendChild(el("div", { className: "tester-group-title", textContent: def.title }));
    const host = el("div", { className: "tester-group-fields" });
    section.appendChild(host);
    fieldsHost.appendChild(section);
    groupHosts.set(def.id, host);
    return host;
  }

  /** 描画するキー列から、**中身のある群だけ**を宣言順に先に並べる。
   *  先に並べないと群の順序が key_order の出現順になり、見出しの並びが schema 依存で揺れる。 */
  function prepareGroups(keys) {
    const present = new Set(keys);
    for (const def of FIELD_GROUPS) {
      if (def.keys.some((k) => present.has(k))) createGroup(def);
    }
  }

  /** キーの群のフィールド置き場を返す（割当表に無いキーは既定群を末尾に作って落とす）。 */
  function groupHostFor(key) {
    const def = groupDefOf(key);
    return groupHosts.get(def.id) || createGroup(def);
  }

  function labelFor(key) {
    const required = (schema.required_keys || []).includes(key);
    return required ? `${key} *` : key;
  }

  function onChanged(key) {
    renderWarnings();
    renderUnsupportedActivation();
    // 銘柄を変えたら外へ通知する（実行対象データセットの決め直しは合成根が担う）。
    if (key === SYMBOL_KEY && symbolCb) symbolCb(selectedSymbol());
  }

  function buildControl(key) {
    const options = optionsFor(key);
    let node;
    if (options) {
      node = el("select", {
        id: `tester${key}`, className: "tester-input", dataset: { key, mt5: `tester:${key}` },
      });
      for (const option of options) {
        node.appendChild(el("option", { value: option.token, textContent: option.label }));
      }
      node.value = options.length ? options[0].token : "";
    } else {
      node = el("input", {
        id: `tester${key}`, className: "tester-input", type: "text",
        value: INITIAL_SCALARS[key] || "", dataset: { key, mt5: `tester:${key}` },
      });
    }
    node.addEventListener("change", () => onChanged(key));
    node.addEventListener("input", () => onChanged(key));
    const wrap = el("label", { className: "tester-field", textContent: labelFor(key) });
    wrap.appendChild(node);
    controls.set(key, node);
    groupHostFor(key).appendChild(wrap);
  }

  /** 期間形式の切替（規則 E をフォームが破らないための唯一の分岐）。 */
  function buildDateModeToggle() {
    const wrap = el("label", { className: "tester-field", textContent: "期間をカスタム指定する" });
    dateCustom = el("input", {
      id: "testerDateCustom", className: "tester-date-mode", type: "checkbox", checked: false,
      dataset: { mt5: "ui:date-mode" },
    });
    dateCustom.addEventListener("change", () => onChanged(PRESET_DATE_KEY));
    wrap.appendChild(dateCustom);
    // 切替は「何を出し分けるか」の対象（期間キー）と同じ群に置く（分岐と対象を離さない）。
    groupHostFor(PRESET_DATE_KEY).appendChild(wrap);
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

  /** キー K の現在値（未生成なら null）。 */
  function currentToken(key) {
    const node = controls.get(key);
    return node ? String(node.value == null ? "" : node.value) : null;
  }

  /** キー K に対して配られた候補トークン（自由入力なら空）。 */
  function offeredTokens(key) {
    const node = controls.get(key);
    // 実 DOM の `children` は HTMLCollection＝Array メソッドを持たない（fake DOM は配列
    // なので単体では露見しない・ISSUE-425 実測）。必ず Array.from を経由する。
    return Array.from((node && node.children) || []).map((option) => String(option.value));
  }

  /** キー K の権威値（実行対象データセットが供給する値。無ければ null）。 */
  function profileAuthority(key) {
    const field = PROFILE_FIELD_OF_KEY[key];
    const value = profile && field ? profile[field] : null;
    return value === undefined || value === null ? null : String(value);
  }

  /** 告知 N がキー K で発火するか。**判定は宣言（trigger/tokens）だけで行う**。 */
  function triggeredOn(notice, key, submitted) {
    const token = currentToken(key);
    const tokens = notice.tokens || [];
    switch (notice.trigger) {
      case TRIGGER_ON_PRESENCE:
        return Object.prototype.hasOwnProperty.call(submitted, key);
      case TRIGGER_ON_TOKENS:
        return token !== null && tokens.includes(token);
      case TRIGGER_EXCEPT_TOKENS:
        return token !== null && !tokens.includes(token);
      case TRIGGER_OFF_CANDIDATES:
        return token !== null && !offeredTokens(key).includes(token);
      case TRIGGER_OFF_PROFILE: {
        const authority = profileAuthority(key);
        return token !== null && authority !== null && token !== authority;
      }
      default:
        // 生トークンでは判定できないと宣言された告知（構造不変条件の防壁）。
        // 「動かしたら光らせる」等の代理判定を置かない（過剰発火は警告を無意味にする）。
        return false;
    }
  }

  /** 全一覧の開閉状態を DOM へ書く（実際の隠し方は CSS が持つ）。 */
  function setUnsupportedExpanded(open) {
    unsupportedHost.dataset.expanded = open ? "1" : "0";
    unsupportedToggle.textContent = open
      ? UNSUPPORTED_TOGGLE_TEXT.expanded : UNSUPPORTED_TOGGLE_TEXT.collapsed;
  }

  /** 告知 1 件の表示文（全一覧と常時表示で同じ 1 箇所から作る）。 */
  function noticeText(notice) {
    const tbd = notice.tbd ? `（${notice.tbd}）` : "";
    return `${notice.unsupported_id} ${notice.field}: ${notice.reason}${tbd}`;
  }

  function renderUnsupported() {
    clear(unsupportedHost);
    for (const notice of (schema && schema.unsupported) || []) {
      unsupportedHost.appendChild(el("div", {
        className: "tester-unsupported-line",
        dataset: { unsupportedId: notice.unsupported_id, active: "0" },
        textContent: noticeText(notice),
      }));
    }
  }

  function renderUnsupportedActivation() {
    const active = activeUnsupported();
    const activeIds = new Set(active.map((n) => n.unsupported_id));
    for (const line of unsupportedHost.children || []) {
      line.dataset.active = activeIds.has(line.dataset.unsupportedId) ? "1" : "0";
    }
    // 現在値が該当する告知だけを**畳まずに**出す。該当が無ければ 0 件＝壁を出さない。
    // 該当集合は `activeUnsupported()`（schema.unsupported の field 宣言）が決める。
    clear(unsupportedActiveHost);
    for (const notice of active) {
      unsupportedActiveHost.appendChild(el("div", {
        className: "tester-unsupported-active-line",
        dataset: { unsupportedId: notice.unsupported_id },
        textContent: noticeText(notice),
      }));
    }
  }

  function renderWarnings() {
    warnNode.textContent = warnings().join(" / ");
  }

  function rebuild() {
    clear(fieldsHost);
    clear(unsupportedHost);
    clear(unsupportedActiveHost);
    // 開く中身の件数をトグルへ書く（0 件なら CSS が消す＝押しても何も出ないボタンを残さない）。
    // schema を取れない構成でも必ず通る位置に置く（下の早期 return より前）。
    unsupportedToggle.dataset.count = String(((schema && schema.unsupported) || []).length);
    groupHosts.clear();
    controls.clear();
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
    const renderedKeys = (schema.key_order || []).filter((k) => k !== INDICATOR_KEY);
    prepareGroups(renderedKeys);   // 規則 D: 本パネルは Expert テスト（Indicator は出さない）
    for (const key of renderedKeys) {
      if (key === PRESET_DATE_KEY) buildDateModeToggle();
      buildControl(key);
    }
    applyProfileDefaults();
    renderUnsupported();
    renderUnsupportedActivation();
    renderWarnings();
  }

  /** 実行対象の銘柄（未生成なら空文字）。 */
  function selectedSymbol() {
    const token = currentToken(SYMBOL_KEY);
    return token === null ? "" : token;
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
    // `on_presence` は「実際に投入本文へ載るか」で決まるため、組み上がった Mapping を見る
    // （期間形式の排他・空欄の非搭載といった出し分けを二重に実装しない）。
    const submitted = buildTesterMapping();
    return (schema.unsupported || []).filter((notice) =>
      (notice.keys || []).some((key) => triggeredOn(notice, key, submitted)));
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
      // 現在値に効いている告知の常時表示席（畳まない）。全一覧より前に置く。
      unsupportedActiveHost = el("div", {
        id: "simTesterUnsupportedActive", className: "tester-unsupported-active",
      });
      // 開閉の状態は DOM 属性 1 つ（`data-expanded`）が持ち、実際の隠し方は CSS が決める
      // （View は「開いているか」だけを言う）。初期状態は下の `setUnsupportedExpanded(false)`
      // が 1 箇所で決める（属性と字面を別々に初期化しない）。
      unsupportedHost = el("div", { id: "simTesterUnsupported", className: "tester-unsupported" });
      root.appendChild(emptyNote);
      root.appendChild(fieldsHost);
      // 全一覧の開閉（既定は畳んだまま）。押した本人だけが開く＝自動で開かない。
      unsupportedToggle = el("button", {
        id: "simTesterUnsupportedToggle", className: "tester-unsupported-toggle", type: "button",
        dataset: { mt5: "ui:unsupported-toggle" },
      });
      unsupportedToggle.addEventListener("click", () => setUnsupportedExpanded(
        unsupportedHost.dataset.expanded !== "1",
      ));
      setUnsupportedExpanded(false);
      root.appendChild(warnNode);
      root.appendChild(unsupportedActiveHost);
      root.appendChild(unsupportedToggle);
      root.appendChild(unsupportedHost);
      host.appendChild(root);
      this.elements = { root, fieldsHost, warnNode, unsupportedActiveHost, unsupportedHost };
      return root;
    },

    /** schema（GET /sim/settings-schema の payload）を注入してフォームを組み直す。 */
    setSchema(payload) {
      schema = payload || null;
      rebuild();
    },

    /** 銘柄候補（run-options の datasets 由来）を注入する（Phase 9 S4）。
     *
     *  schema より**先に**渡すこと（schema 注入時の組み直しで候補が使われる）。schema が
     *  既にある状態で渡した場合はフォームを組み直す（入力中の値は初期値へ戻る）。 */
    setSymbolCandidates(list) {
      symbolCandidates = Array.isArray(list) ? list.map((v) => String(v)) : [];
      if (schema) rebuild();
    },

    /** 選択中のデータセット profile を注入する（Symbol/Period/Leverage/Currency の既定値）。 */
    setRunProfile(runProfile) {
      profile = runProfile || null;
      if (!schema) return;
      applyProfileDefaults();
      renderUnsupportedActivation();
      renderWarnings();
    },

    buildTesterMapping,

    /** 投入本文の第 4 ブロック。`inputs` は常に空（T-2: EA 入力欄を出さない）。
     *  schema を取れていなければ**組まない**（null）——空の設定ブロックを載せると、候補 0 の
     *  Expert から投入不能な本文が出来る（Phase 8 で実測した壊れ方）。 */
    buildSettings() {
      if (!schema) return null;
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

    selectedSymbol,

    /** 銘柄変更時のコールバックを登録する（新しい銘柄を渡す）。 */
    onSymbolChange(cb) { symbolCb = cb; },

    warnings,
    activeUnsupported,

  };
}
