// tick_rate（domain/tick_rate.js）— 更新**頻度**から効果の濃度を出す規則。
//
// 設計入力（依頼者指示 2026-08-31）: 現在値の更新は方向色の濃度で表現し、2 秒でフェード
//   アウトする。濃度は「速く動いているほど濃い」——単色では速さが読めない。
//
// なぜ View から出したのか（ISSUE-502 段階 4C・F-2）:
//   これは**統計**（観測窓・件数・率）であって版面ではない。View に置くと、規則を単体で
//   検証するのに DOM を組み立てる必要があり、窓の長さや上限の変更が「表を描く関数」の
//   改変として現れる。変更要求の出所（依頼者の見た目指示）と版面の構造（列・セル）は
//   別のアクターなので、同じモジュールに同居させない（SRP）。
//
// 濃度の定義:
//   clamp(直近 WINDOW_SECONDS 秒の更新回数 / 秒 ÷ FULL_RATE, MIN_STRENGTH, 100) %
//   FULL_RATE は「これ以上で最濃」となる更新頻度。再生粒度は 100ms＝最大 10 回/秒で、
//   その半分（5 回/秒）を最濃に採った。下限は「動いたことが見える」最小濃度。
//   フェードの**時間**の唯一源は CSS（dash-tick-fade・2s）でありここには無い。
//
// 計算量: register 1 回あたり観測窓の長さに比例（窓は秒数で切るので有界）。時計を持たず、
//   発行も予約もしない——時刻は呼び手が注入する（View は時計を持たない規約と対称）。

/** 頻度の観測窓（秒）。 */
export const TICK_RATE_WINDOW_SECONDS = 2;

/** これ以上で最濃となる更新頻度（回/秒）。 */
export const TICK_FULL_RATE = 5;

/** 「動いたことが見える」最小濃度（%）。 */
export const TICK_MIN_STRENGTH = 25;

/**
 * 更新頻度の計器を作る。
 *
 * @param {object} [opts]
 * @param {number} [opts.windowSeconds] 観測窓（秒）
 * @param {number} [opts.fullRate]      最濃となる頻度（回/秒）
 * @param {number} [opts.minStrength]   下限の濃度（%）
 * @returns {{register: Function, fade: Function, reset: Function, strength: Function}}
 */
export function createTickRateMeter({
  windowSeconds = TICK_RATE_WINDOW_SECONDS,
  fullRate = TICK_FULL_RATE,
  minStrength = TICK_MIN_STRENGTH,
} = {}) {
  /** 直近の更新時刻（unix 秒）。 */
  let times = [];
  /** 現在の濃度（0〜100）。0＝無色。 */
  let strength = 0;

  return {
    /**
     * 更新 1 回を観測窓へ入れ、濃度を出す。
     *
     * 時計が無い環境（`nowSec === null`）は**最小濃度**にする——頻度を測れないまま
     * 濃さを名乗ると、観測していない量を発明することになる。
     *
     * @param {?number} nowSec 観測時刻（unix 秒）。null＝時計なし
     * @returns {number} 濃度（0〜100）
     */
    register(nowSec) {
      if (nowSec === null || nowSec === undefined) {
        strength = minStrength;
        return strength;
      }
      times.push(nowSec);
      times = times.filter((at) => nowSec - at < windowSeconds);
      const perSecond = times.length / windowSeconds;
      strength = Math.min(100, Math.max(minStrength, (perSecond / fullRate) * 100));
      return strength;
    },

    /** 効果だけを落とす（観測窓は保つ）。更新の無い描画周期・フェード完了で使う。 */
    fade() {
      strength = 0;
    },

    /** 観測ごと捨てる（版面を畳むとき）。 */
    reset() {
      times = [];
      strength = 0;
    },

    /** 現在の濃度（0〜100）。 */
    strength() {
      return strength;
    },
  };
}
