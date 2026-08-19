// 縮退面（View・Phase 9 S3 M4）。
//
// 役割: Tester Settings の schema を取れなかった構成で、実行に最低限要る 3 つ——銘柄・
//   実行対象 EA・初期資金——を受け取る。MT5 の設定は組めないので `settings` ブロックは
//   **作らない**（null）。
//   投入本文は Phase 8 以前の旧フォームと byte 等価になる。
//
// 責務（SRP）: DOM の生成と、実行対象（SubjectSource）としての値の供給だけ。fetch はしない
//   （EA 候補は setEaCandidates で、銘柄候補は setSymbolCandidates で**注入**する）。
//
// なぜ M1（Tester Settings 面）と同じ Port を実装するか: 合成根は schema の有無で
//   **どちらか 1 つだけ**を実行対象の供給元として使う。契約が同じなら合成根に分岐が要らない
//   （S2 までは「Tester 面が居れば…」の三項分岐が本文の組み立てに残っていた）。
//
// id を `execEaName` / `execDeposit` のまま保つ理由: この 2 つは Phase 8 以前から縮退経路の
//   入力欄であり、python 側の実 UI 検定が「縮退構成でこの欄が権威である」ことをこの id で
//   観測している。面の持ち主が変わっても観測点は動かさない。
//
// 画面契約（S6）: この面の 3 欄は MT5 の `Symbol` / `Expert` / `Deposit` の縮退版である。
//   設定ブロックは組まないが、欄そのものは MT5 の対応物を持つので `tester:` を名乗る。
//
// fake DOM 前提: querySelector は使わず、要素参照を JS 側で保持する。

/** 初期資金の初期表示。schema があれば Tester 面の `Deposit` が同じ役割を担う
 *  （両面が同時に立つことは無いので、画面上でこの値が競合することはない）。 */
const INITIAL_DEPOSIT = "10000";

export function createSimSchemaFallbackView({ doc } = {}) {
  let root = null;
  let eaSel = null;
  let depositInput = null;
  let symbolHost = null;
  let symbolNode = null;
  let eaCandidates = [];
  let symbolCandidates = [];
  let symbolCb = null;

  const el = (tag, props) => {
    const node = doc.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (k === "dataset") Object.assign(node.dataset, v);
      else node[k] = v;
    }
    return node;
  };

  const option = (value) => el("option", { value, textContent: value });

  function fillOptions(select, values) {
    // `select.children = []` は実ブラウザで getter 専用プロパティへの代入になる（実 UI でのみ
    // TypeError）。実 DOM でも fake DOM でも動く removeChild ループで空にする。
    for (const child of Array.from(select.children || [])) select.removeChild(child);
    for (const v of values) select.appendChild(option(v));
    if (values.length && !values.includes(select.value)) select.value = values[0];
  }

  /** 銘柄の入力欄を組み直す（候補があれば select・無ければ自由入力へ縮退）。
   *  mount と setSymbolCandidates が同じ 1 本を通る（型の決め方を 2 箇所に置かない）。 */
  function rebuildSymbol() {
    if (!symbolHost) return;
    const previous = symbolNode ? String(symbolNode.value || "") : "";
    for (const child of Array.from(symbolHost.children || [])) symbolHost.removeChild(child);
    if (symbolCandidates.length) {
      symbolNode = el("select", {
        id: "execSymbol", className: "fallback-symbol", dataset: { mt5: "tester:Symbol" },
      });
      fillOptions(symbolNode, symbolCandidates);
      if (symbolCandidates.includes(previous)) symbolNode.value = previous;
    } else {
      symbolNode = el("input", {
        id: "execSymbol", className: "fallback-symbol", type: "text", value: previous,
        dataset: { mt5: "tester:Symbol" },
      });
    }
    symbolNode.addEventListener("change", () => { if (symbolCb) symbolCb(selectedSymbol()); });
    symbolHost.appendChild(symbolNode);
  }

  /** 実行対象の銘柄（未生成なら空文字）。 */
  function selectedSymbol() {
    return symbolNode ? String(symbolNode.value == null ? "" : symbolNode.value) : "";
  }

  return {
    elements: {},

    mount(host) {
      root = el("div", { id: "simSchemaFallbackPanel", className: "fallback-panel" });
      root.appendChild(el("div", { className: "fallback-title", textContent: "実行対象" }));

      symbolHost = el("label", { className: "fallback-field", textContent: "Symbol" });
      rebuildSymbol();
      root.appendChild(symbolHost);

      const eaWrap = el("label", { className: "fallback-field", textContent: "指標セット" });
      eaSel = el("select", {
        id: "execEaName", className: "fallback-ea", dataset: { mt5: "tester:Expert" },
      });
      fillOptions(eaSel, eaCandidates);
      eaWrap.appendChild(eaSel);

      const depositWrap = el("label", { className: "fallback-field", textContent: "初期資金" });
      depositInput = el("input", {
        id: "execDeposit", className: "fallback-deposit",
        type: "number", value: INITIAL_DEPOSIT, min: "0",
        dataset: { mt5: "tester:Deposit" },
      });
      depositWrap.appendChild(depositInput);

      root.appendChild(eaWrap);
      root.appendChild(depositWrap);
      host.appendChild(root);
      this.elements = { root, eaSel, depositInput, symbolNode };
      return root;
    },

    /** ea_name（指標セット）候補（string[]）を注入する。 */
    setEaCandidates(list) {
      eaCandidates = Array.isArray(list) ? list.slice() : [];
      if (eaSel) fillOptions(eaSel, eaCandidates);
    },

    /** 銘柄候補（run-options の datasets 由来）を注入する（Phase 9 S4）。 */
    setSymbolCandidates(list) {
      symbolCandidates = Array.isArray(list) ? list.map((v) => String(v)) : [];
      rebuildSymbol();
    },

    // --- SubjectSource Port（M1 Tester Settings 面と同型）---------------------------

    /** 実行対象データセットを注入する（この面は既定値を持たないので受け取るだけ）。 */
    setRunProfile(_runProfile) {},

    selectedSymbol,

    /** 銘柄変更時のコールバックを登録する（新しい銘柄を渡す）。 */
    onSymbolChange(cb) { symbolCb = cb; },

    /** `backtest` へ渡す実行対象（EA・口座）。 */
    derivedBacktest() {
      return {
        ea_name: String(eaSel ? eaSel.value || "" : ""),
        initial_deposit: Number(depositInput ? depositInput.value : NaN),
      };
    },

    /** schema が無い構成では設定ブロックを組まない（旧フォーム投入と byte 等価）。 */
    buildSettings() { return null; },
  };
}
