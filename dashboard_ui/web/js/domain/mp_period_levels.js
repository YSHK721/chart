// mp_period_levels（domain/mp_period_levels.js）— 借りた tf-period 列から価格ラダーの MP 行を取り出す規則。
//
// 設計入力（依頼者指示 2026-09-29「「日」「週」「月」のMFの「POT」「VAH」「VAL」を追加しろ」・
//   水準到達シート基本設計書 §3.5.1 / §3.5.3）:
//   - 対象は 1D / 1W / 1M の 3 足 × POC（zp では POC*）・VAH・VAL の 9 行。
//   - 当期の列は**列の time がその足の現在バーの time に一致する列**。ライブの参照実装
//     （market_profile_primitive.js の tfPeriodLevelAt）と同じ `c.time === t` の規則であり、
//     近い列で代用しない（確定済みの過去期間を使わない）。
//   - 取得窓はその列だけを含む [time, time + 1)（controller は from <= 列 time < to で拾う）。
//   - 行の名前は live の描画ラベル（POC* / VAH / VAL）と同じ語。POC* か POC かは src の能力記述子
//     （mpSourceCapability の poc 様式）で決まる。記述子は live の公開面から注入で受け取る。
//
// 値は列の poc / va_high / va_low を**そのまま**使う。サーバは有限でない価格を要求ごと拒むため
//   （シート全体が落ちる）、値の無い水準は送らず、名前を返して呼び手が掲示する（無言の縮退の禁止）。
//
// 純関数のみ（DOM / HTTP / 時計を持たない）。

/** 対象の足（設計書 §3.5.1 の表の順）。 */
export const MP_PERIOD_TIMEFRAMES = Object.freeze(['1D', '1W', '1M']);

/** 列の欄 → 行の水準名（POC は様式で決まるので null）。並びは表示の順。 */
const LEVEL_FIELDS = Object.freeze([
  Object.freeze({ field: 'poc', level: null }),
  Object.freeze({ field: 'va_high', level: 'VAH' }),
  Object.freeze({ field: 'va_low', level: 'VAL' }),
]);

/**
 * POC 行の名前（ライブの描画と同じ語）。
 *
 * @param {?object} capability live の `mpSourceCapability(src)` の戻り値
 * @returns {string} 'POC*'（poc 様式が star）または 'POC'
 */
export function pocLabelOf(capability) {
  return capability && capability.poc === 'star' ? 'POC*' : 'POC';
}

/**
 * 当期の列（列の time が現在バーの time に一致する列。無ければ null）。
 *
 * @param {?Array<object>} columns `/tf_period_profile` の columns
 * @param {number} time その足の現在バーの time（秒）
 * @returns {?object}
 */
export function currentColumnOf(columns, time) {
  if (!Array.isArray(columns)) {
    return null;
  }
  const target = Number(time);
  return columns.find((column) => column && Number(column.time) === target) ?? null;
}

/**
 * 当期の列 1 本から行（要求の欄 `mp_levels` の要素）を作る。
 *
 * @param {object} opts
 * @param {string} opts.timeframe 足
 * @param {object} opts.column    当期の列
 * @param {string} opts.pocLabel  POC 行の名前（pocLabelOf の結果）
 * @returns {{levels: Array<{timeframe: string, level: string, price: number}>, missing: string[]}}
 */
export function levelsOfColumn({ timeframe, column, pocLabel }) {
  const levels = [];
  const missing = [];
  for (const { field, level } of LEVEL_FIELDS) {
    const name = level ?? pocLabel;
    const value = column ? column[field] : null;
    const price = value === null || value === undefined ? Number.NaN : Number(value);
    if (Number.isFinite(price)) {
      levels.push({ timeframe, level: name, price });
    } else {
      missing.push(name);
    }
  }
  return { levels, missing };
}

/**
 * 当期の列だけを含む取得窓。
 *
 * @param {number} time その足の現在バーの time（秒）
 * @returns {{from: number, to: number}}
 */
export function periodWindowOf(time) {
  return { from: Number(time), to: Number(time) + 1 };
}
