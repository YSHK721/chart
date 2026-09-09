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
// 行構成は MT5 ストラテジーテスター「設定」タブと同期する（依頼者指示 2026-09-06・
//   参照 `.doc/ss20260906192940.jpg`＝行の並びとラベル、`.doc/ss20260906195130.jpg`＝
//   活性/不活性の実画面）。ここに置く行割当・行ラベル・チェックボックス文言は
//   **表示メタデータ**であり、語彙値（トークン・選択肢）は 1 つも持たない。
//   MT5 の IDE ボタン・銘柄仕様ボタン・スリッページエミュレート切替は `.ini` キーを
//   持たない（投入本文に写らない）ため出さない（受け口だけの死んだ操作を作らない）。
//
// 欄の有効/無効（活性）は**宣言駆動**: schema の `activation`（規則 B/F と MT5 実画面が
//   根拠・サーバ宣言）を評価するだけで、front は規則の第 2 実装を持たない。期間の排他
//   （規則 E）だけは UI 専用の「カスタム期間」選択肢が切替を担う（`Dates` プリセットと
//   `FromDate`/`ToDate` は同時に送れない）。
//
// 非対象の該当判定は**宣言駆動**（R-9）: どの選択がどの告知に当たるかは schema が配る
//   `keys` × `trigger`（+`tokens`）だけで決める。キー名から宣言側の field 名を正規表現で
//   再導出したり、「既定値から動かしたか」を該当の代理にしたりしない。
//
// EA inputs（`[TesterInputs]`）欄は出さない（裁定 T-2）。
//
// fake DOM 前提: querySelector は使わず、キーごとに要素参照を JS 側で保持する。

import { createSimDatePickerView } from "./sim_date_picker_view.js";

/** 対象種別（規則 D）: 本パネルは Expert テストだけを組む。`Indicator` は出さない。 */
const SUBJECT_KEY = "Expert";
const INDICATOR_KEY = "Indicator";
/** 期間（規則 E）: プリセット 1 キー ⇄ カスタム 2 キーの排他。 */
const PRESET_DATE_KEY = "Dates";
const CUSTOM_DATE_KEYS = ["FromDate", "ToDate"];
/** フォワード分割（規則 F）: 分割比は schema の `ForwardMode` 選択肢が配る宣言
 *  （enums `FORWARD_MODE_SPLIT_DENOMINATORS` 由来・表示専用）。 */
const FORWARD_MODE_KEY = "ForwardMode";
const FORWARD_DATE_KEY = "ForwardDate";
/** 空欄なら送らないキー（規則 F: `ForwardMode` がカスタム日付のときだけ要る）。 */
const BLANK_MEANS_ABSENT = [FORWARD_DATE_KEY];
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

/** 期間カスタム形（規則 E の `FromDate`+`ToDate` 形）を選ぶ **UI 専用**の選択肢。
 *  MT5 の「日付」ドロップダウンと同じく、プリセットの並びの末尾に置く（実画面準拠）。
 *  このトークンは投入本文に**決して載らない**（`.ini` の語彙ではない）。 */
export const CUSTOM_RANGE_OPTION = { token: "__custom_range__", label: "期間指定" };

/** MT5「設定」タブの行構成（**表示メタデータ**・出典 `.doc/ss20260906192940.jpg`）。
 *
 *  ここに置くのは「どのキーをどの行にどのラベルで並べるか」だけである。語彙値は持たない。
 *  **割当表に無いキーは既定行へ落とす**（`DEFAULT_ROW`）。schema が新しいキーを配ったとき、
 *  ここを直し忘れても UI から**消えない**（表を直せば置き場所だけが変わる＝OCP）。 */
const MT5_ROWS = [
  { id: "expert", label: "エキスパート", keys: ["Expert"] },
  { id: "symbol", label: "銘柄", keys: ["Symbol", "Period"] },
  { id: "dates", label: "日付", keys: ["Dates", "FromDate", "ToDate"] },
  { id: "forward", label: "フォワードテスト", keys: ["ForwardMode", "ForwardDate"] },
  { id: "delay", label: "延滞", keys: ["ExecutionMode"] },
  { id: "model", label: "モデル", keys: ["Model", "ProfitInPips"] },
  { id: "deposit", label: "入金", keys: ["Deposit", "Currency", "Leverage"], note: "レバレッジ" },
  { id: "optimize", label: "オプティマイズ", keys: ["Optimization", "OptimizationCriterion", "Visual"] },
];
/** 割当表に無いキーの落とし先（新キーを黙って捨てないための受け皿）。 */
const DEFAULT_ROW = { id: "other", label: "その他", keys: [] };

/** 旗キー（チェックボックス）の説明文（表示メタデータ・出典は上記 MT5 実画面）。 */
const FLAG_TEXTS = {
  ProfitInPips: "より高速計算のためのピップ単位利益",
  Visual: "チャート、指標、取引を表示するビジュアルモード",
};

/** 不活性のとき**隠す**キー（MT5 実画面で最適化が無効のとき出ない・他はグレーアウト）。 */
const HIDDEN_WHEN_INACTIVE = ["OptimizationCriterion"];

/** レバレッジの MT5 表示（`1:10`）。値そのもの（`10`）は `.ini` トークンのまま。 */
const LEVERAGE_KEY = "Leverage";

/** 非対象の発火条件（サーバ宣言 `UI_TRIGGER_*` と同一語彙）。front は条件を発明しない。
 *  `on_tokens` / `except_tokens` は欄の活性宣言（schema.activation）でも同じ意味で使う。 */
const TRIGGER_ON_TOKENS = "on_tokens";
const TRIGGER_EXCEPT_TOKENS = "except_tokens";
const TRIGGER_ON_PRESENCE = "on_presence";
const TRIGGER_OFF_CANDIDATES = "off_candidates";
const TRIGGER_OFF_PROFILE = "off_profile";

/** キーが属する行の定義を返す（無ければ既定行）。 */
function rowDefOf(key) {
  return MT5_ROWS.find((r) => r.keys.includes(key)) || DEFAULT_ROW;
}

/** 1 日のミリ秒数（UTC 日付トークンの日数演算用）。 */
const DAY_MS = 86400000;

/** `.ini` 日付トークン `YYYY.MM.DD` → UTC ミリ秒（形が崩れていれば null）。 */
function utcOfDateToken(token) {
  const parts = String(token || "").split(".");
  if (parts.length !== 3 || parts.some((p) => !/^\d+$/.test(p))) return null;
  const ms = Date.UTC(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
  return Number.isFinite(ms) ? ms : null;
}

/** フォワード分割の開始日トークン（表示専用・計算できなければ null）。
 *
 *  セマンティクス: フォワード期間＝設定期間の**後ろ** 1/denominator（MT5 公式ヘルプ＋
 *  保存 ini ファイル名の実測 `.doc/mt5_options/` 2026-09-06 で分母の対応を実証）。
 *  日単位の丸めは MT5 未実測のため**暫定**: フォワード日数 = floor(期間日数 / 分母)、
 *  開始日 = To − フォワード日数。実測で確定したらこの 1 箇所だけを直す。
 *  期間長に依らず固定回数の演算で求める（走査しない・計算量テストで固定）。 */
export function computeForwardSplitDate(fromToken, toToken, denominator) {
  const from = utcOfDateToken(fromToken);
  const to = utcOfDateToken(toToken);
  if (from === null || to === null || from > to) return null;
  if (!Number.isInteger(denominator) || denominator < 2) return null;
  const spanDays = Math.round((to - from) / DAY_MS);
  const start = new Date(to - Math.floor(spanDays / denominator) * DAY_MS);
  const pad2 = (n) => String(n).padStart(2, "0");
  return `${start.getUTCFullYear()}.${pad2(start.getUTCMonth() + 1)}.${pad2(start.getUTCDate())}`;
}

export function createSimTesterSettingsPanelView({ doc, today } = {}) {
  /** 日付キーのカレンダー（依頼者参照デザイン 2026-09-06）。欄の値は `.ini` の日付トークン
   *  `YYYY.MM.DD` そのもの（変換層を挟まない・手入力も同じ形）。 */
  const picker = createSimDatePickerView({ doc, today });
  /** カレンダーを開いているキー（トグル判定用）。 */
  let pickerKey = null;
  let root = null;
  let fieldsHost = null;
  let warnNode = null;
  let unsupportedHost = null;
  let unsupportedActiveHost = null;
  let unsupportedToggle = null;
  let emptyNote = null;
  let schema = null;
  let profile = null;
  let symbolCb = null;
  /** 実行対象データセットが供給する銘柄候補（Phase 9 S4）。空なら自由入力へ縮退する。 */
  let symbolCandidates = [];
  /** 行 id → その行の控え置き場（`.tester-row-controls`）。rebuild ごとに作り直す。 */
  const rowHosts = new Map();
  /** `.ini` キー → 入力要素。 */
  const controls = new Map();
  /** `.ini` キー → 欄を包む見た目の箱（日付箱・レバレッジ箱など。無いキーは登録しない）。 */
  const controlBoxes = new Map();
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

  /** キー K の値型（schema の宣言だけを見る。キー名から推測しない）。 */
  function valueTypeOf(key) {
    return (((schema && schema.scalar_specs) || {})[key] || {}).value_type || "";
  }
  const isDateKey = (key) => valueTypeOf(key) === "date";
  const isFlagKey = (key) => valueTypeOf(key) === "flag";

  /** 控え 1 個の現在値（チェックボックスは 0/1 の生トークンへ写す）。 */
  function valueOf(node) {
    if (node.type === "checkbox") return node.checked ? "1" : "0";
    return String(node.value == null ? "" : node.value);
  }

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

  /** 実行遅延の候補（実証済み ＋ 暫定）。表示は schema が配るラベル（MT5 実測写像）・
   *  無ければ生値表記。暫定は TBD をラベルに出す（実証済みに見せない）。 */
  function delayOptions(spec) {
    const labels = spec.labels || {};
    const labelOf = (token) => labels[token] || String(token);
    const proven = (spec.proven || []).map((v) => ({
      token: String(v), label: labelOf(String(v)),
    }));
    const provisional = Object.entries(spec.provisional || {}).map(([token, tbd]) => ({
      token: String(token),
      label: `${labelOf(token)}（${tbd}）`,
    }));
    return [...proven, ...provisional];
  }

  /** 行の器（ラベル＋控え置き場＋任意の後置注記）を作って登録し、控え置き場を返す。 */
  function createRow(def) {
    const row = el("div", { className: "tester-row", dataset: { row: def.id } });
    row.appendChild(el("div", { className: "tester-row-label", textContent: def.label }));
    const host = el("div", { className: "tester-row-controls" });
    row.appendChild(host);
    if (def.note) row.appendChild(el("span", { className: "tester-row-note", textContent: def.note }));
    fieldsHost.appendChild(row);
    rowHosts.set(def.id, host);
    return host;
  }

  /** 描画するキー列から、**中身のある行だけ**を MT5 の行順に先に並べる。 */
  function prepareRows(keys) {
    const present = new Set(keys);
    for (const def of MT5_ROWS) {
      if (def.keys.some((k) => present.has(k))) createRow(def);
    }
  }

  /** キーの行の控え置き場を返す（割当表に無いキーは既定行を末尾に作って落とす）。 */
  function rowHostFor(key) {
    const def = rowDefOf(key);
    return rowHosts.get(def.id) || createRow(def);
  }

  function onChanged(key) {
    applyActivation();
    // 期間プリセットを選び直したら、解決済み期間の表示を出し直す（期間指定なら触らない）。
    if (key === PRESET_DATE_KEY) applyPresetRangeDisplay();
    // 期間かフォワード種別が動いたときだけ分割日を引き直す（無関係な欄で発行しない＝
    // 発行した計算はすべて表示に使う。プリセット表示の書き戻しの**後**に置く）。
    if (key === FORWARD_MODE_KEY || key === PRESET_DATE_KEY || CUSTOM_DATE_KEYS.includes(key)) {
      applyForwardSplitDisplay();
    }
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
      // 期間プリセットの末尾に UI 専用の「カスタム期間」を足す（MT5 の日付ドロップダウンと
      // 同形・規則 E の切替）。このトークンは buildTesterMapping が決して送らない。
      if (key === PRESET_DATE_KEY) {
        node.appendChild(el("option", {
          value: CUSTOM_RANGE_OPTION.token, textContent: CUSTOM_RANGE_OPTION.label,
        }));
      }
      node.value = options.length ? options[0].token : "";
    } else if (isFlagKey(key)) {
      node = el("input", {
        id: `tester${key}`, className: "tester-flag", type: "checkbox",
        checked: (INITIAL_SCALARS[key] || "0") === "1", dataset: { key, mt5: `tester:${key}` },
      });
    } else {
      node = el("input", {
        id: `tester${key}`, className: "tester-input", type: "text",
        value: INITIAL_SCALARS[key] || "", dataset: { key, mt5: `tester:${key}` },
      });
    }
    node.addEventListener("change", () => onChanged(key));
    node.addEventListener("input", () => onChanged(key));
    controls.set(key, node);

    let piece = node;
    if (isDateKey(key)) piece = buildDateBox(key, node);
    else if (isFlagKey(key)) piece = buildFlagBox(key, node);
    else if (key === LEVERAGE_KEY) piece = buildLeverageBox(key, node);
    if (piece !== node) controlBoxes.set(key, piece);
    // fake DOM の classList は className と独立のため、className の連結で付ける（検定可視）。
    if (HIDDEN_WHEN_INACTIVE.includes(key)) {
      piece.className = `${piece.className} tester-hide-inactive`.trim();
    }
    rowHostFor(key).appendChild(piece);
  }

  /** 旗キーの箱（チェックボックス＋説明文・MT5 のチェックボックス行と同形）。 */
  function buildFlagBox(key, node) {
    const box = el("label", { className: "tester-flag-box" });
    box.appendChild(node);
    box.appendChild(el("span", {
      className: "tester-flag-text", textContent: FLAG_TEXTS[key] || key,
    }));
    return box;
  }

  /** レバレッジの箱（MT5 の `1:10` 表示。送る値は数値トークンのまま）。 */
  function buildLeverageBox(_key, node) {
    const box = el("span", { className: "tester-lev-box" });
    box.appendChild(el("span", { className: "tester-lev-prefix", textContent: "1:" }));
    box.appendChild(node);
    return box;
  }

  /** 日付欄の箱（欄＋開閉ボタン）。カレンダーの確定はトークンを欄へ書き戻して通知する。 */
  function buildDateBox(key, node) {
    const box = el("div", { className: "tester-date-wrap" });
    const btn = el("button", {
      id: `tester${key}CalBtn`, className: "tester-cal-btn", type: "button",
      textContent: "▾", dataset: { mt5: `ui:cal:${key}` },
    });
    btn.addEventListener("click", () => {
      if (btn.disabled) return;
      const wasOpenHere = picker.isOpen() && pickerKey === key;
      picker.close();
      pickerKey = null;
      if (wasOpenHere) return;
      pickerKey = key;
      picker.openFor({
        anchor: box,                 // 値列（日付箱）が座標の基準（fixed 配置の実測元）
        value: node.value,
        onCommit: (token) => {
          pickerKey = null;
          node.value = token;
          onChanged(key);
        },
      });
    });
    box.appendChild(node);
    box.appendChild(btn);
    return box;
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

  /** 期間カスタム形（規則 E の `FromDate`+`ToDate` 形）を選んでいるか。 */
  function isCustomRange() {
    const node = controls.get(PRESET_DATE_KEY);
    return !!node && String(node.value) === CUSTOM_RANGE_OPTION.token;
  }

  /** プリセットが表示する解決期間（{from, to} トークン・計算できなければ null）。
   *  種別（entire / year_to_date / month_to_date）は schema の Dates 選択肢が配る宣言
   *  （enums `DATES_PRESET_RANGE_KINDS` 由来）で、データ範囲は run profile が供給する。 */
  function presetDisplayRange() {
    const token = currentToken(PRESET_DATE_KEY);
    const option = ((schema && schema.enum_options) || {}).Dates?.find?.(
      (o) => String(o.token) === token,
    );
    const kind = option && option.range_kind;
    const last = profile && profile.data_last_date;
    if (!kind || !last) return null;
    if (kind === "entire") {
      const first = profile.data_first_date;
      return first ? { from: String(first), to: String(last) } : null;
    }
    const [year, month] = String(last).split(".");
    if (kind === "year_to_date") return { from: `${year}.01.01`, to: String(last) };
    if (kind === "month_to_date") return { from: `${year}.${month}.01`, to: String(last) };
    return null;   // 未知の種別は表示しない（発明しない・投入には関与しない）
  }

  /** プリセット選択時、解決済み期間を不活性の From/To ボックスへ**表示**する（MT5 実測:
   *  ss20260906204441/204651。値は表示専用＝不活性キーは投入本文に載らない）。期間指定
   *  （カスタム）では上書きしない——直前のプリセット表示が手入力の起点として残る。 */
  function applyPresetRangeDisplay() {
    if (!schema || isCustomRange()) return;
    const fromNode = controls.get(CUSTOM_DATE_KEYS[0]);
    const toNode = controls.get(CUSTOM_DATE_KEYS[1]);
    if (!fromNode || !toNode) return;
    const range = presetDisplayRange();
    fromNode.value = range ? range.from : "";
    toNode.value = range ? range.to : "";
  }

  /** 現在の `ForwardMode` 選択肢が宣言する分割比の分母（分割形でなければ null）。
   *  判定は schema の選択肢宣言（`split_denominator`）だけ——トークン値から推測しない。 */
  function forwardSplitDenominator() {
    const token = currentToken(FORWARD_MODE_KEY);
    const option = (((schema && schema.enum_options) || {})[FORWARD_MODE_KEY] || []).find(
      (o) => String(o.token) === token,
    );
    const denominator = option && option.split_denominator;
    return typeof denominator === "number" ? denominator : null;
  }

  /** 分割フォワード（1/2・1/3・1/4）選択時、分割開始日を不活性の ForwardDate ボックスへ
   *  **表示**する（プリセット期間表示と同形。不活性キーは投入本文に載らない＝規則 F/F-10:
   *  MT5 も分割選択時 `ForwardDate` を `.ini` に書かない）。期間の入力元は From/To ボックス
   *  そのもの（プリセットなら解決済み表示・期間指定なら手入力＝供給元を 2 つ作らない）。
   *  キャンセル・カスタム日付では触らない——手入力と直前表示を消さない。 */
  function applyForwardSplitDisplay() {
    if (!schema) return;
    const node = controls.get(FORWARD_DATE_KEY);
    if (!node) return;
    const denominator = forwardSplitDenominator();
    if (denominator === null) return;
    const token = computeForwardSplitDate(
      currentToken(CUSTOM_DATE_KEYS[0]), currentToken(CUSTOM_DATE_KEYS[1]), denominator,
    );
    node.value = token === null ? "" : token;
  }

  /** キー K の活性宣言が「表示だけ隠す」形か（不活性でも投入本文には載せ続ける）。 */
  function isDisplayOnlyActivation(key) {
    const rule = ((schema && schema.activation) || {})[key];
    return !!rule && rule.effect === "display";
  }

  /** キー K が活性か（宣言駆動）。期間カスタム 2 キーだけは UI の切替そのものが決める。 */
  function isActive(key) {
    if (CUSTOM_DATE_KEYS.includes(key)) return isCustomRange();
    const rule = ((schema && schema.activation) || {})[key];
    if (!rule) return true;
    const token = currentToken(rule.key);
    if (token === null) return true;
    const tokens = rule.tokens || [];
    if (rule.mode === TRIGGER_ON_TOKENS) return tokens.includes(token);
    if (rule.mode === TRIGGER_EXCEPT_TOKENS) return !tokens.includes(token);
    // 未知の形の宣言では欄を殺さない（fail-open。誤投入はサーバの Fail-Stop が受ける）。
    return true;
  }

  /** 全控えへ活性状態を書く（欄の disabled と箱の `data-inactive`。隠すかは CSS が決める）。 */
  function applyActivation() {
    for (const [key, node] of controls) {
      const active = isActive(key);
      node.disabled = !active;
      const piece = controlBoxes.get(key) || node;
      piece.dataset.inactive = active ? "0" : "1";
      if (isDateKey(key)) {
        // 日付箱の▾ボタンも同時に殺す（欄だけ殺すとカレンダーから書けてしまう）。
        for (const child of piece.children || []) {
          if (child.tagName === "BUTTON") child.disabled = !active;
        }
      }
    }
    // 開いているカレンダーの欄が不活性になったら閉じる（不活性の欄へ書かせない）。
    if (pickerKey && !isActive(pickerKey)) {
      picker.close();
      pickerKey = null;
    }
  }

  /** キー K の現在値（未生成なら null）。 */
  function currentToken(key) {
    const node = controls.get(key);
    return node ? valueOf(node) : null;
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
    rowHosts.clear();
    controls.clear();
    controlBoxes.clear();
    expertLabels.clear();
    picker.close();      // 組み直しで欄が入れ替わるため、開いたままのカレンダーを残さない
    pickerKey = null;
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
    prepareRows(renderedKeys);   // 規則 D: 本パネルは Expert テスト（Indicator は出さない）
    // 行の中の並びは MT5 の行内順（割当表の keys 順）。行に属さないキーは key_order 順。
    const ordered = [
      ...MT5_ROWS.flatMap((def) => def.keys.filter((k) => renderedKeys.includes(k))),
      ...renderedKeys.filter((k) => rowDefOf(k) === DEFAULT_ROW),
    ];
    for (const key of ordered) buildControl(key);
    applyProfileDefaults();
    applyActivation();
    applyPresetRangeDisplay();
    applyForwardSplitDisplay();
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
      const chosen = valueOf(node);
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
    const mapping = {};
    for (const key of schema.key_order || []) {
      if (key === INDICATOR_KEY) continue;
      // UI 専用トークン（カスタム期間）は `.ini` の語彙ではない——決して送らない（規則 E）。
      if (key === PRESET_DATE_KEY && isCustomRange()) continue;
      // 不活性の欄は送らない（規則 B/E/F の宣言駆動。値は欄に残る＝MT5 のグレーアウトと同形）。
      // ただし宣言が `effect: "display"` のキーは表示だけ隠れ、値は載せ続ける（規則 H:
      // Expert 専用キーは常に必須。omit すると投入が必ず E-08 で失敗する・実測 2026-09-06）。
      if (!isActive(key) && !isDisplayOnlyActivation(key)) continue;
      const node = controls.get(key);
      if (!node) continue;
      const value = valueOf(node);
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
      applyPresetRangeDisplay();   // データ範囲の供給元が変わった＝表示期間も引き直す
      applyForwardSplitDisplay();  // 表示期間が動いた＝分割日も引き直す
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
