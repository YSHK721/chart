// playback_tempo.js — 再生テンポ（速度・実時間アンカ・ETA・フレーム待機）を所有するロール（ISSUE-256・SRP）。
//
// 設計入力（ISSUE-256）: `setupReplay` は 847 行の単一関数で、合成・再生駆動・足内アニメーション・
//   MP 連動・時間足反映が同一スコープに同居していた。変更要求の出所が違うものが 1 つの関数に
//   同居していると、どこを触っても全体が壊れうる。本モジュールはその 1 つ「テンポ」を切り出す。
//
// 状態も一緒に移す（ISSUE-181 と同じ方針）: 旧 setupReplay の局所変数
//   `rtAnchorMs` / `emaPeriodMs` / `lastComputeMs` / `frameTimer` / `frameResolve` / `frameStart`
//   は本クラスが所有する。呼び出し側はフィールドではなくメソッドで依頼する。
//
// 純ロジック（値と式）は `replay/timing.js` が唯一源。本クラスはその**状態と副作用**
//   （DOM 読み書き・setTimeout）だけを担い、数式を再実装しない。
//
// 依存は注入する（DOM/lwc/時計/タイマーを直接掴まない＝テスト可能）:
//   view            : 再生バーの読み書き（readSpeed / writeSpeed / readMode / setText）
//   getCandles/getBar/getTimeframe : 現在の再生対象（ETA と実時間アンカの計算に使う読み取り）
//   onSpeedAxisChanged : 速度の「軸」が変わったときの通知（比↔実時間再生。先読み計画の破棄）
//   now / setTimeout / clearTimeout : 時計とタイマー（既定は実装環境のもの）
import {
  clampSpeed, frameMs as frameMsOf, emaUpdate, periodMs, fmtEta,
  remainingTickvol, etaRealTicksMs, isRealtime,
} from './timing.js';
import { durationSecs } from './stream.js';

export class PlaybackTempo {
  constructor({
    view,
    getCandles,
    getBar,
    getTimeframe,
    onSpeedAxisChanged = null,
    now = (typeof performance !== 'undefined' ? () => performance.now() : () => Date.now()),
    // 既定はグローバルのタイマーを**呼び出し形で**包む。素の参照（`setTimeout`）を保持して
    //   `this._setTimeout(...)` と呼ぶと、ブラウザでは receiver が window でなくなり
    //   TypeError: Illegal invocation になる（node の偽タイマー注入では露見しない・実 UI で実測）。
    setTimeout: setTimeoutImpl = ((fn, ms) => setTimeout(fn, ms)),
    clearTimeout: clearTimeoutImpl = ((id) => clearTimeout(id)),
  }) {
    this._view = view;
    this._getCandles = getCandles;
    this._getBar = getBar;
    this._getTimeframe = getTimeframe;
    this._onSpeedAxisChanged = typeof onSpeedAxisChanged === 'function' ? onSpeedAxisChanged : null;
    this._now = now;
    this._setTimeout = setTimeoutImpl;
    this._clearTimeout = clearTimeoutImpl;

    // 旧 setupReplay の局所状態（そのまま移送）。
    this._rtAnchorMs = 0;      // 現在バーの「足始端」に対応する壁時計時刻（now 基準）
    this._emaPeriodMs = null;  // 1 足あたり所要の EMA（実測）
    this._lastComputeMs = null; // 直近 1 足の計算所要（ETA モデルの材料）
    this._frameTimer = null;
    this._frameResolve = null;
    this._frameStart = 0;
  }

  // ---- 速度（比 0.00〜1.00 / 実時間再生は別軸の番兵値） ----

  speed() { return clampSpeed(this._view.readSpeed()); }

  // 実時間再生「リアルタイム」（依頼者指示 2026-08-01）。比の速度とは別軸のテンポで、1足の壁時計所要は
  //   時間足の長さそのもの（1m→60秒）。足の窓 [winStart, nextCandle) ではなく時間足長を使うのは、
  //   窓は週末・休場をまたぐと数日に伸び（winEnd=次足 time）、そこで再生が数日止まるため
  //   ＝「市場が動いている時間だけを 1:1 で流す」が実時間再生の意味。
  realtime() { return isRealtime(this.speed()); }

  paused() { return !this.realtime() && this.speed() <= 0; } // 比の 0.00＝一時停止（実時間再生に 0 は無い）

  rtBarMs() { return durationSecs(this._getTimeframe()) * 1000; }

  // 速度を書き込み、依存する推定・待機・計画を整合させる（旧 applySpeed と同一手順）。
  applySpeed(v) {
    const wasRealtime = this.realtime();
    this._view.writeSpeed(clampSpeed(v));
    this._emaPeriodMs = null; // 旧速度の実測は陳腐化
    // 実時間再生の切替は /intraday の tick_secs 取得可否が変わる＝先読み済み計画のティック列も別物。
    if (this.realtime() !== wasRealtime && this._onSpeedAxisChanged) this._onSpeedAxisChanged();
    this.setEta();
    this.rescheduleFrameWait();
  }

  // ---- 実時間再生のアンカ ----

  // 足の始端（drive も足の予算に含める）。旧 `rtAnchorMs = barStart`。
  anchorBarStart(atMs) { this._rtAnchorMs = Number.isFinite(atMs) ? atMs : this._now(); }

  // 途中再開時のアンカ再計算。旧 `rtAnchorMs = performance.now() - off[startI]`。
  reanchorFromOffset(offsetMs) { this._rtAnchorMs = this._now() - offsetMs; }

  // 実時間再生の「足内 i 番目の目標時刻」。旧 `rtAnchorMs + off[i + 1]`。
  targetAtOffset(offsetMs) { return this._rtAnchorMs + offsetMs; }

  // ---- 所要の実測（ETA モデルの材料） ----

  noteComputeMs(ms) { this._lastComputeMs = ms; }

  observeBarDuration(dtMs) { this._emaPeriodMs = emaUpdate(this._emaPeriodMs, dtMs); }

  // 速度・モード変更で旧速度の実測を捨てる（旧 `emaPeriodMs = null`）。
  resetPeriodEstimate() { this._emaPeriodMs = null; }

  // ---- ETA 表示 ----

  setEta() {
    const candles = this._getCandles();
    const bar = this._getBar();
    const view = this._view;
    const remain = Math.max(0, (candles.length - 1) - bar);
    if (remain === 0) { view.setText('rp-eta', '完了予想 —'); return; }
    if (this.paused()) { view.setText('rp-eta', `完了予想 —（一時停止・残り${remain}足）`); return; }
    // 実時間再生は 1足＝時間足の長さ（計算・描画は足内に収まる前提）＝残り足数から厳密に出る。
    if (this.realtime()) {
      view.setText('rp-eta', `完了予想 ${fmtEta(remain * this.rtBarMs())}（残り${remain}足）`);
      return;
    }
    // ISSUE-044: real_ticks は cap 廃止（間引かない・絶対仕様）＝1足あたり点数が足ごとに桁で異なる
    //   （月足は数十万 tick）ため、旧 800 点 cap 前提のモデルも per-bar EMA も使わず、/candles の
    //   tickvol（実 tick 数）の残り総数から算出する。tickvol 欠損（旧データセット等）は従来モデルへ
    //   フォールバック（回帰なし）。他モードは点数 cap 済みで従来モデル（実測 EMA 優先）のまま。
    if (view.readMode() === 'real_ticks') {
      const tv = remainingTickvol(candles, bar);
      if (tv != null) {
        view.setText('rp-eta', `完了予想 ${fmtEta(etaRealTicksMs(tv, remain, this._lastComputeMs, this.speed()))}（残り${remain}足）`);
        return;
      }
    }
    const period = periodMs(this._emaPeriodMs, this._lastComputeMs, view.readMode(), this.speed());
    view.setText('rp-eta', `完了予想 ${fmtEta(remain * period)}（残り${remain}足）`);
  }

  // ---- フレーム待機 ----

  settleFrameWait() {
    if (this._frameTimer != null) { this._clearTimeout(this._frameTimer); this._frameTimer = null; }
    const resolve = this._frameResolve; this._frameResolve = null;
    if (resolve) resolve();
  }

  // 現時点から見たフレーム待機の残り ms。比の速度は「固定間隔 − 経過」、実時間再生は
  //   「足終端（アンカ＋時間足長）までの実残り」＝足内アニメで消費した分が自動的に差し引かれる。
  frameRemainMs() {
    return this.realtime()
      ? Math.max(0, this._rtAnchorMs + this.rtBarMs() - this._now())
      : frameMsOf(this.speed()) - (this._now() - this._frameStart);
  }

  waitFrame() {
    return new Promise((resolve) => {
      this._frameResolve = resolve;
      this._frameStart = this._now();
      this._frameTimer = this._setTimeout(() => this.settleFrameWait(), Math.max(0, this.frameRemainMs()));
    });
  }

  rescheduleFrameWait() {
    if (this._frameResolve == null) return;
    if (this._frameTimer != null) { this._clearTimeout(this._frameTimer); this._frameTimer = null; }
    const remaining = this.frameRemainMs();
    if (remaining <= 0) { this.settleFrameWait(); return; }
    this._frameTimer = this._setTimeout(() => this.settleFrameWait(), remaining);
  }
}
