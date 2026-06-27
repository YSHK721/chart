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

export async function setupReplay({ chart, controller, renderer, datasetRef, recentBars, document: doc }) {
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
  // テスト期間プリセットで選択する再生開始位置。replayStart=区間の開始 bar（playhead のジャンプ先）。
  let replayStart = 0;                        // 既定=全期間（先頭から）
  let activeSecs = null;                      // 選択中プリセットの期間秒（ハイライト用・null=全期間）

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
      // playhead(bar) を右端に置く（標準的なリプレイ）。未来は隠れ ▶ で右端に1足ずつ現れる。
      //   追従 OFF=左端固定(0)で過去すべて表示（リビール）／ON=直近 FOLLOW_BARS 本だけ表示。
      //   プリセットは開始位置のジャンプを担い、表示モード（追従の有無）とは独立。
      const from = followOn ? Math.max(0, bar - FOLLOW_BARS) : 0;
      chart.timeScale().setVisibleLogicalRange({ from, to: bar + RIGHT_MARGIN });
    } catch (_e) { /* レイアウト未確定時の単発失敗は無視 */ }
  }

  // 現在の時間足に応じたテスト期間プリセットを #rp-presets に並べる。クリックで「再生区間」を選択：
  //   playhead を present から期間ぶん遡った開始位置へジャンプし、区間 [開始, present] を枠表示する。
  //   以降 ▶ で前進再生＝その期間をリプレイ。期間は present 起点で算出するので新足追加でも自動追従。
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
        const presentTime = candles.length ? candles[candles.length - 1].time : 0;
        replayStart = (secs == null) ? 0 : idxForTime(presentTime - secs);  // 区間開始 bar（全期間=先頭）
        renderPresets();
        drive(replayStart);   // 開始位置へジャンプ（区間を枠表示・足リビール）。以降 ▶ で前進再生。
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
    replayStart = 0; activeSecs = null;                    // 時間足切替で「全期間」へ戻す
    candles = await fetchCandles(tf);
    $('rp-slider').min = 0;
    $('rp-slider').max = Math.max(0, candles.length - 1);
    renderPresets();                                       // その時間足のプリセットを再構築
    await drive(candles.length - 1);                       // 開始は present（最新足）＝ライブ同様の表示
  }
  async function playLoop() {
    const frameMs = () => 1000 / (+$('rp-speed').value || 1);   // fps 上限 → フレーム間隔
    while (playing && bar < candles.length - 1) {
      await drive(bar + 1);
      await new Promise((resolve) => setTimeout(resolve, frameMs()));
    }
    playing = false;
    $('rp-play').textContent = '▶ 再生';
  }

  // ---- UI 配線 ----
  $('rp-play').onclick = () => {
    playing = !playing;
    $('rp-play').textContent = playing ? '⏸ 停止' : '▶ 再生';
    if (playing) playLoop();
  };
  $('rp-next').onclick = () => drive(bar + 1);
  $('rp-prev').onclick = () => drive(bar - 1);
  $('rp-first').onclick = () => drive(0);
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
