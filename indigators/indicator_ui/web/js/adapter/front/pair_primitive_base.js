// pair_primitive_base.js — 売買ペア系カスタム primitive 共通基底（v4・リファクタ）。
//
// 設計入力: CHART_TRADE_MARKERS_DETAILED_DESIGN.md §10。lwc ISeriesPrimitive のライフサイクル/状態保持
//   （attach・座標源・pairs/highlight 状態・再描画要求・paneView）を共通スキャフォールドとして本基底へ集約し、
//   サブクラスは描画本体 _draw(target) の override のみで責務を分化する（依存方向・公開シグネチャ・後方互換は不変）。
//   現在の唯一のサブクラスは PairLinesPrimitive（§10・ペア線描画）。
//   （v5 の PairDimPrimitive（§11・帯減光）は §12 v6 で廃止済。ローソク減光は ChartRenderer の per-bar 着色へ移行。）
//
// 公開契約（サブクラスへ継承される不変条件）:
//   - attached({chart,series,requestUpdate}) / detached() で座標源を授受する。
//   - setPairs(pairs) / setHighlight(i) は状態を更新し requestUpdate を発火（attach 前は no-op）。
//   - paneViews() は単一 paneView を返し、その renderer().draw(target) が _draw(target) を呼ぶ。
//   - _draw(target) はサブクラスが override する描画フック（基底は no-op）。
//
// ライフサイクル定型（上記のうち attach・paneView・再描画要求・_draw フック）は
//   SeriesPrimitiveLifecycle が単一ソースとして持つ（ISSUE-479 Wave2b J-6）。本ファイルに残るのは
//   **ペア固有の状態**（_pairs / _highlight / setPairs / setHighlight）だけである。
//   公開契約そのものは 1 つも変わっていない（基底へ移しただけ）。

import { SeriesPrimitiveLifecycle } from './series_primitive_lifecycle.js';

export class PairPrimitiveBase extends SeriesPrimitiveLifecycle {
  // pairs: [{ i, side, win, entry:{time,price}, exit:{time,price} }]
  constructor(pairs = []) {
    super();
    this._pairs = pairs;
    this._highlight = null; // null=非ハイライト、i=トレード i を強調・他を減光。
  }

  // pairs を差し替えて再描画要求（load 時）。
  setPairs(pairs) {
    this._pairs = pairs || [];
    this._update();
  }

  // ハイライト対象トレード i（null で解除）を設定し再描画要求。
  setHighlight(i) {
    this._highlight = i;
    this._update();
  }
}
