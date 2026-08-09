// color_derivation.js — 宣言済みトークンから残りのトークンを導出する（usecase・純関数）。
//
// 目的（依頼者指示 2026-08-09）: 全色を変更可能にするとトークンが増えて認知負荷が上がる。それを
//   機能で相殺する 2 本のうちの 1 本＝「ユーザーが決める項目を減らす」。基点 5 語（surface /
//   bullish / bearish / alert / primary）を宣言すれば、残り 9 語は導出で埋まる。
//
// 責務（SRP）: 「宣言されたトークンから、宣言されていないトークンの**既定値**を計算する」だけ。
// 非責務: 色の値の書き方の吸収（normalizeHexColor）・語彙の判定（isColorRole・F-C3）・
//   保存（color_themes）・適用（color_resolver）・診断（color_diagnostics）。DOM・Storage・
//   console のいずれにも触れない（R-4）。
//
// 3 つの規律（検定 tests/color_derivation.test.js が全数で固定する）:
//   1. 部分写像 — 導出元が揃わなければ導出先のキーを**生成しない**。これが恒等（D-11）の唯一の
//      保証であり、`expandRoleColors({})` が `{}` であることに帰着する。テーマ未設定・空テーマの
//      見た目が 1 ピクセルも動かないのは、分岐ではなくこの構成による。
//   2. 明示 > 導出 — 宣言済みのキーは絶対に上書きしない。導出は既定値の供給であって、
//      ユーザーの決定を書き換える機構ではない。
//   3. 出口の型 — 導出結果が保存形 hex6 にならなければキーを生成しない（domain/color_value.js の
//      「hex6 か null」規律にそのまま乗る＝消費者へ型ガードを配らない）。
//
// 導出値を**永続化しない**（§4.9 前方互換）: 本関数は読み出し側の射影（projectThemeForUse）
//   からのみ呼ばれる。永続値に導出結果を書き戻すと、係数を直したときに既存テーマだけ古い導出値で
//   固まり、「基点を変えたのに従属色が追随しない」という破綻が起きる。
//
// 係数の出所（実測・2026-08-09）: 現行 14 色（PRESET_THEMES「基本」）を標本として、各規則の軸上で
//   最小二乗により逆算した。導出は**既定値**であって現行値の再現ではないため一致は求めない。
//   基点 5 語に現行値を入れたときの、導出 9 語と現行値の距離（最大チャネル差）は次のとおり:
//
//     highlight  0   （厳密一致）        border     1     grid       2     text       5
//     neutral   15                       level     17     muted     18
//     range     28   （色相のみ一致）    secondary 51     （色相のみ一致）
//
//   7 語が差 28 以下に収まる。色相回転の 2 語（range / secondary）が外れるのは、rotateHue が
//   彩度・明度（max/min）を保つためである。現行 #26c6da / #7e57c2 は primary と彩度・明度が
//   異なり、色相差以外の情報を持つ。色相だけを規則にした帰結であり、差 28 / 51 は「現行値は
//   primary の色相回転では表せない」という事実の測定値である。
//
//   range の規則を「primary と alert の中間」から**色相回転**へ改めた経緯（実測 2026-08-09）:
//     当初表の mix(primary, alert, 0.5) は逆算不能だった。現行 range #26c6da は primary #42a5f5 と
//     alert #ffa726 を結ぶ線分の上に無く（チャネル別 t = [-0.1481, 16.5000, 0.1304] と発散、
//     最小二乗 t = 0.0046 は「range ≒ primary」＝ 2 色が判別できない退化解）、規則どおり 0.5 を
//     採ると #a1a68e（くすんだ黄緑）で最大チャネル差 123 になる。原因は表の設計思想の誤りで、
//     現行プリセットは range を「主出力と警戒の中間」ではなく**第 3 の寒色**（シアン）として
//     置いている。色相で逆算し直すと差は 123 → 28 に減り（primary 206.82 度 → range 186.67 度）、
//     secondary（+55 度）と対称な 1 つの規則「primary の色相回転」に揃う。

import { COLOR_ROLES } from '../domain/color_roles.js';
import {
  contrastRatio, desaturate, isNormalizedHex, mixChannels, rotateHue,
} from '../domain/color_value.js';

const WHITE = '#ffffff';
const BLACK = '#000000';

// 地（surface）から見て「対比が立つ側」。暗い地なら白、明るい地なら黒。
//   設計表の文言は「明度を上げる」だが、方向を明度で固定すると明るい地のテーマで構造線・文字が
//   地に溶ける（実測: surface=#ffffff で text が白へ寄る）。方向は**地に対して**定義する。
//   現行の暗い地（#131722）では対比側が白になるため、この一般化は現行値の逆算を変えない
//   （実測: CR(#131722, 白) = 17.898 / CR(#131722, 黒) = 1.173）。
function contrastAnchor(surface) {
  return contrastRatio(surface, WHITE) >= contrastRatio(surface, BLACK) ? WHITE : BLACK;
}

// 現行値からの逆算（最小二乗）で確定した係数。括弧内はチャネル別 t の実測。
const T_GRID = 0.058; //      LS 0.0579（[0.0508, 0.0603, 0.0633]）
const T_BORDER = 0.100; //    LS 0.1001（[0.0975, 0.0991, 0.1041]）
const T_TEXT = 0.820; //      LS 0.8196（[0.8051, 0.8147, 0.8416]）
const T_LEVEL = 0.609; //     LS 0.6085（[0.5316, 0.6402, 0.6559]）
const T_MUTED = 0.300; //     LS 0.2995（[0.3564, 0.2810, 0.2787]）
const T_HIGHLIGHT = 0.761; // LS 0.7611（[0.7826, 0.7674, 0.7143]）
const DEG_SECONDARY = 55; //  色相差 55.05 度（primary 206.82 度 → secondary 261.87 度）
const DEG_RANGE = -20; //     色相差 -20.15 度（primary 206.82 度 → range 186.67 度）

// 導出表（設計表の 9 行と同順・同内容）。token は導出先、from は導出元。
//   from が**すべて保存形 hex6 として揃ったときだけ**導出する（規律 1）。from は宣言値でも
//   導出値でもよく、表の順序が連鎖の順序になる（surface → text → level → muted）。
//   OCP: トークンを増やすときは本表へ 1 行足すだけで、expandRoleColors・台帳・呼び出し側は不変。
const DERIVATIONS = Object.freeze([
  {
    token: 'grid',
    from: Object.freeze(['surface']),
    of: (c) => mixChannels(c.surface, contrastAnchor(c.surface), T_GRID),
  },
  {
    token: 'border',
    from: Object.freeze(['surface']),
    of: (c) => mixChannels(c.surface, contrastAnchor(c.surface), T_BORDER),
  },
  {
    token: 'text',
    from: Object.freeze(['surface']),
    of: (c) => mixChannels(c.surface, contrastAnchor(c.surface), T_TEXT),
  },
  {
    token: 'level',
    from: Object.freeze(['text', 'surface']),
    of: (c) => mixChannels(c.surface, c.text, T_LEVEL),
  },
  {
    token: 'muted',
    from: Object.freeze(['level', 'surface']),
    of: (c) => mixChannels(c.level, c.surface, T_MUTED),
  },
  {
    token: 'highlight',
    from: Object.freeze(['text', 'surface']),
    of: (c) => mixChannels(c.text, contrastAnchor(c.surface), T_HIGHLIGHT),
  },
  {
    token: 'secondary',
    from: Object.freeze(['primary']),
    of: (c) => rotateHue(c.primary, DEG_SECONDARY),
  },
  {
    token: 'range',
    from: Object.freeze(['primary']),
    of: (c) => rotateHue(c.primary, DEG_RANGE),
  },
  {
    token: 'neutral',
    from: Object.freeze(['primary']),
    of: (c) => desaturate(c.primary),
  },
].map(Object.freeze));

// 導出先の一覧（表から機械的に導く）。手書きの配列を置くと表と二重定義になり取り残しが出る。
export const DERIVED_ROLE_TOKENS = Object.freeze(DERIVATIONS.map((r) => r.token));

const DERIVED_SET = new Set(DERIVED_ROLE_TOKENS);

// 基点＝語彙のうち導出先でないもの。台帳の全数性（基点 ∪ 導出 = 語彙 14 語）が構成上保たれる。
export const BASE_ROLE_TOKENS = Object.freeze(COLOR_ROLES.filter((t) => !DERIVED_SET.has(t)));

// 宣言済み roleColors を導出で埋めた新しい写像を返す（入力は破壊しない）。
//
//   入力は**正規化済み**（保存形 hex6）であること。書き方の吸収は normalizeHexColor の責務で、
//   本関数は色の演算だけを担う（変換点を 1 つに保つ）。語彙の判定も持たない（F-C3 は
//   normalizeRoleColors が済ませている）ため、語彙外キーは触らずそのまま持ち越す。
//
//   全域的（§7.3 LSP）: 非オブジェクト・null・配列でも例外を投げず `{}` を返す。
//
// @param {Object<string,string>} declared 宣言済み roleColors（保存形 hex6）。
// @returns {Object<string,string>} 宣言値 ＋ 導出値。宣言値は 1 つも書き換わらない。
export function expandRoleColors(declared) {
  if (!declared || typeof declared !== 'object' || Array.isArray(declared)) {
    return {};
  }
  const out = { ...declared };
  for (const rule of DERIVATIONS) {
    // 「宣言済み」はキーの在席ではなく**読める色の在席**で判定する。`in` で判定すると、値が色で
    //   ないキー（null・'red' 等）が導出を黙って抑止し、出口の型（生成キーは必ず hex6）が入力
    //   由来の値で破れる。読めない値は未宣言として扱う（F-C9 と同じ規律）。
    if (isNormalizedHex(out[rule.token])) {
      continue;
    }
    if (!rule.from.every((t) => isNormalizedHex(out[t]))) {
      continue;
    }
    const value = rule.of(out);
    if (isNormalizedHex(value)) {
      out[rule.token] = value;
    }
  }
  return out;
}
