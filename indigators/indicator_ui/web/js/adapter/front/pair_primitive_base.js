// pair_primitive_base.js — 売買ペア系カスタム primitive 共通基底（v4/v5・リファクタ）。
//
// 設計入力: CHART_TRADE_MARKERS_DETAILED_DESIGN.md §10/§11。PairLinesPrimitive（§10・ペア線描画）と
//   PairDimPrimitive（§11・帯減光）は lwc ISeriesPrimitive のライフサイクル/状態保持（attach・座標源・
//   pairs/highlight 状態・再描画要求・paneView）が同形であり、唯一の差異は描画本体 _draw(target) である。
//   両者の SRP（ペア線描画 vs 帯減光）は _draw の override により分離を維持し、共通スキャフォールドのみ
//   本基底へ集約する（依存方向・公開シグネチャ・後方互換は不変）。
//
// 公開契約（サブクラスへ継承される不変条件）:
//   - attached({chart,series,requestUpdate}) / detached() で座標源を授受する。
//   - setPairs(pairs) / setHighlight(i) は状態を更新し requestUpdate を発火（attach 前は no-op）。
//   - paneViews() は単一 paneView を返し、その renderer().draw(target) が _draw(target) を呼ぶ。
//   - _draw(target) はサブクラスが override する描画フック（基底は no-op）。

export class PairPrimitiveBase {
  // pairs: [{ i, side, win, entry:{time,price}, exit:{time,price} }]
  constructor(pairs = []) {
    this._pairs = pairs;
    this._highlight = null; // null=非ハイライト、i=トレード i を強調・他を減光。
    this._chart = null;
    this._series = null;
    this._requestUpdate = null;
    // pane view（renderer を返す）。draw が現在の状態を読むため単一インスタンスで足りる。
    this._paneView = { renderer: () => ({ draw: (target) => this._draw(target) }) };
  }

  // lwc が attach 時に chart/series/requestUpdate を供給する。
  attached({ chart, series, requestUpdate }) {
    this._chart = chart;
    this._series = series;
    this._requestUpdate = requestUpdate;
  }

  detached() {
    this._chart = null;
    this._series = null;
    this._requestUpdate = null;
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

  // lwc へ再描画を要求（attach 前は no-op）。
  _update() {
    if (typeof this._requestUpdate === 'function') {
      this._requestUpdate();
    }
  }

  paneViews() {
    return [this._paneView];
  }

  // 描画フック。サブクラスが override する（基底は no-op）。
  _draw(_target) {}
}
