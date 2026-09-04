// replay_view.js — 再生層の副作用アダプタ。chart(lwc)/mainSeries/renderer/減光primitive/
//   再生バー DOM への副作用を隠蔽し、setupReplay（合成）と純ロジック（replay/*）が
//   lwc/DOM に直接触れないようにする。chart_renderer / replay_boundary_dim と同じ「lwc 隔離点」。
//
// 参照実装＝プロト web/js/replay.js の副作用呼び出し（mainSeries.update /
//   chart.timeScale().setVisibleLogicalRange / renderer.setCandles / attachPrimitive /
//   panes()/getSeries() 同期 / rp-* DOM）を、順序・分岐を 1つも足さず/削らず method 化。

import { ReplayBoundaryDimPrimitive } from './replay_boundary_dim.js';
import { boundaryTimeValue } from '../../replay/state.js';
import { REALTIME, isRealtime } from '../../replay/timing.js';

export class ReplayView {
  constructor({ chart, mainSeries, renderer, document: doc }) {
    this._chart = chart;
    this._mainSeries = mainSeries;
    this._renderer = renderer;
    this._doc = doc;
    // 減光境界プリミティブ（メイン系列へ装着）＋ pane 系列の追跡（series -> primitive）。
    this._boundaryDim = new ReplayBoundaryDimPrimitive();
    this._paneDims = new Map();
    if (mainSeries && typeof mainSeries.attachPrimitive === 'function') {
      mainSeries.attachPrimitive(this._boundaryDim);
    }
    // 減光色（#20）はテーマの面（surface）に従属する（FR-C13）。配信済みの色を保持するのは
    //   ChartRenderer（クロムの色の書き手 1 箇所・§7.8）で、本 View はその購読者として
    //   受け取った色を自分が所有するプリミティブ群へ配るだけ（色を決めない）。
    //   購読口を持たない renderer（後方互換 Fake・単体テスト）では既定色のまま＝挙動不変。
    this._dimColor = null;
    if (renderer && typeof renderer.addChromeObserver === 'function') {
      // 登録直後に現在の保持値が 1 回届くため、起動時のテーマ復元にも追随する。
      renderer.addChromeObserver((slots) => this._applyBoundaryDimColor(slots && slots.replayBoundaryDim));
    }
  }

  // 配信された減光色を、メイン・全 pane のプリミティブへ配る（後から作る分は生成時に渡す）。
  _applyBoundaryDimColor(color) {
    if (typeof color !== 'string') {
      return;
    }
    this._dimColor = color;
    this._boundaryDim.setColor(color);
    for (const dim of this._paneDims.values()) {
      dim.setColor(color);
    }
  }

  // pane へ装着する減光プリミティブを作る（現在の配信色で生成する＝生成順序で色がずれない）。
  //   未配信（_dimColor=null）はプリミティブ側の既定＝現行リテラルになる（本 View は色を持たない）。
  _newBoundaryDim() {
    return new ReplayBoundaryDimPrimitive({ color: this._dimColor });
  }

  // ---- DOM ヘルパ ---- //
  el(id) { return this._doc.getElementById(id); }
  setText(id, text) { const e = this.el(id); if (e) e.textContent = text; }

  // ---- 期間ラベル（[ 3か月 ] 表示部） ---- //
  setRangeLabel(text) { this.setText('rp-range', text); }

  // ---- 速度 UI ---- //
  //   値の保持先は #rp-speed ボタンの data-speed（唯一の現在値）。表示は "x1.00" 形式。
  //   値域のクランプは replay/timing.js の clampSpeed が権威（View は素の値を読み書きする）。
  //   実時間テンポ（リアルタイム）は比でないため data-speed="realtime" の番兵値で保持し "リアルタイム" と表示する。
  readSpeed() {
    const e = this.el('rp-speed');
    if (!e) return NaN;
    return isRealtime(e.dataset.speed) ? REALTIME : parseFloat(e.dataset.speed);
  }
  writeSpeed(v) {
    const e = this.el('rp-speed');
    if (!e) return;
    if (isRealtime(v)) { e.dataset.speed = REALTIME; e.textContent = 'リアルタイム'; return; }
    const n = parseFloat(v);
    e.dataset.speed = String(n);
    e.textContent = `x${Number.isFinite(n) ? n.toFixed(2) : '—'}`;
  }

  // ---- モード UI ---- //
  readMode() { const e = this.el('rp-mode'); return e ? e.value : 'real_ticks'; }
  // 縮退モード集合に応じて option を hidden/disabled にし、選択中が縮退なら real_ticks へ退避。（syncModeOptions）
  applyModeDegeneration(degenerateSet) {
    const sel = this.el('rp-mode');
    if (!sel) return;
    for (const opt of sel.options) {
      const hide = degenerateSet.has(opt.value);
      opt.hidden = hide; opt.disabled = hide;
    }
    if (degenerateSet.has(sel.value)) sel.value = 'real_ticks';
  }

  // ---- 再生ボタン ---- //
  setPlayLabel(text) { const e = this.el('rp-play'); if (e) e.textContent = text; }
  setPlaying(on) { const e = this.el('rp-play'); if (e) e.classList.toggle('rp-playing', on); }
  // 未来足が無ければ減光＋クリック抑止し理由を title 表示。（updatePlayEnabled）
  setPlayEnabled(enabled, disabledMsg) {
    const e = this.el('rp-play');
    if (!e) return;
    e.classList.toggle('rp-disabled', !enabled);
    e.title = enabled ? '' : disabledMsg;
  }

  // ---- チャート表示 ---- //
  setVisibleLogicalRange(range) {
    try { this._chart.timeScale().setVisibleLogicalRange(range); } catch (_e) { /* レイアウト未確定は無視 */ }
  }
  getVisibleLogicalRange() {
    try { return this._chart.timeScale().getVisibleLogicalRange(); } catch (_e) { return null; }
  }
  // メイン系列の足を全置換（内部 fitContent を含む）。（renderer.setCandles）
  setCandles(candles) { this._renderer.setCandles(candles); }
  // [ISSUE-296] いま表示している基準ローソク（読み取り専用・renderer.getCandles）。リプレイ開始時に
  //   「ライブで表示中の窓」をそのまま引き継ぐために使う（同じ窓を取り直さない）。未提供時は空配列。
  getCandles() {
    return typeof this._renderer.getCandles === 'function' ? this._renderer.getCandles() : [];
  }
  // 最新足の足内更新（1 ティック）。ライブ同一設計: mainSeries.update 直呼びは renderer（＝
  //   ChartRenderer の candle observer）を迂回し、価格legend（currentPriceView）が確定足リビール
  //   （setCandles）でしか更新されず粒度が bar 単位に落ちる。ライブは足内 tick を renderer.updateLastCandle
  //   経由で流し tick 毎に observer を発火させて凡例を追従させる（live_tick_player→CandleFeed.updateLastCandle）。
  //   リプレイも同経路へ一本化し tick 粒度へ揃える。forming の time は常にリビール末尾足と同一のため
  //   updateLastCandle の後退ガードに抵触せず、trim は replay では未使用（setCandles が毎リビールで
  //   _lastTrimIdx=null）＝ガード非該当で従来同様に描画される。
  updateForming(bar) {
    try { this._renderer.updateLastCandle(bar); } catch (_e) { /* noop */ }
  }

  // ---- 減光境界（メイン＋全 pane を同一境界で同期） ---- //
  // replayStart/candles から境界 time を算出しメイン＋pane を同期。（syncBoundary/syncPaneDims）
  syncBoundary({ replayStart, candles }) {
    const t = boundaryTimeValue({ replayStart, candles });
    this._boundaryDim.setBoundaryTime(t);
    this._syncPaneDims(t);
  }
  _syncPaneDims(boundaryTime) {
    const panes = (typeof this._chart.panes === 'function') ? this._chart.panes() : [];
    const live = new Set();
    for (let i = 1; i < panes.length; i++) { // pane 0=メイン（mainSeries で減光済み）
      const seriesList = (typeof panes[i].getSeries === 'function') ? panes[i].getSeries() : [];
      const host = seriesList[0]; // pane 内の1系列へ装着＝pane 全体が減光
      if (!host || typeof host.attachPrimitive !== 'function') continue;
      live.add(host);
      let dim = this._paneDims.get(host);
      if (!dim) {
        dim = this._newBoundaryDim();
        host.attachPrimitive(dim);
        this._paneDims.set(host, dim);
      }
      dim.setBoundaryTime(boundaryTime);
    }
    for (const series of [...this._paneDims.keys()]) { // 再生成/削除で消えた系列の追跡を破棄
      if (!live.has(series)) this._paneDims.delete(series);
    }
  }

  // ---- チャート直接操作の検出（wheel/drag → autoFrame OFF） ---- //
  bindManualBrowse(onManual) {
    try {
      const elx = this._chart.chartElement ? this._chart.chartElement() : null;
      if (!elx) return;
      let down = false;
      elx.addEventListener('wheel', () => onManual(), { passive: true });
      elx.addEventListener('mousedown', () => { down = true; });
      elx.addEventListener('mousemove', () => { if (down) onManual(); });
      this._doc.addEventListener('mouseup', () => { down = false; });
    } catch (_e) { /* chartElement 非対応環境では従来挙動 */ }
  }

  chart() { return this._chart; } // E2E フック用（__rpChart）
}
