// color_resolver.js — 「色の決定」の純関数群（基本設計_指標カラーテーマ.md §4.5〜§4.8・§5.8）。
//
// 責務は色を**決める**ことだけ（SRP）。適用先（applySeriesStyle / chart.applyOptions /
//   :root への setProperty）は知らない。DOM・Storage・lightweight-charts のいずれにも依存
//   しない純 usecase であり、テストは値の写像だけで全分岐を固定できる（§7.3 DIP）。
//
// 全域性（§7.3 LSP）: すべての公開関数は role が null でも未知トークンでも不正な色でも同じ
//   シグネチャで定義され、呼び出し側に分岐を要求しない。例外は投げない。
//
// ライブ・リプレイ共有（replay 側は symlink 参照＝単一実体）。

import { COLOR_ROLES, isColorRole } from '../domain/color_roles.js';
import { CHROME_DEFAULT, CHROME_SLOTS, chromeSlot } from './chrome_tokens.js';
import { TF_CODES } from '../domain/tf_meta.js';

// 解決順の最終段（§4.5 ステップ 5）。既存 properties_dialog の静的フォールバックと同値。
export const DEFAULT_SERIES_COLOR = '#2962ff';

// 既存 toHex（property_control_builders.js）が受理する集合と一致させる（§4.5）。
const RE_HEX3 = /^#[0-9a-fA-F]{3}$/;
const RE_HEX6 = /^#[0-9a-fA-F]{6}$/;
const RE_RGB = /^rgba?\(/i;
// テーマの保存値は正規化済みの小文字 6 桁のみ（§4.4 roleColors）。
const RE_HEX6_LOWER = /^#[0-9a-f]{6}$/;

function isColor(v) {
  return typeof v === 'string' && (RE_HEX3.test(v) || RE_HEX6.test(v) || RE_RGB.test(v));
}

function isHex6(v) {
  return typeof v === 'string' && RE_HEX6_LOWER.test(v);
}

function hex2(n) {
  return n.toString(16).padStart(2, '0');
}

function toChannels(hex) {
  return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
}

const clamp255 = (n) => Math.min(255, Math.max(0, n));

// =========================================================================
// §4.7 tfModifier 変調（指標系列のみ・クロムには適用しない）
// =========================================================================

// 明度係数 l ∈ [-1, 1] で hex を白/黒へ寄せる。l = 0 は恒等・l = 1 は #ffffff・l = -1 は #000000。
//   アルファは扱わない（変調式の入力を 1 形式に固定して決定論性を保つ・§4.7）。hex6 以外は素通し。
export function applyTfModifier(hex, tfModifier, timeframe) {
  if (!isHex6(hex)) {
    return hex;
  }
  const raw = tfModifier && Number.isFinite(tfModifier[timeframe]) ? tfModifier[timeframe] : 0;
  const clamped = Math.min(1, Math.max(-1, raw));
  const l = Math.floor(clamped * 1000 + 0.5) / 1000;
  if (l === 0) {
    return hex;
  }
  const out = toChannels(hex).map((c) => {
    const v = l >= 0 ? c + (255 - c) * l : c * (1 + l);
    return clamp255(Math.floor(v + 0.5));
  });
  return `#${out.map(hex2).join('')}`;
}

// =========================================================================
// §4.8 計算.時間足の解決（tfModifier の入力）
// =========================================================================

const TF_SET = new Set(TF_CODES);

// インスタンスの「計算.時間足」を解決する。'chart' / 未設定 / 未知値はチャート時間足へ落ちる。
export function resolveInstanceTimeframe(instanceParams, chartTimeframe) {
  const tf = instanceParams && instanceParams.timeframe;
  return TF_SET.has(tf) ? tf : chartTimeframe;
}

// =========================================================================
// §4.5 指標系列の解決順（5 段）
// =========================================================================

// 系列 1 本の色を決める。段の順序は固定（上から評価し最初に成立した段の値を返す）。
//   payloadColor は系列生成時に記録した**不変の** backend 既定色（styleMeta.baseColor）である
//   こと（R-6）。現在の描画色（styleMeta.color）を渡すと、テーマ A→B の切替結果が適用履歴に
//   依存して非決定になる（E-20: applySeriesStyle が styleMeta.color を破壊的に上書きするため）。
//   defaultColor（ステップ 5 の戻り値）は差し替えられる。既定は '#2962ff'（値を必ず返す全域関数
//   としての契約）。**適用側は null を渡す**こと: ロック色・意味色・個別色・payload 色のいずれも
//   存在しない系列は「色を決める材料が無い」のであって「#2962ff である」わけではない。そこへ
//   既定色を書き込むと、payload が色を持たず lwc の既定色で描かれていた系列の色を捏造して
//   変えてしまう（実測: getSeriesStyles が baseColor を供給しない後方互換 renderer で発生）。
export function resolveSeriesColor({
  styles = null, seriesName = null, role = null, theme = null,
  timeframe = null, payloadColor = null, defaultColor = DEFAULT_SERIES_COLOR,
} = {}) {
  const s = (styles && seriesName != null && styles[seriesName]) || null;
  // ステップ 1（ロック色・最優先）: テーマ適用の対象外にする明示の意思表示。
  if (s && s.colorLocked === true && isColor(s.color)) {
    return s.color;
  }
  // ステップ 2（意味色）: テーマが当該トークンを宣言しているときのみ。
  if (theme && isColorRole(role)) {
    const declared = theme.roleColors ? theme.roleColors[role] : null;
    if (isHex6(declared)) {
      return applyTfModifier(declared, theme.tfModifier, timeframe);
    }
  }
  // ステップ 3（ロックなし個別上書き）: これが無いとテーマ未設定時に既存ユーザーの色が
  //   payload 色へ戻る＝現行挙動の破壊になる（U-5）。
  if (s && isColor(s.color)) {
    return s.color;
  }
  // ステップ 4（backend 既定＝payload 色）。
  if (isColor(payloadColor)) {
    return payloadColor;
  }
  // ステップ 5（既定色）。
  return defaultColor;
}

// =========================================================================
// §4.6 クロムの解決順（2 段）
// =========================================================================

// トークン単位の解決（CSS カスタムプロパティへ供給する値・§4.3）。
//   クロムには styles も payload も存在しないため 2 段。tfModifier は引数に取らない
//   （時間足ごとに画面の地が動くと目的 1 に反する＝§4.7 で明示的に定めた非対称）。
export function resolveChromeColor({ token = null, theme = null } = {}) {
  if (theme && isColorRole(token)) {
    const declared = theme.roleColors ? theme.roleColors[token] : null;
    if (isHex6(declared)) {
      return declared;
    }
  }
  return CHROME_DEFAULT[token] ?? null;
}

// hex のチャネルへ整数オフセットを加える（§4.6）。丸め不要・0..255 でクランプ。
export function offsetChannels(hex, delta) {
  if (!isHex6(hex) || !Array.isArray(delta) || delta.length !== 3) {
    return hex;
  }
  const out = toChannels(hex).map((c, i) => clamp255(c + (Number.isFinite(delta[i]) ? delta[i] : 0)));
  return `#${out.map(hex2).join('')}`;
}

// surface 派生（減光ローソク・分析 tint・リプレイ減光境界）。
//   テーマが surface を宣言しないときは CHROME_DEFAULT.surface からの派生になり、現行リテラルを
//   厳密に再現する（E-29 の実測差分。恒等のための分岐は不要）。
export function resolveDerivedChromeColor({ delta = null, theme = null } = {}) {
  return offsetChannels(resolveChromeColor({ token: 'surface', theme }), delta);
}

// 配線点単位の解決（JS 機構が実際に書き込む値）。
//
// 既定値の単位が「トークン」ではなく「配線点」であることが恒等性の要（§7.4 段階 1 通過条件 6 /
//   D-11）。#7 paneSeparatorHover の現行値 rgba(178,181,189,0.2) は、束ねるトークン border の
//   現行値 #2a2e39 とは**別の色**である。テーマ未宣言時にトークン既定から合成すると現行の
//   見た目が変わってしまうため、未宣言時は slot.current を逐語で返す。
//   テーマが当該トークンを宣言したときだけ §4.6 の合成（派生 / alpha 付与）へ切り替える。
export function resolveChromeSlotColor({ slotId = null, theme = null } = {}) {
  const slot = chromeSlot(slotId);
  if (!slot) {
    return null;
  }
  const declared = theme && theme.roleColors ? theme.roleColors[slot.token] : null;
  if (!isHex6(declared)) {
    return slot.current;
  }
  if (slot.derivedFrom != null) {
    return offsetChannels(declared, slot.delta);
  }
  if (slot.alpha != null) {
    const [r, g, b] = toChannels(declared);
    return `rgba(${r}, ${g}, ${b}, ${slot.alpha})`;
  }
  return declared;
}

// クロム 1 回分の配信値をまとめて解決する（§5.2 UC-C02 手順 2）。
//   戻り値は 2 機構ぶん（§4.3 FR-C12）:
//     slots  … 配線点 id → 色（JS 機構が lightweight-charts のオプションへ書く値）
//     tokens … トークン → 色（CSS 機構が :root の --ct-<token> へ書く値。値が無ければ null）
//   トークンは**語彙 14 種すべて**を席として持つ。クロム既定を持たない指標側トークンは、
//   テーマが宣言していなければ null（＝CSS 側は removeProperty で前回の値を残さない）。
//   これにより app.css の残りリテラル（N-9）を v2 で var(--ct-*) へ置換するとき、
//   トークン表・resolver・applier の変更を伴わずに接続できる。
export function resolveAllChrome(theme = null) {
  const slots = {};
  for (const s of CHROME_SLOTS) {
    slots[s.id] = resolveChromeSlotColor({ slotId: s.id, theme });
  }
  const tokens = {};
  for (const token of COLOR_ROLES) {
    const declared = theme && theme.roleColors ? theme.roleColors[token] : null;
    tokens[token] = isHex6(declared) ? declared : (CHROME_DEFAULT[token] ?? null);
  }
  return { slots, tokens };
}

// =========================================================================
// §5.8 系列名 → トークンの解決
// =========================================================================

// 水準線（horizontal_line）は priceLine 経路で生成され styleMeta に載らないため、実描画系列名
//   として現れない（E-10）。よって本索引の対象外にする。除外しないと btlm_trail_marod /
//   ma_marod のように「同名の line（primary）と水平基準線（level）」が競合し、解決が非決定になる
//   （§4.1.3 規則 1 と規則 3 は、この名前空間の分離を入れて初めて両立する）。
const RENDERED_KINDS = new Set(['line', 'histogram', 'level_dash']);

// 実描画系列名 → ColorRole の索引を作る。命名規約の知識は持たず、動的パターンの展開は注入された
//   expandPattern（実体は series_name_matcher.expandSeriesNamePattern）に委ねる（DIP・§5.8）。
//   これにより展開規則の単一情報源が保たれ、usecase が adapter へ依存しない。
export function buildColorRoleIndex({ def = null, params = null, expandPattern = null } = {}) {
  const index = new Map();
  for (const s of (def && def.series) || []) {
    if (!s || !RENDERED_KINDS.has(s.kind) || !isColorRole(s.colorRole)) {
      continue;
    }
    if (s.dynamic && s.seriesNamePattern) {
      if (typeof expandPattern !== 'function') {
        continue;
      }
      for (const name of expandPattern(s.seriesNamePattern, params) ?? []) {
        // 規則 3 により同名は同一トークンのため、先勝ちでも順序が結果に影響しない。
        if (!index.has(name)) {
          index.set(name, s.colorRole);
        }
      }
    } else if (s.seriesName != null && !index.has(s.seriesName)) {
      index.set(s.seriesName, s.colorRole);
    }
  }
  return index;
}

// 系列 1 本のトークンを引く。未知系列は null（F-C7・エラーにしない）。
//   多数の系列を一度に解決する呼び出し側は buildColorRoleIndex を 1 度だけ作って引くこと
//   （本関数は 1 本ごとにパターンを展開するため）。
export function roleForSeriesName({ def = null, seriesName = null, params = null, expandPattern = null } = {}) {
  if (seriesName == null) {
    return null;
  }
  return buildColorRoleIndex({ def, params, expandPattern }).get(seriesName) ?? null;
}
