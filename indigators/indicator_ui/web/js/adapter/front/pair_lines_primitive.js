// pair_lines_primitive.js — 売買ペアを線分で結ぶカスタム ISeriesPrimitive（v4・§10.1/§10.2）。
// @upstream-isolation: pair_lines_primitive.js
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
import { PAIR_DIM_ALPHA } from './pair_render_constants.js';
import { CHROME_CURRENT } from '../../usecase/chrome_tokens.js';

// 非ハイライト線の減光 alpha（§10.2）。共有定数 PAIR_DIM_ALPHA を参照（単一情報源）。
//
// 色（段階 5-E・基本設計_指標カラーテーマ.md FR-C13）: 勝ち / 負けの色は**注入**で受ける。
//   本 primitive が現行リテラルを直接持つと、テーマで成果色を変えてもペア線だけ旧色に残る
//   （replay_boundary_dim.js が解いたのと同じ破綻）。未注入時の既定だけを台帳から引くため、
//   テーマなしの見た目は現行と厳密に同一である。

export class PairLinesPrimitive extends PairPrimitiveBase {
  constructor(pairs = []) {
    super(pairs);
    // 配信済みのペア線色（配信前＝現行リテラル）。setChromeColors だけが書き換える。
    this._win = CHROME_CURRENT.pairLineWin;
    this._loss = CHROME_CURRENT.pairLineLoss;
  }

  // 配信されたクロム色から自分の 2 点を取り込む。全域的（§7.3 LSP）: null・非オブジェクト・
  //   非文字列・部分指定のいずれでも例外を投げず、解釈できない指定は現行値を保つ。
  //   ChartRenderer.addChromeObserver が配る袋をそのまま渡せる形にしてあり、呼び出し側は
  //   「どの id が自分のものか」を知らなくてよい（配線点の知識は本 class に閉じる）。
  setChromeColors(slots) {
    if (!slots || typeof slots !== 'object') {
      return;
    }
    if (typeof slots.pairLineWin === 'string') {
      this._win = slots.pairLineWin;
    }
    if (typeof slots.pairLineLoss === 'string') {
      this._loss = slots.pairLineLoss;
    }
    if (typeof this._requestUpdate === 'function') {
      this._requestUpdate();
    }
  }
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
        ctx.strokeStyle = pair.win ? this._win : this._loss;
        ctx.globalAlpha = dimmed ? PAIR_DIM_ALPHA : 1;
        ctx.lineWidth = 1;
        ctx.moveTo(x1, y1);
        ctx.lineTo(x2, y2);
        ctx.stroke();
        ctx.restore();
      }
    });
  }
}
