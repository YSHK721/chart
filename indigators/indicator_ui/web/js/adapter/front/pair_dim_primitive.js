// pair_dim_primitive.js — ホバー中ペア以外のローソク足を減光する dimming オーバーレイ primitive（v5・§11）。
//
// 設計入力: CHART_TRADE_MARKERS_DETAILED_DESIGN.md §11（案A・自己完結 dimming オーバーレイ）。
//   フェーズ2 確定: 専用 primitive を併設（PairLinesPrimitive に相乗りしない・SRP 分離）。
//   highlight 時のみ、ペア i の [左端, entryX] と [exitX, 右端] の x 帯に半透明暗色矩形を
//   pane 全高（scope.bitmapSize.height）で描画。entryX/exitX は timeScale().timeToCoordinate(time)。
//   座標 null（範囲外）はスキップ。highlight=null は何も描かない。
//
// 共通ライフサイクル/状態（attach・pairs/highlight 保持・再描画要求・paneView）は PairPrimitiveBase に集約。
//   本クラスの責務は「ペア帯外の減光描画（_draw）」と z 配置（zOrder）に限定する（SRP・ペア線は
//   PairLinesPrimitive が担う）。
//
// z 配置: zOrder()==='bottom'（ローソクの上・マーカー/ペア線の下を狙う）。実 z 合成はブラウザ確認。
// 単体検証は fake target/chart で行い（fillRect 矩形を観測）、canvas 実描画・実 z 合成は
//   ブラウザ結合確認へ委譲する（§11 受入・C3）。

import { PairPrimitiveBase } from './pair_primitive_base.js';

const DIM_FILL = '#000000'; // 暗色（半透明で重ねる）。
const DIM_ALPHA = 0.4; // 減光帯の不透明度（半透明）。

export class PairDimPrimitive extends PairPrimitiveBase {
  // ローソクの上・マーカー/ペア線の下に置くことを狙う（実 z 合成はブラウザ確認）。
  zOrder() {
    return 'bottom';
  }

  // highlight 中ペアの [左端, entryX] と [exitX, 右端] に pane 全高の半透明暗色矩形を描く。
  //   highlight=null・一致ペアなし・attach 前・座標 null はそれぞれスキップ（§11・C3）。
  _draw(target) {
    if (this._highlight == null) {
      return; // 非ホバーは減光しない。
    }
    if (!this._chart) {
      return; // attach 前は座標源が無い（防御・後方互換）。
    }
    const pair = this._pairs.find((p) => p.i === this._highlight);
    if (!pair) {
      return; // 一致ペアが無ければ何も描かない。
    }
    const timeScale = this._chart.timeScale && this._chart.timeScale();
    if (!timeScale || typeof timeScale.timeToCoordinate !== 'function') {
      return;
    }
    const entryX = timeScale.timeToCoordinate(pair.entry.time);
    const exitX = timeScale.timeToCoordinate(pair.exit.time);

    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const h = scope.bitmapSize.height; // pane 全高。
      const right = scope.bitmapSize.width; // pane 右端。
      ctx.save();
      ctx.fillStyle = DIM_FILL;
      ctx.globalAlpha = DIM_ALPHA;
      // 左帯 [左端(0), entryX]（entryX が範囲外 null ならスキップ）。
      if (entryX != null) {
        ctx.fillRect(0, 0, entryX, h);
      }
      // 右帯 [exitX, 右端]（exitX が範囲外 null ならスキップ）。
      if (exitX != null) {
        ctx.fillRect(exitX, 0, right - exitX, h);
      }
      ctx.restore();
    });
  }
}
