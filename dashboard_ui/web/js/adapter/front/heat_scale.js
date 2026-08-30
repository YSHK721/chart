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

// 色値は暗色テーマ上での**実測**で決めた（ISSUE-461）。旧値（青 54,122,246 ／ 橙赤 229,92,54）は
//   端（p = 0 / 1）を合成したときの対 uiText コントラスト比が、下部ペインの地では 4.312、行 hover
//   の地では 4.045 しか無く、本文の下限 4.5（WCAG 2.1 SC 1.4.3）を割っていた。色相はそのまま
//   （青＝冷／橙赤＝熱）で明度だけ下げてある。合否は dashboard_theme_contrast.test.js が
//   heat_scale と dashboard.css の**現在値を読み直して**毎回計算する（数値は焼き込まない）。

/** 沈静側（p < 0.5）の色相。青系＝「冷えている」。 */
const CALM_RGB = '48, 104, 214';

/** 過熱側（p > 0.5）の色相。橙赤系＝「熱い」。 */
const HOT_RGB = '198, 74, 46';

/**
 * 端（p = 0 / p = 1）での不透明度。1.0 にすると地を完全に覆って文字が読めなくなるため、
 * 明暗どちらのテーマでも本文が読める上限に留める。
 */
const MAX_ALPHA = 0.62;

/** 色を置かないことを表す値（空文字＝`style.backgroundColor` へ入れると解除になる）。 */
export const NO_LEVEL_COLOR = '';

/**
 * §5.3.2 の「帯外単一色」。目盛りの上のどの色とも一致しない色相（緑系）にして、
 * 濃さを読んだ利用者が在りもしない `p` を読まないようにする。
 */
const TAIL_UNSCALED_COLOR = 'rgba(104, 126, 104, 0.42)';

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
