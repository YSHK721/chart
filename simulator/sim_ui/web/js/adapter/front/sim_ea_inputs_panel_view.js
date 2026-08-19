// EA パラメータ面（View・Phase 9 S2 M2・MT5 の Inputs タブ相当）。
//
// 役割: EA が受け取る実行パラメータ（SL/TP 点数・移動平均・ロット）を入力させ、
//   投入本文の `backtest` へ渡す値へ変換する。
//
// なぜ面を分けるか（SRP）: テスタ設定（Tester Settings＝いつ・どの銘柄で・どう回すか）と
//   EA パラメータ（EA が何を見て売買するか）は変更要求の主体が別である。MT5 も
//   Settings タブと Inputs タブに分けている。同じ器へ混ぜると、片方の都合で他方の並びが動く。
//
// 単一ソース: 下の宣言表 EA_INPUT_FIELDS が唯一の宣言である。DOM（id・ラベル・入力型・
//   初期値）も投入値（キー名・型変換）も**すべてこの表から導出**する。表に無い欄は描画
//   されず、描画された欄は必ず投入本文に現れる（3 者一致は
//   tests/sim_ea_inputs_panel_view.test.js が双方向に固定する）。
//
// fake DOM 前提: querySelector は使わず、param ごとに要素参照を JS 側で保持する。

/** 入力の型 → 投入値への変換（宣言表の `type` が指す）。 */
const COERCE = Object.freeze({
  number: (v) => Number(v),
  int: (v) => Math.trunc(Number(v)),
  text: (v) => String(v),
});

/** 入力の型 → DOM の `input[type]`（数値系はブラウザの数値入力に載せる）。 */
const DOM_TYPE = Object.freeze({ number: "number", int: "number", text: "text" });

/**
 * EA パラメータの宣言表（唯一の宣言）。
 *
 *  id      : DOM 要素の id（画面契約 `data-mt5="inputs:<param>"` と対にする）
 *  param   : 投入本文 `backtest` のキー名
 *  label   : 画面の見出し
 *  type    : 値の型（number / int / text）。DOM の入力型と変換の双方を決める
 *  initial : 初期表示の値（文字列。View にリテラルを書かないための供給元）
 *  min     : 数値入力の下限（任意・表示上の制約のみ。検証はサーバの受付が権威）
 */
export const EA_INPUT_FIELDS = Object.freeze([
  Object.freeze({ id: "eaInputStopLoss", param: "stop_loss_points", label: "SL(点)", type: "number", initial: "0", min: "0" }),
  Object.freeze({ id: "eaInputTakeProfit", param: "take_profit_points", label: "TP(点)", type: "number", initial: "0", min: "0" }),
  Object.freeze({ id: "eaInputMaPeriod", param: "ma_period", label: "MA周期", type: "int", initial: "20", min: "1" }),
  Object.freeze({ id: "eaInputMaMethod", param: "ma_method", label: "MA種別", type: "text", initial: "ema" }),
  Object.freeze({ id: "eaInputLotSize", param: "lot_size", label: "ロット", type: "number", initial: "0.1", min: "0" }),
]);

export function createSimEaInputsPanelView({ doc } = {}) {
  let root = null;
  /** param → 入力要素。 */
  const controls = new Map();

  const el = (tag, props) => {
    const node = doc.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (k === "dataset") Object.assign(node.dataset, v);
      else node[k] = v;
    }
    return node;
  };

  /** 宣言 1 行から入力欄を作る（表に書いていない属性は付けない）。 */
  function buildField(field) {
    const props = {
      id: field.id,
      className: "ea-input",
      type: DOM_TYPE[field.type],
      value: field.initial,
      dataset: { mt5: `inputs:${field.param}` },
    };
    if (field.min !== undefined) props.min = field.min;
    const node = el("input", props);
    const wrap = el("label", { className: "ea-field", textContent: field.label });
    wrap.appendChild(node);
    controls.set(field.param, node);
    return wrap;
  }

  return {
    elements: {},

    mount(host) {
      root = el("div", { id: "simEaInputsPanel", className: "ea-inputs-panel" });
      root.appendChild(el("div", { className: "ea-inputs-title", textContent: "Inputs" }));
      for (const field of EA_INPUT_FIELDS) root.appendChild(buildField(field));
      host.appendChild(root);
      this.elements = { root };
      return root;
    },

    /** 投入本文 `backtest` へ渡す EA パラメータ（宣言表の param をキーに、type で変換）。 */
    values() {
      const out = {};
      for (const field of EA_INPUT_FIELDS) {
        const node = controls.get(field.param);
        out[field.param] = COERCE[field.type](node ? node.value : field.initial);
      }
      return out;
    },
  };
}
