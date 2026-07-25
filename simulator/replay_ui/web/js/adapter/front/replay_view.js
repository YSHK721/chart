// replay_view.js — 再生層の副作用アダプタ。chart(lwc)/mainSeries/renderer/減光primitive/
//   再生バー DOM への副作用を隠蔽し、setupReplay（合成）と純ロジック（replay/*）が
//   lwc/DOM に直接触れないようにする。chart_renderer / replay_boundary_dim と同じ「lwc 隔離点」。
//
// 参照実装＝プロト web/js/replay.js の副作用呼び出し（mainSeries.update /
//   chart.timeScale().setVisibleLogicalRange / renderer.setCandles / attachPrimitive /
//   panes()/getSeries() 同期 / rp-* DOM）を、順序・分岐を 1つも足さず/削らず method 化。

import { ReplayBoundaryDimPrimitive } from './replay_boundary_dim.js';
import { boundaryTimeValue } from '../../replay/state.js';

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
  }

  // ---- DOM ヘルパ ---- //
  el(id) { return this._doc.getElementById(id); }
  setText(id, text) { const e = this.el(id); if (e) e.textContent = text; }
  createButton(label, className) {
    const b = this._doc.createElement('button');
    b.textContent = label;
    if (className) b.className = className;
    return b;
  }

  // ---- スライダー ---- //
  setSliderValue(v) { const e = this.el('rp-slider'); if (e) e.value = v; }
  setSliderBounds(min, max) { const e = this.el('rp-slider'); if (e) { e.min = min; e.max = max; } }
  setSliderMin(min) { const e = this.el('rp-slider'); if (e) e.min = min; }

  // ---- 速度 UI ---- //
  readSpeed() { const e = this.el('rp-speed'); return e ? parseFloat(e.value) : NaN; }
  writeSpeed(v) { const e = this.el('rp-speed'); if (e) e.value = v; }
  setSpeedVal(text) { this.setText('rp-speed-val', text); }
  speedPresets() { return [...this._doc.querySelectorAll('#rp-speed-presets .rp-preset')]; }

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

  // ---- follow トグル ---- //
  setFollow(on) { const e = this.el('rp-follow'); if (e) e.classList.toggle('on', on); }

  // ---- チャート表示 ---- //
  setVisibleLogicalRange(range) {
    try { this._chart.timeScale().setVisibleLogicalRange(range); } catch (_e) { /* レイアウト未確定は無視 */ }
  }
  getVisibleLogicalRange() {
    try { return this._chart.timeScale().getVisibleLogicalRange(); } catch (_e) { return null; }
  }
  // メイン系列の足を全置換（内部 fitContent を含む）。（renderer.setCandles）
  setCandles(candles) { this._renderer.setCandles(candles); }
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
        dim = new ReplayBoundaryDimPrimitive();
        host.attachPrimitive(dim);
        this._paneDims.set(host, dim);
      }
      dim.setBoundaryTime(boundaryTime);
    }
    for (const series of [...this._paneDims.keys()]) { // 再生成/削除で消えた系列の追跡を破棄
      if (!live.has(series)) this._paneDims.delete(series);
    }
  }

  // ---- 期間プリセット描画（時間足別・onSelect で純ロジックへ委譲） ---- //
  renderPresets({ presets, activeSecs, onSelect }) {
    const host = this.el('rp-presets');
    if (!host) return;
    host.innerHTML = '';
    for (const [label, secs] of presets) {
      const btn = this.createButton(label, 'rp-preset' + (secs === activeSecs ? ' on' : ''));
      btn.onclick = () => onSelect(secs);
      host.appendChild(btn);
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
