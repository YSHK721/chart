// chrome_css_var.js — 配線点 id を CSS カスタムプロパティ参照へ変える 1 行の adapter
//   （基本設計_指標カラーテーマ.md §4.3・§7.4 段階 5-E）。
//
// なぜ要るか（E-21 の JS 版）: チャート上の描画物のうち **DOM を書くもの**（取引明細ポップアップ・
//   時間足ツールチップ）は、CSS ファイルではなく JS のインライン style に色を持っていた。5-D で
//   app.css を `var(--ct-*)` へ移したのと同じことを、この経路にもやる必要がある。
//
// fallback は **CHROME_CURRENT から生成する**（リテラルを手書きしない）。ここが CSS ファイルとの
//   違いで、CSS は静的テキストなので fallback を逐語で書かざるを得ず、「fallback と解決値が
//   同じ色か」を別の検定（css_theme_identity）で見張る必要があった。JS は単一情報源を参照
//   できるので、二重定義そのものが構造的に発生しない（見張る対象が消える）。
//
// 責務は文字列の組み立てだけ（SRP）。色を決めない・DOM を触らない・lightweight-charts を知らない。
//
// 全域性（§7.3 LSP）: 未知 id でも例外を投げない。台帳に無い id は fallback を持てないため
//   `var(--ct-<id>)` を返す（CSS 側では未定義の変数＝宣言が無効になり、UA 既定へ落ちる）。
//   未知 id を握り潰さずに検出するのは走査テストの責務であって、実行時の例外ではない。

import { CHROME_CURRENT } from '../../usecase/chrome_tokens.js';

export function chromeVar(slotId) {
  const fallback = CHROME_CURRENT[slotId];
  return fallback == null ? `var(--ct-${slotId})` : `var(--ct-${slotId}, ${fallback})`;
}
