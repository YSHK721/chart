// pair_lines_primitive.js — 売買ペアを線分で結ぶカスタム ISeriesPrimitive（v4・§10.1/§10.2）。
//
// 設計入力: CHART_TRADE_MARKERS_DETAILED_DESIGN.md §10。フェーズ2 実証済 v5 API:
//   mainSeries.attachPrimitive(primitive)・primitive.attached({chart,series,requestUpdate})・
//   paneViews()→renderer().draw(target)→target.useBitmapCoordinateSpace(scope => scope.context 描画)・
//   chart.timeScale().timeToCoordinate(time)・series.priceToCoordinate(price)（範囲外は null）。
//
// 共通ライフサイクル/状態（attach・pairs/highlight 保持・再描画要求・paneView）は PairPrimitiveBase に集約。
//   本クラスの責務は「ペア線の描画（_draw）」に限定する（SRP）。
//   （ローソク減光は §12 v6 で ChartRenderer の per-bar 着色へ移行済。本 primitive は減光を担わない。）
//
// 単体検証は fake target/scale/chart で行い（座標・色・alpha を観測）、canvas 実描画・実 lwc は
//   ブラウザ結合確認へ委譲する（§10.4・C3）。

import { PairPrimitiveBase } from './pair_primitive_base.js';

const C_WIN = '#26a69a';
const C_LOSS = '#ef5350';
const DIM_ALPHA = 0.15; // 非ハイライト線の減光 alpha（§10.2）。

export class PairLinesPrimitive extends PairPrimitiveBase {
  // 各 pair の (entryTime→x, entryPrice→y)〜(exitTime→x, exitPrice→y) を座標化して線分描画。
  //   いずれかの座標が null（範囲外）の pair はスキップ（§10.1・C3）。
  _draw(target) {
    if (!this._chart || !this._series) {
      return; // attach 前は座標源が無いので描画しない（防御・後方互換）。
    }
    const timeScale = this._chart.timeScale && this._chart.timeScale();
    if (!timeScale || typeof timeScale.timeToCoordinate !== 'function') {
      return;
    }
    const toX = (t) => timeScale.timeToCoordinate(t);
    const toY = (p) => this._series.priceToCoordinate(p);

    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      for (const pair of this._pairs) {
        const x1 = toX(pair.entry.time);
        const y1 = toY(pair.entry.price);
        const x2 = toX(pair.exit.time);
        const y2 = toY(pair.exit.price);
        // 範囲外（null）座標を含む pair はスキップ（C3）。
        if (x1 == null || y1 == null || x2 == null || y2 == null) {
          continue;
        }
        const dimmed = this._highlight != null && pair.i !== this._highlight;
        ctx.save();
        ctx.beginPath();
        ctx.strokeStyle = pair.win ? C_WIN : C_LOSS;
        ctx.globalAlpha = dimmed ? DIM_ALPHA : 1;
        ctx.lineWidth = 1;
        ctx.moveTo(x1, y1);
        ctx.lineTo(x2, y2);
        ctx.stroke();
        ctx.restore();
      }
    });
  }
}
