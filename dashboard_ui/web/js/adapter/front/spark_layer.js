// spark_layer（adapter/front/spark_layer.js）— 第 2 表セル背景の**下層**＝指標ミニ描画。
//
// 設計入力（依頼者指示 2026-09-04・同日明確化）:
//   「指標を各セルのバックグラウンドに、指標ペインの最新データから過去に遡って 10 区間表示し、
//    その指標にヒートマップを重ねる。ヒートマップのレイヤーの下に指標ペインが見えるイメージ。
//    棒グラフやラインが表示されるイメージ。」
//   → 背景は 2 層: 上層＝ヒートストリップ（heat_scale.stripGradient）・下層＝本モジュール。
//
// 形は**指標ペインと同じ読み**（参照実装＝各指標の表示ペイン）: 積み上がる量（tickvol の
//   ヒストグラム）＝棒・それ以外（marod / RSI のライン）＝ライン。どちらを使うかはサーバの
//   事実申告 `cells[].cumulative` で決まり、ここで指標名の分岐を作らない。
//
// 値はサーバ応答（history[].value ＋ 現在値）そのもの＝フロントは数値を再計算しない
//   （arch-spec §9）。ここで行うのは表示スケーリング（min–max を版面高さへ写す）だけである。
//
// DOM を増やさず CSS の多層 background（先に書いた層が上）で重ねるため、SVG を data URI の
//   画像レイヤーとして返す。色は heat_scale の CHART_COLORS から引く（front の色定義は
//   1 冊に 1 つ・§5.5.7。ヒートは色相＝量を担うので、下層は中間トーン 1 色に固定して
//   競合する色相を持ち込まない）。

import { CHART_COLORS } from './heat_scale.js';

/** viewBox の高さ（版面へは 100% 100% で伸縮するので比だけが効く）。 */
const VIEW_HEIGHT = 30;

/** 1 区間ぶんの viewBox 幅（ヒートストリップの縞と同じ等分割＝縞と区間が揃う）。 */
const SLOT_WIDTH = 10;

/** 上下の余白（線が縁で切れないため）。 */
const PAD = 2;

/** ライン（積み上がらない量）の透過度。ヒート・文字より控えめに敷く。 */
const LINE_OPACITY = '0.6';

/** 棒（積み上がる量）の透過度。面で塗るのでラインより薄く。 */
const BAR_OPACITY = '0.35';

/**
 * 直近区間の値列 → 背景の画像レイヤー（CSS の `url("data:image/svg+xml,...")`）。
 *
 * @param {Array<number|null|undefined>} values 古い順（右端＝現在区間）。null は「描かない」。
 * @param {{bars?: boolean}} [opts] bars=true で棒（積み上がる量）、それ以外はライン。
 * @returns {string} 画像レイヤー。描ける点が 2 未満なら ''（レイヤーを作らない）。
 */
export function sparkLayer(values, { bars = false } = {}) {
  if (!Array.isArray(values) || values.length === 0) {
    return '';
  }
  const points = values.map(
    (value) => (typeof value === 'number' && Number.isFinite(value) ? value : null),
  );
  const present = points.filter((value) => value !== null);
  if (present.length < 2) {
    return '';
  }
  const width = points.length * SLOT_WIDTH;
  let min = Math.min(...present);
  const max = Math.max(...present);
  if (bars) {
    // ヒストグラムの基線は 0（指標ペインの tickvol と同じ読み）。負値が混ざる系では
    // 最小値を基線にする（棒が viewBox の外へ出ない）。
    min = Math.min(min, 0);
  }
  const span = max - min;
  const innerBottom = VIEW_HEIGHT - PAD;
  const yOf = (value) => (
    span === 0 ? VIEW_HEIGHT / 2 : PAD + (1 - (value - min) / span) * (innerBottom - PAD)
  );

  let shape;
  if (bars) {
    shape = points.map((value, index) => {
      if (value === null) return '';
      const top = yOf(value);
      return `<rect x="${(index * SLOT_WIDTH + 2).toFixed(2)}" y="${top.toFixed(2)}" `
        + `width="${SLOT_WIDTH - 4}" height="${Math.max(0, innerBottom - top).toFixed(2)}" `
        + `fill="${CHART_COLORS.text}" fill-opacity="${BAR_OPACITY}"/>`;
    }).join('');
  } else {
    const path = points
      .map((value, index) => (
        value === null
          ? null
          : `${(index * SLOT_WIDTH + SLOT_WIDTH / 2).toFixed(2)},${yOf(value).toFixed(2)}`
      ))
      .filter(Boolean)
      .join(' ');
    shape = `<polyline points="${path}" fill="none" stroke="${CHART_COLORS.text}" `
      + `stroke-opacity="${LINE_OPACITY}" stroke-width="1.5" vector-effect="non-scaling-stroke"/>`;
  }
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${VIEW_HEIGHT}" `
    + `viewBox="0 0 ${width} ${VIEW_HEIGHT}" preserveAspectRatio="none">${shape}</svg>`;
  return `url("data:image/svg+xml;utf8,${encodeURIComponent(svg)}")`;
}
