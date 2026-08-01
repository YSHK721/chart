// replay.js — 再生ドライバの合成（composition）。本番フロントへの唯一の差し替え点。
//   LiveUpdater.start()（present 60秒ポーリング）の代わりに、現在時刻 untilTime をフレーム駆動し
//   controller.recomputeAllApplied で全適用インジを「その時点」で計算する（因果リビール）。
//
// 参照実装＝プロト web/js/replay.js（挙動の正解定義）。本ファイルは同一の制御フロー
//   （render/drive/playLoop/animateForming/waitFrame・generation/coalesce/animGen/pausedForm）を
//   保ったまま、① 値算出を純ロジック（replay/timing・stream・state）へ、② 副作用（chart/mainSeries/
//   renderer/減光primitive/rp-* DOM）を ReplayView へ委譲する。分岐・境界・await 順序は不変。

import { ReplayView } from './adapter/front/replay_view.js';
import { ReplayRangeMenu } from './adapter/front/replay_range_menu.js';
import { ReplaySpeedMenu } from './adapter/front/replay_speed_menu.js';
import { dayKey } from './replay/calendar.js';
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
// カレンダーで選んだ再生開始日の手前に付ける足数（指標のウォームアップ＋開始日より前の相場文脈）。
const CALENDAR_PRE_BARS = 300;
// 再生ボタンのグリフ（停止中＝▷ / 再生中＝一時停止）。
const PLAY_GLYPH = '▷';
const PAUSE_GLYPH = '❚❚';
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

  // [統合レイヤ外殻] live 既定の計算窓（disable で復帰させる基準）。DOM リスナ／monkey-patch の
  //   解除記録（destroy で原状復帰）。いずれも「駆動の停止／解除／リスナ解除」の外殻であり、
  //   render/animateForming/値算出には一切関与しない（計算ロジック無改変）。
  const liveDefaultRecentBars = controller._recentBars;
  const disposers = [];
  let wasEnabled = false; // enable() 済み（＝reveal トリムが起きうる）か。disable の全長復帰の発火条件。

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
  //
  // ISSUE-037: 以前は `controller.applyIndicator` / `removeInstance` を実行時に **monkeypatch**
  //   して後処理を差し込み、destroy で原状復帰していた。monkeypatch は (1) 差し替え順序に依存して
  //   壊れる (2) 復元漏れが静かに残る (3) subclass の override と二重に噛む、という脆さがある。
  //   controller が公開する購読スロット（`setAppliedObserver`・`setTimeframeObserver` と同型）へ
  //   置き換えた。通知は適用/削除の**完了後**に 1 回で、monkeypatch 時代と同じ位置に入る。
  const hasAppliedObserver = typeof controller.setAppliedObserver === 'function';
  if (hasAppliedObserver) {
    controller.setAppliedObserver(() => syncBoundary());
  }

  // ---- データ取得 ----
  //   startUnix=null: 従来どおり末尾 recentBars 本（present 窓）。
  //   startUnix 指定（カレンダーで選んだ再生開始日）: その日の手前 CALENDAR_PRE_BARS 本から
  //     recentBars 本を取る窓（＝取得済み範囲に縛られず全期間の任意日から再生できる）。
  async function fetchCandles(tf, startUnix = null) {
    let url = `/candles?datasetRef=${encodeURIComponent(datasetRef)}`
      + `&timeframe=${encodeURIComponent(tf)}&limit=${recentBars}`;
    if (startUnix != null) url += `&from=${startUnix}&pre=${CALENDAR_PRE_BARS}`;
    const payload = await (await fetchImpl(url)).json();
    return (payload && payload.ok) ? payload.candles : [];
  }

  // カレンダーの選択可能日（足が 1 本以上ある UTC 日・"YYYY-MM-DD" 昇順）。
  async function fetchDays(tf) {
    const url = `/available_days?datasetRef=${encodeURIComponent(datasetRef)}`
      + `&timeframe=${encodeURIComponent(tf)}`;
    const payload = await (await fetchImpl(url)).json();
    return (payload && payload.ok && Array.isArray(payload.days)) ? payload.days : [];
  }

  // ---- 表示 ----
  function applyView() {
    if (!autoFrame) return; // 手動閲覧中は上書きしない
    view.setVisibleLogicalRange(visibleRange({ bar, followOn, activePeriodBars }));
  }

  // 期間メニュー（[ 3か月 ] [∨]）。時間足別のプリセット候補を供給し、選択結果を再生位置へ反映する。
  //   - 期間プリセット: present から遡る（従来の #rp-presets ボタン列と同一ロジック＝presetSelection）。
  //   - カレンダー:     選んだ日を起点に窓を取り直し（fetchCandles(from)）、その日から再生する。
  const rangeMenu = new ReplayRangeMenu({
    document: doc,
    loadDays: () => fetchDays(timeframe),
    onSelectPreset: (secs) => {
      activeSecs = secs;
      autoFrame = true;
      const sel = presetSelection({ candles, secs });
      replayStart = sel.replayStart;
      activePeriodBars = sel.activePeriodBars;
      window.__rpReplayStart = replayStart;
      syncBoundary();
      view.setRangeLabel(presetLabel(secs));
      drive(replayStart);
    },
    onSelectDate: (startUnix, key) => { loadFromDate(startUnix, key); },
  });

  // 現在の期間プリセット secs に対応する表示ラベル（見つからなければ「全期間」）。
  function presetLabel(secs) {
    const presets = RANGE_PRESETS[timeframe] || [['全期間', null]];
    const hit = presets.find(([, s]) => s === secs);
    return hit ? hit[0] : '全期間';
  }

  function syncRangeMenu() {
    rangeMenu.setPresets(RANGE_PRESETS[timeframe] || [['全期間', null]]);
  }

  // ---- 1 フレーム描画（その時点を計算 → 足・帯・ビューを同時反映） ----
  async function render(target) {
    if (!candles.length) return;
    const g = ++generation;
    bar = clampBar(target, candles.length);
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
      // ステータス欄は新リプレイバーで撤去したため、失敗はコンソールへ残す（診断性の維持）。
      setStatus(`${fmt(t)} 計算エラー: ${(e && e.message) || e}`);
      console.error('[replay] 計算エラー', fmt(t), e);
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
    // 時間足が変われば足の存在日も変わる＝カレンダーの選択可能日を取り直させる。
    rangeMenu.invalidateDays();
    syncRangeMenu();
    // [ISSUE-158 ②] 時間足切替で一括リビール基底を全破棄（次フレームで新 tf のレンジを再構築）。
    if (typeof controller.clearRevealCache === 'function') controller.clearRevealCache();
    candles = await fetchCandles(tf);
    syncBoundary();
    view.setRangeLabel('全期間');
    await drive(candles.length - 1); // 開始は present（最新足）
  }

  // カレンダーで選んだ日を再生開始日にする。窓ごと取り直し（present 窓の外の過去日も選べる）、
  //   その日の最初の足を再生開始点（減光境界）にして、そこから再生できる状態にする。
  async function loadFromDate(startUnix, key) {
    if (typeof controller.clearRevealCache === 'function') controller.clearRevealCache();
    const loaded = await fetchCandles(timeframe, startUnix);
    if (!loaded.length) return; // 取得できないときは現状維持（ビューを勝手に動かさない）
    candles = loaded;
    activeSecs = null;
    activePeriodBars = null;
    autoFrame = true;
    replayStart = idxForTime(candles, startUnix);
    window.__rpReplayStart = replayStart;
    syncBoundary();
    view.setRangeLabel(key || dayKey(startUnix));
    await drive(replayStart);
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
    view.setPlayLabel(PLAY_GLYPH);
    view.setPlaying(false);
  }

  // ---- UI 配線 ----
  function updatePlayEnabled() {
    const atEnd = !candles.length || bar >= candles.length - 1;
    view.setPlayEnabled(!atEnd, NO_FUTURE_MSG);
  }
  // ▷ は「再生」と「再生追随」を兼ねる（依頼者確定 2026-07-26）。再生開始でビューを再生位置へ
  //   追随させる（＝ユーザーの明示イベント起点。ISSUE-164 の自動介入禁止に抵触しない）。
  //   停止時は追随を切らない（停止中はバーが進まないためビューを動かさない＝介入なし）。
  view.el('rp-play').onclick = () => {
    if (bar >= candles.length - 1) return; // 未来足が無い＝再生不可
    playing = !playing;
    view.setPlayLabel(playing ? PAUSE_GLYPH : PLAY_GLYPH);
    view.setPlaying(playing);
    if (playing) {
      followOn = true;
      autoFrame = true;
      applyView();
      playLoop();
    } else {
      settleFrameWait(); // 停止＝フレーム待機を即解除
    }
  };
  function applySpeed(v) {
    view.writeSpeed(clampSpeed(v));
    emaPeriodMs = null; // 旧速度の実測は陳腐化
    setEta();
    rescheduleFrameWait();
  }
  const speedMenu = new ReplaySpeedMenu({
    document: doc,
    readSpeed: () => clampSpeed(view.readSpeed()),
    onSelect: applySpeed,
  });
  const onSpeedClick = () => speedMenu.toggle(view.el('rp-speed'));
  view.el('rp-speed').addEventListener('click', onSpeedClick);
  disposers.push(() => { const e = view.el('rp-speed'); if (e) e.removeEventListener('click', onSpeedClick); });
  disposers.push(() => speedMenu.destroy());
  const onRangeClick = () => rangeMenu.toggle(view.el('rp-range-caret'));
  for (const id of ['rp-range', 'rp-range-caret']) {
    const e = view.el(id);
    if (!e) continue;
    e.addEventListener('click', onRangeClick);
    disposers.push(() => e.removeEventListener('click', onRangeClick));
  }
  disposers.push(() => rangeMenu.destroy());

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
  const onModeChange = () => {
    animGen++;          // 実行中の形成を supersede
    pausedForm = null;
    emaPeriodMs = null;
    setEta();
  };
  view.el('rp-mode').addEventListener('change', onModeChange);
  disposers.push(() => { const e = view.el('rp-mode'); if (e) e.removeEventListener('change', onModeChange); });
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
  // 時間足の再駆動は controller の「反映役」スロット（setTimeframeApplier）で受ける（ISSUE-231）。
  //   旧実装は [data-timeframe] へ独自リスナ（setTimeout 60ms）を追加していたが、時間足ボタンには
  //   共有ベースの bind() が張るライブ経路（controller.setTimeframe）も結線されているため、
  //   リプレイ中は 1 クリックで 2 経路が走っていた（実測 2026-08-01）:
  //     1) ライブ経路が先着し、ISSUE-196 の裁定どおり **ローソクだけ先に** 新足へ差し替える
  //        （指標は空にされ compute 完了後に描かれる＝実測 359ms 遅延）。
  //     2) その約 750ms 後にリプレイ経路が同じ切替をもう一度やり直す（全再計算の二重実行）。
  //   リプレイの不変条件は「その時点（T）のローソクと指標が同時に現れる」ことであり、1) の中間状態は
  //   これに反する。反映役として loadTimeframe を登録し、切替をリプレイの単一経路
  //   （render → preRender でローソク＋指標を await を挟まず同期一括描画）へ一本化する。
  //   時間足の確定（_timeframe 更新・ボタン active 同期・スケールリセット・永続化・購読者通知）は
  //   共有ベース側が従来どおり担う（＝ライブと同一の入口・リプレイは反映方法だけが異なる）。
  if (typeof controller.setTimeframeApplier === 'function') {
    controller.setTimeframeApplier(loadTimeframe);
    disposers.push(() => controller.setTimeframeApplier(null));
  }
  view.bindManualBrowse(() => { autoFrame = false; });
  // onclick 代入系（rp-play/next/prev/view-left/view-right）を destroy で解除する。
  disposers.push(() => {
    for (const id of ['rp-play', 'rp-next', 'rp-prev', 'rp-view-left', 'rp-view-right']) {
      const e = view.el(id); if (e) e.onclick = null;
    }
  });

  // ---- 起動 ----
  window.__rpChart = view.chart();
  window.__rpController = controller;
  window.__rpSetAuto = (v) => { autoFrame = !!v; };
  window.__rpAuto = () => autoFrame;
  await loadTimeframe(controller._timeframe);
  window.__rpReady = true;

  // ---- モード外殻ハンドル（統合レイヤ用・計算ロジックは無改変） --------------------
  //   単一 mount の統合レイヤが「リプレイ層」をオン・オフするための最小外殻。駆動の停止／
  //   untilTime の解除／計算窓の復帰／リスナ・monkey-patch の解除のみを担い、render/
  //   animateForming/値算出には一切触れない。
  //   - disable(): 再生停止＋in-flight を supersede＋settleFrameWait＋untilTime=undefined
  //                （ライブ等価・compute gate 不送信）＋_recentBars を live 既定へ復帰。
  //   - enable():  現在バー T を untilTime に設定し、計算窓をリビール範囲へ（:128-129 相当）。
  //   - destroy(): disable() ＋追加 DOM リスナ解除＋applyIndicator/removeInstance の monkey-patch を復元。
  async function disable() {
    playing = false;
    followOn = false; // 既定（リビール＝追従 OFF）へ戻す。次の enable は非追従で始まる。
    generation += 1; // in-flight render を supersede（isStale で破棄させる）
    animGen += 1;    // in-flight animateForming を supersede（isSuperseded で破棄させる）
    view.setPlayLabel(PLAY_GLYPH);
    view.setPlaying(false);
    settleFrameWait();                              // フレーム待機を即解除
    // 時間足切替の反映役を外す＝ライブ既定経路（ISSUE-196）へ戻す（ISSUE-231）。
    if (typeof controller.setTimeframeApplier === 'function') {
      controller.setTimeframeApplier(null);
    }
    controller.setUntilTime(undefined);             // ライブ等価（undefined＝!==undefined gate で不送信）
    controller._recentBars = liveDefaultRecentBars; // 計算窓を live 既定へ復帰
    // reveal トリム未発生（初期 mount 等・enable 未経由）なら全長復帰は不要＝軽量停止のみ。
    if (!wasEnabled) {
      return;
    }
    wasEnabled = false;
    // reveal 表示解除: render() が view.setCandles(candles.slice(0,bar+1)) で切り詰めたメイン系列と、
    //   revealTo(t) で切り詰めた指標を、**ライブ全長**へ戻す。再構築ではなく既存 API での再描画:
    //   (1) ライブ全長 candles を再取得（fetchCandles＝既存経路）→ driver 状態も現在へ同期（次 enable の
    //       陳腐化防止）、(2) view.setCandles(full)＝renderer.setCandles で全置換＋内部 fitContent＋_lastBar
    //       復帰（現在値 observer がライブ末尾値へ更新）、(3) controller.recomputeAllApplied({mode:'full'})
    //       ＝base（live と同一）入口で untilTime=undefined のまま全指標を全長再描画。値算出は無改変。
    try {
      const live = await fetchCandles(controller._timeframe);
      if (live && live.length) {
        candles = live;
        bar = candles.length - 1;
        replayStart = 0; activeSecs = null; activePeriodBars = null; autoFrame = true;
        view.setRangeLabel('全期間');
        view.setCandles(candles); // 全長表示（内部 fitContent・_lastBar＝ライブ末尾へ復帰）
        syncBoundary();           // 減光境界を全長（末尾）へ＝リプレイ減光の消去
      }
      await controller.recomputeAllApplied({ mode: 'full' }); // 指標を live 全長で再描画
    } catch (_e) {
      // 復帰の取得/再計算失敗は次の live poller（LiveUpdater 等）が回復する（描画は止めない）。
    }
  }
  async function enable() {
    wasEnabled = true;
    // 時間足切替の反映役を再登録＝リプレイ単一経路（同期一括描画）へ戻す（ISSUE-231）。
    if (typeof controller.setTimeframeApplier === 'function') {
      controller.setTimeframeApplier(loadTimeframe);
    }
    // 現在の live データから再取得して present（最新足）へ駆動する（＝リプレイ現在バー＝ライブ最新）。
    //   loadTimeframe は既存の入口（fetch＋slider/preset 同期＋drive(present)）で、drive→render が
    //   :128-129（setUntilTime(現在バー)＋_recentBars=bar+1）を確立する。値算出・分岐は無改変。
    await loadTimeframe(controller._timeframe);
  }
  function destroy() {
    disable();
    for (const off of disposers) { try { off(); } catch (_e) { /* noop */ } }
    disposers.length = 0;
    if (hasAppliedObserver) {
      controller.setAppliedObserver(null);   // ISSUE-037: 購読解除（monkeypatch 復元の置き換え）。
    }
  }

  return { enable, disable, destroy };
}
