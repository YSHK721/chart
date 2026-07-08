// replay.js setEta の real_ticks×tickvol 配線検証（統合・fake harness で closure を実駆動）。
//
// ISSUE-044: real_ticks は cap 廃止（間引かない・絶対仕様）なのに ETA モデルが旧 cap（ANIM_FINE=800 点）
//   前提のままで、月足×実ティックの完了予想（例「53秒（残り11足)」）が実測と桁違いに乖離した。
//   /candles の各足 tickvol（実 tick 数）を用いた etaRealTicksMs で ETA を算出することを固定する。
//   tickvol 欠損時は従来モデルへフォールバック（回帰なし）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { setupReplay } from '../js/replay.js';

// --- fake DOM（replay_mp_wiring.test.js と同型・最小） ---
function fakeEl(extra = {}) {
  return {
    _l: {}, value: '', min: 0, max: 0, textContent: '', title: '', hidden: false, disabled: false,
    style: {}, dataset: {}, options: [], innerHTML: '',
    classList: { toggle() {}, add() {}, remove() {} },
    appendChild() {}, removeChild() {},
    addEventListener(ev, fn) { (this._l[ev] ||= []).push(fn); },
    set onclick(fn) { this._onclick = fn; }, get onclick() { return this._onclick; },
    set oninput(fn) { this._oninput = fn; }, get oninput() { return this._oninput; },
    ...extra,
  };
}

function fakeDoc(mode) {
  const els = {
    'rp-speed': fakeEl({ value: '1' }),
    'rp-mode': fakeEl({ value: mode }),
    'rp-slider': fakeEl(),
  };
  return {
    getElementById: (id) => (els[id] || (els[id] = fakeEl())),
    querySelectorAll: () => [],
    createElement: () => fakeEl(),
    addEventListener() {},
    _els: els,
  };
}

function fakeChart() {
  const ts = { fitContent() {}, setVisibleLogicalRange() {}, getVisibleLogicalRange() { return null; } };
  return { timeScale: () => ts, panes: () => [], chartElement: () => null };
}

function fakeController() {
  return {
    _timeframe: '1M', _recentBars: 0,
    setUntilTime() {}, isRecomputing() { return false; },
    async recomputeAllApplied({ preRender } = {}) { if (preRender) preRender(); },
    async recomputeFormingLatest() {},
  };
}

function fakeFetch(candles) {
  return async (url) => ({
    ok: true,
    async json() {
      if (String(url).startsWith('/candles')) return { ok: true, candles };
      if (String(url).startsWith('/intraday')) return { ok: true, m1: [], ticks: [], tick_secs: [] };
      return { ok: true };
    },
  });
}

async function boot({ mode, candles }) {
  globalThis.window = globalThis.window || {};
  const doc = fakeDoc(mode);
  await setupReplay({
    chart: fakeChart(),
    mainSeries: { attachPrimitive() {}, update() {} },
    controller: fakeController(),
    renderer: { setCandles() {} },
    datasetRef: 'jp225_tick',
    recentBars: 1500,
    document: doc,
    fetchImpl: fakeFetch(candles),
    marketProfile: null,
  });
  return { doc };
}

// 月足相当: 残り 1 足に実 tick 30000 個 → s=1 で 30000×6ms + (compute+50ms) ≒ 180秒＝「3分00秒」。
//   旧モデル（ANIM_FINE=800 前提）は ≒5秒 と表示していた（Red 実証点）。compute は実測（数 ms）で
//   秒丸め後は 3分00秒 に安定する。
const CANDLES_TV = [
  { time: 100, open: 1, high: 2, low: 0.5, close: 1.5, tickvol: 50000 },
  { time: 200, open: 1.5, high: 2.5, low: 1, close: 2, tickvol: 30000 },
];

test('setEta (real_ticks + tickvol): ETA は残り足の実 tick 総数から算出される（旧 800 点 cap モデルでない）', async () => {
  const { doc } = await boot({ mode: 'real_ticks', candles: CANDLES_TV });
  // Act: スライダで bar=0 へ（drive→render→setEta。残り 1 足＝tickvol 30000）。
  await doc._els['rp-slider']._oninput({ target: { value: '0' } });
  // Assert: 30000×6ms≒180秒 → 「3分00秒（残り1足）」。旧モデルは「5秒」（800×6ms+固定費）だった。
  const text = doc._els['rp-eta'].textContent;
  assert.match(text, /完了予想 3分00秒（残り1足）/, `実 tick 数由来の ETA を表示する（actual: ${text}）`);
});

test('setEta (real_ticks, tickvol 欠損): 従来モデルへフォールバック（回帰なし）', async () => {
  const noTv = CANDLES_TV.map(({ tickvol, ...c }) => c);
  const { doc } = await boot({ mode: 'real_ticks', candles: noTv });
  await doc._els['rp-slider']._oninput({ target: { value: '0' } });
  // 従来モデル: compute(実測数ms) + (800×6+50)/1 ≒ 4.85 秒 → 「5秒（残り1足）」。
  assert.match(doc._els['rp-eta'].textContent, /完了予想 5秒（残り1足）/);
});

test('setEta (非 real_ticks): tickvol が有っても従来モデルのまま（他モード回帰なし）', async () => {
  const { doc } = await boot({ mode: 'ohlc_1min', candles: CANDLES_TV });
  await doc._els['rp-slider']._oninput({ target: { value: '0' } });
  // ohlc_1min モデル: compute + (200×6+50)/1 ≒ 1.3 秒 → 「1秒（残り1足）」。
  assert.match(doc._els['rp-eta'].textContent, /完了予想 1秒（残り1足）/);
});
