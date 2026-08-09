// op_log.test.js — 操作ログ（ISSUE-298）の純ロジック検証。
//
// 固定する機構: リングバッファの容量・発生順、押した要素の識別、出力の形。
// 配線（クリック購読・fetch ラップ・MutationObserver）は DOM/ネットの実体が要るため、
//   ここでは「install が挙動を変えない」ことだけを最小の偽 window で確かめる。

import { describe, it, expect } from 'vitest';
import {
  createOpLog, describeTarget, formatEntry, formatLog, formatState, installOpLog,
} from '../js/op_log.js';

describe('createOpLog', () => {
  it('発生順に積み、容量を超えたら古いものから捨てる', () => {
    const log = createOpLog({ capacity: 3 });
    for (const n of [1, 2, 3, 4]) log.record({ at: n, kind: 'click', text: `t${n}` });

    expect(log.entries().map((e) => e.text)).toEqual(['t2', 't3', 't4']);
    expect(log.size()).toBe(3);
  });

  it('clear で空になる', () => {
    const log = createOpLog({ capacity: 5 });
    log.record({ at: 0, kind: 'init', text: 'x' });
    log.clear();
    expect(log.size()).toBe(0);
  });
});

describe('describeTarget', () => {
  it('id を最優先で使う（診断が参照する識別子と一致させる）', () => {
    expect(describeTarget({ id: 'enter-replay', tagName: 'BUTTON', textContent: 'リプレイ' }))
      .toBe('#enter-replay 「リプレイ」');
  });

  it('id が無ければ祖先の id とラベルで位置を示す', () => {
    const el = {
      id: '', tagName: 'BUTTON', textContent: ' 1日 ',
      closest: () => ({ id: 'rp-range-menu' }),
    };
    expect(describeTarget(el)).toBe('#rp-range-menu > button 「1日」');
  });

  it('時間足ボタンは data-timeframe を残す', () => {
    const el = { id: '', tagName: 'BUTTON', textContent: '1分', dataset: { timeframe: '1m' }, closest: () => null };
    expect(describeTarget(el)).toBe('button [tf=1m] 「1分」');
  });

  it('要素が無くても落ちない', () => {
    expect(describeTarget(null)).toBe('(unknown)');
  });
});

describe('formatState / formatEntry / formatLog', () => {
  it('状態は 1 行にまとまる', () => {
    const line = formatState({ mode: 'replay', tf: '1m', bar: 1499, untilTime: 100, recentBars: 1500, window: '1500本/末尾100', applied: 6 });
    expect(line).toBe('mode=replay tf=1m bar=1499 until=100 limit=1500 窓=1500本/末尾100 指標=6');
  });

  it('状態が無ければ空文字（記録は続く）', () => {
    expect(formatState(null)).toBe('');
  });

  it('1 件は「時刻 種別 内容」＋状態＋詳細の形になる', () => {
    const text = formatEntry({ at: 1234.6, kind: 'click', text: '#enter-replay', state: 'mode=live', detail: null });
    expect(text.startsWith('[   1235]')).toBe(false);
    expect(text).toContain('ms] click  #enter-replay');
    expect(text).toContain('mode=live');
  });

  it('見出しと件数を付けて出力する', () => {
    const out = formatLog([{ at: 0, kind: 'init', text: 'start', state: '', detail: null }]);
    expect(out).toContain('===== 操作ログ（発生順・1 件）=====');
    expect(out).toContain('===== 操作ログここまで =====');
  });

  it('記録が無ければ「記録なし」と出す（無言にしない）', () => {
    expect(formatLog([])).toContain('（記録なし）');
  });
});

describe('installOpLog', () => {
  // 最小の偽 window/document（DOM 実体は無し）。
  const fakeEnv = () => {
    const listeners = [];
    const store = new Map();
    const doc = {
      body: { className: 'um-mode-live' },
      addEventListener: (t, f, o) => listeners.push([t, f, o]),
      removeEventListener: () => {},
    };
    const win = {
      performance: { now: () => 0 },
      addEventListener: (t, f) => listeners.push([t, f]),
      removeEventListener: () => {},
      sessionStorage: { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) },
      console: { error: () => {}, log: () => {} },
      fetch: async () => ({ ok: true }),
    };
    return { win, doc, listeners };
  };

  it('install すると取り出し口が生え、init が 1 件記録される', () => {
    const { win, doc } = fakeEnv();
    const api = installOpLog({ win, doc, capacity: 10 });

    expect(typeof win.__opsDump).toBe('function');
    expect(api.log.entries().map((e) => e.kind)).toEqual(['init']);
    expect(api.dump()).toContain('操作ログ開始');
  });

  it('二重 install しても記録は 1 つのまま（購読の重複を作らない）', () => {
    const { win, doc } = fakeEnv();
    const first = installOpLog({ win, doc });
    const second = installOpLog({ win, doc });

    expect(second).toBe(first);
    expect(first.log.entries().filter((e) => e.kind === 'init').length).toBe(1);
  });

  it('fetch は透過（戻り値も例外もそのまま）', async () => {
    const { win, doc } = fakeEnv();
    const marker = { ok: true, url: '/candles' };
    win.fetch = async () => marker;
    installOpLog({ win, doc });

    await expect(win.fetch('/candles?x=1')).resolves.toBe(marker);
  });

  it('fetch の失敗はそのまま伝播する（握りつぶさない）', async () => {
    const { win, doc } = fakeEnv();
    const boom = new Error('network down');
    win.fetch = async () => { throw boom; };
    installOpLog({ win, doc });

    await expect(win.fetch('/compute')).rejects.toBe(boom);
  });

  it('console.error は元の実装へ必ず通す', () => {
    const { win, doc } = fakeEnv();
    const seen = [];
    win.console.error = (...a) => seen.push(a.join(' '));
    installOpLog({ win, doc });

    win.console.error('boom');

    expect(seen).toEqual(['boom']);
  });

  it('uninstall で fetch と console.error が元へ戻る', () => {
    const { win, doc } = fakeEnv();
    const originalFetch = win.fetch;
    const originalError = win.console.error;
    const api = installOpLog({ win, doc });

    api.uninstall();

    expect(win.fetch).toBe(originalFetch);
    expect(win.console.error).toBe(originalError);
    expect(win.__opLog).toBeUndefined();
  });
});
