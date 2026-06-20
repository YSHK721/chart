// trade_markers_renderer.js — 上流 lwc API（createSeriesMarkers）を隔離する adapter。
//
// 設計入力: CHART_TRADE_MARKERS_DETAILED_DESIGN.md §3.1、
//   CHART_TRADE_MARKERS_BASIC_DESIGN.md §12.5（C-3 v5 ハンドル方式）、§12.7（M-2 lwc サブセット・M-3 失敗時挙動）。
// chart_renderer.js と同層・同規約（upstream API の唯一の隔離点）。

export class TradeMarkersRenderer {
  constructor({ lwc, mainSeries }) {
    this._lwc = lwc;
    this._series = mainSeries;
    this._handle = null;
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
      this.setMarkers(lwc);
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
