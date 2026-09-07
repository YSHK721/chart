// tickvol_bands_actor.js — 取引密度帯（時刻帯の背景色）のアクター。
//
// 責務: /tickvol_profile から帯を取得（キャッシュ）→ 表示中の足へ写像（domain/tickvol_bands）→
//   価格パネル（メインペイン）の背景プリミティブへ渡す。系列（線）は一切持たない（market_profile と
//   同じアクター駆動型）。
//
// 因果性（リプレイ）: `getUntil()` がリビール T（単一時計 to）を返す。帯は「until が属するセッション日
//   より前の N セッション」から作られる（当日非参照）ため、**同一セッション日内では応答が不変**。
//   よって再取得は sessionDayStart(until) が変わったときだけで足りる（バー送りごとには叩かない）。
//   ライブは getUntil() が null＝サーバの現在時刻。
//
// 時間足: 帯そのものは時間足に依存しない（バックエンドは常に 1 分足原子・15 分ビンで集計する）。
//   時間足の変更で必要なのは「どのバーを塗るか」の再計算だけで、再取得は起きない。

import { TickvolBandsPrimitive } from './tickvol_bands_primitive.js';
import { TF_BAR_SEC } from '../../domain/tf_meta.js';
import { sessionDayStart } from '../../domain/session_day.js';
import { bandRangesForCandles, tickvolBandsSupportsTf } from '../../domain/tickvol_bands.js';

const TVB_PRIMITIVE_KEY = 'tickvol_bands';

export class TickvolBandsActor {
  /**
   * @param {object} o
   * @param {Function} o.fetch fetch 実装（注入・テストで差し替え）。
   * @param {string} o.datasetRef データセット。
   * @param {object} o.renderer ChartRenderer（getCandles / attachBackgroundPrimitive）。
   * @param {() => string} o.getTimeframe 現在の時間足。
   * @param {() => (number|null)} [o.getUntil] リビール T（ライブは null を返す）。
   * @param {() => object} [o.makePrimitive] プリミティブ生成（既定 TickvolBandsPrimitive）。
   */
  constructor({ fetch: fetchImpl, datasetRef, renderer, getTimeframe, getUntil = () => null, makePrimitive }) {
    this._fetch = fetchImpl;
    this._datasetRef = datasetRef;
    this._renderer = renderer;
    this._getTimeframe = getTimeframe;
    this._getUntil = getUntil;
    this._makePrimitive = makePrimitive || (() => new TickvolBandsPrimitive());
    this._enabled = false;
    this._params = { sessions: null, pct: null };
    this._bands = [];
    this._key = null;      // 取得済みプロファイルのキー（同一なら再取得しない）
    this._inFlight = null; // 取得の二重発行を防ぐ
  }

  isEnabled() {
    return this._enabled;
  }

  // gear / apply / restore 共通の params 反映（値が変われば次回 refresh で再取得される）。
  setParams(params = {}) {
    this._params = {
      sessions: params.sessions ?? null,
      pct: params.pct ?? null,
    };
  }

  async setEnabled(on) {
    this._enabled = !!on;
    if (!this._enabled) {
      this._pushRanges([]); // 消灯（プリミティブは残すが塗らない）
      return;
    }
    await this.refresh();
  }

  // 帯の取得（必要時）と再描画。キーが同じなら取得せず写像だけをやり直す。
  async refresh() {
    if (!this._enabled) {
      return;
    }
    const key = this._cacheKey();
    if (key !== this._key) {
      await this._fetchBands(key);
    }
    this._apply();
  }

  // リプレイ時計の前進。セッション日が変わったときだけ再取得する（当日非参照＝日内は応答不変）。
  onClock() {
    if (!this._enabled) {
      return Promise.resolve();
    }
    return (this._cacheKey() === this._key) ? Promise.resolve() : this.refresh();
  }

  // 時間足変更: 帯は時間足に依存しないため再取得せず、塗るバーだけを引き直す。
  onTimeframeChange() {
    this._apply();
  }

  // 足の差し替え・足内更新（ChartRenderer の candle observer から）。
  onCandlesChanged() {
    this._apply();
  }

  // 指標の追加・削除後の再描画フック（メイン系列は作り直されないため装着は不要・塗り直しのみ）。
  onPanesChanged() {
    this._apply();
  }

  _cacheKey() {
    const until = this._getUntil();
    // ライブ（until=null）はサーバの現在時刻＝日付が変われば別キーにしたいので、日境界を
    //   クライアント時計から導く（分解能はセッション日単位で十分）。
    const day = (until == null)
      ? sessionDayStart(Math.floor(Date.now() / 1000))
      : sessionDayStart(until);
    return `${this._datasetRef}|${this._params.sessions}|${this._params.pct}|${day}`;
  }

  async _fetchBands(key) {
    if (this._inFlight) {
      await this._inFlight;
      if (key === this._key) {
        return;
      }
    }
    const until = this._getUntil();
    let url = `/tickvol_profile?datasetRef=${encodeURIComponent(this._datasetRef)}`;
    if (this._params.sessions != null) {
      url += `&sessions=${encodeURIComponent(this._params.sessions)}`;
    }
    if (this._params.pct != null) {
      url += `&pct=${encodeURIComponent(this._params.pct)}`;
    }
    if (until != null) {
      url += `&until=${encodeURIComponent(until)}`;
    }
    this._inFlight = (async () => {
      try {
        const res = await this._fetch(url);
        const body = await res.json();
        this._bands = (body && body.ok && Array.isArray(body.bands)) ? body.bands : [];
        this._key = key;
      } catch (_e) {
        this._bands = []; // 取得失敗は「塗らない」（前回の帯を別の日に流用しない）
        this._key = null;
      } finally {
        this._inFlight = null;
      }
    })();
    await this._inFlight;
  }

  // 帯 → 塗るバー → 背景プリミティブへ渡す。非対応時間足・無効時は空（＝塗らない）。
  _apply() {
    if (!this._enabled) {
      this._pushRanges([]);
      return;
    }
    const tf = this._getTimeframe();
    if (!tickvolBandsSupportsTf(tf)) {
      this._pushRanges([]); // 1 時間足より上は帯なし（依頼者確定）
      return;
    }
    const candles = (typeof this._renderer.getCandles === 'function') ? this._renderer.getCandles() : [];
    this._pushRanges(bandRangesForCandles(candles, this._bands, TF_BAR_SEC[tf]));
  }

  _pushRanges(ranges) {
    if (typeof this._renderer.attachBackgroundPrimitive !== 'function') {
      return; // 背景プリミティブを提供しない renderer（テストの fake 等）は非干渉
    }
    const primitive = this._renderer.attachBackgroundPrimitive(TVB_PRIMITIVE_KEY, this._makePrimitive);
    if (primitive) {
      primitive.setRanges(ranges);
    }
  }
}
