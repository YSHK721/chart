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
    'rp-prev': fakeEl(),
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
    calls: { enter: [], enterFrom: [], grow: [], feed: [], settle: 0 },
    isEnabled() { return this._en; },
    setEnabled(v) { this._en = !!v; },
    async enterBar(t, from) { this.calls.enter.push(t); this.calls.enterFrom.push(from); },
    async growTo(t, from) { this.calls.grow.push([t, from]); },
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

test('render passes from=replayStart bar time to enterBar (Fix #1: cumulate from replay start, not today)', async () => {
  // Arrange: MP 有効の spy。preset 未選択＝replayStart=0（最古足 time=100 が累積下限）。
  const mp = spyMarketProfile(true);
  // Act
  await drive({ marketProfile: mp });
  // Assert: enterBar の from は candles[replayStart=0].time=100（再生開始点から累積・当日窓でない）。
  //   最新足 time=200 の enterBar に対応する from が 100（＝replayStart のバー時刻）で載る。
  const idx = mp.calls.enter.indexOf(200);
  assert.ok(idx >= 0, 'enterBar(now=200) が存在する');
  assert.equal(mp.calls.enterFrom[idx], 100, 'from=candles[replayStart].time（replayStart 累積下限）を driver が透過する');
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

// --- ISSUE-048（完成足フラッシュの回帰禁止）: 参照実装 prototype_260626-01 は「リビール→畳み込み」間に
//   await を挟まない不変条件で完成足のチラ見せを防ぐが、render() の MP enterBar（HTTP await）が
//   リビール後に挟まり、その待ち時間ぶん完成足が露出していた（実測 0.5〜1.5s）。修正: 再生中
//   （playing かつ mode≠math）は enterBar の await より前（同一同期ブロック＝paint 前）に最新足を
//   始値の同事足へ畳む。本テストは「畳み込み update（O=H=L=C=open）が enterBar より先」を順序で固定する。 ---
test('during play, the revealed bar is collapsed to its open BEFORE the MP enterBar await (no completed-bar flash — ISSUE-048)', async () => {
  const candles3 = [
    { time: 100, open: 1, high: 2, low: 0.5, close: 1.5 },
    { time: 200, open: 1.5, high: 2.5, low: 1, close: 2 },
    { time: 300, open: 2, high: 3, low: 1.5, close: 2.5 },
  ];
  const events = [];
  const mainSeries = {
    attachPrimitive() {},
    update(bar) { events.push({ kind: 'update', ...bar }); },
  };
  const mp = {
    _en: true,
    isEnabled() { return this._en; },
    setEnabled(v) { this._en = !!v; },
    async enterBar(t) { events.push({ kind: 'enter', t }); },
    async growTo() {},
    feedTick() {},
    settleTick() {},
  };
  const fetchImpl = async (url) => ({
    ok: true,
    async json() {
      if (String(url).startsWith('/candles')) return { ok: true, candles: candles3 };
      if (String(url).startsWith('/intraday')) return { ok: true, m1: [], ticks: [11, 12], tick_secs: [210, 220] };
      return { ok: true };
    },
  });
  globalThis.window = globalThis.window || {};
  const doc = fakeDoc();
  await setupReplay({
    chart: fakeChart(),
    mainSeries,
    controller: fakeController(),
    // ISSUE-170: 足内更新は ReplayView.updateForming → renderer.updateLastCandle へ一本化されており
    //   （ライブ同一経路化・replay_view.js の docstring 参照）、mainSeries.update 直呼びは経由しない。
    //   fake がこのメソッドを持たないと updateForming の try/catch が例外を握り潰し、畳み込みが
    //   観測できず本テストだけが常時 fail していた。**観測点を実経路へ合わせる**（ISSUE-048 の
    //   不変条件＝「畳み込みが enterBar の await より先」は変更していない）。
    renderer: {
      setCandles() {},
      updateLastCandle(bar) { events.push({ kind: 'update', ...bar }); },
    },
    datasetRef: 'jp225_tick',
    recentBars: 1500,
    document: doc,
    fetchImpl,
    marketProfile: mp,
  });
  // 起動 drive は最新足（bar=2）。「1足戻る」×2 で bar=0 へ戻してから再生する。
  await doc._els['rp-prev'].onclick();
  await doc._els['rp-prev'].onclick();
  events.length = 0;
  // Act: ▶再生（playLoop: drive(bar+1)=リビール → animateForming）。完走を待つ。
  doc._els['rp-play'].onclick();
  const playBtn = doc._els['rp-play'];
  for (let i = 0; i < 200 && playBtn.textContent !== '▷'; i += 1) {
    await new Promise((r) => { setTimeout(r, 25); });
  }
  // Assert: バー time=200 について「始値へ畳む update（O=H=L=C=open=1.5）」が enterBar(200) より先。
  const isCollapse200 = (e) => e.kind === 'update' && e.time === 200
    && e.open === 1.5 && e.high === 1.5 && e.low === 1.5 && e.close === 1.5;
  const collapseIdx = events.findIndex(isCollapse200);
  const enterIdx = events.findIndex((e) => e.kind === 'enter' && e.t === 200);
  assert.ok(enterIdx >= 0, '再生で enterBar(200) が呼ばれる（前提）');
  assert.ok(collapseIdx >= 0, '再生リビール時に始値畳み込み update が発火する');
  assert.ok(collapseIdx < enterIdx,
    `畳み込みは enterBar の await より先（collapse@${collapseIdx} < enter@${enterIdx}＝完成足を露出しない）`);
});

// --- ISSUE-048 ガードの反対側固定（レビュー🟡）: 非再生（playing=false）の手動ナビでは畳み込まない
//   ＝完成足表示を保つ。`playing &&` ガードを誤って外す退行を検出する。 ---
test('manual navigation (playing=false) does NOT collapse the revealed bar (completed candle preserved — ISSUE-048 guard)', async () => {
  const candles3 = [
    { time: 100, open: 1, high: 2, low: 0.5, close: 1.5 },
    { time: 200, open: 1.5, high: 2.5, low: 1, close: 2 },
    { time: 300, open: 2, high: 3, low: 1.5, close: 2.5 },
  ];
  const events = [];
  const mainSeries = {
    attachPrimitive() {},
    update(bar) { events.push({ kind: 'update', ...bar }); },
  };
  const mp = {
    _en: true,
    isEnabled() { return this._en; },
    setEnabled(v) { this._en = !!v; },
    async enterBar(t) { events.push({ kind: 'enter', t }); },
    async growTo() {},
    feedTick() {},
    settleTick() {},
  };
  const fetchImpl = async (url) => ({
    ok: true,
    async json() {
      if (String(url).startsWith('/candles')) return { ok: true, candles: candles3 };
      if (String(url).startsWith('/intraday')) return { ok: true, m1: [], ticks: [11, 12], tick_secs: [210, 220] };
      return { ok: true };
    },
  });
  globalThis.window = globalThis.window || {};
  const doc = fakeDoc();
  await setupReplay({
    chart: fakeChart(),
    mainSeries,
    controller: fakeController(),
    // ISSUE-170: 足内更新は ReplayView.updateForming → renderer.updateLastCandle へ一本化されており
    //   （ライブ同一経路化・replay_view.js の docstring 参照）、mainSeries.update 直呼びは経由しない。
    //   fake がこのメソッドを持たないと updateForming の try/catch が例外を握り潰し、畳み込みが
    //   観測できず本テストだけが常時 fail していた。**観測点を実経路へ合わせる**（ISSUE-048 の
    //   不変条件＝「畳み込みが enterBar の await より先」は変更していない）。
    renderer: {
      setCandles() {},
      updateLastCandle(bar) { events.push({ kind: 'update', ...bar }); },
    },
    datasetRef: 'jp225_tick',
    recentBars: 1500,
    document: doc,
    fetchImpl,
    marketProfile: mp,
  });
  // 起動 drive（playing=false・最新足 bar=2）に続き、「1足戻る」で bar=1 へ手動ナビ（playing=false のまま）。
  events.length = 0;
  await doc._els['rp-prev'].onclick();
  // Assert: enterBar は呼ばれるが、始値畳み込み update（O=H=L=C=open）は一切発火しない（完成足のまま）。
  assert.ok(events.some((e) => e.kind === 'enter' && e.t === 200), '手動ナビでも enterBar は呼ばれる（前提）');
  const collapsed = events.filter((e) => e.kind === 'update'
    && e.open === e.high && e.high === e.low && e.low === e.close);
  assert.equal(collapsed.length, 0, '非再生（playing=false）の手動ナビは畳み込まない（完成足表示を保つ）');
});
