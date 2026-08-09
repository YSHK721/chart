// op_log.js — 操作ログ（不具合の再現手順を確定させるための記録・葉モジュール）。
//
// なぜ要るか（ISSUE-298）: 実 UI でだけ出る不具合は「その直前に何をしたか」が分からないと原因を
//   特定できない。診断スクリプトは**壊れた後の状態**しか写せず、そこへ至る操作順序は残らない。
//   本モジュールは操作と、その時点の状態・通信・例外を発生順に記録し、`__opsDump()` で取り出せる
//   ようにする（推測ではなく記録で原因を確定させるための道具）。
//
// 記録するもの（原因特定に必要な最小集合）:
//   - click : 押した要素（id / ラベル）と、その時点の状態スナップショット
//   - mode  : ライブ⇄リプレイの切替（body クラスの変化を観測）
//   - fetch : /candles・/compute・/intraday 等の発行と完了（所要 ms・失敗）
//   - error : window.onerror / unhandledrejection / console.error（スタック付き）
//
// 無波及順守:
//   - 本モジュールは app の他モジュールを一切 import しない（葉モジュール）。
//   - 監視は受動のみ（capture フェーズの click 購読・MutationObserver・fetch と console.error の
//     透過ラップ）。preventDefault も戻り値の改変も行わない＝挙動を変えない。
//   - 再生ループ中の毎フレーム記録はしない（記録自体が負荷にならないよう、操作・通信・例外に限る）。

const DEFAULT_CAPACITY = 300;

// ---- 純ロジック（DOM 非依存・単体検証対象）--------------------------------------

// 発生順のリングバッファ。容量を超えたら古いものから捨てる。
export function createOpLog({ capacity = DEFAULT_CAPACITY } = {}) {
  const entries = [];
  return {
    record(entry) {
      entries.push(entry);
      while (entries.length > capacity) {
        entries.shift();
      }
      return entry;
    },
    entries: () => entries.slice(),
    size: () => entries.length,
    clear: () => { entries.length = 0; },
    capacity: () => capacity,
  };
}

// 押した要素を「人が読める 1 つの名前」にする。id が最優先（診断で参照する識別子と一致するため）。
//   id が無ければ祖先の id ＋ ラベル（テキスト先頭 24 文字）で位置を特定できるようにする。
export function describeTarget(el) {
  if (!el) {
    return '(unknown)';
  }
  const label = String(el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 24);
  const tf = el.dataset ? el.dataset.timeframe : undefined;
  const parts = [];
  if (el.id) {
    parts.push(`#${el.id}`);
  } else {
    const host = typeof el.closest === 'function' ? el.closest('[id]') : null;
    if (host && host.id) {
      parts.push(`#${host.id} >`);
    }
    parts.push(String(el.tagName || '?').toLowerCase());
  }
  if (tf) {
    parts.push(`[tf=${tf}]`);
  }
  if (label) {
    parts.push(`「${label}」`);
  }
  return parts.join(' ');
}

// 1 行のテキスト化（診断出力へそのまま貼れる形）。
export function formatEntry(e) {
  const at = `[${String(Math.round(e.at)).padStart(7)}ms]`;
  const kind = String(e.kind).padEnd(6);
  const head = `${at} ${kind} ${e.text || ''}`.trimEnd();
  const state = e.state ? `\n              ${e.state}` : '';
  const detail = e.detail ? `\n              ${String(e.detail).split('\n').join('\n              ')}` : '';
  return `${head}${state}${detail}`;
}

export function formatLog(entries) {
  const head = `===== 操作ログ（発生順・${entries.length} 件）=====`;
  const body = entries.length ? entries.map(formatEntry).join('\n') : '（記録なし）';
  return `${head}\n${body}\n===== 操作ログここまで =====`;
}

// 状態スナップショットを 1 行にする。読み手（controller/driver）が居なければ空。
export function formatState(s) {
  if (!s) {
    return '';
  }
  const bits = [
    `mode=${s.mode}`,
    `tf=${s.tf ?? '-'}`,
    `bar=${s.bar ?? '-'}`,
    `until=${s.untilTime ?? '-'}`,
    `limit=${s.recentBars ?? '-'}`,
    `窓=${s.window ?? '-'}`,
    `指標=${s.applied ?? '-'}`,
  ];
  return bits.join(' ');
}

// ---- 配線（ブラウザ側・受動監視のみ）---------------------------------------------

// 状態の読み取り。リプレイ層が公開している診断用グローバルだけを読む（app へ依存しない）。
function readState(win, doc) {
  const c = win.__rpController;
  const body = doc && doc.body ? doc.body.className : '';
  const mode = /um-mode-replay/.test(body) ? 'replay' : (/um-mode-live/.test(body) ? 'live' : '?');
  if (!c) {
    return { mode };
  }
  let window_ = null;
  try {
    const candles = c._renderer && typeof c._renderer.getCandles === 'function' ? c._renderer.getCandles() : null;
    if (Array.isArray(candles) && candles.length) {
      window_ = `${candles.length}本/末尾${candles[candles.length - 1].time}`;
    }
  } catch (_e) { /* 読み取り失敗は状態欄を空にするだけ（記録は続ける） */ }
  return {
    mode,
    tf: c._timeframe,
    bar: win.__rpbar,
    untilTime: c._untilTime,
    recentBars: c._recentBars,
    window: window_,
    applied: c._state && c._state.applied ? c._state.applied.length : null,
  };
}

// 記録を仕掛ける。戻り値は { dump, log, uninstall }。二重 install は無視する。
export function installOpLog({
  win = (typeof window !== 'undefined' ? window : undefined),
  doc = (typeof document !== 'undefined' ? document : undefined),
  capacity = DEFAULT_CAPACITY,
  storageKey = 'unifiedUi.opLog.v1',
} = {}) {
  if (!win || !doc || win.__opLog) {
    return win && win.__opLog ? win.__opLog : null;
  }
  const log = createOpLog({ capacity });
  const t0 = win.performance && win.performance.now ? win.performance.now() : 0;
  const now = () => ((win.performance && win.performance.now ? win.performance.now() : 0) - t0);
  const disposers = [];

  const add = (kind, text, { detail = null, withState = true } = {}) => log.record({
    at: now(), kind, text,
    state: withState ? formatState(readState(win, doc)) : '',
    detail,
  });

  // 直前の状態を残す（リロードで消えないよう、離脱時と例外時に保存する）。
  const persist = () => {
    try {
      win.sessionStorage.setItem(storageKey, JSON.stringify(log.entries()));
    } catch (_e) { /* 容量超過等は保存を諦める（メモリ側は残る） */ }
  };

  // 1) クリック（capture・passive＝既定動作へ非干渉）。
  const onClick = (ev) => {
    try { add('click', describeTarget(ev.target)); } catch (_e) { /* 記録失敗で操作を止めない */ }
  };
  doc.addEventListener('click', onClick, { capture: true, passive: true });
  disposers.push(() => doc.removeEventListener('click', onClick, { capture: true }));

  // 2) モード切替（body クラスの変化を観測＝app へ手を入れない）。
  let lastMode = null;
  if (typeof win.MutationObserver === 'function' && doc.body) {
    const mo = new win.MutationObserver(() => {
      const mode = readState(win, doc).mode;
      if (mode !== lastMode) {
        const from = lastMode;
        lastMode = mode;
        if (from !== null) {
          add('mode', `${from} → ${mode}`);
        }
      }
    });
    mo.observe(doc.body, { attributes: true, attributeFilter: ['class'] });
    disposers.push(() => mo.disconnect());
    lastMode = readState(win, doc).mode;
  }

  // 3) 通信（発行と完了・所要 ms）。API だけを記録し、静的資産は記録しない（ログを埋めないため）。
  const API = /\/(candles|compute|compute_seq|intraday|forming_bar|market_profile|market_profile_forming|available_days|catalog|live_ticks|tickvol_profile)(\?|$)/;
  const originalFetch = win.fetch;
  if (typeof originalFetch === 'function') {
    const wrapped = function fetchWithOpLog(...args) {
      const url = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url) || '';
      const promise = originalFetch.apply(this, args);
      if (!API.test(String(url))) {
        return promise;
      }
      const started = now();
      const short = String(url).replace(/^https?:\/\/[^/]+/, '');
      return promise.then(
        (res) => {
          add('fetch', `${res && res.ok ? '✓' : `✗${res && res.status}`} ${Math.round(now() - started)}ms  ${short}`, { withState: false });
          return res;
        },
        (err) => {
          add('fetch', `✗ ${Math.round(now() - started)}ms  ${short}`, { detail: (err && err.message) || String(err), withState: false });
          throw err;
        },
      );
    };
    win.fetch = wrapped;
    disposers.push(() => { if (win.fetch === wrapped) { win.fetch = originalFetch; } });
  }

  // 4) 例外（発生時は即保存する＝リロードしても失わない）。
  const onError = (ev) => {
    add('ERROR', (ev && ev.message) || 'error', {
      detail: ev && ev.error && ev.error.stack ? ev.error.stack : null,
    });
    persist();
  };
  const onRejection = (ev) => {
    const r = ev && ev.reason;
    add('ERROR', `unhandledrejection: ${(r && r.message) || String(r)}`, {
      detail: r && r.stack ? r.stack : null,
    });
    persist();
  };
  win.addEventListener('error', onError);
  win.addEventListener('unhandledrejection', onRejection);
  disposers.push(() => { win.removeEventListener('error', onError); win.removeEventListener('unhandledrejection', onRejection); });

  // console.error は透過ラップ（app 側の握った例外＝`[replay] 計算エラー` を拾う唯一の口）。
  const originalConsoleError = win.console && win.console.error;
  if (typeof originalConsoleError === 'function') {
    const wrapped = function consoleErrorWithOpLog(...args) {
      try {
        const text = args.map((a) => ((a && a.message) ? a.message : String(a))).join(' | ');
        const stack = args.find((a) => a && a.stack);
        add('ERROR', text.slice(0, 300), { detail: stack ? stack.stack : null });
        persist();
      } catch (_e) { /* 記録失敗で本来の出力を止めない */ }
      return originalConsoleError.apply(this, args);
    };
    win.console.error = wrapped;
    disposers.push(() => { if (win.console.error === wrapped) { win.console.error = originalConsoleError; } });
  }

  // 5) 離脱時に保存（次の読込で「前のセッションのログ」も取り出せる）。
  const onHide = () => persist();
  win.addEventListener('pagehide', onHide);
  disposers.push(() => win.removeEventListener('pagehide', onHide));

  add('init', `操作ログ開始（容量 ${capacity} 件）`);

  const api = {
    log,
    dump: () => formatLog(log.entries()),
    // 直前のセッション（リロード前）のログ。
    previous: () => {
      try {
        const raw = win.sessionStorage.getItem(storageKey);
        return raw ? formatLog(JSON.parse(raw)) : '（前セッションの記録なし）';
      } catch (_e) {
        return '（前セッションの記録を読めません）';
      }
    },
    persist,
    uninstall: () => {
      for (const off of disposers) { try { off(); } catch (_e) { /* noop */ } }
      disposers.length = 0;
      delete win.__opLog;
    },
  };
  win.__opLog = api;
  win.__opsDump = () => {
    const text = api.dump();
    if (win.console && win.console.log) { win.console.log(text); }
    return text;
  };
  win.__opsPrev = () => {
    const text = api.previous();
    if (win.console && win.console.log) { win.console.log(text); }
    return text;
  };
  return api;
}
