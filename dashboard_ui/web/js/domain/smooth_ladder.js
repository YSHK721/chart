// smooth_ladder（domain/smooth_ladder.js）— なめらか再生の水準台帳と、距離・差の**式**。
//
// 設計入力（依頼者指示 2026-08-31: 距離・価格・差もライブチャートと同じ更新粒度）:
//   1s の応答描画とは別に、100ms 粒度で流れてくる末尾値（サーバ計算の tails）で水準価格を
//   上書きし、そこから距離と差を出し直す。
//
// 式はサーバの参照定義（dashboard_ui/domain/price_ladder.py）そのもの:
//   距離 = 水準価格 − 現在値 / 差 = 直前行（サーバ全行順）の水準価格 − 自行の水準価格
// ここで使う材料（水準価格＝tails・現在値＝再生価格）はどちらもサーバ計算の値であり、
// フロントが統計や並びを再計算するわけではない（並び・地平・p は 1s の応答描画が持ち主）。
//
// なぜ View から出したのか（ISSUE-502 段階 4C・F-2）:
//   台帳（全行の順序・rowKey → 添字・末尾値の上書き）と 2 本の式は、DOM を一切必要としない。
//   View に置くと「隣接行の差」が可視行だけで計算されていないこと（絞り込みや窓の外の行も
//   隣接に効く）を、表を組まないと確かめられない。**式が版面の都合で歪んでいない**ことを
//   単体で固定できる形にする。
//
// 差の隣接は**サーバの全行順**で決まる（絞り込み・窓と無関係）。可視行だけで隣を取ると、
// 行を 1 本外しただけで差の意味が変わる（版面は数字を出し続けるので出力の検査では落ちない）。
//
// 計算量: reset は行数に比例（1 巡）、numbersAt は定数時間。発行も再計算も生まない。

import { rowKeyOf } from './ladder_row.js';

/**
 * なめらか再生の台帳を作る。
 *
 * @returns {{reset: Function, indexOf: Function, size: Function,
 *            applyTails: Function, numbersAt: Function, clear: Function}}
 */
export function createSmoothLedger() {
  /** 全行（サーバ並び順）。各要素 {key, instanceKey, series, smooth}。 */
  let entries = [];
  /** rowKey → entries の添字。 */
  const indexByKey = new Map();

  return {
    /**
     * 応答 1 件で台帳を組み直す（種はサーバ価格）。
     *
     * @param {Array<object>} rows 全行（サーバ並び順）
     */
    reset(rows) {
      entries = rows.map((row) => ({
        key: rowKeyOf(row),
        instanceKey: Array.isArray(row.instance_key) ? row.instance_key : null,
        series: typeof row.series === 'string' && row.series ? row.series : null,
        smooth: Number(row.price),
      }));
      indexByKey.clear();
      entries.forEach((entry, index) => indexByKey.set(entry.key, index));
    },

    /** rowKey の添字（未登録は undefined）。 */
    indexOf(key) {
      return indexByKey.get(key);
    },

    /** 台帳の行数。 */
    size() {
      return entries.length;
    },

    /**
     * 1 tick ぶんの末尾値を流し込む。
     *
     * @param {Function} lookup (instance_key 配列, series 名) => 末尾値 | undefined。
     *   値の実体はサーバ計算の tails で、呼び手（合成根）が閉じ込めて渡す
     *   （台帳は tails のキー構造を知らない）。
     */
    applyTails(lookup) {
      for (const entry of entries) {
        if (!entry.instanceKey || !entry.series) {
          continue;
        }
        const value = lookup(entry.instanceKey, entry.series);
        if (typeof value === 'number' && Number.isFinite(value)) {
          entry.smooth = value;
        }
      }
    },

    /**
     * 1 行ぶんの数値（価格・距離・差）。台帳に無い / 値が非有限なら null（＝書き換えない）。
     *
     * 差は先頭行と直前行が非有限のとき null（欄は空になる）——0 を書くと「差が無い」と
     * 「差が 0」が版面で区別できなくなる。
     *
     * @param {number} index        全行順の添字
     * @param {number} currentPrice 再生中の現在値
     * @returns {?{price: number, distance: number, gap: ?number}}
     */
    numbersAt(index, currentPrice) {
      const entry = entries[index];
      if (!entry || !Number.isFinite(entry.smooth)) {
        return null;
      }
      const previous = index === 0 ? null : entries[index - 1];
      return {
        price: entry.smooth,
        distance: entry.smooth - currentPrice,
        gap: previous === null || !Number.isFinite(previous.smooth)
          ? null
          : previous.smooth - entry.smooth,
      };
    },

    /** 台帳を捨てる（版面を畳むとき）。 */
    clear() {
      entries = [];
      indexByKey.clear();
    },
  };
}
