// price_levels.js（domain）— 価格水準の単一ソース E-02 PriceLevels（ISSUE-368 スライス 2）。
//
// 不変ルール: **保持するのは価格だけ**。損切り距離 D・利確距離 TP・各建玉の距離は
//   毎回そこから派生させる。距離を状態に持つと「価格を動かしたのに距離が古い」という
//   二重ソースが生まれる（距離指定モードの撤廃＝依頼者確定要件の恒久化）。
//
// **JS 固有**（Python 権威なし）。この不変条件は本統合で新設したもので、参照実装にも
//   Python 側にも対応物が無いため golden fixture の対象外。ただし派生の式と配置の不変条件は
//   参照実装 integrated_position_sizing_calculator.html に従う:
//     :949  D  = long: max(P₀ − stop, 0) / short: max(stop − P₀, 0)
//     :953  TP = long: (take − P₀) / short: (P₀ − take)。0 以下は 0（=無効）
//     :965  nearest = long: min(建値) / short: max(建値)
//     :974  各建玉の距離 = long: 建値 − stop / short: stop − 建値
//   P₀ は :359 のラベル「第1建値 P₀」＝ direct 既定シード :931 の customP[0]。建値の
//   単一ソース化（TBD-1）後は entryPrices[0] がその役割を負う。
//   gap（建玉間隔）は TBD-1 で撤廃済みのため本モジュールは一切扱わない。
//
// **量子化の不変条件（ISSUE-368 スライス S-4）**: 呼び値の刻み `tick` を注入すると、
//   「刻み上にない価格は PriceLevels に存在できない」。関門は構築・更新口（コンストラクタ）1 か所で、
//   create / withEntry / withStop / withTake の 4 経路すべてがここを通る。関門を domain に置く理由は
//   水準線 drag（`adapter/front/price_level_drag_controller.js:157-176`）が resolver を通らないため
//   （設計「追補: 工程 2」E-4・丸めの適用点 経路 6）。`tick` は**任意注入・既定は素通し**で、
//   未注入なら従来と完全に同一に振る舞う。丸めの式は `price_quantize.js` にしか置かない。
//
// 依存ゼロ（DOM・fetch・lwc を触らない）。import は同層 domain のみ。

import { quantize, usableTick } from './price_quantize.js';

export const LONG = 'long';
export const SHORT = 'short';

/** validate() が返す違反コード。 */
export const STOP_INVALID = 'stop_invalid';
export const TAKE_INVALID = 'take_invalid';

class PriceLevels {
  constructor(direction, entryPrices, stopPrice, takePrice, tick) {
    this.direction = direction;
    this.entryPrices = Object.freeze(entryPrices.map((p) => quantize(p, tick)));
    this.stopPrice = quantize(stopPrice, tick);
    this.takePrice = quantize(takePrice, tick);
    // 刻みは価格ではないので**列挙可能な状態にしない**（「保持するのは価格だけ」を崩さない）。
    Object.defineProperty(this, '_tick', { value: tick, enumerable: false });
    Object.freeze(this);
  }

  get _long() {
    return this.direction === LONG;
  }

  /** :359 「第1建値 P₀」（距離の基準）。 */
  basePrice() {
    return this.entryPrices[0];
  }

  /** :965 損切り側に最も近い建玉（long は min・short は max）。 */
  nearestEntry() {
    return this._long ? Math.min(...this.entryPrices) : Math.max(...this.entryPrices);
  }

  /** :949 損切り距離 D（P₀ 基準・逆側なら 0）。 */
  stopDistance() {
    const d = this._long ? this.basePrice() - this.stopPrice : this.stopPrice - this.basePrice();
    return Math.max(d, 0);
  }

  /** :953 利確距離 TP（P₀ 基準）。利確が無い／逆側なら 0（=無効）。 */
  takeDistance() {
    if (this.takePrice === null || this.takePrice === undefined) {
      return 0;
    }
    const t = this._long ? this.takePrice - this.basePrice() : this.basePrice() - this.takePrice;
    return t > 0 ? t : 0;
  }

  /** :974 各建玉→損切りの向き付き距離。 */
  entryDistances() {
    return this.entryPrices.map((p) => (this._long ? p - this.stopPrice : this.stopPrice - p));
  }

  /**
   * 配置の不変条件を検査し、違反コードの配列を返す（違反が無ければ空配列）。
   * 先に見つかった 1 件で打ち切らない（複数の誤配置を同時に見せる）。
   */
  validate() {
    const violations = [];
    if (this.entryDistances().some((d) => !(d > 0))) {
      violations.push(STOP_INVALID);   // :971/:974 stopInvalid と同条件
    }
    if (this.takePrice !== null && this.takePrice !== undefined && !(this.takeDistance() > 0)) {
      violations.push(TAKE_INVALID);
    }
    return violations;
  }

  /** 損切り価格を差し替えた新しい水準（非破壊）。 */
  withStop(price) {
    return new PriceLevels(this.direction, this.entryPrices, price, this.takePrice, this._tick);
  }

  /** i 番目の建値を差し替えた新しい水準（非破壊）。 */
  withEntry(index, price) {
    if (!Number.isInteger(index) || index < 0 || index >= this.entryPrices.length) {
      throw new Error(`建玉番号が範囲外です: ${index}（0〜${this.entryPrices.length - 1}）`);
    }
    const next = this.entryPrices.slice();
    next[index] = price;
    return new PriceLevels(this.direction, next, this.stopPrice, this.takePrice, this._tick);
  }

  /** 利確価格を差し替えた新しい水準（非破壊。null で無効化）。 */
  withTake(price) {
    return new PriceLevels(this.direction, this.entryPrices, this.stopPrice, price, this._tick);
  }
}

/**
 * 価格水準を作る。
 * @param {{direction:string, entryPrices:number[], stopPrice:number, takePrice:(number|null),
 *           tick:(number|null|undefined)}} spec `tick` は任意（未指定なら量子化しない）。
 * @returns {PriceLevels}
 */
export function createPriceLevels(spec) {
  const direction = spec.direction;
  if (direction !== LONG && direction !== SHORT) {
    throw new Error(`direction は '${LONG}' / '${SHORT}' です: ${direction}`);
  }
  const entryPrices = spec.entryPrices;
  if (!Array.isArray(entryPrices) || entryPrices.length === 0) {
    throw new Error('entryPrices は 1 本以上必要です');
  }
  const takePrice = spec.takePrice === undefined ? null : spec.takePrice;
  const tick = spec.tick === undefined ? null : spec.tick;
  // 関門が自分では刻みを検証していなかった（工程 5 🟡-3）。同じ domain に「使える刻みか」の
  //   唯一源 `usableTick` があるのに呼ばず、無音の誤答を通していた（実測 node v24・2026-08-20）:
  //     tick=0 / NaN / Infinity → 全価格が NaN（`quantize` の素通し契約は null/未指定だけ）
  //     tick=-1                 → **無音で丸まる**（58998.75 → 58999）＝誤りが正常値の顔で入る
  //     tick=1e-101             → `toFixed` の RangeError が domain の外へ漏れる
  //   「刻み上にない価格は存在できない」を名乗る以上、刻み自体の妥当性もここで閉じる。
  //   判定は書き写さず唯一源へ委ねる（規則が 1 つで実装が 2 つある状態を作らない＝原因 β）。
  //   素通し契約（`null` / 未指定＝量子化しない）は変えない。
  if (tick !== null && usableTick(tick) === null) {
    throw new Error(`量子化に使えない刻み（tick）です: ${String(tick)}`);
  }
  return new PriceLevels(direction, entryPrices, spec.stopPrice, takePrice, tick);
}

// --- 水準種別 → 更新経路（ISSUE-479 Wave2 J-3・OCP）------------------------------------
//
// 「どの水準が掴めて、掴んだら何が変わるか」は**水準そのものの性質**であって、ドラッグ殻の
// 都合ではない。従来この対応は adapter（price_level_drag_controller）の if 連鎖にあり、掴める
// 水準を 1 種増やすたびに殻が伸びていた。宣言を水準の所有者である本モジュールへ置く。
//
// 読み取り専用の水準（`losscut` の自動線など）と未知種別は**表に載らない**＝掴んでも更新しない。
// 「掴めない」を表の不在で表すので、禁止リストを別に持たない（規則が 1 つで実装が 2 つある状態を
// 作らない）。線プリミティブ側の読み取り専用宣言と交わらないことは web/tests が固定する。

/** 種別 → 「その種別の価格を差し替えた新しい水準」を返す更新経路。 */
const WITH_PRICE = Object.freeze({
  entry: (levels, index, price) => levels.withEntry(index, price),
  stop: (levels, _index, price) => levels.withStop(price),
  take: (levels, _index, price) => levels.withTake(price),
});

/** ドラッグで価格を変更できる水準種別（表の鍵集合＝唯一の宣言）。 */
export const DRAGGABLE_KINDS = Object.freeze(new Set(Object.keys(WITH_PRICE)));

/**
 * 掴んだ水準へ価格を反映した新しい水準を返す（非破壊）。
 * @param {PriceLevels} levels 現在の水準。
 * @param {string} kind 水準種別（`entry` / `stop` / `take`）。
 * @param {number|null} index `entry` のときの建玉番号（他の種別では無視される）。
 * @param {number} price 新しい価格。
 * @returns {PriceLevels|null} 更新後の水準。表に無い種別（読み取り専用・未知）は `null`＝更新しない。
 */
export function withPriceAt(levels, kind, index, price) {
  const update = WITH_PRICE[kind];
  return update ? update(levels, index, price) : null;
}
