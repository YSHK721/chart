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
  const FOLLOW_BARS = 150;                   // 追従モードで右端から遡って表示する本数
  const fmt = (t) => new Date(t * 1000).toISOString().slice(0, 16).replace('T', ' ');

  // ---- 状態（既定はリビール: 追従 OFF） ----
  let candles = [];
  let bar = 0;
  let playing = false;
  let followOn = false;
  let generation = 0;                        // スクラブ等で古い計算結果を破棄する世代番号

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
    try {
      const from = followOn ? Math.max(0, bar - FOLLOW_BARS) : 0;   // リビール=0 固定 / 追従=直近窓
      chart.timeScale().setVisibleLogicalRange({ from, to: bar + RIGHT_MARGIN });
    } catch (_e) { /* レイアウト未確定時の単発失敗は無視 */ }
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
    $('rp-status').textContent = `${fmt(t)} 計算中…`;
    const started = performance.now();
    try {
      // 計算待ち中は前フレーム据え置き。完了後、preRender（足リビール＋ビュー）→ 帯描画が
      // await を挟まず 1 ブロックで走る＝足・帯・ビューが同一 t で同時更新（アトミック）。
      await controller.recomputeAllApplied({
        mode: 'full',
        preRender: () => { renderer.setCandles(candles.slice(0, bar + 1)); applyView(); },
      });
    } catch (e) {
      $('rp-status').textContent = `${fmt(t)} 計算エラー: ${(e && e.message) || e}`;
      return;
    }
    if (g !== generation) return;                          // 後発レンダが来ていれば破棄
    applyView();                                           // 念のため再確定（同一レンジ＝ちらつき無し）
    $('rp-status').textContent =
      `bar ${bar}/${candles.length - 1}  ${fmt(t)}  計算 ${Math.round(performance.now() - started)}ms（その場計算）`;
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
  async function loadTimeframe(timeframe) {
    candles = await fetchCandles(timeframe);
    $('rp-slider').min = 0;
    $('rp-slider').max = Math.max(0, candles.length - 1);
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
  $('rp-follow').onclick = () => {
    followOn = !followOn;
    $('rp-follow').classList.toggle('on', followOn);
    drive(bar);
  };
  // 時間足ボタンは本番 setTimeframe を発火する。その後に再生 candles を取り直して再生フレームへ。
  for (const btn of doc.querySelectorAll('.tb-interval')) {
    btn.addEventListener('click', () => setTimeout(() => loadTimeframe(btn.dataset.timeframe), 60));
  }

  // ---- 起動 ----
  window.__rpChart = chart;                                // E2E/verify 用フック（可視範囲計測）
  await loadTimeframe(controller._timeframe);
  window.__rpReady = true;                                 // E2E/verify 用フック
}
