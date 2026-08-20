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
// 依存ゼロ（DOM・fetch・lwc を触らない）。

export const LONG = 'long';
export const SHORT = 'short';

/** validate() が返す違反コード。 */
export const STOP_INVALID = 'stop_invalid';
export const TAKE_INVALID = 'take_invalid';

class PriceLevels {
  constructor(direction, entryPrices, stopPrice, takePrice) {
    this.direction = direction;
    this.entryPrices = Object.freeze(entryPrices.slice());
    this.stopPrice = stopPrice;
    this.takePrice = takePrice;
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
    return new PriceLevels(this.direction, this.entryPrices, price, this.takePrice);
  }

  /** i 番目の建値を差し替えた新しい水準（非破壊）。 */
  withEntry(index, price) {
    if (!Number.isInteger(index) || index < 0 || index >= this.entryPrices.length) {
      throw new Error(`建玉番号が範囲外です: ${index}（0〜${this.entryPrices.length - 1}）`);
    }
    const next = this.entryPrices.slice();
    next[index] = price;
    return new PriceLevels(this.direction, next, this.stopPrice, this.takePrice);
  }

  /** 利確価格を差し替えた新しい水準（非破壊。null で無効化）。 */
  withTake(price) {
    return new PriceLevels(this.direction, this.entryPrices, this.stopPrice, price);
  }
}

/**
 * 価格水準を作る。
 * @param {{direction:string, entryPrices:number[], stopPrice:number, takePrice:(number|null)}} spec
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
  return new PriceLevels(direction, entryPrices, spec.stopPrice, takePrice);
}
