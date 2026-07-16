// pair_render_constants.js — 売買ペア overlay（ペア線・売買マーカー）の共有描画定数（ISSUE-095 項目3）。
//
// 設計入力: CHART_TRADE_MARKERS_DETAILED_DESIGN.md §10.2（非ハイライト要素の減光）。
// 由来: pair_lines_primitive.js（`DIM_ALPHA=0.15`）と trade_markers_renderer.js（`_DIM_ALPHA=0.15`）が
//   同値・同責務（売買ペアのホバー時に当該ペア以外を減光する alpha）で個別宣言していたものを単一情報源へ抽出。
// 命名の根拠（実測）: A方式（file:// フラットバンドル）は import を剥がし全 ES Modules を 1 スコープへ連結する。
//   market_profile_primitive.js が別責務・別値の `const DIM_ALPHA = 0.30`（累積バー減光）をトップレベル宣言しており、
//   本共有定数を `DIM_ALPHA` にするとバンドル時に二重宣言（SyntaxError）を再発させる。ゆえに distinct-name
//   `PAIR_DIM_ALPHA` とし、pair 側からトップレベル `DIM_ALPHA` を除去して衝突を解消する（挙動・実効値は不変）。

// 売買ペアのホバー減光 alpha（非ハイライトのペア線・売買マーカーに適用）。
export const PAIR_DIM_ALPHA = 0.15;
