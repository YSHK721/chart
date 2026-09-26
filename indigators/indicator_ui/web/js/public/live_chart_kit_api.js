// live_chart_kit_api.js — live core のチャート部品を他サブシステムへ公開する面（2026-09-26）。
//
// 借り手: sim の表示層（統合層経由で注入される）。sim のジョブ結果を上のチャートへ出すとき、
//   ライブチャートと**同じ部品**（チャートの生成・売買マークの描画）で描く（依頼者指示「sim の
//   機能はライブのチャートへ継承したい」「インポートで作れないか」）。部品を sim 側へ書き写すと、
//   ライブの見た目・操作（売買ペア線・hover 減光・明細ポップアップ）と食い違う第 2 実装が生まれる。
//
// なぜ `live_public_api.js` と分けるか: あちらは dashboard core も読み込む軽い面である。チャート
//   部品（クロム色・ペア線 primitive 等の推移閉包）をそこへ足すと、借り手でない dashboard の
//   読み込みに巻き込まれる（`live_root_api.js` を分けたのと同じ重さの境界）。
//
// 中身を持たない: 再輸出だけを置く。

export { createChartWithMainSeries } from '../adapter/front/chart_bootstrap.js';
export { TradeMarkersRenderer } from '../adapter/front/trade_markers_renderer.js';
