// live_chart_kit_api.js — live core のチャート部品を他サブシステムへ公開する面（2026-09-26）。
//
// 借り手: sim の表示層（統合層経由で注入される）。sim のジョブ結果を上のチャート領域（simチャート）へ
//   出すとき、ライブチャートと**同じ部品**で組み立てる（依頼者指示「sim の機能はライブのチャートへ
//   継承したい」「インポートで作れないか」、2026-09-27「チャートの操作性もライブモードと同期しろ」）。
//   部品を sim 側へ書き写すと、ライブチャートの見た目・操作性と食い違う第 2 実装が生まれる。
//
// 公開する名前（すべて借り手は simチャート）:
//   composeChartViewer     … チャート・読み取り欄・ペイン別凡例・ChartRenderer（ライブチャートと同じ組み立て）
//   installPaneGeometry    … ペイン幾何の供給（価格軸ホイールズームの pane 高・凡例の位置）
//   installChartOperations … 操作性（価格軸ホイール・ダブルクリック復帰・縦ドラッグ・最新足ボタン・右クリック）
//   ChartToastView         … 右クリック「情報をコピーする」の告知
//   TradeMarkersRenderer   … 売買マーク（売買ペア線・hover 減光・明細ポップアップ）
//   fetchCandleRange       … 時刻範囲の足（ライブチャートの fetchCandles と同じ /candles の問い合わせ）
//
// なぜ `live_public_api.js` と分けるか: あちらは dashboard core も読み込む軽い面である。チャート
//   部品（クロム色・ペア線 primitive 等の推移閉包）をそこへ足すと、借り手でない dashboard の
//   読み込みに巻き込まれる（`live_root_api.js` を分けたのと同じ重さの境界）。
//
// 中身を持たない: 再輸出だけを置く。

export {
  composeChartViewer, installPaneGeometry, installChartOperations, fetchCandleRange,
} from '../adapter/front/chart_app_wiring.js';
export { ChartToastView } from '../adapter/front/chart_toast_view.js';
export { TradeMarkersRenderer } from '../adapter/front/trade_markers_renderer.js';
