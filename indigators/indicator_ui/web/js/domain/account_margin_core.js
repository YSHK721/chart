// account_margin_core.js（domain）— 証拠金・ロスカットの**権威の鏡**（ISSUE-368 スライス 2）。
//
// 権威は Python: simulator/usecase/account_engine.py の official_required_margin /
//   official_losscut_price（出典 docs/oanda_indices_cfd_about.md §3(2)/§1-2）。
//   言語が違うため共有できないので写しを 1 つだけ置き、golden fixture
//   （simulator/tests/fixtures/account_engine/js_golden_cases.json）との一致を
//   tests/py_parity_account_margin.test.js が拘束する（.doc/LAYERING_CONVENTIONS.md:28-30）。
//   **写しはここ 1 箇所だけ**。他所で証拠金・ロスカットの式を書いてはならない。
//
// 権威との対応:
//   official_required_margin  → requiredMargin
//   official_losscut_price    → losscutPrice
//   （avg_price / total_units は両関数の内部量。UI と検定が使うため公開する）
//
// 演算の順序は Python 側と 1:1 に保つ（IEEE754 は結合則を満たさないため、順序を変えると
//   最終桁がずれて golden 検定が落ちる）。
//
// **総和の取り方（実測 2026-08-20・重要）**: 権威 official_* は Python 組込みの sum() を使う。
//   CPython 3.12 以降の sum() は float に対し **Neumaier の補正付き総和**を行い、素朴な逐次加算と
//   最終桁が食い違う（本リポの CPython 3.13.5 で実測: 建玉 3 本の約定代金で 1 ULP 差＝
//   58032.5525976814 対 58032.55259768141）。したがって本モジュールの総和も Neumaier で行う。
//   素朴な加算にすると golden 検定が「式は同じなのに落ちる」状態になる。
//   （権威側の split_entry_plan.py が明示ループで積み上げている量＝合計ロット・約定代金・
//    リスク合計は素朴加算であり、そちらを写す split_entry_plan.js も素朴加算のままでよい。）
//
// 依存ゼロ（DOM・fetch・lwc を触らない）。

/** ロング／ショートの識別子（Python 側 account_engine.LONG / SHORT と同値）。 */
export const LONG = 'long';
export const SHORT = 'short';

/**
 * Python 組込み sum()（CPython 3.12+ の float 経路＝Neumaier 補正付き総和）と同一の総和。
 * 権威 official_* が sum() を使っている箇所の写しにだけ使う。
 * @param {number[]} values
 * @returns {number}
 */
export function sumLikePython(values) {
  let result = 0;
  let c = 0;
  for (const x of values) {
    const t = result + x;
    if (Math.abs(result) >= Math.abs(x)) {
      c += (result - t) + x;
    } else {
      c += (x - t) + result;
    }
    result = t;
  }
  return result + c;
}

/**
 * 建玉の合計単位数（権威 official_losscut_price の sum(u for _, u in entries) の写し）。
 * @param {Array<{price:number, units:number}>} entries
 * @returns {number}
 */
export function totalUnits(entries) {
  return sumLikePython(entries.map((e) => e.units));
}

/**
 * 加重平均建値（Σpᵢuᵢ / Σuᵢ）。建玉が無ければ 0。
 * @param {Array<{price:number, units:number}>} entries
 * @returns {number}
 */
export function averagePrice(entries) {
  const units = totalUnits(entries);
  if (units <= 0) {
    return 0;
  }
  const notional = sumLikePython(entries.map((e) => e.price * e.units));
  return notional / units;
}

/**
 * 必要証拠金 M = Σuᵢ·Pᵢ·V × mr（§3(2)「約定代金に必要証拠金率を乗じて算出」＝建値固定）。
 * @param {Array<{price:number, units:number}>} entries
 * @param {number} marginRate 証拠金率（比）
 * @param {number} [pointValue] 円換算 V
 * @returns {number}
 */
export function requiredMargin(entries, marginRate, pointValue = 1) {
  const notional = sumLikePython(entries.map((e) => e.price * e.units));
  return notional * pointValue * marginRate;
}

/**
 * ロスカット価格（発動条件 §1-2: 有効証拠金 ≤ 必要証拠金 M・建値固定）。
 *   long:  X = avgP·(1+mr) − E/(U·V)
 *   short: X = avgP·(1−mr) + E/(U·V)
 * 建玉が無ければ null（Python の None と同義。0 へ倒すと「到達不能」と区別できない）。
 *
 * @param {string} direction 'long' | 'short'
 * @param {Array<{price:number, units:number}>} entries
 * @param {number} balance 口座残高 E
 * @param {number} marginRate 証拠金率（比）
 * @param {number} [pointValue] 円換算 V
 * @returns {number|null}
 */
export function losscutPrice(direction, entries, balance, marginRate, pointValue = 1) {
  const units = totalUnits(entries);
  if (units <= 0) {
    return null;
  }
  const notional = sumLikePython(entries.map((e) => e.price * e.units));
  const avgP = notional / units;
  const capU = units * pointValue;
  if (direction === LONG) {
    return avgP * (1 + marginRate) - balance / capU;
  }
  return avgP * (1 - marginRate) + balance / capU;
}
