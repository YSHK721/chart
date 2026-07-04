// replay.js setupReplay の MP tick-live 配線検証（統合・fake harness で closure を実駆動）。
//
// 設計入力: Phase2 arch 確定「駆動結線（replay.js）」。render()→enterBar(now=T)（バー単位ジャンプで
//   base を因果取得・await ready）、MP OFF/未配線は既存 replay へ一切干渉しない。
//   setupReplay は起動時 loadTimeframe→drive→render を同期的に流すため、render seam（enterBar）は
//   タイマ非依存で決定論的に観測できる。feedTick/settleTick/secs の逐次成長は actor（slim actor test）と
//   stream（stream secs test）で Red→Green 済み＝本統合は render seam と非干渉を固定する。
//
// ★#rp-mp トグル撤去後も、render→enterBar / animateForming→feedTick / settleTick の駆動フックは維持する。
//   有効化は indicator メニュー（controller.applyIndicator('market_profile')→actor.setEnabled(true)）へ
//   一本化された。本 wiring は「actor が有効（isEnabled()=true）なら render seam が enterBar を呼ぶ／
//   無効・未配線なら一切干渉しない」を固定する（menu→setEnabled は controller test、feedTick 成長は
//   actor test でそれぞれ緑）。spy の _en は menu 有効化後の状態を代表する。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { setupReplay } from '../js/replay.js';

// --- fake DOM 要素（auto-vivify）: onclick/addEventListener/classList/value/options を最小提供 ---
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

function fakeDoc() {
  const els = {
    'rp-speed': fakeEl({ value: '1' }),
    'rp-mode': fakeEl({ value: 'real_ticks' }),
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
    _timeframe: '1D', _recentBars: 0,
    setUntilTime() {}, isRecomputing() { return false; },
    async recomputeAllApplied({ preRender } = {}) { if (preRender) preRender(); },
    async recomputeFormingLatest() {},
  };
}

const CANDLES = [
  { time: 100, open: 1, high: 2, low: 0.5, close: 1.5 },
  { time: 200, open: 1.5, high: 2.5, low: 1, close: 2 },
];

function fakeFetch() {
  return async (url) => ({
    ok: true,
    async json() {
      if (String(url).startsWith('/candles')) return { ok: true, candles: CANDLES };
      if (String(url).startsWith('/intraday')) return { ok: true, m1: [], ticks: [11, 12], tick_secs: [210, 220] };
      return { ok: true };
    },
  });
}

function spyMarketProfile(enabled) {
  return {
    _en: !!enabled,
    calls: { enter: [], feed: [], settle: 0 },
    isEnabled() { return this._en; },
    setEnabled(v) { this._en = !!v; },
    async enterBar(t) { this.calls.enter.push(t); },
    feedTick(s, m) { this.calls.feed.push([s, m]); },
    settleTick() { this.calls.settle += 1; },
  };
}

async function drive({ marketProfile }) {
  globalThis.window = globalThis.window || {};
  const doc = fakeDoc();
  await setupReplay({
    chart: fakeChart(),
    mainSeries: { attachPrimitive() {}, update() {} },
    controller: fakeController(),
    renderer: { setCandles() {} },
    datasetRef: 'jp225_tick',
    recentBars: 1500,
    document: doc,
    fetchImpl: fakeFetch(),
    marketProfile,
  });
  return { doc };
}

test('render calls marketProfile.enterBar(now=T) for the current bar when MP is enabled', async () => {
  // Arrange: MP 有効の spy。
  const mp = spyMarketProfile(true);
  // Act: setupReplay 起動（loadTimeframe→drive(最新足)→render）。
  await drive({ marketProfile: mp });
  // Assert: 現在バー（最新足 time=200）で enterBar が呼ばれる（バー単位ジャンプの base 取り直し・因果 T）。
  assert.ok(mp.calls.enter.includes(200), 'render seam で enterBar(now=T) が呼ばれる');
});

test('render does NOT call enterBar when MP is disabled (OFF non-interference)', async () => {
  // Arrange: MP 無効の spy。
  const mp = spyMarketProfile(false);
  // Act
  await drive({ marketProfile: mp });
  // Assert: enterBar は一切呼ばれない（既存 replay へ非干渉）。
  assert.equal(mp.calls.enter.length, 0);
});

test('setupReplay runs unchanged when marketProfile is not wired (null) — legacy path intact', async () => {
  // Arrange / Act: marketProfile 未配線（既存 replay 構成）でも例外なく起動する。
  await drive({ marketProfile: null });
  // Assert: ここまで到達＝既存 replay 経路が非干渉で完走する。
  assert.ok(true);
});
