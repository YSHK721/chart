// 実行トレースの指定面（View・ISSUE-508 段階 3 §6.6.2）。
//
// 役割: 「トレースを記録するか」と「どこを残すか（期間）」を人が表現できるようにすること
//   ——この 2 つだけである。何を本文へ載せるか（`{enabled, start, end}` の形）も、日付を
//   epoch 秒へどう直すかも、この面は知らない。規則は投入契約（M5 sim_submission_builder）
//   が唯一持ち、この面は**打たれた値をそのまま報告する**。
//
// なぜ実行指示面（M3）に同居させないか（SRP）: M3 の責務は「実行を開始させることと結果への
//   導線」だと当のモジュールが明記している。記録の要否と期間は改訂の動機が別のアクター
//   （運用の要求＝期間指定・記録量の抑制。基本設計 §3 の `trace_window` と同じ動機）であり、
//   同居させると実行操作の改訂とトレース運用の改訂が同じファイルを開くことになる。
//
// なぜ日付欄が `YYYY.MM.DD` か: front には既に日付入力が在り（Tester Settings の日付欄）、
//   その表記とカレンダーは `sim_date_picker_view` が唯一持っている。ここだけ別表記にすると、
//   同じ「日付」という概念に画面上の呼び名が 2 つできる。表記もカレンダーも共有する。
//
// 画面契約: トレース記録は MT5 に対応物を持たない表示制御であり、投入本文のキー名にも
//   ならない。したがって `data-mt5` の宣言はすべて `ui:` 接頭辞である
//   （`sim_form_mt5_contract.test.js` の語彙規則）。
//
// fake DOM 前提: querySelector は使わず、要素参照を JS 側で保持する。

import { createSimDatePickerView } from "./sim_date_picker_view.js";

export function createSimTracePanelView({ doc, today } = {}) {
  // カレンダーは front に 1 つだけ（表記も月送りも隣接月の扱いも、既にここが所有している）。
  const picker = createSimDatePickerView({ doc, today });
  // 今どの欄のために開いているか（同じ▾の 2 度押しで閉じるトグルのため）。
  let pickerFor = null;
  let root = null;
  let enabledBox = null;
  let fromField = null;
  let toField = null;

  const el = (tag, props) => {
    const node = doc.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (k === "dataset") Object.assign(node.dataset, v);
      else node[k] = v;
    }
    return node;
  };

  /** 日付欄 1 本（見出し＋欄＋カレンダーの開閉ボタン）を組む。 */
  function buildDateRow({ id, label, mt5 }) {
    const row = el("div", { className: "trace-row" });
    row.appendChild(el("span", { className: "trace-label", textContent: label }));
    const box = el("div", { className: "trace-date-wrap" });
    const field = el("input", {
      id, className: "trace-input", type: "text", value: "",
      placeholder: "YYYY.MM.DD", dataset: { mt5 },
    });
    const btn = el("button", {
      id: `${id}CalBtn`, className: "trace-cal-btn", type: "button",
      // 宣言は欄の名前から導く（`ui:trace-from` → `ui:cal:trace-from`）。手で書くと
      // 欄と開閉ボタンで表記が割れる（実際に `ui:cal:traceFrom` になっていた）。
      textContent: "▾", dataset: { mt5: mt5.replace("ui:", "ui:cal:") },
    });
    btn.addEventListener("click", () => {
      // 同じ▾の 2 度押しは閉じる（開き直さない）。他方の▾なら付け替える。
      const wasOpenHere = picker.isOpen() && pickerFor === id;
      picker.close();
      pickerFor = null;
      if (wasOpenHere) return;
      pickerFor = id;
      picker.openFor({
        anchor: box,               // 座標の基準は値の箱（Tester 面と同じ流儀）
        value: field.value,
        onCommit: (token) => { pickerFor = null; field.value = token; },
      });
    });
    box.appendChild(field);
    box.appendChild(btn);
    row.appendChild(box);
    return { row, field };
  }

  return {
    elements: {},

    mount(host) {
      root = el("div", { id: "simTracePanel", className: "trace-panel" });

      // 明示 ON（依頼者裁定「明示 ON ＋期間指定」）。既定は OFF＝既存投入と byte 等価。
      const flagBox = el("label", { className: "trace-flag-box" });
      enabledBox = el("input", {
        id: "traceEnabled", className: "trace-flag", type: "checkbox", checked: false,
        dataset: { mt5: "ui:trace-enabled" },
      });
      flagBox.appendChild(enabledBox);
      flagBox.appendChild(el("span", { className: "trace-flag-text", textContent: "トレース記録" }));
      root.appendChild(flagBox);

      const from = buildDateRow({ id: "traceFrom", label: "開始日", mt5: "ui:trace-from" });
      const to = buildDateRow({ id: "traceTo", label: "終了日", mt5: "ui:trace-to" });
      fromField = from.field;
      toField = to.field;
      root.appendChild(from.row);
      root.appendChild(to.row);

      host.appendChild(root);
      this.elements = { root, enabledBox, fromField, toField };
      return root;
    },

    /**
     * 打たれた指定を**そのまま**返す（解釈も検証もしない）。
     *
     * 面が独自に判定を持つと、同じ規則が画面と投入契約の 2 箇所に生まれ、片方が腐る。
     * 期間の妥当性（片側だけ・逆転）を落とすのは M5 `traceBlockOf` であり、最終的な
     * 権威はサーバの `TraceWindow` である。
     *
     * 器がまだ無い（mount 段で落ちた）構成では OFF を返す——ここで例外を投げると
     * 投入そのものが道連れになる。
     *
     * @returns {{enabled: boolean, from: string, to: string}}
     */
    traceSpec() {
      return {
        enabled: !!(enabledBox && enabledBox.checked),
        from: (fromField && fromField.value) || "",
        to: (toField && toField.value) || "",
      };
    },
  };
}
