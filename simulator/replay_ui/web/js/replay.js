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
import { foldTick } from './domain/forming_fold.js';
import {
  clampSpeed, stepMs, ANIM_MIN_MS, FORMING_MIN_INTERVAL_MS, realtimeOffsetsMs,
} from './replay/timing.js';
import { intrabarWindow, buildStreamFromResponse } from './replay/stream.js';
import { FormingSeqClient } from './adapter/front/forming_seq_client.js';
import {
  clampBar, idxForTime, visibleRange, scrollRange, presetSelection,
  degenerateModes, resumeDecision, isStale, isSuperseded,
} from './replay/state.js';
import { createMpGrowthDriver } from './replay/mp_growth_driver.js';
import { PlaybackTempo } from './replay/playback_tempo.js';
import { FormingPlanCache } from './replay/forming_plan_cache.js';
import { FormingAnimator } from './replay/forming_animator.js';
import { ReplayCursor } from './replay/replay_cursor.js';

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
  // 再生カーソル（対象データ・現在位置・世代）は ReplayCursor が所有する（ISSUE-256）。
  //   旧: candles / bar / replayStart / timeframe / generation / activeSecs / activePeriodBars を
  //   素の局所変数として関数全体へ露出し、どこからでも書き換えられる状態だった。
  const cursor = new ReplayCursor({
    fetchImpl, datasetRef, recentBars, preBars: CALENDAR_PRE_BARS, timeframe: controller._timeframe,
  });
  let playing = false;
  let followOn = false;
  let autoFrame = true;

  // [統合レイヤ外殻] live 既定の計算窓（disable で復帰させる基準）。DOM リスナ／monkey-patch の
  //   解除記録（destroy で原状復帰）。いずれも「駆動の停止／解除／リスナ解除」の外殻であり、
  //   render/animateForming/値算出には一切関与しない（計算ロジック無改変）。
  const liveDefaultRecentBars = controller._recentBars;
  const disposers = [];
  let wasEnabled = false; // enable() 済み（＝reveal トリムが起きうる）か。disable の全長復帰の発火条件。

  const syncBoundary = () => view.syncBoundary({ replayStart: cursor.replayStart(), candles: cursor.candles() });

  // MP normal 成長の base 累積下限（UNIX 秒）= 再生開始点 cursor.replayStart() のバー時刻。
  //   再生を始めた位置から現在まで累積する（過去 revealed 足を保持・全期間より見やすい・日跨ぎでも非リセット）。
  //   replayStart=0（全期間プリセット）は最古足 time＝実質全期間。actor 側が formingStart へクランプし
  //   不変条件 from<=formingStart・未来リーク禁止を保つ。候補足が無ければ undefined（actor は GrowthWindow
  //   フォールバックへ委譲）。
  const mpBaseFrom = () => (cursor.candles()[cursor.replayStart()] ? cursor.candles()[cursor.replayStart()].time : undefined);

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
    // [ISSUE-232] 指標の適用/削除は足内一括計算の計画を陳腐化させる（対象集合が変わる）→ 破棄。
    controller.setAppliedObserver(() => { invalidatePlans(); syncBoundary(); });
  }

  // ---- データ取得 ----
  //   startUnix=null: 従来どおり末尾 recentBars 本（present 窓）。
  //   startUnix 指定（カレンダーで選んだ再生開始日）: その日の手前 CALENDAR_PRE_BARS 本から
  //     recentBars 本を取る窓（＝取得済み範囲に縛られず全期間の任意日から再生できる）。


  const fetchCandles = (tf, startUnix = null) => cursor.fetchCandles(tf, startUnix);
  const fetchDays = (tf) => cursor.fetchDays(tf);

  // ---- 表示 ----
  function applyView() {
    if (!autoFrame) return; // 手動閲覧中は上書きしない
    view.setVisibleLogicalRange(visibleRange({ bar: cursor.bar(), followOn, activePeriodBars: cursor.activePeriodBars() }));
  }

  // 期間メニュー（[ 3か月 ] [∨]）。時間足別のプリセット候補を供給し、選択結果を再生位置へ反映する。
  //   - 期間プリセット: present から遡る（従来の #rp-presets ボタン列と同一ロジック＝presetSelection）。
  //   - カレンダー:     選んだ日を起点に窓を取り直し（fetchCandles(from)）、その日から再生する。
  const rangeMenu = new ReplayRangeMenu({
    document: doc,
    loadDays: () => fetchDays(cursor.timeframe()),
    onSelectPreset: (secs) => {
      cursor.setActivePeriod({ secs, bars: cursor.activePeriodBars() });
      autoFrame = true;
      const sel = presetSelection({ candles: cursor.candles(), secs });
      cursor.setReplayStart(sel.replayStart);
      cursor.setActivePeriod({ secs: cursor.activeSecs(), bars: sel.activePeriodBars });
      window.__rpReplayStart = cursor.replayStart();
      syncBoundary();
      view.setRangeLabel(presetLabel(secs));
      drive(cursor.replayStart());
    },
    onSelectDate: (startUnix, key) => { loadFromDate(startUnix, key); },
  });

  // 現在の期間プリセット secs に対応する表示ラベル（見つからなければ「全期間」）。
  function presetLabel(secs) {
    const presets = RANGE_PRESETS[cursor.timeframe()] || [['全期間', null]];
    const hit = presets.find(([, s]) => s === secs);
    return hit ? hit[0] : '全期間';
  }

  function syncRangeMenu() {
    rangeMenu.setPresets(RANGE_PRESETS[cursor.timeframe()] || [['全期間', null]]);
  }

  // ---- 1 フレーム描画（その時点を計算 → 足・帯・ビューを同時反映） ----
  async function render(target) {
    if (!cursor.candles().length) return;
    const g = cursor.bumpGeneration();
    cursor.setBar(target);
    updatePlayEnabled();
    const t = cursor.candles()[cursor.bar()].time;

    controller.setUntilTime(t);
    controller._recentBars = cursor.bar() + 1; // 計算窓＝リビール範囲
    // [ISSUE-158 ②] 一括リビール基底: 登録指標（causal_reveal_ids）は全レンジを 1 回だけ計算して
    //   キャッシュし、以降のバー送りは同期スライス描画のみ（per-step HTTP を発行しない）。
    //   必要時（時間足切替・指標追加・params 変更後の初回フレーム）のみ構築する。
    if (typeof controller.revealNeedsBuild === 'function' && controller.revealNeedsBuild()) {
      setStatus(`${fmt(t)} 一括計算中…`);
      try {
        await controller.buildRevealBase(cursor.candles()[cursor.candles().length - 1].time, cursor.candles().length);
      } catch (e) {
        // 構築失敗は per-step 計算へフォールバック（描画は止めない）。
      }
      if (isStale(g, cursor.generation())) return;
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
          view.setCandles(cursor.candles().slice(0, cursor.bar() + 1));
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
    if (isStale(g, cursor.generation())) return; // 後発レンダが来ていれば破棄
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
      const cd = cursor.candles()[cursor.bar()];
      view.updateForming({ time: cd.time, open: cd.open, high: cd.open, low: cd.open, close: cd.open });
    }
    // MP tick-live: バー単位ジャンプで base を now=T（因果）で取り直す（rollover 兼・await ready で
    //   直後の animateForming feedTick 取りこぼしを防ぐ）。MP OFF/未配線時は完全に非干渉。
    if (mpOn()) await mpDriver.enterBar(t);
    // [ISSUE-232] このバーの足内計画を先読みする（fire-and-forget）。再生開始直後の 1 バー目や
    //   手動ナビ後の再生開始でも計画が用意される（animateForming 側の先読みは「次バー」担当）。
    //   **リプレイ層が起きている時だけ**発火させる（playing＝再生中／wasEnabled＝リプレイモード）。
    //   統合レイヤは live モードのまま setupReplay を 1 回 mount するため、この gate が無いと
    //   ライブ表示中に /intraday を要求してしまう（SW が /live へ回して 404・実測 2026-08-01）。
    if (playing || wasEnabled) {
      prefetchPlan(cursor.bar(), view.readMode());
    }
    tempo.noteComputeMs(performance.now() - started);
    setEta();
    setStatus(`bar ${cursor.bar()}/${cursor.candles().length - 1}  ${fmt(t)}  計算 ${Math.round(performance.now() - started)}ms（その場計算）`);
    window.__rpbar = cursor.bar();
  }

  // ---- 直列化（多重再計算の防止・最新フレームへ coalesce） ----
  let busy = false;
  let queued = null;
  async function drive(target) {
    animator.clearPausedForm(); // bar を動かす操作は停止足の続きを無効化
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
    cursor.setTimeframe(tf);
    cursor.setReplayStart(0); cursor.clearActivePeriod();
    syncModeOptions(tf);
    // 時間足が変われば足の存在日も変わる＝カレンダーの選択可能日を取り直させる。
    rangeMenu.invalidateDays();
    syncRangeMenu();
    // [ISSUE-158 ②] 時間足切替で一括リビール基底を全破棄（次フレームで新 tf のレンジを再構築）。
    if (typeof controller.clearRevealCache === 'function') controller.clearRevealCache();
    invalidatePlans();  // [ISSUE-232] 時間足が変われば足内の窓も計画も別物＝破棄
    cursor.setCandles(await fetchCandles(tf));
    syncBoundary();
    view.setRangeLabel('全期間');
    await drive(cursor.candles().length - 1); // 開始は present（最新足）
  }

  // カレンダーで選んだ日を再生開始日にする。窓ごと取り直し（present 窓の外の過去日も選べる）、
  //   その日の最初の足を再生開始点（減光境界）にして、そこから再生できる状態にする。
  async function loadFromDate(startUnix, key) {
    if (typeof controller.clearRevealCache === 'function') controller.clearRevealCache();
    invalidatePlans();  // [ISSUE-232] 窓を取り直す＝既存計画は無効
    const loaded = await fetchCandles(cursor.timeframe(), startUnix);
    if (!loaded.length) return; // 取得できないときは現状維持（ビューを勝手に動かさない）
    cursor.setCandles(loaded);
    cursor.clearActivePeriod();
    autoFrame = true;
    cursor.setReplayStartAtTime(startUnix);
    window.__rpReplayStart = cursor.replayStart();
    syncBoundary();
    view.setRangeLabel(key || dayKey(startUnix));
    await drive(cursor.replayStart());
  }

  // ---- 速度 / フレーム待機（ISSUE-256: PlaybackTempo が状態ごと所有する） ----
  //   旧: 本関数内に rtAnchorMs / emaPeriodMs / lastComputeMs / frameTimer 等を直に持ち、
  //   速度・ETA・フレーム待機の手続きが再生駆動と同一スコープで混ざっていた。
  const tempo = new PlaybackTempo({
    view,
    getCandles: () => cursor.candles(),
    getBar: () => cursor.bar(),
    getTimeframe: () => cursor.timeframe(),
    // 実時間再生の切替は /intraday の tick_secs 取得可否が変わる＝先読み済み計画も別物。
    onSpeedAxisChanged: () => invalidatePlans(),
  });
  const speed = () => tempo.speed();
  const realtime = () => tempo.realtime();
  const paused = () => tempo.paused();
  const rtBarMs = () => tempo.rtBarMs();
  const setEta = () => tempo.setEta();
  const settleFrameWait = () => tempo.settleFrameWait();
  const waitFrame = () => tempo.waitFrame();
  const rescheduleFrameWait = () => tempo.rescheduleFrameWait();
  const applySpeed = (v) => tempo.applySpeed(v);

  async function playLoop() {
    while (playing && cursor.bar() < cursor.candles().length - 1) {
      while (playing && paused()) await sleepMs(80); // 速度0.00=一時停止（凍結）
      if (!playing) break;
      const barStart = performance.now();
      tempo.anchorBarStart(barStart); // 実時間再生の足始端＝この足の計算（drive）も足の予算内に含める
      let resume = null;
      if (resumeDecision(animator.pausedForm(), cursor.candles()[cursor.bar()])) {
        resume = animator.pausedForm(); // 停止した足の続きから再開
      } else {
        await drive(cursor.bar() + 1);
      }
      await animateForming(() => !playing, resume);
      if (!playing) break;
      await waitFrame();
      const dt = performance.now() - barStart;
      tempo.observeBarDuration(dt);
      setEta();
    }
    playing = false;
    view.setPlayLabel(PLAY_GLYPH);
    view.setPlaying(false);
  }

  // ---- UI 配線 ----
  function updatePlayEnabled() {
    const atEnd = !cursor.candles().length || cursor.bar() >= cursor.candles().length - 1;
    view.setPlayEnabled(!atEnd, NO_FUTURE_MSG);
  }
  // ▷ は「再生」と「再生追随」を兼ねる（依頼者確定 2026-07-26）。再生開始でビューを再生位置へ
  //   追随させる（＝ユーザーの明示イベント起点。ISSUE-164 の自動介入禁止に抵触しない）。
  //   停止時は追随を切らない（停止中はバーが進まないためビューを動かさない＝介入なし）。
  view.el('rp-play').onclick = () => {
    if (cursor.bar() >= cursor.candles().length - 1) return; // 未来足が無い＝再生不可
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

  // ---- 足内一括計算（ISSUE-232）: バー開始前に足内各時点の指標値を作り置きする ----
  //   ISSUE-256: ティック列の取得・署名・構築・先読み・受け取りと、その状態（seqClient / planCache /
  //   planInFlight）は FormingPlanCache が所有する。ここは委譲の薄いラッパだけを残す
  //   （呼び出し側の記述と await 順序は抽出前と同一＝挙動不変）。
  const plans = new FormingPlanCache({
    fetchImpl,
    datasetRef,
    seqClient: new FormingSeqClient({ fetch: fetchImpl }),
    controller,
    getCandles: () => cursor.candles(),
    getTimeframe: () => cursor.timeframe(),
  });
  const invalidatePlans = () => plans.invalidate();
  const buildStream = (idx, mode) => plans.buildStream(idx, mode);
  const prefetchPlan = (idx, mode) => plans.prefetch(idx, mode);
  const takePlan = (idx, mode) => plans.take(idx, mode);

  // ---- 足内アニメーション（ISSUE-256: FormingAnimator が状態ごと所有する） ----
  //   旧: 本関数内に animGen / formingInFlight / lastFormingMs / pausedForm を直に持ち、
  //   形成描画・足内指標追従・MP 連動が再生駆動と同一スコープで混ざっていた。
  const animator = new FormingAnimator({
    view,
    controller,
    getCandles: () => cursor.candles(),
    getBar: () => cursor.bar(),
    getTimeframe: () => cursor.timeframe(),
    tempo,
    plans,
    mpOn,
    mpDriver,
    sleepMs,
  });
  const animateForming = (shouldAbort, resume) => animator.animate(shouldAbort, resume);
  const onModeChange = () => animator.onModeChange();
  view.el('rp-mode').addEventListener('change', onModeChange);
  disposers.push(() => { const e = view.el('rp-mode'); if (e) e.removeEventListener('change', onModeChange); });

  // 1 足スキップ（再生せず次の確定足へ）。
  view.el('rp-next').onclick = () => drive(cursor.bar() + 1);
  view.el('rp-prev').onclick = () => drive(cursor.bar() - 1);
  window.__rpAnimateOnce = () => animateForming(); // promise を返す（決定論テストが await 可能・実行時挙動は不変）
  function scrollViewTo(edge) {
    autoFrame = false;
    const r = view.getVisibleLogicalRange();
    if (!r) return;
    view.setVisibleLogicalRange(scrollRange({ edge, currentRange: r, bar: cursor.bar() }));
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
  // [ISSUE-232] 診断: 現在保持している足内計画のバー添字（実 UI 計測・テストの観測点）。
  window.__rpPlans = () => plans.keys();
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
    cursor.bumpGeneration(); // in-flight render を supersede（isStale で破棄させる）
    animator.supersede(); // in-flight animateForming を supersede（isSuperseded で破棄させる）
    view.setPlayLabel(PLAY_GLYPH);
    view.setPlaying(false);
    settleFrameWait();                              // フレーム待機を即解除
    invalidatePlans();                              // [ISSUE-232] 足内計画を破棄（ライブでは使わない）
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
    //   (1) ライブ全長 cursor.candles() を再取得（fetchCandles＝既存経路）→ driver 状態も現在へ同期（次 enable の
    //       陳腐化防止）、(2) view.setCandles(full)＝renderer.setCandles で全置換＋内部 fitContent＋_lastBar
    //       復帰（現在値 observer がライブ末尾値へ更新）、(3) controller.recomputeAllApplied({mode:'full'})
    //       ＝base（live と同一）入口で untilTime=undefined のまま全指標を全長再描画。値算出は無改変。
    try {
      const live = await fetchCandles(controller._timeframe);
      if (live && live.length) {
        cursor.setCandles(live);
        cursor.setBar(live.length - 1);
        cursor.setReplayStart(0); cursor.clearActivePeriod(); autoFrame = true;
        view.setRangeLabel('全期間');
        view.setCandles(cursor.candles()); // 全長表示（内部 fitContent・_lastBar＝ライブ末尾へ復帰）
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
    //   :128-129（setUntilTime(現在バー)＋_recentBars=cursor.bar()+1）を確立する。値算出・分岐は無改変。
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
