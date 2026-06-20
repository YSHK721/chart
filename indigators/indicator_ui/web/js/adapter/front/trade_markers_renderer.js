// trade_markers_renderer.js — 上流 lwc API（createSeriesMarkers）を隔離する adapter。
//
// 設計入力: CHART_TRADE_MARKERS_DETAILED_DESIGN.md §3.1、
//   CHART_TRADE_MARKERS_BASIC_DESIGN.md §12.5（C-3 v5 ハンドル方式）、§12.7（M-2 lwc サブセット・M-3 失敗時挙動）。
// chart_renderer.js と同層・同規約（upstream API の唯一の隔離点）。

export class TradeMarkersRenderer {
  // chart は任意（後方互換）。chart.timeScale().subscribeVisibleTimeRangeChange を購読できる場合のみ
  //   「可視範囲内マーカーのみ描画」モードに入る（§9 Fix v3・左端クランプ列の除去）。購読 API 非提供・
  //   chart 省略時は全件描画フォールバック（現行挙動）。
  constructor({ lwc, mainSeries, chart = null }) {
    this._lwc = lwc;
    this._series = mainSeries;
    this._handle = null;
    this._all = []; // load した全 lwc マーカー（昇順）。範囲フィルタの元集合。
    this._range = null; // 直近の可視時間範囲（null=初期・未確定）。
    this._rangeAware = false; // 可視範囲購読が成立したか（成立時のみ範囲フィルタを適用）。

    const sub = chart && chart.timeScale && chart.timeScale();
    if (sub && typeof sub.subscribeVisibleTimeRangeChange === 'function') {
      this._rangeAware = true;
      sub.subscribeVisibleTimeRangeChange((range) => this._applyRange(range));
    }
  }

  // 可視範囲変更時のコールバック。範囲を保持し、範囲内（from<=time<=to）のマーカーのみ適用する。
  _applyRange(range) {
    this._range = range;
    this._render();
  }

  // 現在の可視マーカー集合を upstream へ反映する単一の経路（範囲変更・load 共通）。
  _render() {
    this.setMarkers(this._visibleMarkers());
  }

  // _rangeAware 時は _range で絞った集合、それ以外（フォールバック）は全件を返す。
  //   range が null（初期未確定）の場合は空（左端クランプ列を出さない）。
  _visibleMarkers() {
    if (!this._rangeAware) {
      return this._all;
    }
    const r = this._range;
    if (!r) {
      return [];
    }
    return this._all.filter((m) => r.from <= m.time && m.time <= r.to);
  }

  // lwcMarkers: [{time,position,shape,color,text}]（昇順）。
  //   初回は createSeriesMarkers でハンドル生成、以降はハンドルへ setMarkers（v5・C-3）。
  setMarkers(lwcMarkers) {
    if (!this._handle) {
      this._handle = this._lwc.createSeriesMarkers(this._series, lwcMarkers);
    } else {
      this._handle.setMarkers(lwcMarkers);
    }
  }

  // 既存マーカーを空配列で除去（ハンドル未生成時は no-op）。
  clear() {
    if (this._handle) {
      this._handle.setMarkers([]);
    }
  }

  // JSON を取得し lwc サブセットのみ抽出して付与する。失敗は warn + 0 件（candles 非干渉＝M-3）。
  async load(url, fetchFn = fetch) {
    try {
      const res = await fetchFn(url);
      if (!res.ok) {
        console.warn(`[trade-markers] fetch ${res.status}`);
        return 0;
      }
      const json = await res.json();
      const lwc = (json.markers || []).map((m) => m.lwc); // lwc サブセットのみ抽出（M-2）
      this._all = lwc; // 全件保持（範囲フィルタの元集合・§9）。
      this._render(); // 範囲確定済みなら範囲内のみ、フォールバックは全件。
      if (json.count != null) {
        console.info(`[trade-markers] ${json.count} markers`); // H-4 明示
      }
      return lwc.length;
    } catch (e) {
      console.warn('[trade-markers] load failed', e);
      return 0;
    }
  }
}
