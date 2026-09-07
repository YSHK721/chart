// color_diagnostics.js — テーマの間違いを検出する（usecase・純関数）。
//
// 目的（依頼者指示 2026-08-09）: 全色を変更可能にするとトークンが増えて認知負荷が上がる。それを
//   機能で相殺する 2 本のうちの 1 本＝「間違いを検出する」。
//
// 責務（SRP）: 宣言済みの色の組を測って**事実**（どの語が、どれだけ）を返すだけ。
// 非責務: 保存の可否・UI 文言・報告先（console / 画面 / 無出力）。DOM・Storage・console のいずれ
//   にも触れない（R-4）。値の正規化（normalizeHexColor）と語彙の検査（F-C3）は上流の責務。
//
// **警告は保存を妨げない**（本モジュールの存在理由と同じくらい重要な規律）:
//   - `ok` / `code` のような合否を返さない。戻り値は Diagnostic の配列ただ 1 つで、空配列は
//     「欠陥が見つからなかった」であって「合格」ではない。
//   - color_themes.js は本モジュールを **import しない**。saveTheme は診断を呼ばず、戻り値にも
//     含めない。これは規約ではなく構造で、tests/color_theme_derivation_wiring.test.js の走査
//     テストが固定する。
//   参照実装は F-C3 — usecase が事実（`res.ignoredTokens`）を戻り値で返し、adapter が warn し、
//   `res.ok` の判定には一切関与しない（color_theme_controller.js）。同じ規律に従う。
//
// Diagnostic = { code: string, tokens: string[], measured: number }
//   measured は**実測値**（W-C1 は衝突トークン数、W-C2 / W-C3 はコントラスト比）。閾値との差を
//   呼び出し側が示せるようにするため、真偽ではなく測定値を返す。
//
// 全域性（§7.3 LSP）: 不正入力でも例外を投げず空配列を返す。

import { COLOR_ROLES } from '../domain/color_roles.js';
import { contrastRatio, isNormalizedHex } from '../domain/color_value.js';

export const DIAGNOSTIC = Object.freeze({
  collision: 'W-C1',
  surfaceContrast: 'W-C2',
  directionContrast: 'W-C3',
});

// 地との比較で「図」として読ませるトークン（＝語彙 14 語から対象外 3 語を引いたもの）。
//   surface は地そのもの。grid・border は地との低コントラストが意図（最も控えめな構造線）。
const NOT_FIGURE = new Set(['surface', 'grid', 'border']);
export const FIGURE_TOKENS = Object.freeze(COLOR_ROLES.filter((t) => !NOT_FIGURE.has(t)));

// WCAG 2.1 SC 1.4.11（非テキストコントラスト）。着色対象は線・帯・ローソク＝グラフィカル
//   オブジェクトなので、本文テキストの 4.5 ではなく 3.0 を採る。
export const SURFACE_CONTRAST_MIN = 3.0;

// 方向（上下）の輝度差の下限。根拠は参照実装の実測（後述の detectDirectionContrast 参照）。
export const DIRECTION_CONTRAST_MIN = 1.15;

// W-C1 意味の衝突: 2 つ以上のトークンが同じ色を持つ＝画面上で意味を復元できない。
//   走査は語彙 14 語に対して行う（語彙外キーはトークンではないので衝突にならない）。
//   報告は「組」単位で 1 件（トークン数ぶんに増やさない＝F-C3 の「1 回」と同じ規律）。
function detectCollisions(roleColors) {
  const byValue = new Map();
  for (const token of COLOR_ROLES) {
    const value = roleColors[token];
    if (!isNormalizedHex(value)) {
      continue;
    }
    if (!byValue.has(value)) {
      byValue.set(value, []);
    }
    byValue.get(value).push(token);
  }
  const out = [];
  for (const tokens of byValue.values()) {
    if (tokens.length >= 2) {
      out.push({ code: DIAGNOSTIC.collision, tokens, measured: tokens.length });
    }
  }
  return out;
}

// W-C2 地とのコントラスト不足: 図として読ませるトークンが地から浮き上がるか。
//   地（surface）が宣言されていなければ判定しない — 既定の地との比較へ勝手に落とすと、
//   テーマが地を宣言していないだけの状態に「地に対して」の助言を出す（前提の捏造）ことになる。
//   実測（現行 14 色）: 図側の下限は muted 3.314 / secondary 3.434 で、閾値 3.0 を上回る。
function detectSurfaceContrast(roleColors) {
  const surface = roleColors.surface;
  if (!isNormalizedHex(surface)) {
    return [];
  }
  const out = [];
  for (const token of FIGURE_TOKENS) {
    const value = roleColors[token];
    if (!isNormalizedHex(value)) {
      continue;
    }
    const measured = contrastRatio(value, surface);
    if (measured < SURFACE_CONTRAST_MIN) {
      out.push({ code: DIAGNOSTIC.surfaceContrast, tokens: [token, 'surface'], measured });
    }
  }
  return out;
}

// 上下（bullish / bearish）が輝度で分かれているか。両方が宣言されているときだけ判定する。
//
//   閾値の根拠は**参照実装の実測**であって WCAG ではない（実測: 同梱プリセット「基本」1.366962 /
//   クロム既定 #26a69a・#ef5350 が 1.162741）。上下は色相で分けられており、WCAG の 3.0 や 4.5 を
//   採ると参照実装そのものが不合格になる。よって閾値は「現行の見た目より方向の輝度差が悪化して
//   いる」を検出する線に置く。捉えられるのは輝度側だけで、色相が同一で輝度だけ違う組は捉え
//   られない（色相距離の診断は語彙・domain 関数の追加を要するため本段階の範囲外）。
function detectDirectionContrast(roleColors) {
  const { bullish, bearish } = roleColors;
  if (!isNormalizedHex(bullish) || !isNormalizedHex(bearish)) {
    return [];
  }
  const measured = contrastRatio(bullish, bearish);
  if (measured >= DIRECTION_CONTRAST_MIN) {
    return [];
  }
  return [{ code: DIAGNOSTIC.directionContrast, tokens: ['bullish', 'bearish'], measured }];
}

// テーマ 1 件を診断する。3 種の診断は互いに独立で、片方が他方を抑止しない
//   （同色の上下は「意味の衝突」であり同時に「方向が分からない」＝両方が出る）。
//
// @param {{roleColors?: Object<string,string>}} input 宣言済み roleColors（保存形 hex6）。
// @returns {Array<{code: string, tokens: string[], measured: number}>} 助言の一覧（合否ではない）。
export function diagnoseTheme(input = {}) {
  const roleColors = input && typeof input === 'object' ? input.roleColors : null;
  if (!roleColors || typeof roleColors !== 'object' || Array.isArray(roleColors)) {
    return [];
  }
  return [
    ...detectCollisions(roleColors),
    ...detectSurfaceContrast(roleColors),
    ...detectDirectionContrast(roleColors),
  ];
}
