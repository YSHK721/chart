// replay_diag.js — リプレイの現在設定・描画状態を 1 回で吸い出す診断スクリプト。
//
// 用途: 不具合報告時に「時間足 / 再生モード / 適用中の指標 / どの系列が異常値か」を
//   手作業の確認なしに確定させる。実 UI の実状態のみを読み、何も変更しない（副作用なし）。
//
// 使い方:
//   1. 不具合が出ている状態のチャートのタブで DevTools を開く（F12）
//   2. 「Console」タブへ本ファイルの中身を全部貼り付けて Enter
//   3. 出力されたテキストが自動でクリップボードへ入る（失敗時は console から手動コピー）
//
// 読む対象（すべて既存の公開グローバル・内部状態。本スクリプトのために何も追加しない）:
//   window.__rpController / __rpbar / __rpForm / __rpPlans / __rpReplayStart / __rpAnimating
//   #tf-menu-label / [data-timeframe].is-active / #rp-mode / #rp-speed / #rp-range / #rp-eta
//   controller._state.applied / controller._renderer._instances（styleMeta・seriesData）
//   localStorage の indicatorUi.* キー

(() => {
  const L = [];
  const P = (s = '') => L.push(String(s));
  const j = (v) => { try { return JSON.stringify(v); } catch (_e) { return '<circular>'; } };
  const txt = (id) => { const e = document.getElementById(id); return e ? (e.textContent || '').trim() : '(要素なし)'; };
  const num = (v) => (typeof v === 'number' && isFinite(v) ? v : null);

  P('===== リプレイ診断 ' + new Date().toISOString() + ' =====');
  P('URL: ' + location.href);

  // ---- 1. 時間足 -----------------------------------------------------------
  P('');
  P('--- 1. 時間足 ---');
  let activeTf = null;
  document.querySelectorAll('[data-timeframe]').forEach((b) => {
    if (b.classList && b.classList.contains('is-active')) activeTf = b.dataset.timeframe;
  });
  P('  現在の時間足キー : ' + (activeTf ?? '(is-active なし)'));
  P('  トリガー表示     : ' + txt('tf-menu-label'));

  // ---- 2. リプレイの操作状態 ------------------------------------------------
  P('');
  P('--- 2. リプレイ操作状態 ---');
  const modeEl = document.getElementById('rp-mode');
  const modeVal = modeEl ? modeEl.value : null;
  const modeTxt = modeEl && modeEl.selectedOptions && modeEl.selectedOptions[0]
    ? modeEl.selectedOptions[0].textContent : '(不明)';
  const speedEl = document.getElementById('rp-speed');
  P('  再生モード       : ' + modeVal + '（' + modeTxt + '）');
  P('  再生速度         : ' + (speedEl ? (speedEl.dataset.speed + ' / 表示 ' + (speedEl.textContent || '').trim()) : '(要素なし)'));
  P('  表示期間         : ' + txt('rp-range'));
  P('  完了予想         : ' + txt('rp-eta'));
  // リプレイ中の判定: リビール時計 _untilTime が入っているかで見る（ライブは未設定）。
  const ctlForMode = window.__rpController;
  const inReplay = !!(ctlForMode && ctlForMode._untilTime != null);
  P('  モード           : ' + (inReplay ? 'リプレイ中' : 'ライブ（リプレイ未起動）')
    + (inReplay ? '' : '  ← 不具合が出ている状態で実行し直すこと'));
  P('  再生位置 bar     : ' + (window.__rpbar ?? '(未設定)'));
  P('  再生開始 index   : ' + (window.__rpReplayStart ?? '(未設定)'));
  P('  アニメ実行中     : ' + (window.__rpAnimating ?? '(未設定)'));
  P('  リプレイ準備完了 : ' + (window.__rpReady ?? '(未設定)'));

  // ---- 3. 足内更新の粒度（ISSUE-232 の一括計算） -----------------------------
  P('');
  P('--- 3. 足内更新の粒度 ---');
  const f = window.__rpForm;
  if (!f) {
    P('  __rpForm 未設定（まだ 1 本も足内アニメが走っていない）');
  } else {
    const n = num(f.n);
    const planned = num(f.planned);
    P('  直近バーのモード : ' + f.mode);
    P('  ローソク更新回数 : ' + (n ?? '(不明)') + ' 回（足内のティック点数）');
    P('  指標更新回数     : ' + (planned == null ? '(計画なし＝従来経路)' : planned + ' 回（一括計算のサンプル点数）'));
    if (n && planned) {
      P('  → 指標はローソク ' + (n / planned).toFixed(1) + ' 回につき 1 回しか動かない');
    }
  }
  try {
    P('  計画キャッシュ   : ' + (typeof window.__rpPlans === 'function' ? j(window.__rpPlans()) : '(未設定)'));
  } catch (e) { P('  計画キャッシュ   : 取得失敗 ' + e); }

  // ---- 4. 適用中の指標 ------------------------------------------------------
  P('');
  P('--- 4. 適用中の指標 ---');
  const c = window.__rpController;
  if (!c) {
    P('  __rpController 未設定（リプレイが起動していない）。リプレイ中のタブで実行すること。');
  } else {
    const applied = (c._state && c._state.applied) || [];
    P('  件数: ' + applied.length);
    applied.forEach((inst, i) => {
      P('  [' + i + '] ' + inst.indicatorId
        + '  instanceId=' + inst.instanceId
        + '  variant=' + (inst.variant ?? 'default')
        + '  visible=' + inst.visible);
      let params = inst.params;
      try { if (typeof c._paramsObject === 'function') params = c._paramsObject(inst.params); } catch (_e) { /* 生値のまま */ }
      P('       params=' + j(params));
    });

    // ---- 5. 描画中の系列と異常値の検出 ------------------------------------
    P('');
    P('--- 5. 描画中の系列（末尾 3 点と最大跳躍）---');
    P('    ※「最大跳躍」= 末尾 20 点における隣接差の絶対値の最大。垂直に落ちている線の特定用。');
    const r = c._renderer;
    const inst = r && r._instances;
    if (!inst || typeof inst.get !== 'function') {
      P('  renderer._instances を読めない（実装変更の可能性）');
    } else {
      applied.forEach((a) => {
        const slot = inst.get(a.instanceId);
        if (!slot) { P('  ' + a.indicatorId + ' [' + a.instanceId + ']: スロットなし（未描画）'); return; }
        P('  ' + a.indicatorId + ' [' + a.instanceId + ']');
        const meta = slot.styleMeta;
        if (!meta || typeof meta.forEach !== 'function') { P('       styleMeta なし'); return; }
        meta.forEach((m, key) => {
          // 実描画データの正は lightweight-charts の系列オブジェクト（slot.lines）。
          //   slot.seriesData は histogram など一部系列にしか積まれないため、線系列は
          //   series.data() から読む（未対応版は seriesData へフォールバック）。
          let data = null;
          try {
            const s = slot.lines && slot.lines.get ? slot.lines.get(key) : null;
            if (s && typeof s.data === 'function') data = s.data();
          } catch (_e) { data = null; }
          if (!Array.isArray(data) || data.length === 0) {
            data = slot.seriesData && slot.seriesData.get ? slot.seriesData.get(key) : null;
          }
          const arr = Array.isArray(data) ? data : [];
          const tail = arr.slice(-3).map((p) => {
            const v = (p && (p.value !== undefined ? p.value : p.close));
            return (p && p.time) + ':' + (typeof v === 'number' ? v.toFixed(4) : v);
          });
          let jump = null, jumpAt = null;
          const win = arr.slice(-20);
          for (let i = 1; i < win.length; i++) {
            const a0 = win[i - 1], b0 = win[i];
            const v0 = a0 && (a0.value !== undefined ? a0.value : a0.close);
            const v1 = b0 && (b0.value !== undefined ? b0.value : b0.close);
            if (typeof v0 === 'number' && typeof v1 === 'number') {
              const d = Math.abs(v1 - v0);
              if (jump == null || d > jump) { jump = d; jumpAt = b0.time; }
            }
          }
          P('       ' + String(m.name).padEnd(28)
            + ' kind=' + m.kind
            + ' visible=' + m.visible
            + ' color=' + m.color
            + ' n=' + arr.length
            + ' 最大跳躍=' + (jump == null ? '-' : jump.toFixed(2)) + (jumpAt ? '@' + jumpAt : ''));
          P('           末尾3点: ' + (tail.length ? tail.join('  ') : '(データなし)'));
        });
      });
    }

    // ---- 6. 計算窓 --------------------------------------------------------
    P('');
    P('--- 6. 計算窓（compute へ送る値）---');
    P('  _recentBars(limit) : ' + (c._recentBars ?? '(未設定)'));
    P('  _untilTime         : ' + (c._untilTime ?? '(未設定)'));
    try {
      P('  実効時間足         : ' + (c._tf && typeof c._tf.current === 'function' ? c._tf.current() : '(不明)'));
    } catch (_e) { P('  実効時間足         : 取得失敗'); }
  }

  // ---- 7. 永続化状態 --------------------------------------------------------
  P('');
  P('--- 7. localStorage（indicatorUi.*）---');
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (!k || k.indexOf('indicatorUi.') !== 0) continue;
      const v = localStorage.getItem(k) || '';
      let extra = '';
      try {
        const o = JSON.parse(v);
        if (o && Array.isArray(o.instances)) extra = ' instances=' + o.instances.length;
        else if (o && typeof o === 'object') extra = ' keys=' + j(Object.keys(o));
      } catch (_e) { /* 非 JSON */ }
      P('  ' + k + '  (' + v.length + ' 文字)' + extra);
    }
  } catch (e) { P('  読み取り失敗: ' + e); }

  P('');
  P('===== ここまで =====');

  const report = L.join('\n');
  console.log(report);
  window.__rpDiag = report;
  // クリップボードへの自動コピーは、DevTools へフォーカスが移っていると Clipboard API が
  //   拒否される（document がフォーカスされていない）。失敗時は DevTools のコンソール API
  //   `copy()` を案内する（こちらはページのフォーカスを要求しないため確実に通る）。
  const MANUAL = '※ コンソールで  copy(window.__rpDiag)  を実行するとコピーできます。';
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(report)
        .then(() => console.log('※ 上記をクリップボードへコピーしました。'))
        .catch(() => console.log(MANUAL));
    } else {
      console.log(MANUAL);
    }
  } catch (_e) { console.log(MANUAL); }
  return '診断完了（結果は上に出力・window.__rpDiag にも保持）';
})();
