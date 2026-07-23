// replay.js — 再生ドライバの合成（composition）。本番フロントへの唯一の差し替え点。
//   LiveUpdater.start()（present 60秒ポーリング）の代わりに、現在時刻 untilTime をフレーム駆動し
//   controller.recomputeAllApplied で全適用インジを「その時点」で計算する（因果リビール）。
//
// 参照実装＝プロト web/js/replay.js（挙動の正解定義）。本ファイルは同一の制御フロー
//   （render/drive/playLoop/animateForming/waitFrame・generation/coalesce/animGen/pausedForm）を
//   保ったまま、① 値算出を純ロジック（replay/timing・stream・state）へ、② 副作用（chart/mainSeries/
//   renderer/減光primitive/rp-* DOM）を ReplayView へ委譲する。分岐・境界・await 順序は不変。

import { ReplayView } from './adapter/front/replay_view.js';
import {
  clampSpeed, frameMs as frameMsOf, stepMs,
  estimatePeriodMs, emaUpdate, periodMs, fmtEta, ANIM_MIN_MS, FORMING_MIN_INTERVAL_MS,
  remainingTickvol, etaRealTicksMs,
} from './replay/timing.js';
import { intrabarWindow, buildStreamFromResponse } from './replay/stream.js';
import {
  clampBar, idxForTime, visibleRange, scrollRange, presetSelection,
  degenerateModes, resumeDecision, isStale, isSuperseded,
} from './replay/state.js';
import { createMpGrowthDriver } from './replay/mp_growth_driver.js';

const DAY = 86400;
// 表示レンジ・テンプレート（時間足別）。期間は「秒」で持ち t 起点で [t-期間, t] を毎回算出。null=全期間。
const RANGE_PRESETS = {
  '1D':  [['3か月', 90 * DAY], ['6か月', 182 * DAY], ['1年', 365 * DAY], ['全期間', null]],
  '1W':  [['1年', 365 * DAY], ['3年', 3 * 365 * DAY], ['全期間', null]],
  '1M':  [['1年', 365 * DAY], ['3年', 3 * 365 * DAY], ['全期間', null]],
  '4h':  [['1週', 7 * DAY], ['1か月', 30 * DAY], ['3か月', 90 * DAY], ['全期間', null]],
  '1h':  [['1週', 7 * DAY], ['1か月', 30 * DAY], ['全期間', null]],
  '30m': [['1日', DAY], ['1週', 7 * DAY], ['全期間', null]],
  '15m': [['1日', DAY], ['1週', 7 * DAY], ['全期間', null]],
  '5m':  [['1日', DAY], ['1週', 7 * DAY], ['全期間', null]],
  '1m':  [['1日', DAY], ['全期間', null]],
};
const NO_FUTURE_MSG = '最新足のため再生できません（未来足が存在しません）';
const sleepMs = (ms) => new Promise((r) => setTimeout(r, ms));

export async function setupReplay({ chart, mainSeries, controller, renderer, datasetRef, recentBars, document: doc, fetchImpl = (typeof fetch !== 'undefined' ? fetch : undefined), marketProfile = null }) {
  const view = new ReplayView({ chart, mainSeries, renderer, document: doc });
  const fmt = (t) => new Date(t * 1000).toISOString().slice(0, 16).replace('T', ' ');
  const setStatus = (text) => view.setText('rp-status', text);
  // MP tick-live 有効判定（未配線=null は常に false＝既存 replay へ非干渉）。駆動結線の各所で共用する。
  const mpOn = () => !!(marketProfile && marketProfile.isEnabled());

  // ---- 状態（既定はリビール: 追従 OFF） ----
  let candles = [];
  let bar = 0;
  let playing = false;
  let timeframe = controller._timeframe;
  let generation = 0;
  let followOn = false;
  let autoFrame = true;
  let replayStart = 0;
  let activeSecs = null;
  let activePeriodBars = null;

  const syncBoundary = () => view.syncBoundary({ replayStart, candles });

  // MP normal 成長の base 累積下限（UNIX 秒）= 再生開始点 replayStart のバー時刻。
  //   再生を始めた位置から現在まで累積する（過去 revealed 足を保持・全期間より見やすい・日跨ぎでも非リセット）。
  //   replayStart=0（全期間プリセット）は最古足 time＝実質全期間。actor 側が formingStart へクランプし
  //   不変条件 from<=formingStart・未来リーク禁止を保つ。候補足が無ければ undefined（actor は GrowthWindow
  //   フォールバックへ委譲）。
  const mpBaseFrom = () => (candles[replayStart] ? candles[replayStart].time : undefined);

  // MP tick-live 成長駆動は独立ドライバへ分離（ISSUE-133 SRP）。再生制御（render/animateForming）は
  //   mpOn() gate の下で本ドライバへ委譲する（growInFlight・grow/settle/feed の駆動シーケンスを所有）。
  const mpDriver = createMpGrowthDriver({
    marketProfile, mpBaseFrom, sleepMs, animMinMs: ANIM_MIN_MS,
  });

  // 指標の適用/削除（render を経ない経路）でも pane の減光を即同期する。
  for (const name of ['applyIndicator', 'removeInstance']) {
    const orig = (typeof controller[name] === 'function') ? controller[name].bind(controller) : null;
    if (!orig) continue;
    controller[name] = (...a) => {
      const r = orig(...a);
      if (r && typeof r.then === 'function') return r.then((v) => { syncBoundary(); return v; });
      syncBoundary();
      return r;
    };
  }

  // ---- データ取得 ----
  async function fetchCandles(tf) {
    const url = `/candles?datasetRef=${encodeURIComponent(datasetRef)}`
      + `&timeframe=${encodeURIComponent(tf)}&limit=${recentBars}`;
    const payload = await (await fetchImpl(url)).json();
    return (payload && payload.ok) ? payload.candles : [];
  }

  // ---- 表示 ----
  function applyView() {
    if (!autoFrame) return; // 手動閲覧中は上書きしない
    view.setVisibleLogicalRange(visibleRange({ bar, followOn, activePeriodBars }));
  }

  // 期間プリセットを #rp-presets に描画（クリックで present−N へズーム）。
  function renderPresets() {
    view.renderPresets({
      presets: RANGE_PRESETS[timeframe] || [['全期間', null]],
      activeSecs,
      onSelect: (secs) => {
        activeSecs = secs;
        autoFrame = true;
        const sel = presetSelection({ candles, secs });
        replayStart = sel.replayStart;
        activePeriodBars = sel.activePeriodBars;
        window.__rpReplayStart = replayStart;
        view.setSliderMin(0);
        syncBoundary();
        renderPresets();
        drive(replayStart);
      },
    });
  }

  // ---- 1 フレーム描画（その時点を計算 → 足・帯・ビューを同時反映） ----
  async function render(target) {
    if (!candles.length) return;
    const g = ++generation;
    bar = clampBar(target, candles.length);
    view.setSliderValue(bar);
    updatePlayEnabled();
    const t = candles[bar].time;

    controller.setUntilTime(t);
    controller._recentBars = bar + 1; // 計算窓＝リビール範囲
    // [ISSUE-158 ②] 一括リビール基底: 登録指標（causal_reveal_ids）は全レンジを 1 回だけ計算して
    //   キャッシュし、以降のバー送りは同期スライス描画のみ（per-step HTTP を発行しない）。
    //   必要時（時間足切替・指標追加・params 変更後の初回フレーム）のみ構築する。
    if (typeof controller.revealNeedsBuild === 'function' && controller.revealNeedsBuild()) {
      setStatus(`${fmt(t)} 一括計算中…`);
      try {
        await controller.buildRevealBase(candles[candles.length - 1].time, candles.length);
      } catch (e) {
        // 構築失敗は per-step 計算へフォールバック（描画は止めない）。
      }
      if (isStale(g, generation)) return;
    }
    setStatus(`${fmt(t)} 計算中…`);
    const started = performance.now();
    try {
      // 計算後、preRender（足リビール＋ビュー＋一括リビール）→ 帯描画が await を挟まず
      //   1 ブロック＝アトミック（完成足チラ見せ防止の不変条件は revealTo が同期のため保たれる）。
      await controller.recomputeAllApplied({
        mode: 'full',
        // 一括リビール済み指標は per-step 計算から除外（revealTo が同フレームで描画する）。
        skip: (inst) => typeof controller.hasRevealFor === 'function'
          && controller.hasRevealFor(inst.instanceId),
        preRender: () => {
          const saved = (!autoFrame) ? view.getVisibleLogicalRange() : null;
          view.setCandles(candles.slice(0, bar + 1));
          if (saved) { view.setVisibleLogicalRange(saved); }
          else applyView();
          if (typeof controller.revealTo === 'function') controller.revealTo(t);
        },
      });
    } catch (e) {
      setStatus(`${fmt(t)} 計算エラー: ${(e && e.message) || e}`);
      return;
    }
    if (isStale(g, generation)) return; // 後発レンダが来ていれば破棄
    applyView();
    syncBoundary(); // full 再計算で再生成された pane 系列へ減光を再装着
    // ISSUE-048（完成足フラッシュ防止）: 参照実装 prototype_260626-01 は「リビール→animateForming 冒頭の
    //   同期畳み込み」の間に await を挟まない不変条件で完成足のチラ見せを防ぐが、直下の MP enterBar
    //   （HTTP await）が挟まると、その待ち時間ぶんブラウザが paint して完成足が露出する（実測 0.5〜1.5s）。
    //   再生中（playing かつ足内更新モード）は enterBar の await より前＝リビールと同一同期ブロック内
    //   （paint 前）で最新足を始値の同事足へ畳む。ガードは操作種別でなく playing フラグの現在値で判定する:
    //   非再生時の手動ナビ（rp-next/prev/slider）は畳まず完成足のまま（従来どおり）。再生中に slider 等を
    //   操作した場合は畳まれるが、直後の animateForming が同値で上書きするため良性。math（足内更新なし＝
    //   完成足のまま）は除外。animateForming 冒頭の畳み込みは防御として温存。
    if (playing && view.readMode() !== 'math') {
      const cd = candles[bar];
      view.updateForming({ time: cd.time, open: cd.open, high: cd.open, low: cd.open, close: cd.open });
    }
    // MP tick-live: バー単位ジャンプで base を now=T（因果）で取り直す（rollover 兼・await ready で
    //   直後の animateForming feedTick 取りこぼしを防ぐ）。MP OFF/未配線時は完全に非干渉。
    if (mpOn()) await mpDriver.enterBar(t);
    lastComputeMs = performance.now() - started;
    setEta();
    setStatus(`bar ${bar}/${candles.length - 1}  ${fmt(t)}  計算 ${Math.round(performance.now() - started)}ms（その場計算）`);
    window.__rpbar = bar;
  }

  // ---- 直列化（多重再計算の防止・最新フレームへ coalesce） ----
  let busy = false;
  let queued = null;
  async function drive(target) {
    pausedForm = null; // bar を動かす操作は停止足の続きを無効化
    if (busy) { queued = target; return; }
    busy = true;
    try {
      let cur = target;
      do { queued = null; await render(cur); cur = queued; } while (cur !== null);
    } finally {
      busy = false;
    }
  }

  // ---- 時間足ロード / 連続再生 ----
  function syncModeOptions(tf) {
    view.applyModeDegeneration(degenerateModes(tf));
  }
  async function loadTimeframe(tf) {
    timeframe = tf;
    replayStart = 0; activeSecs = null; activePeriodBars = null;
    syncModeOptions(tf);
    // [ISSUE-158 ②] 時間足切替で一括リビール基底を全破棄（次フレームで新 tf のレンジを再構築）。
    if (typeof controller.clearRevealCache === 'function') controller.clearRevealCache();
    candles = await fetchCandles(tf);
    syncBoundary();
    view.setSliderBounds(0, Math.max(0, candles.length - 1));
    renderPresets();
    await drive(candles.length - 1); // 開始は present（最新足）
  }

  // ---- 速度 / フレーム待機 ----
  const speed = () => clampSpeed(view.readSpeed());
  const frameMs = () => frameMsOf(speed());
  let emaPeriodMs = null;
  let lastComputeMs = null;
  const setEta = () => {
    const remain = Math.max(0, (candles.length - 1) - bar);
    if (remain === 0) { view.setText('rp-eta', '完了予想 —'); return; }
    if (speed() <= 0) { view.setText('rp-eta', `完了予想 —（一時停止・残り${remain}足）`); return; }
    // ISSUE-044: real_ticks は cap 廃止（間引かない・絶対仕様）＝1足あたり点数が足ごとに桁で異なる
    //   （月足は数十万 tick）ため、旧 800 点 cap 前提のモデルも per-bar EMA も使わず、/candles の
    //   tickvol（実 tick 数）の残り総数から算出する。tickvol 欠損（旧データセット等）は従来モデルへ
    //   フォールバック（回帰なし）。他モードは点数 cap 済みで従来モデル（実測 EMA 優先）のまま。
    if (view.readMode() === 'real_ticks') {
      const tv = remainingTickvol(candles, bar);
      if (tv != null) {
        view.setText('rp-eta', `完了予想 ${fmtEta(etaRealTicksMs(tv, remain, lastComputeMs, speed()))}（残り${remain}足）`);
        return;
      }
    }
    const period = periodMs(emaPeriodMs, lastComputeMs, view.readMode(), speed());
    view.setText('rp-eta', `完了予想 ${fmtEta(remain * period)}（残り${remain}足）`);
  };
  let frameTimer = null;
  let frameResolve = null;
  let frameStart = 0;
  function settleFrameWait() {
    if (frameTimer != null) { clearTimeout(frameTimer); frameTimer = null; }
    const resolve = frameResolve; frameResolve = null;
    if (resolve) resolve();
  }
  function waitFrame() {
    return new Promise((resolve) => {
      frameResolve = resolve;
      frameStart = performance.now();
      frameTimer = setTimeout(settleFrameWait, frameMs());
    });
  }
  function rescheduleFrameWait() {
    if (frameResolve == null) return;
    if (frameTimer != null) { clearTimeout(frameTimer); frameTimer = null; }
    const remaining = frameMs() - (performance.now() - frameStart);
    if (remaining <= 0) { settleFrameWait(); return; }
    frameTimer = setTimeout(settleFrameWait, remaining);
  }
  async function playLoop() {
    while (playing && bar < candles.length - 1) {
      while (playing && speed() <= 0) await sleepMs(80); // 速度0.00=一時停止（凍結）
      if (!playing) break;
      const barStart = performance.now();
      let resume = null;
      if (resumeDecision(pausedForm, candles[bar])) {
        resume = pausedForm; // 停止した足の続きから再開
      } else {
        await drive(bar + 1);
      }
      await animateForming(() => !playing, resume);
      if (!playing) break;
      await waitFrame();
      const dt = performance.now() - barStart;
      emaPeriodMs = emaUpdate(emaPeriodMs, dt);
      setEta();
    }
    playing = false;
    view.setPlayLabel('▶ 再生');
    view.setPlaying(false);
  }

  // ---- UI 配線 ----
  function updatePlayEnabled() {
    const atEnd = !candles.length || bar >= candles.length - 1;
    view.setPlayEnabled(!atEnd, NO_FUTURE_MSG);
  }
  view.el('rp-play').onclick = () => {
    if (bar >= candles.length - 1) return; // 未来足が無い＝再生不可
    playing = !playing;
    view.setPlayLabel(playing ? '⏸ 停止' : '▶ 再生');
    view.setPlaying(playing);
    if (playing) playLoop();
    else settleFrameWait(); // 停止＝フレーム待機を即解除
  };
  function syncSpeedUI() {
    const v = clampSpeed(view.readSpeed());
    view.setSpeedVal(v.toFixed(2));
    for (const b of view.speedPresets()) b.classList.toggle('on', Math.abs(+b.dataset.spd - v) < 1e-9);
    emaPeriodMs = null; // 旧速度の実測は陳腐化
    setEta();
    rescheduleFrameWait();
  }
  view.el('rp-speed').addEventListener('input', syncSpeedUI);
  for (const b of view.speedPresets()) {
    b.addEventListener('click', () => { view.writeSpeed(b.dataset.spd); syncSpeedUI(); });
  }

  // Market Profile の有効化は indicator メニュー（controller.applyIndicator('market_profile')）へ
  //   一本化した（#rp-mp トグル撤去）。有効化された actor は同一実体で composition root から
  //   controller と本 setupReplay 双方へ注入され、下記の駆動フック（render→enterBar /
  //   animateForming→feedTick / settleTick）が isEnabled()=true を観測して育てる。

  // ---- 最新足の足内更新（MT5 モデリング 5 モード相当） ----
  async function buildStream(cd, mode) {
    if (mode === 'open_only' || mode === 'math') {
      return buildStreamFromResponse({ mode, cd }); // fetch 前短絡（窓/取得なし）
    }
    const { winStart, winEnd } = intrabarWindow({
      timeframe, cd, prevCandle: candles[bar - 1] || null, nextCandle: candles[bar + 1] || null,
    });
    // MP tick-live 有効かつ real_ticks のときだけ secs=1 gate を付与し tick_secs を並走取得する
    //   （他バー・MP OFF は従来 payload 不変＝forming MA/OHLC アニメ回帰ゼロ）。
    const wantSecs = mpOn() && mode === 'real_ticks';
    let url = `/intraday?datasetRef=${encodeURIComponent(datasetRef)}&start=${winStart}&end=${winEnd}&mode=${encodeURIComponent(mode)}`;
    if (wantSecs) url += '&secs=1';
    let resp = {};
    try { resp = await (await fetchImpl(url)).json(); } catch (_e) { /* noop */ }
    // winStart/winEnd を渡し every_tick/ohlc_1min は合成 dwell secs（窓等分・クライアント合成）を並走取得する。
    //   real_ticks は実 tick_secs のまま（byte 不変・窓は無視）。open_only/math は上で短絡済み。
    return buildStreamFromResponse({ mode, cd, m1: resp.m1 || [], ticks: resp.ticks || [], secs: resp.tick_secs || [], winStart, winEnd });
  }
  let animGen = 0;
  let formingInFlight = false;
  let lastFormingMs = -1e9;
  function pushFormingMA(forming) {
    const nowMs = performance.now();
    if (formingInFlight || (nowMs - lastFormingMs) < FORMING_MIN_INTERVAL_MS) return;
    if (controller.isRecomputing()) return; // 他の再計算中は譲る
    lastFormingMs = nowMs;
    formingInFlight = true;
    controller.recomputeFormingLatest(forming)
      .catch(() => { /* 足内 MA 失敗はアニメ継続 */ })
      .finally(() => { formingInFlight = false; });
  }
  async function settleFormingMA(forming) {
    while (formingInFlight) { await sleepMs(ANIM_MIN_MS); }
    if (controller.isRecomputing()) return;
    formingInFlight = true;
    try { await controller.recomputeFormingLatest(forming); }
    catch (_e) { /* 確定着地の失敗は次フレームの full 再計算が回復 */ }
    finally { formingInFlight = false; lastFormingMs = performance.now(); }
  }
  // MP tick-live グリッド拡張の駆動（growInFlight・pushGrowTo・settleGrowTo）は mpDriver（独立ドライバ）
  //   が所有する（ISSUE-133 SRP）。以下 animateForming は mpDriver.onFormingTick / settleMath / settleBar
  //   へ委譲する（await 順序・coalesce 意味論は抽出前と同一）。
  let pausedForm = null;
  view.el('rp-mode').addEventListener('change', () => {
    animGen++;          // 実行中の形成を supersede
    pausedForm = null;
    emaPeriodMs = null;
    setEta();
  });
  async function animateForming(shouldAbort, resume) {
    if (!candles.length) return;
    const cd = candles[bar];
    if (!cd) return;
    const myGen = ++animGen;
    const superseded = () => isSuperseded(myGen, animGen);
    const mode = view.readMode();
    if (mode === 'math') {
      window.__rpForm = { mode, n: 0 }; pausedForm = null;
      // math（終値）: 足内推移なし → その時間足の完成プロファイルを settleGrowTo(winEnd) で一度描く（成長なし）。
      //   確定形は全モード共通で real_ticks と同一（[当日, winEnd) の backend 実 dwell 全窓 fold へ収束）。
      //   MP OFF/未配線は非干渉。winEnd は intrabarWindow（fetch 不要・因果窓・未来リークなし）。
      if (mpOn()) {
        const { winEnd } = intrabarWindow({
          timeframe, cd, prevCandle: candles[bar - 1] || null, nextCandle: candles[bar + 1] || null,
        });
        await mpDriver.settleMath(winEnd);
      }
      return;
    }
    window.__rpAnimating = true;
    try {
      let prices, secs, o, hi, lo, startI;
      if (resume && resume.time === cd.time) {
        prices = resume.prices; o = resume.o; hi = resume.hi; lo = resume.lo; startI = resume.i;
        secs = resume.secs || []; // MP tick-live: 停止再開時も sec 並行配列を additive 保持。
      } else {
        // 確定足のチラ見せ防止: fetch を await する前（同期）に最新足を始値へ畳む。
        if (mode !== 'math') {
          view.updateForming({ time: cd.time, open: cd.open, high: cd.open, low: cd.open, close: cd.open });
        }
        ({ prices, secs } = await buildStream(cd, mode));
        if (superseded()) return;
        o = prices[0]; hi = prices[0]; lo = prices[0]; startI = 0;
      }
      window.__rpForm = { mode, n: prices.length };
      for (let i = startI; i < prices.length; i++) {
        if (shouldAbort && shouldAbort()) {
          pausedForm = { time: cd.time, prices, secs, o, hi, lo, i };
          return;
        }
        if (superseded()) return;
        const p = prices[i];
        hi = Math.max(hi, p); lo = Math.min(lo, p); // 高安は流入ティックの極値のみ
        view.updateForming({ time: cd.time, open: o, high: hi, low: lo, close: p });
        pushFormingMA({ time: cd.time, open: o, high: hi, low: lo, close: p });
        // MP tick-live: この tick を DwellAccumulator へ供給し足内成長させる（sec 並走が有るバーのみ＝
        //   real_ticks・MP 有効。secs 空バーは skip＝base 継続）。速度0凍結/supersede の既存制御に追従。
        //   グリッド外 tick の growTo 発火（in-flight coalesce）＋feedTick は mpDriver が担う（ISSUE-133 SRP）。
        if (mpOn() && secs && secs[i] != null) {
          mpDriver.onFormingTick(p, secs[i]);
        }
        while (speed() <= 0 && !superseded() && !(shouldAbort && shouldAbort())) await sleepMs(80); // 速度0=凍結
        if (superseded() || (shouldAbort && shouldAbort())) continue;
        await sleepMs(stepMs(speed())); // 再生速度で減速（毎ステップ読込＝速度変更を即時反映）
      }
      pausedForm = null;
      // 足確定: ティック列由来の OHLC で確定（cd.high/low へスナップしない）。
      const fc = prices[prices.length - 1];
      view.updateForming({ time: cd.time, open: o, high: hi, low: lo, close: fc });
      // MP tick-live: 確定時に当日窓全 tick を winEnd で再畳み込みしてグリッド確定（mp_core 一致点＝
      //   backend base=1 dwell と一致）してから最終 snapshot を強制描画する（throttle 無視）。
      //   確定形は MP 有効なら全モード winEnd で fold（growth の secs 有無から分離＝一般化）。real_ticks は
      //   最終実 tick 秒 t_k(<winEnd) ではなく winEnd で settle し、open_only は secs 空でも settle を発火する
      //   ＝全モード（real_ticks/every_tick/ohlc_1min/open_only/math）の完成 MP が backend fold(winEnd) で
      //   byte 一致（合成 dwell/始値のみは transient・settle=truth）。winEnd=足終端=settle 時の now（因果・
      //   未来リークなし＝次足 tick は半開区間 [dayStart, winEnd) で除外）。actor は空/縮退 forming を非破壊で
      //   扱う（データ無バーは前回描画保持）。
      if (mpOn()) {
        const { winEnd } = intrabarWindow({
          timeframe, cd, prevCandle: candles[bar - 1] || null, nextCandle: candles[bar + 1] || null,
        });
        await mpDriver.settleBar(winEnd);
      }
      if (myGen === animGen) {
        await settleFormingMA({ time: cd.time, open: o, high: hi, low: lo, close: fc });
      }
    } finally { if (myGen === animGen) window.__rpAnimating = false; }
  }

  // 1 足スキップ（再生せず次の確定足へ）。
  view.el('rp-next').onclick = () => drive(bar + 1);
  view.el('rp-prev').onclick = () => drive(bar - 1);
  window.__rpAnimateOnce = () => animateForming(); // promise を返す（決定論テストが await 可能・実行時挙動は不変）
  function scrollViewTo(edge) {
    autoFrame = false;
    const r = view.getVisibleLogicalRange();
    if (!r) return;
    view.setVisibleLogicalRange(scrollRange({ edge, currentRange: r, bar }));
  }
  view.el('rp-view-left').onclick = () => scrollViewTo('left');
  view.el('rp-view-right').onclick = () => scrollViewTo('right');
  view.el('rp-slider').oninput = (e) => drive(+e.target.value);
  view.el('rp-follow').onclick = () => {
    followOn = !followOn;
    view.setFollow(followOn);
    autoFrame = true;
    applyView();
  };
  // 時間足の再駆動は data-timeframe を持つ要素（共有 TimeframeMenu の項目）に結線する。
  //   旧静的ボタン（.tb-interval ＋ data-timeframe 併持）が共有メニュー化（ISSUE-122/123）で
  //   トリガー（.tb-interval のみ・tf 属性なし）と項目（data-timeframe のみ）に分離されたため、
  //   .tb-interval 選択ではトリガーに誤結線し loadTimeframe(undefined) が走る（ISSUE-142）。
  for (const btn of doc.querySelectorAll('[data-timeframe]')) {
    btn.addEventListener('click', () => setTimeout(() => loadTimeframe(btn.dataset.timeframe), 60));
  }
  view.bindManualBrowse(() => { autoFrame = false; });

  // ---- 起動 ----
  window.__rpChart = view.chart();
  window.__rpController = controller;
  window.__rpSetAuto = (v) => { autoFrame = !!v; };
  window.__rpAuto = () => autoFrame;
  await loadTimeframe(controller._timeframe);
  window.__rpReady = true;
}
