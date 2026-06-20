// trade_markers_renderer.js — 上流 lwc API（createSeriesMarkers）を隔離する adapter。
//
// 設計入力: CHART_TRADE_MARKERS_DETAILED_DESIGN.md §3.1、
//   CHART_TRADE_MARKERS_BASIC_DESIGN.md §12.5（C-3 v5 ハンドル方式）、§12.7（M-2 lwc サブセット・M-3 失敗時挙動）。
// chart_renderer.js と同層・同規約（upstream API の唯一の隔離点）。

import { PairLinesPrimitive } from './pair_lines_primitive.js';
import { PairDimPrimitive } from './pair_dim_primitive.js';

// v4 §10.2: 非ハイライト marker の減光色（rgba・低 alpha）。
const _DIM_ALPHA = 0.15;

// "#rrggbb" を rgba(r,g,b,alpha) へ変換する。非 hex はそのまま返す（防御）。
function _withAlpha(color, alpha) {
  if (typeof color !== 'string' || color[0] !== '#' || color.length !== 7) {
    return color;
  }
  const r = parseInt(color.slice(1, 3), 16);
  const g = parseInt(color.slice(3, 5), 16);
  const b = parseInt(color.slice(5, 7), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

export class TradeMarkersRenderer {
  // chart は任意（後方互換）。chart.timeScale().subscribeVisibleTimeRangeChange を購読できる場合のみ
  //   「可視範囲内マーカーのみ描画」モードに入る（§9 Fix v3・左端クランプ列の除去）。購読 API 非提供・
  //   chart 省略時は全件描画フォールバック（現行挙動）。
  //   v4: chart.subscribeCrosshairMove があれば購読し、hoveredObjectId（"t{i}:..."）から
  //   _highlight=i を解析して当該ペア以外を減光する（§10.2・C1）。mainSeries.attachPrimitive が
  //   あれば PairLinesPrimitive を付与して売買ペア線を描く（§10.1）。
  constructor({ lwc, mainSeries, chart = null }) {
    this._lwc = lwc;
    this._series = mainSeries;
    this._handle = null;
    this._all = []; // load した全 lwc マーカー（昇順）。範囲フィルタの元集合。
    this._range = null; // 直近の可視時間範囲（null=初期・未確定）。
    this._rangeAware = false; // 可視範囲購読が成立したか（成立時のみ範囲フィルタを適用）。
    this._highlight = null; // v4: ホバー中トレード i（null=非ホバー・全件通常）。
    this._primitive = null; // v4: 売買ペア線 primitive（attachPrimitive 非提供時 null）。
    this._dimPrimitive = null; // v5: ローソク減光 primitive（§11・attachPrimitive 非提供時 null）。

    const sub = chart && chart.timeScale && chart.timeScale();
    if (sub && typeof sub.subscribeVisibleTimeRangeChange === 'function') {
      this._rangeAware = true;
      sub.subscribeVisibleTimeRangeChange((range) => this._applyRange(range));
    }

    // v4・C1: crosshair 購読（マルチキャスト・既存 ChartRenderer 購読と共存）。
    //   副作用非衝突はブラウザ確認（DoD 分離）。node:test は購読登録のみ検証。
    if (chart && typeof chart.subscribeCrosshairMove === 'function') {
      chart.subscribeCrosshairMove((param) => this._onCrosshair(param));
    }
  }

  // 可視範囲変更時のコールバック。範囲を保持し、範囲内（from<=time<=to）のマーカーのみ適用する。
  _applyRange(range) {
    this._range = range;
    this._render();
  }

  // v4・§10.2: hoveredObjectId（"t{i}:entry"/"t{i}:exit"）から i を解析して _highlight を更新。
  //   無ければ解除（null）。いずれも単一 _render() 経路で再描画する（C2）。
  _onCrosshair(param) {
    const id = param && param.hoveredObjectId;
    const next = this._parseTradeIndex(id);
    this._highlight = next;
    this._render();
  }

  // "t{i}:..." から数値 i を取り出す（不一致は null）。
  _parseTradeIndex(id) {
    if (typeof id !== 'string') {
      return null;
    }
    const m = /^t(\d+):/.exec(id);
    return m ? Number(m[1]) : null;
  }

  // 現在の可視マーカー集合を upstream へ反映する単一の経路（範囲変更・load・hover 共通＝C2）。
  //   _highlight!=null の時は非ハイライト marker を減光色へ変換し、primitive へ highlight を転送する。
  _render() {
    // load 前（マーカー未保持・ハンドル未生成）は lwc に一切触れない（candles 非干渉・C1 共存）。
    //   crosshair 購読は既存 ChartRenderer と共有されるため、load 前の hover では何もしない。
    if (this._all.length === 0 && !this._handle) {
      return;
    }
    const visible = this._visibleMarkers();
    const applied = this._highlight == null
      ? visible
      : visible.map((mk) => (this._parseTradeIndex(mk.id) === this._highlight
        ? mk
        : { ...mk, color: _withAlpha(mk.color, _DIM_ALPHA) }));
    if (this._primitive) {
      this._primitive.setHighlight(this._highlight);
    }
    if (this._dimPrimitive) {
      this._dimPrimitive.setHighlight(this._highlight); // v5・§11: ローソク減光も単一経路で連動（C2）。
    }
    this.setMarkers(applied);
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

  // v4・§10.1: 売買ペア線 primitive を mainSeries へ付与する。attachPrimitive 非提供（旧 API）の
  //   series では skip（後方互換・throw しない）。再 load 時は既存 primitive へ pairs を差し替える。
  //   v5・§11: 同 series へ dimming オーバーレイ primitive も併設し（SRP 分離）、同じ pairs を供給する。
  _attachPairLines(pairs) {
    const canAttach = this._series && typeof this._series.attachPrimitive === 'function';
    // 売買ペア線 primitive（v4）。
    if (this._primitive) {
      this._primitive.setPairs(pairs);
    } else if (canAttach) {
      this._primitive = new PairLinesPrimitive(pairs);
      this._series.attachPrimitive(this._primitive);
    }
    // ローソク減光 primitive（v5・§11）。pairs は setPairs 経由で供給する（再 load も同経路）。
    if (!this._dimPrimitive && canAttach) {
      this._dimPrimitive = new PairDimPrimitive();
      this._series.attachPrimitive(this._dimPrimitive);
    }
    if (this._dimPrimitive) {
      this._dimPrimitive.setPairs(pairs);
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
      this._attachPairLines(json.pairs || []); // v4: 売買ペア線 primitive（§10.1）。
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
