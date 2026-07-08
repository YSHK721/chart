/* prototype_260630-01 — Market Profile (TPO) ビューア
 * 左: lightweight-charts のローソク足。価格軸(y)に揃えて右側へ
 * TPO 横ヒートヒストグラム(累積多い=赤)を重ねる。POC/VA も canvas に描画。
 * 使い捨て試作（スパイク）。完成度より「動く」を優先。
 */
(() => {
  'use strict';
  const LWC = window.LightweightCharts;
  const $ = (id) => document.getElementById(id);

  // ---- chart 構築（v5: createChart -> addSeries(CandlestickSeries, opts)） ----
  const chartEl = $('chart');
  const chart = LWC.createChart(chartEl, {
    layout: { background: { color: '#0b0e13' }, textColor: '#9aa4b2' },
    grid: {
      vertLines: { color: 'rgba(255,255,255,.04)' },
      horzLines: { color: 'rgba(255,255,255,.04)' },
    },
    rightPriceScale: { borderColor: '#222b36', scaleMargins: { top: 0.08, bottom: 0.08 } },
    timeScale: { borderColor: '#222b36', timeVisible: true, secondsVisible: false },
    crosshair: { mode: 0 },
  });
  const candleSeries = chart.addSeries(LWC.CandlestickSeries, {
    upColor: '#26a69a', downColor: '#ef5350',
    wickUpColor: '#26a69a', wickDownColor: '#ef5350',
    borderVisible: false,
  });

  // ---- ヒート canvas（チャートに重ねた絶対配置） ----
  const cv = $('heat');
  const ctx = cv.getContext('2d');
  let dpr = window.devicePixelRatio || 1;

  function resizeCanvas() {
    dpr = window.devicePixelRatio || 1;
    const w = chartEl.clientWidth, h = chartEl.clientHeight;
    cv.style.width = w + 'px'; cv.style.height = h + 'px';
    cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
    chart.applyOptions({ width: w, height: h });
  }

  // ---- ヒート配色: norm 0..1 を 青→シアン→黄→赤 (hue 240→0) ----
  function heatColor(norm, alpha = 0.95) {
    const t = Math.max(0, Math.min(1, norm));
    const hue = 240 * (1 - t);          // 0 → 青(240), 1 → 赤(0)
    const light = 46 + 12 * t;          // 高いほど明るく（低 norm も視認できる明度）
    return `hsla(${hue.toFixed(0)}, 95%, ${light.toFixed(0)}%, ${alpha})`;
  }

  // ---- 状態 ----
  let prof = null;          // /profile 応答
  let priceAxisW = 60;      // 右価格軸の幅(px)
  let lastCandles = [];     // 直近に描画したローソク（logical→time 変換用）
  let selRange = null;      // マウスドラッグで選択した期間 {from,to}（UNIX秒）
  let selectMode = false;   // 範囲選択モード
  let drag = null;          // ドラッグ中ピクセル {x0,x1}（範囲選択）
  let scrubDrag = null;     // スワイプ遡り中 {startX, startIdx}
  let lastTrimIdx = -1;     // 直近の当時表示トリム位置（変化時のみ再setData）
  let resMode = 'bins';     // 解像度指定モード: 'bins'(本数) ⇄ 'range'(価格幅pt)
  const PROFILE_FRAC = 0.30; // プロファイル(ヒート/セッション)が占める右側の幅割合
  const HEAT_BG_BASE = 0.03; // 全幅ヒート背景の基準不透明度(3%)
  const HEAT_BG_GRAD = 0.15; // TPO(norm)に応じて加算するグラデーション分

  // 横線を canvas に直接描く（POC/VA/現値）。priceToCoordinate は描画後に有効。
  function hLine(y, color, dash, label) {
    if (y == null || !isFinite(y)) return;
    ctx.save();
    ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.setLineDash(dash || []);
    ctx.beginPath(); ctx.moveTo(0, y + 0.5); ctx.lineTo(paneRight(), y + 0.5); ctx.stroke();
    if (label) {
      ctx.setLineDash([]); ctx.fillStyle = color; ctx.font = '11px system-ui';
      ctx.textBaseline = 'bottom';
      ctx.fillText(label, 6, y - 2);
    }
    ctx.restore();
  }

  function paneRight() {
    // 価格軸の左端＝描画領域の右端
    return Math.max(0, chartEl.clientWidth - priceAxisW);
  }

  // ---- 毎フレーム再描画（priceToCoordinate はチャート描画後に有効） ----
  function redrawHeat() {
    if (!prof) return;
    try { priceAxisW = chart.priceScale('right').width() || priceAxisW; } catch (_) {}

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cv.width, cv.height);

    const right = paneRight();
    const profX = right * (1 - PROFILE_FRAC);  // プロファイル領域の左端（ここより右に収める）
    const maxBarW = right * PROFILE_FRAC;       // ヒートバー最大幅（右余白に収める）
    const half = (prof.bin_h || 0) / 2;

    // --- VA帯（薄黄の塗り・全幅） ---
    const yH = candleSeries.priceToCoordinate(prof.va_high);
    const yL = candleSeries.priceToCoordinate(prof.va_low);
    if (yH != null && yL != null) {
      ctx.fillStyle = 'rgba(227,179,65,0.08)';
      ctx.fillRect(0, Math.min(yH, yL), right, Math.abs(yL - yH));
    }
    // （全幅ヒート背景はローソクの視認性を優先して廃止。プロファイルは右ヒストグラム＋POC/VA線で表現）

    // --- プロファイル領域とチャートの境界（薄い区切り線） ---
    ctx.save();
    ctx.strokeStyle = 'rgba(255,255,255,.08)'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(profX + 0.5, 0); ctx.lineTo(profX + 0.5, chartEl.clientHeight); ctx.stroke();
    ctx.restore();

    if ($('sessions').checked && prof.sessions && prof.sessions.length) {
      drawSessions(right);
    } else {
      drawComposite(right, maxBarW, half);
    }

    // --- POC / VAH / VAL / 現値 ---
    hLine(yH, 'rgba(154,164,178,.9)', [5, 4], 'VAH ' + fmt(prof.va_high));
    hLine(yL, 'rgba(154,164,178,.9)', [5, 4], 'VAL ' + fmt(prof.va_low));
    hLine(candleSeries.priceToCoordinate(prof.poc), '#ff3b3b', [], 'POC ' + fmt(prof.poc));
    hLine(candleSeries.priceToCoordinate(prof.last_close), '#58a6ff', [4, 4],
          'last ' + fmt(prof.last_close));

    drawSelection();   // 選択期間の縦帯/ドラッグ矩形
  }

  // 選択期間（確定 selRange と、ドラッグ中の矩形）を縦帯で描く
  function drawSelection() {
    const H = chartEl.clientHeight;
    if (selRange) {
      const ts = chart.timeScale();
      const xa = ts.timeToCoordinate(selRange.from), xb = ts.timeToCoordinate(selRange.to);
      if (xa != null && xb != null) {
        ctx.save();
        ctx.fillStyle = 'rgba(88,166,255,0.08)';
        ctx.fillRect(Math.min(xa, xb), 0, Math.abs(xb - xa), H);
        ctx.strokeStyle = 'rgba(88,166,255,0.6)'; ctx.setLineDash([4, 3]); ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(xa + .5, 0); ctx.lineTo(xa + .5, H);
        ctx.moveTo(xb + .5, 0); ctx.lineTo(xb + .5, H); ctx.stroke();
        ctx.restore();
      }
    }
    if (drag) {
      const x0 = Math.min(drag.x0, drag.x1), w = Math.abs(drag.x1 - drag.x0);
      ctx.save();
      ctx.fillStyle = 'rgba(88,166,255,0.15)';
      ctx.fillRect(x0, 0, w, H);
      ctx.strokeStyle = 'rgba(88,166,255,0.8)'; ctx.lineWidth = 1;
      ctx.strokeRect(x0 + .5, .5, w, H - 1);
      ctx.restore();
    }
    // 遡り時点T の縦線（当時表示OFF時のみ。ON時はTが右端なので不要）
    if ($('asof').checked && !$('asoftrim').checked && lastCandles.length) {
      const x = chart.timeScale().timeToCoordinate(lastCandles[asofIdx()].time);
      if (x != null) {
        ctx.save();
        ctx.strokeStyle = 'rgba(255,209,102,0.9)'; ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.moveTo(x + .5, 0); ctx.lineTo(x + .5, H); ctx.stroke();
        ctx.restore();
      }
    }
  }

  const DIM_ALPHA = 0.30;   // 当日以外（累積）バーの減光アルファ

  // composite: 各ビンに水平バー。「当時表示」ON時のみ 当日以外を減光＋当日分を明るく強調。
  function drawComposite(right, maxBarW, half) {
    const showToday = $('asoftrim').checked;   // 既存の「当時表示」に紐付け
    const today = prof.today || null;
    const tMax = prof.today_max || 1;     // 当日内スケール（当日データのみ・累積とは独立）
    for (let i = 0; i < prof.bins.length; i++) {
      const b = prof.bins[i];
      if (!b.tpo) continue;
      const yC = candleSeries.priceToCoordinate(b.price);
      if (yC == null) continue;
      const yTop = candleSeries.priceToCoordinate(b.price + half);
      const yBot = candleSeries.priceToCoordinate(b.price - half);
      let bh = (yTop != null && yBot != null) ? Math.abs(yBot - yTop) : 3;
      bh = Math.max(2, bh - 0.5);
      const w = Math.max(4, b.norm * maxBarW);   // 低 norm も最小幅で視認
      // 累積: 通常は明るく。当日表示ON時のみ減光（当日を際立たせるため）
      ctx.fillStyle = heatColor(b.norm, showToday ? DIM_ALPHA : 0.95);
      ctx.fillRect(right - w, yC - bh / 2, w, bh);
      // 当日分（当日表示ON時のみ）＝当日データを当日内スケールで明るく重畳
      if (showToday && today && today[i] > 0) {
        const tn = today[i] / tMax;
        const tw = Math.max(4, tn * maxBarW);
        ctx.fillStyle = heatColor(tn, 0.98);
        ctx.fillRect(right - tw, yC - bh / 2, tw, bh);
      }
    }
  }

  const SESS_MIN_COL = 102;  // 1セッションの最小幅(px)。分析できる幅を確保

  // sessions: チャートは非表示（価格軸のみ維持）。各営業日のプロファイルを幅広の列で表示（直近優先）
  function drawSessions(right) {
    const all = prof.sessions;
    if (!all || !all.length) return;
    const H = chartEl.clientHeight;
    const nFit = Math.max(1, Math.floor(right / SESS_MIN_COL));
    const ss = all.slice(Math.max(0, all.length - nFit));   // 幅確保のため直近 nFit 日を表示
    const colW = right / ss.length;
    const half = (prof.bin_h || 0) / 2;
    ctx.font = '10px system-ui'; ctx.textBaseline = 'top';
    for (let i = 0; i < ss.length; i++) {
      const arr = ss[i].tpo || [];
      let dmax = 1e-9, pocj = -1; for (let j = 0; j < arr.length; j++) if (arr[j] > dmax) { dmax = arr[j]; pocj = j; }
      const cx = i * colW;
      ctx.fillStyle = i % 2 ? 'rgba(255,255,255,.05)' : 'rgba(255,255,255,.015)';  // 列を交互に区別
      ctx.fillRect(cx, 0, colW, H);
      for (let j = 0; j < arr.length; j++) {
        const v = arr[j]; if (!v) continue;
        const b = prof.bins[j]; if (!b) continue;
        const yC = candleSeries.priceToCoordinate(b.price); if (yC == null) continue;
        const yT = candleSeries.priceToCoordinate(b.price + half);
        const yB = candleSeries.priceToCoordinate(b.price - half);
        let bh = (yT != null && yB != null) ? Math.abs(yB - yT) : 2; bh = Math.max(1.5, bh - 0.4);
        const w = Math.max(1.5, (v / dmax) * (colW - 4));
        ctx.fillStyle = (j === pocj) ? 'rgba(255,255,255,.95)' : heatColor(v / dmax, 0.98);  // POCは白で強調
        ctx.fillRect(cx + 2, yC - bh / 2, w, bh);
      }
      // 列上部に日付(短縮)
      ctx.fillStyle = 'rgba(154,164,178,.6)';
      ctx.fillText((ss[i].date || '').slice(5), cx + 3, 4);
    }
    if (ss.length < all.length) {
      ctx.fillStyle = 'rgba(154,164,178,.8)'; ctx.font = '11px system-ui';
      ctx.fillText(`直近${ss.length}/${all.length}日（n↓で広く）`, 6, H - 16);
    }
  }

  function fmt(v) { return (v == null) ? '-' : Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 }); }

  // ---- footer 表示（VA上下判定） ----
  function updateReadout() {
    const ro = $('readout');
    if (!prof) { ro.textContent = '—'; return; }
    const lc = prof.last_close;
    let cls = 'eq', word = 'VA内(均衡)';
    if (lc > prof.va_high) { cls = 'up'; word = 'VA上(割高)'; }
    else if (lc < prof.va_low) { cls = 'down'; word = 'VA下(割安)'; }
    const unitTxt = prof.src === 'dwell'
      ? `滞在 ${(prof.tpo_units / 3600).toLocaleString(undefined, { maximumFractionDigits: 1 })}h`
      : `${prof.atom || ''} ${prof.tpo_units.toLocaleString()}`;
    ro.innerHTML =
      `POC ${fmt(prof.poc)}   VA [${fmt(prof.va_low)} – ${fmt(prof.va_high)}]   ` +
      `原子=${prof.atom || prof.src}  ${unitTxt}   bins ${prof.n_bins}(≈${prof.bar_width}pt/バー)   ` +
      `現値 ${fmt(lc)}  →  <span class="${cls}">${word}</span>`;
  }

  // ---- データ取得 ----
  const ROLL_BARS = 60;   // ローリング窓(B)の本数

  function asofIdx() {    // スライダ位置→表示足のインデックス
    const L = lastCandles.length;
    if (!L) return 0;
    const v = parseInt($('asoft').value, 10);   // 0 を偽扱いしない（|| は使わない）
    return Math.max(0, Math.min(L - 1, Number.isNaN(v) ? (L - 1) : v));
  }

  function params() {
    const p = {
      tf: $('tf').value, n: $('n').value, bins: $('bins').value,
      src: $('src').value, va: $('va').value,
      sessions: $('sessions').checked ? '1' : '0',
    };
    if ($('asof').checked && lastCandles.length) {   // 遡り（時点T）が最優先
      const idx = asofIdx();
      const T = lastCandles[idx].time;
      const anchor = $('asofmode').value === 'anchor';
      const i0 = anchor ? 0 : Math.max(0, idx - ROLL_BARS + 1);
      p.from = lastCandles[i0].time; p.to = T;
    } else if (selRange) { p.from = selRange.from; p.to = selRange.to; }  // 範囲選択
    if (resMode === 'range') p.barw = $('barw').value;   // rangeモード時のみ価格幅で指定
    p.today = '1';                    // 窓の最終日ぶん（別カラー表示用）を要求
    return p;
  }

  function updateAsofLabel() {
    $('asoflabel').textContent = $('asof').checked && lastCandles.length
      ? '@' + new Date(lastCandles[asofIdx()].time * 1000).toISOString().slice(0, 10)
        + ($('asofmode').value === 'anchor' ? ' アンカー' : ' ローリング' + ROLL_BARS)
      : '';
  }

  // ---- スクラブ用: プロファイルのみ取得（ローソク再取得なし・最新1件のみ反映・間引き） ----
  let profSeq = 0, scrubRunning = false, scrubQueued = false;

  async function fetchProfileOnly() {
    const seq = ++profSeq;
    const qs = new URLSearchParams(params()).toString();
    const res = await fetch(`/profile?${qs}`);
    const j = await res.json();
    if (seq !== profSeq) return;          // 古い応答は破棄（最新のみ反映）
    if (j.error) throw new Error(j.error);
    prof = j; window.__prof = j;
    updateReadout();
    redrawHeat();
  }

  async function scrubProfile() {          // 連続入力を最新1件へ合流（coalesce）
    updateAsofLabel();
    if (scrubRunning) { scrubQueued = true; return; }
    scrubRunning = true;
    try { await fetchProfileOnly(); } catch (_) {}
    scrubRunning = false;
    if (scrubQueued) { scrubQueued = false; scrubProfile(); }   // 末尾実行
  }

  // 当時表示ON時: ローソクを局所トリム（/candles再取得なし・位置変化時のみ setData）
  function applyAsofView() {
    if (!lastCandles.length) return;
    const trim = $('asof').checked && $('asoftrim').checked;
    const idx = trim ? asofIdx() : (lastCandles.length - 1);
    if (idx === lastTrimIdx) return;       // 変化なしは何もしない（重い再描画を回避）
    lastTrimIdx = idx;
    const disp = trim ? lastCandles.slice(0, idx + 1) : lastCandles;
    candleSeries.setData(disp);
    const L = disp.length;
    const blank = $('sessions').checked ? 0 : L * PROFILE_FRAC / (1 - PROFILE_FRAC);
    chart.timeScale().setVisibleLogicalRange({ from: -0.5, to: L - 0.5 + blank });
  }

  function scrubHandler() { applyAsofView(); scrubProfile(); }   // スライダ操作の軽量ハンドラ

  async function reload() {
    const p = params();
    $('status').textContent = '取得中…';
    try {
      const qs = new URLSearchParams(p).toString();
      const [cRes, pRes] = await Promise.all([
        fetch(`/candles?tf=${p.tf}&n=${p.n}`),
        fetch(`/profile?${qs}`),
      ]);
      const cJson = await cRes.json();
      prof = await pRes.json();
      if (prof.error) throw new Error(prof.error);

      const candles = cJson.candles.map((d) => ({
        time: d.time, open: d.open, high: d.high, low: d.low, close: d.close,
      }));
      lastCandles = candles;
      $('asoft').max = String(Math.max(0, candles.length - 1));
      // 遡り＋当時表示: ローソクを時点Tまでに切る（当時の見え方を再現）
      let disp = candles;
      if ($('asof').checked && $('asoftrim').checked) {
        disp = candles.slice(0, asofIdx() + 1);
      }
      candleSeries.setData(disp);
      lastTrimIdx = disp.length - 1;   // applyAsofView の重複setDataを防ぐ
      // sessions時はローソクを透明化（非表示・価格軸は維持）。通常時は色を戻す
      candleSeries.applyOptions($('sessions').checked
        ? { upColor: 'rgba(0,0,0,0)', downColor: 'rgba(0,0,0,0)', wickUpColor: 'rgba(0,0,0,0)', wickDownColor: 'rgba(0,0,0,0)' }
        : { upColor: '#26a69a', downColor: '#ef5350', wickUpColor: '#26a69a', wickDownColor: '#ef5350' });
      // 通常はローソクを左へ寄せ右にプロファイル余白。sessions(オーバーレイ)時は全幅
      const L = disp.length;
      const blank = $('sessions').checked ? 0 : L * PROFILE_FRAC / (1 - PROFILE_FRAC);
      chart.timeScale().setVisibleLogicalRange({ from: -0.5, to: L - 0.5 + blank });
      updateAsofLabel();
      updateReadout();
      resizeCanvas();
      redrawHeat();
      $('status').textContent =
        `OK  bins=${prof.bins.length}  原子=${prof.atom || prof.src}` +
        (selRange ? '  [範囲選択中]' : '') +
        ((p.src === 'm1' || p.src === 'dwell') ? '  (ティックベース/やや重い)' : '');
      window.__ready = true;
    } catch (e) {
      $('status').textContent = 'ERR: ' + e.message;
      $('readout').textContent = 'fetch 失敗: ' + e.message;
      console.error(e);
    }
  }

  // ---- 再描画ループ（チャート再描画/スクロール/ズームに追従） ----
  function loop() { redrawHeat(); requestAnimationFrame(loop); }

  // ---- イベント ----
  ['bins', 'barw', 'src', 'va', 'sessions'].forEach((id) =>
    $(id).addEventListener('change', reload));

  // 解像度: bins(本数) ⇄ range(価格幅) トグル。片方だけ表示
  function updateResMode() {
    $('resmode').textContent = resMode;
    $('bins').style.display = resMode === 'bins' ? '' : 'none';
    $('barw').style.display = resMode === 'range' ? '' : 'none';
  }
  $('resmode').addEventListener('click', () => {
    resMode = resMode === 'bins' ? 'range' : 'bins';
    updateResMode();
    reload();
  });
  updateResMode();
  // tf/n は表示窓が変わるので選択範囲をクリアして再取得
  ['tf', 'n'].forEach((id) =>
    $(id).addEventListener('change', () => { selRange = null; reload(); }));

  // 遡り（時間カーソル）: AB兼用（累積/ローリング）＋当時表示
  ['asofmode', 'asoftrim'].forEach((id) => $(id).addEventListener('change', reload));
  function syncAsofControls() {          // 遡りOFF時はスライダ/モードを無効化(グレーアウト)
    const on = $('asof').checked;
    $('asoft').disabled = !on;
    $('asofmode').disabled = !on;
  }
  $('asof').addEventListener('change', () => {
    if ($('asof').checked) { selRange = null; $('asoft').value = $('asoft').max; }
    syncAsofControls();
    updateCaptureMode();   // 遡りON時はスワイプ遡りを有効化（canvas捕捉・パン停止）
    reload();
  });
  syncAsofControls();   // 初期状態を反映
  $('asoft').addEventListener('input', scrubHandler);   // ドラッグ中: 局所トリム＋プロファイルのみ(fetchなし/間引き)
  $('asoft').addEventListener('change', scrubHandler);  // 離した時も同じ軽量処理（/candles再取得しない）

  // ---- マウスドラッグ / ダブルクリックでの期間範囲選択 ----
  cv.style.pointerEvents = 'none';   // 既定はチャート操作優先
  // ダブルクリックの既定動作（時間軸リセット）は無効化し、選択アームに使う
  chart.applyOptions({ handleScale: { axisDoubleClickReset: false } });

  // 範囲選択 or 遡り(スワイプ) 時に canvas がドラッグを捕捉（チャートのパン/ズームは停止）
  function updateCaptureMode() {
    const asof = $('asof').checked;
    const capture = selectMode || asof;
    cv.style.pointerEvents = capture ? 'auto' : 'none';
    cv.style.cursor = selectMode ? 'crosshair' : (asof ? 'ew-resize' : 'default');
    chart.applyOptions({ handleScroll: !capture, handleScale: capture ? false : { axisDoubleClickReset: false } });
  }

  function setSelectMode(on) {
    selectMode = on;
    $('rangesel').textContent = '範囲選択: ' + (on ? 'ON' : 'OFF');
    $('rangesel').classList.toggle('on', on);
    updateCaptureMode();
  }

  $('rangesel').addEventListener('click', () => setSelectMode(!selectMode));
  $('resetrange').addEventListener('click', () => { selRange = null; setSelectMode(false); reload(); });
  // ダブルクリック（macOSのダブルタップ含む）で選択をアーム→ドラッグで範囲→離すと自動復帰
  chartEl.addEventListener('dblclick', () => { if (!selectMode) setSelectMode(true); });

  cv.addEventListener('mousedown', (e) => {
    const x = e.clientX - cv.getBoundingClientRect().left;
    if (selectMode) { drag = { x0: x, x1: x }; }
    else if ($('asof').checked) { scrubDrag = { startX: x, startIdx: asofIdx() }; }  // スワイプ遡り開始
  });
  cv.addEventListener('mousemove', (e) => {
    const x = e.clientX - cv.getBoundingClientRect().left;
    if (selectMode && drag) { drag.x1 = x; return; }
    if ($('asof').checked && scrubDrag && lastCandles.length) {   // スワイプで T を移動
      const ts = chart.timeScale();
      const c0 = ts.logicalToCoordinate(0), c1 = ts.logicalToCoordinate(1);
      const px = (c0 != null && c1 != null && Math.abs(c1 - c0) > 0.5) ? Math.abs(c1 - c0) : 8;
      const dIdx = Math.round((x - scrubDrag.startX) / px);   // 左ドラッグ=過去へ（スライダと同方向）
      const ni = Math.max(0, Math.min(lastCandles.length - 1, scrubDrag.startIdx + dIdx));
      if (String(ni) !== $('asoft').value) { $('asoft').value = ni; scrubHandler(); }
    }
  });
  window.addEventListener('mouseup', () => {
    if (scrubDrag) scrubDrag = null;
    if (!selectMode || !drag) return;
    const d = drag; drag = null;
    if (Math.abs(d.x0 - d.x1) < 5 || !lastCandles.length) return;  // クリック誤操作は無視
    const ts = chart.timeScale();
    const la = ts.coordinateToLogical(d.x0), lb = ts.coordinateToLogical(d.x1);
    if (la == null || lb == null) return;
    const L = lastCandles.length;
    let ia = Math.max(0, Math.min(L - 1, Math.round(Math.min(la, lb))));
    let ib = Math.max(0, Math.min(L - 1, Math.round(Math.max(la, lb))));
    if (ib - ia < 1) return;
    selRange = { from: lastCandles[ia].time, to: lastCandles[ib].time };
    setSelectMode(false);   // 選択完了→自動で通常操作(パン/ズーム)に復帰
    reload();
  });

  new ResizeObserver(() => { resizeCanvas(); redrawHeat(); }).observe(chartEl);
  chart.timeScale().subscribeVisibleTimeRangeChange(redrawHeat);
  chart.timeScale().subscribeVisibleLogicalRangeChange(redrawHeat);

  resizeCanvas();
  reload();
  requestAnimationFrame(loop);
})();
