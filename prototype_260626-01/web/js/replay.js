// 再生ドライバ（本番フロントへの唯一の差し替え点）。
//
// 本番 indicator_ui を 1:1 のまま使い、データ駆動だけ「ライブ → 再生」に替える。
// すなわち LiveUpdater（present を 60 秒ポーリング）の代わりに、現在時刻 untilTime を
// フレーム駆動し、本番 controller.recomputeAllApplied で全適用インジを「その時点」で
// 計算する（ライブ完全同一・df[:t+1]・seed 固定）。本番フロント側は無改変。
//
// 設計の要点:
//   - リビール: 足は CANDLES.slice(0, bar+1) で t まで表示し未来を隠す（既定）。
//     ビューは左端固定で右へ伸ばす（直近窓追従はトグルで任意）。スライドしない。
//   - 同時更新（アトミック）: 足リビールとビューを recomputeAllApplied の preRender
//     （計算後の同期描画バッチ内）で行う。await を挟まず帯描画と同フレームに乗るため、
//     足・帯・ビューが常に同一 t で揃う（帯が t-n に遅れない）。preRender は適用 0 でも
//     実行されるので「足だけ」のケースも動く。
//   - 直列化: 再計算の多重実行（系列の二重描画・"計算中"固着）を防ぐため、render を
//     1 本ずつ実行し、実行中の新要求は最新フレームへ coalesce する。

import { ReplayBoundaryDimPrimitive } from './replay_boundary_dim.js';

export async function setupReplay({ chart, mainSeries, controller, renderer, datasetRef, recentBars, document: doc }) {
  const $ = (id) => doc.getElementById(id);
  const RIGHT_MARGIN = 6;                    // 最新足の右に置く余白（バー数）
  const FOLLOW_BARS = 150;                   // 直近窓追従モードで playhead から遡って表示する本数
  const DAY = 86400;
  // 表示レンジ・テンプレート（時間足別）。期間は「秒」で持ち、t 起点で [t-期間, t] を毎回算出する
  //   （バー本数固定より正確・全時間足対応・新足追加で自動追従＝再設定不要）。null=全期間（左端から）。
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
  const fmt = (t) => new Date(t * 1000).toISOString().slice(0, 16).replace('T', ' ');
  const setStatus = (text) => { const el = $('rp-status'); if (el) el.textContent = text; };  // 表示要素が無ければ no-op

  // ---- 状態（既定はリビール: 追従 OFF） ----
  let candles = [];
  let bar = 0;
  let playing = false;
  let timeframe = controller._timeframe;      // 現在の時間足（プリセット表示の切替に使用）
  let generation = 0;                        // スクラブ等で古い計算結果を破棄する世代番号
  let followOn = false;                       // 直近窓追従トグル（OFF=過去すべて表示／ON=直近 FOLLOW_BARS 本）
  let autoFrame = true;                        // 自動フレーム。ユーザーがチャートを直接操作すると false（手動閲覧）
  // 期間プリセット状態。replayStart=既定窓の開始 bar（減光境界の算出に使用）。
  let replayStart = 0;                        // 既定=全期間（先頭から）
  let activeSecs = null;                      // 選択中プリセットの期間秒（ハイライト用・null=全期間）
  let activePeriodBars = null;                // 選択中プリセットの可視窓「幅」（バー数）。null=全期間（左端0まで）。

  // 期間プリセット境界（再生区間の開始位置）より過去側の背景を減光するプリミティブ（本番無改変・装着のみ）。
  const boundaryDim = new ReplayBoundaryDimPrimitive();
  if (mainSeries && typeof mainSeries.attachPrimitive === 'function') {
    mainSeries.attachPrimitive(boundaryDim);
  }
  // replayStart に応じて減光境界を更新する（0=全期間は減光なし）。
  const syncBoundary = () => boundaryDim.setBoundaryTime(
    (replayStart > 0 && candles[replayStart]) ? candles[replayStart].time : null,
  );

  // candles 全体[0, length-1] から time >= target の最小 index を返す（二分探索）。
  function idxForTime(target) {
    let lo = 0, hi = candles.length - 1;
    while (lo < hi) { const m = (lo + hi) >> 1; if (candles[m].time < target) lo = m + 1; else hi = m; }
    return lo;
  }

  // ---- データ取得 ----
  async function fetchCandles(timeframe) {
    const url = `/candles?datasetRef=${encodeURIComponent(datasetRef)}`
      + `&timeframe=${encodeURIComponent(timeframe)}&limit=${recentBars}`;
    const payload = await (await fetch(url)).json();
    return (payload && payload.ok) ? payload.candles : [];
  }

  // ---- 表示 ----
  // 可視範囲を確定する。本番 setCandles が内部で呼ぶ fitContent() を上書きするため、
  // logical レンジで明示し（空白/不安定回避）、同期呼び出しでちらつきを防ぐ。
  function applyView() {
    if (!autoFrame) return;                  // 手動閲覧中（ユーザーが pan/zoom）は上書きしない＝リセットしない
    try {
      // 期間プリセット＝可視窓の「幅」、スライダー(=playhead bar)＝その窓を履歴上でパンする位置。
      //   窓は [bar-幅, bar] とし、最新リビール足(bar)を常に右端へ置く（左端移動バグなし）。
      //   スライダーを動かすと窓ごと履歴がスクロール＝期間プリセットとスライダーが連動する。
      //   直近窓追従(followOn)=固定本数 FOLLOW_BARS の窓。全期間(activePeriodBars=null)=左端0。
      const width = followOn ? FOLLOW_BARS : activePeriodBars;
      const from = (width == null) ? 0 : Math.max(0, bar - width);
      const to = bar + RIGHT_MARGIN;
      chart.timeScale().setVisibleLogicalRange({ from, to });
    } catch (_e) { /* レイアウト未確定時の単発失敗は無視 */ }
  }

  // 現在の時間足に応じたテスト期間プリセットを #rp-presets に並べる。クリックで「直近 N 期間」へズーム：
  //   可視範囲を [present-N(=replayStart), present] にし、最新足は右端に固定したまま全足リビール。
  //   （旧実装は playhead を開始位置へジャンプさせ最新足が左端へ移動するバグ＝本修正で解消）。
  //   期間は present 起点で算出するので新足追加でも自動追従。過去側へは ◀/スライダーで遡れる。
  function renderPresets() {
    const host = $('rp-presets');
    if (!host) return;
    host.innerHTML = '';
    for (const [label, secs] of (RANGE_PRESETS[timeframe] || [['全期間', null]])) {
      const btn = doc.createElement('button');
      btn.textContent = label;
      btn.className = 'rp-preset' + (secs === activeSecs ? ' on' : '');
      btn.onclick = () => {
        activeSecs = secs;
        autoFrame = true;                                  // 明示的な表示操作＝自動フレーム再開
        const present = candles.length - 1;
        const presentTime = candles.length ? candles[present].time : 0;
        replayStart = (secs == null) ? 0 : idxForTime(presentTime - secs);  // 再生位置＝present−期間分
        activePeriodBars = (secs == null) ? null : (present - replayStart);  // 可視窓の幅（バー数）
        window.__rpReplayStart = replayStart;              // E2E/verify 用フック（再生位置=present−期間分）
        // スライダーは全履歴 [0, present] をスクロール。窓幅=プリセットなので、スライダーを動かすと
        //   幅一定の期間窓が履歴上をパンする＝期間プリセットとスライダーが連動する。
        $('rp-slider').min = 0;
        syncBoundary();                                    // 過去側の背景減光境界を更新
        renderPresets();
        // 再生位置(playhead)を replayStart（present−期間分）へ。ここから ▶ で前進再生。
        //   可視窓は [bar−幅, bar] なので最新リビール足(=replayStart)は右端（左端移動なし）。
        drive(replayStart);
      };
      host.appendChild(btn);
    }
  }

  // ---- 1 フレーム描画（その時点を計算 → 足・帯・ビューを同時反映） ----
  async function render(target) {
    if (!candles.length) return;
    const g = ++generation;
    bar = Math.max(0, Math.min(candles.length - 1, target));
    $('rp-slider').value = bar;
    updatePlayEnabled();                                   // 最新足では再生ボタンを減光・抑止
    const t = candles[bar].time;

    controller.setUntilTime(t);                            // 再生のその時点（ライブ同一）
    // インジの計算窓を「リビール済みの足の範囲」に一致させる。controller は limit=recentBars を
    //   渡すため、放置すると指標は [t-recentBars, t] を計算し、リビール足 [present-recentBars, t]
    //   より左（t 以前）へはみ出してスライドに見える。limit=bar+1 で両者の窓を揃える。
    controller._recentBars = bar + 1;
    setStatus(`${fmt(t)} 計算中…`);
    const started = performance.now();
    try {
      // 計算待ち中は前フレーム据え置き。完了後、preRender（足リビール＋ビュー）→ 帯描画が
      // await を挟まず 1 ブロックで走る＝足・帯・ビューが同一 t で同時更新（アトミック）。
      await controller.recomputeAllApplied({
        mode: 'full',
        preRender: () => {
          // 本番 setCandles は内部で fitContent() を呼び可視範囲を戻す。手動閲覧中(autoFrame=false)は
          //   直前の範囲を保存→setCandles 後に復元して fitContent を打ち消す（リセットしない）。
          const saved = (!autoFrame) ? chart.timeScale().getVisibleLogicalRange() : null;
          renderer.setCandles(candles.slice(0, bar + 1));
          if (saved) { try { chart.timeScale().setVisibleLogicalRange(saved); } catch (_e) { /* noop */ } }
          else applyView();
        },
      });
    } catch (e) {
      setStatus(`${fmt(t)} 計算エラー: ${(e && e.message) || e}`);
      return;
    }
    if (g !== generation) return;                          // 後発レンダが来ていれば破棄
    applyView();                                           // 念のため再確定（同一レンジ＝ちらつき無し）
    setStatus(`bar ${bar}/${candles.length - 1}  ${fmt(t)}  計算 ${Math.round(performance.now() - started)}ms（その場計算）`);
    window.__rpbar = bar;                                  // E2E/verify 用フック
  }

  // ---- 直列化（多重再計算の防止・最新フレームへ coalesce） ----
  let busy = false;
  let queued = null;
  async function drive(target) {
    pausedForm = null;          // bar を動かす操作（前進/スクラブ/1足/プリセット）は停止足の続きを無効化
    if (busy) { queued = target; return; }
    busy = true;
    try {
      let cur = target;
      do { queued = null; await render(cur); cur = queued; } while (cur !== null);
    } finally {
      busy = false;                                        // 例外でも必ず解除（固着＝全停止を防ぐ）
    }
  }

  // ---- 時間足ロード / 連続再生 ----
  async function loadTimeframe(tf) {
    timeframe = tf;
    replayStart = 0; activeSecs = null; activePeriodBars = null;  // 時間足切替で「全期間」へ戻す
    candles = await fetchCandles(tf);
    syncBoundary();                                        // 全期間へ戻る＝減光解除（candles 差替後）
    $('rp-slider').min = 0;
    $('rp-slider').max = Math.max(0, candles.length - 1);
    renderPresets();                                       // その時間足のプリセットを再構築
    await drive(candles.length - 1);                       // 開始は present（最新足）＝ライブ同様の表示
  }
  // 再生フレーム待機。速度(fps上限)を再生中に変えたら即時反映するため、
  // 進行中の待機を「残り時間 × 新fps」で組み直せるようにする（待機を中断→再スケジュール）。
  const frameMs = () => 1000 / (+$('rp-speed').value || 1);   // fps 上限 → フレーム間隔
  let frameTimer = null;        // 進行中フレーム待機のタイマーID
  let frameResolve = null;      // 進行中フレーム待機の解決関数（待機中のみ非null）
  let frameStart = 0;           // 現フレーム待機の開始時刻
  function settleFrameWait() {                 // 待機を解消して次フレームへ進める
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
  function rescheduleFrameWait() {             // 速度変更時：残り時間を新fpsで取り直す
    if (frameResolve == null) return;         // 再生待機中でなければ何もしない
    if (frameTimer != null) { clearTimeout(frameTimer); frameTimer = null; }
    const remaining = frameMs() - (performance.now() - frameStart);
    if (remaining <= 0) { settleFrameWait(); return; }
    frameTimer = setTimeout(settleFrameWait, remaining);
  }
  async function playLoop() {
    while (playing && bar < candles.length - 1) {
      let resume = null;
      if (pausedForm && candles[bar] && pausedForm.time === candles[bar].time) {
        resume = pausedForm;            // 停止した足の続きから再開＝次の足へ飛ばさない
      } else {
        await drive(bar + 1);           // 通常前進＝次の足を出す
      }
      await animateForming(() => !playing, resume);   // 形成途中でも停止要求で即中断（停止ボタンの即応性）
      if (!playing) break;
      await waitFrame();
    }
    playing = false;
    $('rp-play').textContent = '▶ 再生';
    $('rp-play').classList.remove('rp-playing');            // ループ終了（末尾到達等）で色を戻す
  }

  // ---- UI 配線 ----
  // 未来足が無い（playhead が最新足）と再生できない。減光＋クリック抑止し、理由を title でホバー表示。
  const NO_FUTURE_MSG = '最新足のため再生できません（未来足が存在しません）';
  function updatePlayEnabled() {
    const btn = $('rp-play');
    if (!btn) return;
    const atEnd = !candles.length || bar >= candles.length - 1;
    btn.classList.toggle('rp-disabled', atEnd);
    btn.title = atEnd ? NO_FUTURE_MSG : '';
  }
  $('rp-play').onclick = () => {
    if (bar >= candles.length - 1) return;                  // 未来足が無い＝再生不可（減光中）
    playing = !playing;
    $('rp-play').textContent = playing ? '⏸ 停止' : '▶ 再生';
    $('rp-play').classList.toggle('rp-playing', playing);   // 再生中は色を変えて状態を明示
    if (playing) playLoop();
    else settleFrameWait();                                 // 停止＝フレーム待機(最大 frameMs)を即解除し待ち時間をゼロに
  };
  $('rp-speed').addEventListener('change', rescheduleFrameWait);  // 再生中の速度変更を即時反映

  // ---- 最新足の足内更新（MT5 モデリング 5 モード相当） ----
  //   現在の最新足(bar)を、選択モードの足内ティック列で 1 ティックずつ更新（mainSeries.update）。
  //   指標(TGP帯)は足確定値のまま（頻度分離: 帯=足/判定=足内）＝足内では再フィットしない。
  const DAY_SECS = 86400;
  const ANIM_MS = 15;            // 足内更新の描画間隔（再生 fps とは別概念・滑らかさ優先）
  const ANIM_CAP = 400;          // 足内更新の最大ステップ（間引き上限）
  const sleepMs = (ms) => new Promise((r) => setTimeout(r, ms));
  const cap = (arr, n) => {      // 最大 n 点へ間引く。高値/安値(極値)と先頭/末尾は必ず保持する
                                 //   （極値ティックを捨てると、後段でティックに無い高安が表示される）。
    if (arr.length <= n) return arr;
    let iMax = 0, iMin = 0;
    for (let i = 1; i < arr.length; i++) {
      if (arr[i] > arr[iMax]) iMax = i;
      if (arr[i] < arr[iMin]) iMin = i;
    }
    const keep = new Set([0, arr.length - 1, iMax, iMin]);   // 先頭/末尾/最高/最安は必ず残す
    const stride = arr.length / n;
    for (let k = 0; k < n; k++) keep.add(Math.floor(k * stride));
    return [...keep].sort((a, b) => a - b).map((i) => arr[i]);
  };
  const flattenM1 = (m1) => {    // 各 M1 を O→H→L→C の 4 疑似ティックへ（1分OHLC）
    const out = [];
    for (const b of m1) { out.push(b[0], b[1], b[2], b[3]); }
    return out;
  };
  const synthM1 = (m1) => {      // 各 M1 を O→H→L→C の補間で多数化（全ティック合成）
    const out = [];
    for (const [o, h, l, c] of m1) { out.push(o, (o + h) / 2, h, (h + l) / 2, l, (l + c) / 2, c); }
    return out;
  };
  async function buildStream(cd, mode) {
    if (mode === 'open_only') return { prices: [cd.open], note: '始値のみ1更新' };
    if (mode === 'math') return { prices: [cd.close], note: '終値で1回（足内更新なし）' };
    const url = `/intraday?datasetRef=${encodeURIComponent(datasetRef)}&start=${cd.time}&end=${cd.time + DAY_SECS}`;
    let resp = {};
    try { resp = await (await fetch(url)).json(); } catch (_e) { /* noop */ }
    const m1 = resp.m1 || [], ticks = resp.ticks || [];
    if (mode === 'real_ticks') {
      if (ticks.length) return { prices: cap(ticks, ANIM_CAP), note: `実ティック ${ticks.length}点` };
      if (m1.length) return { prices: cap(flattenM1(m1), ANIM_CAP), note: '実ティック無→M1 OHLC代替' };
      return { prices: [cd.close], note: '足内データ無→終値のみ' };
    }
    if (mode === 'ohlc_1min') {
      if (m1.length) return { prices: cap(flattenM1(m1), ANIM_CAP), note: `1分OHLC ${m1.length}本` };
      return { prices: [cd.open, cd.high, cd.low, cd.close], note: 'M1無→日足OHLC4点' };
    }
    if (m1.length) return { prices: cap(synthM1(m1), ANIM_CAP), note: '全ティック合成(M1×補間)' };
    return { prices: [cd.open, cd.high, cd.low, cd.close], note: 'M1無→OHLC4点' };
  }
  // 形成は「最新の1本のみ有効」。新しい形成（1足送り/モード切替/再生）が始まると、旧形成は次の
  //   更新で自身を破棄する（世代トークン animGen）。旧実装の `if(animating) return` は新規呼び出しを
  //   握り潰し、長いティック形成中の 1足送り・モード変更が無反応に見える原因だった（モード別更新の喪失）。
  let animGen = 0;
  // 「停止」した足が形成途中だった場合、その続き（prices・途中index i・進捗 o/hi/lo）を保持し、
  //   次の「再生」で同じ足を続きから再開する（次の足へ飛ばさない）。bar が変わる操作(drive)で無効化。
  let pausedForm = null;
  // shouldAbort: 再生から呼ぶとき () => !playing を渡す。足内更新ループ途中でも停止要求で即中断し、
  //   「停止」ボタンのタイムラグ（最大 ANIM_CAP×ANIM_MS ≒ 数秒）を解消する。1足送りは未指定（中断なし）。
  // resume: pausedForm を渡すと同一足を続きから再開（畳み直さず途中 index から継続）。
  async function animateForming(shouldAbort, resume) {
    if (!candles.length) return;
    const cd = candles[bar];
    if (!cd) return;
    const myGen = ++animGen;                                // この形成を最新化（旧形成は次の更新で停止）
    const superseded = () => myGen !== animGen;
    window.__rpAnimating = true;                            // E2E/verify 用フック（停止即応性の計測）
    const mode = $('rp-mode').value;                        // モードは呼び出しごとに最新値を読む
    try {
      let prices, o, hi, lo, startI;
      if (resume && resume.time === cd.time) {
        // 同一足の続きから再開：畳み直さず、停止時点の prices・hi/lo・index を引き継ぐ。
        prices = resume.prices; o = resume.o; hi = resume.hi; lo = resume.lo; startI = resume.i;
      } else {
        // 確定日足のチラ見せ防止: fetch を await する前（同期）に最新足を始値の同事足へ畳む。
        //   drive() が描いた完成足を、ティック取得待ちの間に見せない（paint 前に上書き）。
        if (mode !== 'math') {
          try { mainSeries.update({ time: cd.time, open: cd.open, high: cd.open, low: cd.open, close: cd.open }); } catch (_e) { /* noop */ }
        }
        ({ prices } = await buildStream(cd, mode));
        if (superseded()) return;                          // fetch 待ちの間に新形成が来たら破棄
        o = prices[0]; hi = prices[0]; lo = prices[0]; startI = 0;  // 始値はティック列の先頭値
      }
      window.__rpForm = { mode, n: prices.length };         // E2E/verify 用フック（モード別ストリーム確認）
      for (let i = startI; i < prices.length; i++) {
        if (shouldAbort && shouldAbort()) {                // 停止要求＝即中断（タイムラグ解消）＋続き保存
          pausedForm = { time: cd.time, prices, o, hi, lo, i };
          return;
        }
        if (superseded()) return;                          // 新形成に置換＝この足は破棄（pausedForm は保存しない）
        const p = prices[i];
        hi = Math.max(hi, p); lo = Math.min(lo, p);        // 高安は流れてきたティックの極値のみ
        try { mainSeries.update({ time: cd.time, open: o, high: hi, low: lo, close: p }); } catch (_e) { /* noop */ }
        await sleepMs(ANIM_MS);
      }
      pausedForm = null;                                   // 完走＝続き情報は破棄
      // 足確定: ティック列由来の OHLC で確定する。cd.high/low へはスナップしない
      //   （日足集計の高安は流したティックに無い値になり得るため＝バグ「存在しない高安」防止）。
      try { mainSeries.update({ time: cd.time, open: o, high: hi, low: lo, close: prices[prices.length - 1] }); } catch (_e) { /* noop */ }
    } finally { if (myGen === animGen) window.__rpAnimating = false; }  // 最新が終了した時のみ false
  }
  // 1 足送り＝新しく現れた最新足（playhead）を選択モードで足内形成する。
  //   最新足の定義は「期間プリセット起点から前進する先頭足」。本当のデータ末尾ではない。
  $('rp-next').onclick = async () => { await drive(bar + 1); await animateForming(); };
  $('rp-prev').onclick = () => drive(bar - 1);
  // チャート「表示範囲だけ」を左端/右端へスクロールする（再生位置 bar・スライダー・計算は変えない）。
  //   現在のズーム幅（可視バー数）を保ったまま、左端=logical 0／右端=最新リビール足(bar+余白)へ寄せる。
  //   手動閲覧扱い（autoFrame=false）にして、後続フレームが表示を上書きしないようにする（wheel/ドラッグと同義）。
  function scrollViewTo(edge) {
    autoFrame = false;
    try {
      const r = chart.timeScale().getVisibleLogicalRange();
      if (!r) return;
      const width = r.to - r.from;                          // 現在のズーム幅を維持
      if (edge === 'left') {
        chart.timeScale().setVisibleLogicalRange({ from: 0, to: width });
      } else {
        const to = bar + RIGHT_MARGIN;                      // 右端＝最新リビール足＋余白
        chart.timeScale().setVisibleLogicalRange({ from: to - width, to });
      }
    } catch (_e) { /* レイアウト未確定時は無視 */ }
  }
  $('rp-view-left').onclick = () => scrollViewTo('left');
  $('rp-view-right').onclick = () => scrollViewTo('right');
  $('rp-slider').oninput = (e) => drive(+e.target.value);
  $('rp-follow').onclick = () => {                         // 直近窓追従トグル（OFF=過去全表示／ON=直近窓）
    followOn = !followOn;
    $('rp-follow').classList.toggle('on', followOn);
    autoFrame = true;                                      // 明示的な表示操作＝自動フレーム再開
    applyView();                                           // 表示モード切替のみ（再計算不要）
  };
  // 表示期間プリセットは renderPresets() が動的にボタン生成・配線する（時間足別・全期間共通）。
  // 時間足ボタンは本番 setTimeframe を発火する。その後に再生 candles を取り直して再生フレームへ。
  for (const btn of doc.querySelectorAll('.tb-interval')) {
    btn.addEventListener('click', () => setTimeout(() => loadTimeframe(btn.dataset.timeframe), 60));
  }

  // チャートを直接操作（ホイール=拡大縮小／ドラッグ=移動）したら自動フレームを停止＝以降リセットしない。
  //   詳細ポイントの検証を妨げない。再開は「直近窓追従」または期間プリセットのクリックで行う。
  try {
    const el = chart.chartElement ? chart.chartElement() : null;
    if (el) {
      let down = false;
      el.addEventListener('wheel', () => { autoFrame = false; }, { passive: true });
      el.addEventListener('mousedown', () => { down = true; });
      el.addEventListener('mousemove', () => { if (down) autoFrame = false; });
      doc.addEventListener('mouseup', () => { down = false; });
    }
  } catch (_e) { /* chartElement 非対応環境では自動フレームのまま（従来挙動） */ }

  // ---- 起動 ----
  window.__rpChart = chart;                                // E2E/verify 用フック（可視範囲計測）
  window.__rpSetAuto = (v) => { autoFrame = !!v; };        // E2E/verify 用フック（手動操作の模擬）
  window.__rpAuto = () => autoFrame;                       // E2E/verify 用フック（状態確認）
  await loadTimeframe(controller._timeframe);
  window.__rpReady = true;                                 // E2E/verify 用フック
}
