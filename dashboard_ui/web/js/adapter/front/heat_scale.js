// heat_scale（adapter/front/heat_scale.js）— 連続量 `p ∈ [0,1]` から色への**唯一の写像**。
//
// 設計入力: 基本設計書 §5.3（配色の基準は因果ローリング分位 `p`。段の名前［帯内 / 上帯超 /
//   ext 超 …］は廃止し、セルは連続量 1 つで塗る）、§5.5.5（第 1 表の価格セル背景も同じ `p` の
//   目盛りを使う）、§5.5.7（**配色の基準は 1 冊に 1 つ**）、§5.3.2（GPD が当てはまらない
//   セルは帯外を単一色にして「目盛りが無い」ことを示す）。
//
//   0.0 ────────────────── 0.5 ────────────────── 1.0
//   沈静                   中立                   過熱
//
// なぜ「地の色」ではなく「不透明度」で作るのか（テーマで破綻させないため）:
//   本表示層は統合ページの下部ペインへ**直接**挿す（sim のような別文書ではない）。したがって
//   背景色は宿主のテーマ（明 / 暗）の上に載る。地を塗り潰す配色にすると、片方のテーマで
//   文字色との対比が消えて読めなくなる。中立（p = 0.5）を完全透明にして地をそのまま見せ、
//   0.5 から離れるほど濃くすれば、どちらのテーマでも「濃さ ＝ 0.5 からの隔たり」という
//   読み方が保たれる。色相は向き（沈静 / 過熱）だけを担い、量は濃さが担う。
//
// 本モジュールが唯一源であることは heat_scale.test.js の
//   `the_scale_has_no_second_definition_in_the_front_tree` が機械的に強制する
//   （他の front モジュールが自前の色を書き始めたら赤くなる）。

// 色値は版面モックのパレットへ**調和**させた（ISSUE-463・依頼者指示 2026-08-30）。
//   冷側 = --cyan 系 / 熱側 = --down 系 / 帯外 = --muted 系。写像（|p − 0.5| に比例した
//   不透明度・0.5 で完全透明）と署名は変えていない。変えたのは色値と端の不透明度だけである。
//
// なぜ**明色テーマ側の**トークン値を採るのか: 本モジュールは 1 つの rgba を返すので、テーマ
//   ごとに色を変えられない（CSS 変数を読むと純関数でなくなり、検定が実ブラウザ無しでは
//   合否を出せなくなる）。明色側の値は暗色側より明度が低く、暗色テーマの地に載せても地を
//   明るくしすぎない。両テーマの実測は dashboard_theme_contrast.test.js が毎回計算する。
//
// 端の不透明度は 0.62 → 0.40 へ下げた。0.62 のままだと暗色テーマの到達行（--up-bg 帯）の上で
//   --ink2 との対比が 3.54 まで落ちる（実測）。0.40 は 4.5 を満たしつつ、隣り合う p（刻み
//   0.125）の ΔE ≥ 3 も両テーマで保つ上限側の値である。数値は焼き込まず検定が読み直す。

/** 沈静側（p < 0.5）の色相。--cyan（明色側 #117988）＝「冷えている」。 */
const CALM_RGB = '17, 121, 136';

/** 過熱側（p > 0.5）の色相。--down（明色側 #c33e3b）＝「熱い」。 */
const HOT_RGB = '195, 62, 59';

/**
 * 端（p = 0 / p = 1）での不透明度。1.0 にすると地を完全に覆って文字が読めなくなるため、
 * 明暗どちらのテーマでも本文が読める上限に留める。
 */
const MAX_ALPHA = 0.40;

/** 色を置かないことを表す値（空文字＝`style.backgroundColor` へ入れると解除になる）。 */
export const NO_LEVEL_COLOR = '';

/**
 * §5.3.2 の「帯外単一色」。目盛りの上のどの色とも一致しない色相（--muted の灰青系）にして、
 * 濃さを読んだ利用者が在りもしない `p` を読まないようにする。
 */
const TAIL_UNSCALED_COLOR = 'rgba(99, 110, 130, 0.42)';

/** `p` が目盛りに載る値か（載らないなら理由を示して落とす＝フェイルクローズ）。 */
function assertOnScale(p) {
  if (typeof p !== 'number' || !Number.isFinite(p) || p < 0 || p > 1) {
    throw new TypeError(`heat_scale: p は [0,1] の有限数である必要があります: ${String(p)}`);
  }
}

/**
 * 0.5 からの隔たりに比例した不透明度。
 *
 * @param {number} p 因果ローリング分位（§5.3）
 * @returns {number} 0（中立）〜 MAX_ALPHA（両端）
 */
export function alphaForP(p) {
  assertOnScale(p);
  return Math.abs(p - 0.5) * 2 * MAX_ALPHA;
}

/**
 * `p` に対応する背景色。
 *
 * @param {number|null|undefined} p 分位。`null` / `undefined` は「その地平に候補が無い」
 *   （§5.5.5）または「水準なし」を意味し、**色を置かない**（無言で 0.5 を埋めない）。
 * @returns {string} CSS 色（`rgba(...)`）。色を置かない場合は `NO_LEVEL_COLOR`。
 * @throws {TypeError} 数値だが [0,1] に載らないとき（p の定義が壊れた合図なので丸めない）。
 */
export function colorForP(p) {
  if (p === null || p === undefined) {
    return NO_LEVEL_COLOR;
  }
  const alpha = alphaForP(p);
  const rgb = p < 0.5 ? CALM_RGB : HOT_RGB;
  return `rgba(${rgb}, ${alpha})`;
}

/**
 * §5.3.2 の帯外単一色（GPD が当てはまらない 7 セル）。
 *
 * 「目盛りが無い」ことを示す色であり、`p` の濃さとして読んではならない。
 *
 * @returns {string} CSS 色
 */
export function tailUnscaledColor() {
  return TAIL_UNSCALED_COLOR;
}

/**
 * チャート一覧（timeframe_charts_view）の canvas 内配色（ISSUE-452 内容 2）。
 *
 * canvas の内側には CSS トークン（var(--…)）が届かないため、色値をここへ置く。置き場所が
 * 本モジュールなのは「front の色定義は 1 冊に 1 つ」（§5.5.7 と同じ規約・heat_scale.test.js の
 * `the_scale_has_no_second_definition_in_the_front_tree` が機械的に強制）だからである。
 *
 * 意味の対応（dashboard.css のパレットと同じ）:
 *   up = --up-bar（支持側＝現在値より下の水準・上昇ローソク）
 *   down = --down（抵抗側＝現在値より上の水準・下降ローソク）
 *   current = 統合ページのアクセント（モード切替ボタンの active と同色）
 *   text / grid = 明暗どちらのテーマの地（--surface）でも読める中間トーン
 */
export const CHART_COLORS = Object.freeze({
  up: '#26a69a',
  down: '#c33e3b',
  current: '#2962ff',
  text: '#8a94a6',
  grid: 'rgba(128, 140, 160, 0.18)',
});
