// forming_animator.js — 足内アニメーション（形成中バーの推移描画と足内指標追従）を所有するロール（ISSUE-256・SRP）。
//
// 設計入力（ISSUE-256）: `setupReplay` は 847 行の単一関数で、その最大の塊がこの足内アニメーション
//   だった。変更要求の出所は「1 本の足の中をどう動かして見せるか」で、時間足の取得・カーソル移動・
//   DOM 配線とは独立している。
//
// 状態も一緒に移す（ISSUE-181 と同じ方針）: `animGen`（世代＝supersede 判定）・`formingInFlight`・
//   `lastFormingMs`（throttle）・`pausedForm`（停止足の続き）は本クラスが所有する。
//
// 挙動は抽出前と bit 同一（分岐・境界・await 順序・世代判定を 1 つも足さず/削らず移設した）。
//   純ロジック（サンプリング・窓・実時間オフセット）は replay/timing.js・stream.js・forming_plan.js が唯一源。
//
// 依存は注入する:
//   view / controller       : 描画と足内再計算の依頼先
//   getCandles/getBar/getTimeframe : 現在の再生対象（**スナップショットにしない**＝抽出前と同じ遅延読み取り）
//   tempo                   : 速度・実時間アンカ・ETA（PlaybackTempo）
//   plans                   : 足内計画の先読み/受け取り（FormingPlanCache）
//   mpOn / mpDriver         : MP tick-live 連動（無効時は非干渉）
//   sleepMs                 : 待機（テストから差し替え可能）
import { intrabarWindow } from './stream.js';
import { sampleIndices } from './forming_plan.js';
import { stepMs, ANIM_MIN_MS, FORMING_MIN_INTERVAL_MS, realtimeOffsetsMs } from './timing.js';
import { isSuperseded } from './state.js';
import { foldTick } from '../domain/forming_fold.js';

export class FormingAnimator {
  constructor({
    view, controller, getCandles, getBar, getTimeframe, tempo, plans,
    mpOn = () => false, mpDriver = null, sleepMs,
  }) {
    this._view = view;
    this._controller = controller;
    this._getCandles = getCandles;
    this._getBar = getBar;
    this._getTimeframe = getTimeframe;
    this._tempo = tempo;
    this._plans = plans;
    this._mpOn = mpOn;
    this._mpDriver = mpDriver;
    this._sleepMs = sleepMs;

    // 旧 setupReplay の局所状態（そのまま移送）。
    this._animGen = 0;            // 実行中フォーミングの世代（supersede 判定）
    this._formingInFlight = false; // 足内再計算の在飛行（throttle と着地待ちに使う）
    this._lastFormingMs = -1e9;    // 直近の足内再計算時刻（最小間隔ゲート）
    this._pausedForm = null;       // 停止した足の続き（再開時に使う）
  }

  // 停止足の続き（再開判定に使う）。所有者は本クラス。
  pausedForm() { return this._pausedForm; }

  // バーを動かす操作は停止足の続きを無効化する（旧 drive 冒頭の `pausedForm = null`）。
  clearPausedForm() { this._pausedForm = null; }

  // 実行中のフォーミングを supersede する（旧 disable() の `animGen += 1`）。世代の所有者は本クラス。
  supersede() { this._animGen += 1; }

  pushFormingMA(forming, win = null) {
    const nowMs = performance.now();
    if (this._formingInFlight || (nowMs - this._lastFormingMs) < FORMING_MIN_INTERVAL_MS) return;
    if (this._controller.isRecomputing()) return; // 他の再計算中は譲る
    this._lastFormingMs = nowMs;
    this._formingInFlight = true;
    this._controller.recomputeFormingLatest(forming, win)
      .catch(() => { /* 足内 MA 失敗はアニメ継続 */ })
      .finally(() => { this._formingInFlight = false; });
  }
  async settleFormingMA(forming, win = null) {
    while (this._formingInFlight) { await this._sleepMs(ANIM_MIN_MS); }
    if (this._controller.isRecomputing()) return;
    this._formingInFlight = true;
    try { await this._controller.recomputeFormingLatest(forming, win); }
    catch (_e) { /* 確定着地の失敗は次フレームの full 再計算が回復 */ }
    finally { this._formingInFlight = false; this._lastFormingMs = performance.now(); }
  }
  // MP tick-live グリッド拡張の駆動（growInFlight・pushGrowTo・settleGrowTo）は this._mpDriver（独立ドライバ）
  //   が所有する（ISSUE-133 SRP）。以下 animateForming は this._mpDriver.onFormingTick / settleMath / settleBar
  //   へ委譲する（await 順序・coalesce 意味論は抽出前と同一）。
  onModeChange() {
    this._animGen++;          // 実行中の形成を supersede
    this._plans.invalidate();  // [ISSUE-232] モードが変わればティック列も計画も別物＝破棄
    this._pausedForm = null;
    this._tempo.resetPeriodEstimate();
    this._tempo.setEta();
  };
  async animate(shouldAbort, resume) {
    if (!this._getCandles().length) return;
    const cd = this._getCandles()[this._getBar()];
    if (!cd) return;
    const myGen = ++this._animGen;
    const superseded = () => isSuperseded(myGen, this._animGen);
    const mode = this._view.readMode();
    if (mode === 'math') {
      window.__rpForm = { mode, n: 0 }; this._pausedForm = null;
      // math（終値）: 足内推移なし → その時間足の完成プロファイルを settleGrowTo(winEnd) で一度描く（成長なし）。
      //   確定形は全モード共通で real_ticks と同一（[当日, winEnd) の backend 実 dwell 全窓 fold へ収束）。
      //   MP OFF/未配線は非干渉。winEnd は intrabarWindow（fetch 不要・因果窓・未来リークなし）。
      if (this._mpOn()) {
        const { winEnd } = intrabarWindow({
          timeframe: this._getTimeframe(), cd, prevCandle: this._getCandles()[this._getBar() - 1] || null, nextCandle: this._getCandles()[this._getBar() + 1] || null,
        });
        await this._mpDriver.settleMath(winEnd);
      }
      return;
    }
    window.__rpAnimating = true;
    try {
      // [ISSUE-232] 先読み済みの計画があれば受け取る（無ければ null＝従来経路。**待たない**）。
      const plan = this._plans.take(this._getBar(), mode);
      const steps = plan ? plan.steps : null;
      let prices, secs, o, hi, lo, startI;
      if (resume && resume.time === cd.time) {
        prices = resume.prices; o = resume.o; hi = resume.hi; lo = resume.lo; startI = resume.i;
        secs = resume.secs || []; // MP tick-live: 停止再開時も sec 並行配列を additive 保持。
      } else {
        // 確定足のチラ見せ防止: fetch を await する前（同期）に最新足を始値へ畳む。
        if (mode !== 'math') {
          this._view.updateForming({ time: cd.time, open: cd.open, high: cd.open, low: cd.open, close: cd.open });
        }
        if (plan && Array.isArray(plan.prices) && plan.prices.length) {
          ({ prices, secs } = plan);   // 先読み済み＝ティック列取得の往復も省ける
        } else {
          ({ prices, secs } = await this._plans.buildStream(this._getBar(), mode));
        }
        if (superseded()) return;
        o = prices[0]; hi = prices[0]; lo = prices[0]; startI = 0;
      }
      // [ISSUE-232] 次バーの計画を先読み（fire-and-forget）。本バーの再生中にサーバが計算するため
      //   使用時点では出来上がっている＝待ち時間が表に出ない。間に合わなければ従来経路へ落ちる。
      this._plans.prefetch(this._getBar() + 1, mode);
      window.__rpForm = { mode, n: prices.length, planned: steps ? steps.size : 0 };
      // 実時間再生: 足内各点を市場時刻どおりの壁時計位置へ置く（アンカ基準＝sleep 誤差が累積しない）。
      //   点ごとの時刻 secs を持たないモード／欠損時は足を等分する（realtimeOffsetsMs）。
      // 足内窓（ISSUE-238 の実 tick 数算出／実時間再生のアンカ基準で共用・算出は 1 回）。
      let win = null;
      const formingWindow = () => {
        if (!win) {
          win = intrabarWindow({
            timeframe: this._getTimeframe(), cd, prevCandle: this._getCandles()[this._getBar() - 1] || null, nextCandle: this._getCandles()[this._getBar() + 1] || null,
          });
        }
        return win;
      };
      let rtOffsets = null;
      const rtOffsetsOf = () => {
        if (rtOffsets) return rtOffsets;
        const { winStart } = formingWindow();
        rtOffsets = realtimeOffsetsMs({ n: prices.length, secs, winStart, spanMs: this._tempo.rtBarMs() });
        return rtOffsets;
      };
      // 停止再開・途中でのテンポ切替では、再開点 startI が「今」に来るようアンカを巻き戻す。
      if (this._tempo.realtime() && resume && startI > 0) this._tempo.reanchorFromOffset(rtOffsetsOf()[startI]);
      // 1点分の待機。比の速度は従来の固定間隔、実時間再生は次点の到達時刻まで（末尾は待たない
      //   ＝足終端までの残りはフレーム待機 waitFrame が担う）。停止・supersede・テンポ切替を
      //   拾えるよう 50ms 刻みで刻む。
      const stepWait = async (i) => {
        if (!this._tempo.realtime()) { await this._sleepMs(stepMs(this._tempo.speed())); return; }
        const off = rtOffsetsOf();
        if (i + 1 >= off.length) return;
        const target = this._tempo.targetAtOffset(off[i + 1]);
        for (;;) {
          const remain = target - performance.now();
          if (remain <= 0) return;
          if (superseded() || (shouldAbort && shouldAbort()) || !this._tempo.realtime()) return;
          await this._sleepMs(Math.min(50, remain));
        }
      };
      for (let i = startI; i < prices.length; i++) {
        if (shouldAbort && shouldAbort()) {
          this._pausedForm = { time: cd.time, prices, secs, o, hi, lo, i };
          return;
        }
        if (superseded()) return;
        const p = prices[i];
        // 畳み方は domain/forming_fold が唯一源（ISSUE-272）。ここで式を写さない。
        const folded = foldTick({ open: o, high: hi, low: lo }, p);
        hi = folded.high; lo = folded.low;
        this._view.updateForming({ time: cd.time, ...folded });
        // [ISSUE-232] 計画があれば計算済み値を**同一同期ブロック**で反映する（＝ローソクと同時）。
        //   計画が無い／この時点が計画対象でないバーは従来どおりその場計算（非同期・遅延あり）。
        if (steps) {
          const step = steps.get(i);
          if (step) {
            this._controller.applyFormingStep(step);
          }
        } else {
          // ISSUE-238: `to`（リプレイ現在時刻）と足内窓を添える＝サーバが実 tick 数を数える。
          const state = { time: cd.time, ...foldTick({ open: o, high: hi, low: lo }, p) };
          if (secs && secs[i] != null) state.to = Math.floor(secs[i]);
          this.pushFormingMA(state, formingWindow());
        }
        // MP tick-live: この tick を DwellAccumulator へ供給し足内成長させる（sec 並走が有るバーのみ＝
        //   real_ticks・MP 有効。secs 空バーは skip＝base 継続）。速度0凍結/supersede の既存制御に追従。
        //   グリッド外 tick の growTo 発火（in-flight coalesce）＋feedTick は this._mpDriver が担う（ISSUE-133 SRP）。
        if (this._mpOn() && secs && secs[i] != null) {
          this._mpDriver.onFormingTick(p, secs[i]);
        }
        while (this._tempo.paused() && !superseded() && !(shouldAbort && shouldAbort())) await this._sleepMs(80); // 速度0=凍結
        if (superseded() || (shouldAbort && shouldAbort())) continue;
        await stepWait(i); // 再生速度で減速（毎ステップ読込＝速度変更を即時反映）
      }
      this._pausedForm = null;
      // 足確定: ティック列由来の OHLC で確定（cd.high/low へスナップしない）。
      const fc = prices[prices.length - 1];
      this._view.updateForming({ time: cd.time, open: o, high: hi, low: lo, close: fc });
      // MP tick-live: 確定時に当日窓全 tick を winEnd で再畳み込みしてグリッド確定（mp_core 一致点＝
      //   backend base=1 dwell と一致）してから最終 snapshot を強制描画する（throttle 無視）。
      //   確定形は MP 有効なら全モード winEnd で fold（growth の secs 有無から分離＝一般化）。real_ticks は
      //   最終実 tick 秒 t_k(<winEnd) ではなく winEnd で settle し、open_only は secs 空でも settle を発火する
      //   ＝全モード（real_ticks/every_tick/ohlc_1min/open_only/math）の完成 MP が backend fold(winEnd) で
      //   byte 一致（合成 dwell/始値のみは transient・settle=truth）。winEnd=足終端=settle 時の now（因果・
      //   未来リークなし＝次足 tick は半開区間 [dayStart, winEnd) で除外）。actor は空/縮退 forming を非破壊で
      //   扱う（データ無バーは前回描画保持）。
      if (this._mpOn()) {
        const { winEnd } = intrabarWindow({
          timeframe: this._getTimeframe(), cd, prevCandle: this._getCandles()[this._getBar() - 1] || null, nextCandle: this._getCandles()[this._getBar() + 1] || null,
        });
        await this._mpDriver.settleBar(winEnd);
      }
      // [ISSUE-232] 計画は末尾ティックを必ず含む（sampleIndices）ため、確定値はループ内で同期反映
      //   済み＝ここで往復（実測 ~100ms/バー）を発行しない。計画が無いバーのみ従来どおり着地させる。
      if (myGen === this._animGen && !(steps && steps.has(prices.length - 1))) {
        await this.settleFormingMA({ time: cd.time, open: o, high: hi, low: lo, close: fc });
      }
      this._plans.drop(this._getBar());   // 使い終えた計画は破棄（メモリ・陳腐化の抑制）
    } finally { if (myGen === this._animGen) window.__rpAnimating = false; }
  }

}
