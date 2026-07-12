// src=zp（超過占有 z(p)）のフロント拡張検証。
//
//   1) catalog: src ENUM に 'zp' が含まれラベルを持つ。
//   2) primitive: profile.src==='zp' のとき POC 線が POC* 色（黄）・非 zp は従来の赤（byte 不変）。
//      負の z（tpo<0・norm=0）を含む bins でも例外なく最小幅で描ける。
//   3) actor: src=zp かつ growing の onLiveTick は forming 増分に入らず refresh（/market_profile）へ
//      委譲する。refresh は in-flight coalesce（連続再入は末尾 1 回）。
//   構造: Arrange-Act-Assert。fake は既存テストの手本（market_profile_primitive.test.js 等）準拠。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { makeMarketProfileDef } from '../js/usecase/catalog_entry.js';
import { MarketProfileHistogramPrimitive } from '../js/adapter/front/market_profile_primitive.js';
import { MarketProfileActor } from '../js/adapter/front/market_profile_actor.js';

// --- 1) catalog ------------------------------------------------------------ //
// makeMarketProfileDef は present 側ヘルパ（param/型）を注入で受ける factory。
//   検証には構造を素通しする最小 fake を注入する（生成物の params 構造のみ観測）。
function fakeCatalogDeps() {
  return {
    IndicatorDef: class { constructor(o) { Object.assign(this, o); } },
    SeriesDef: class { constructor(o) { Object.assign(this, o); } },
    SeriesKind: { LINE: 'line' },
    ParamType: { ENUM: 'enum', INT: 'int', FLOAT: 'float', BOOL: 'bool' },
    ConstraintKind: { RANGE_OPEN: 'range_open' },
    param: (name, type, defaultValue, constraints, enumValues, ui) => (
      { name, type, defaultValue, constraints, enumValues, ui }
    ),
    OHLC: {},
  };
}

test('catalog: src ENUM は zp を含み、ラベル「超過占有z(p)」を持つ', () => {
  const def = makeMarketProfileDef(fakeCatalogDeps());
  const src = def.params.find((p) => p.name === 'src');
  assert.ok(src, 'src param が存在する');
  assert.ok(src.enumValues.includes('zp'));
  assert.equal(src.ui.enumLabels.zp, '超過占有z(p)');
  assert.equal(src.defaultValue, 'zp', '既定は zp（依頼者指示 2026-07-12 で昇格。API の src 省略時既定は candle のまま＝後方互換）');
});

// --- 2) primitive ----------------------------------------------------------- //
function fakeSeries() {
  return { priceToCoordinate(price) { return price; } };
}
function fakeTarget(width = 800) {
  const rects = [];
  const lines = [];
  let cur = null;
  const context = {
    fillStyle: null, strokeStyle: null, globalAlpha: 1, lineWidth: 1, font: '', textBaseline: '',
    _dash: [],
    fillRect(x, y, w, h) { rects.push({ x, y, w, h, fill: this.fillStyle }); },
    fillText() {},
    beginPath() { cur = { }; },
    moveTo(x, y) { cur.x1 = x; cur.y1 = y; },
    lineTo(x, y) { cur.x2 = x; cur.y2 = y; },
    setLineDash(d) { this._dash = d || []; },
    stroke() { lines.push({ ...cur, color: this.strokeStyle }); },
    save() {}, restore() {},
  };
  return {
    rects, lines,
    useBitmapCoordinateSpace(fn) {
      fn({ context, bitmapSize: { width, height: 600 }, horizontalPixelRatio: 1, verticalPixelRatio: 1 });
    },
  };
}

const ZP_PROFILE = {
  bins: [
    { price: 100, tpo: -1.2, norm: 0 },    // 負の z → norm 0（最小幅・例外なし）
    { price: 101, tpo: 4.5, norm: 1.0 },
    { price: 102, tpo: 0.9, norm: 0.2 },
  ],
  poc: 101, va_low: 100, va_high: 102, price_min: 100, price_max: 102,
  z_max: 4.5, poc_star: 101, src: 'zp',
};

function draw(prim, profile, target) {
  prim.attached({ chart: {}, series: fakeSeries(), requestUpdate: () => {} });
  prim.setProfile(profile);
  prim.setVisible(true);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
}

test('primitive: src=zp の POC 線は POC* 色（黄 #ffd54a）', () => {
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget();
  draw(prim, ZP_PROFILE, target);
  const pocLine = target.lines.find((l) => l.y1 === 101 && l.y1 === l.y2);
  assert.ok(pocLine, 'POC 水平線が描かれる');
  assert.equal(pocLine.color, '#ffd54a');
  // 負 z の bin も最小幅バーとして例外なく描かれる（3 bins → 3 rect）
  assert.equal(target.rects.length, 3);
});

test('primitive: 非 zp（src 無し）の POC 線は従来の赤（byte 不変）', () => {
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget();
  draw(prim, { ...ZP_PROFILE, src: undefined }, target);
  const pocLine = target.lines.find((l) => l.y1 === 101 && l.y1 === l.y2);
  assert.equal(pocLine.color, '#ff3b3b');
});

// --- 3) actor ---------------------------------------------------------------- //
function fakeClient() {
  const calls = [];
  return {
    calls,
    async fetchProfile(ctx) {
      calls.push({ ...ctx });
      return { bins: [], poc: 1, va_low: 1, va_high: 1 };
    },
  };
}
function fakePrimitive() {
  return { setProfile() {}, setVisible() {} };
}

function makeActor({ client } = {}) {
  const c = client ?? fakeClient();
  const forming = { calls: [], async fetchForming(a) { this.calls.push(a); return { ok: true, formingStart: 0, ticks: [] }; } };
  const actor = new MarketProfileActor({
    client: c,
    primitive: fakePrimitive(),
    getContext: () => ({ datasetRef: 'jp225_tick', timeframe: '1h' }),
    formingClient: forming,
    makeAccumulator: () => ({ init() {}, addTick() {}, snapshot() { return { bins: [] }; } }),
  });
  return { actor, client: c, forming };
}

test('actor: src=zp + growing の onLiveTick は forming 増分に入らず refresh へ委譲する', async () => {
  const { actor, client, forming } = makeActor();
  await actor.setEnabled(true);
  actor.setParams({ src: 'zp' });
  actor.applyGrowthState({ growing: true });
  const before = client.calls.length;
  await actor.onLiveTick();
  assert.equal(forming.calls.length, 0, 'forming client は呼ばれない');
  assert.equal(client.calls.length, before + 1, 'refresh（/market_profile）へ委譲');
  assert.equal(client.calls[before].src, 'zp');
});

test('actor: src=dwell + growing は従来どおり forming 増分に入る（回帰ゼロ）', async () => {
  const { actor, forming } = makeActor();
  await actor.setEnabled(true);
  actor.setParams({ src: 'dwell' });
  actor.applyGrowthState({ growing: true });
  await actor.onLiveTick();
  assert.ok(forming.calls.length >= 1, 'forming client が呼ばれる（増分経路維持）');
});

test('actor: refresh は in-flight coalesce（連続再入は末尾 1 回に丸める）', async () => {
  const calls = [];
  let release;
  const gate = new Promise((res) => { release = res; });
  const client = {
    calls,
    async fetchProfile(ctx) {
      calls.push({ ...ctx });
      if (calls.length === 1) { await gate; }  // 初回のみブロック
      return { bins: [], poc: 1, va_low: 1, va_high: 1 };
    },
  };
  const { actor } = makeActor({ client });
  const pEnable = actor.setEnabled(true); // 初回 refresh が in-flight（gate でブロック中）
  const p1 = actor.refresh();  // 再入 → queued
  const p2 = actor.refresh();  // 再入 → queued（上書き＝丸め）
  release();
  await Promise.all([pEnable, p1, p2]);
  // 初回 1 回 + coalesce 末尾 1 回 = 2 回（p1/p2 は 1 回に丸められる）
  assert.equal(calls.length, 2);
});

// --- 4) tf-period src 透過 --------------------------------------------------- //
import { buildTfPeriodUrl } from '../js/adapter/front/tf_period_profile_client.js';
import { TfPeriodJitterBuffer } from '../js/adapter/front/tf_period_jitter_buffer.js';

test('tf-period client: src=zp は &src=zp を付与・省略時は付与しない（URL byte 不変）', () => {
  const base = buildTfPeriodUrl({ datasetRef: 'jp225_tick', timeframe: '1h', from: 1, to: 2 });
  assert.ok(!base.includes('src='));
  const zpUrl = buildTfPeriodUrl({ datasetRef: 'jp225_tick', timeframe: '1h', from: 1, to: 2, src: 'zp' });
  assert.ok(zpUrl.endsWith('&src=zp'));
  assert.equal(zpUrl.replace('&src=zp', ''), base);
});

test('tf-period jitter buffer: src 変更でキャッシュ破棄・fetch へ src 透過・未指定は従来挙動', async () => {
  const calls = [];
  const client = {
    async fetchWindow(args) {
      calls.push({ ...args });
      return { tf: args.timeframe, unit: 1, from: args.from, to: args.to,
               columns: [{ time: args.from, levels: [] }] };
    },
  };
  const buf = new TfPeriodJitterBuffer({ client, datasetRef: 'jp225_tick', windowSec: 100, prefetch: 0 });
  buf.ensure('1h', 0, 50);                 // 従来呼び出し（src なし）
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(calls.length, 1);
  assert.ok(!('src' in calls[0]), 'src 未指定は fetch 引数に src を載せない');
  buf.ensure('1h', 0, 50);                 // 同キー → キャッシュヒット（fetch 増えない）
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(calls.length, 1);
  buf.ensure('1h', 0, 50, 'zp');           // src 変更 → 破棄＋再取得（src 透過）
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(calls.length, 2);
  assert.equal(calls[1].src, 'zp');
  buf.ensure('1h', 0, 50, 'zp');           // 同 src → ヒット
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(calls.length, 2);
  buf.ensure('1h', 0, 50);                 // zp → 従来へ戻す → 破棄＋再取得
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(calls.length, 3);
});

test('primitive: src=zp は POC*/VAH/VAL の価格ラベルを描く・非 zp は描かない（byte 不変）', () => {
  // zp: ラベル 3 件（POC*/VAH/VAL・価格 2 桁）
  const prim = new MarketProfileHistogramPrimitive();
  const texts = [];
  const target = fakeTarget();
  target.useBitmapCoordinateSpace = (fn) => fn({
    context: {
      fillStyle: null, strokeStyle: null, globalAlpha: 1, lineWidth: 1, font: '', textBaseline: '',
      _dash: [],
      fillRect() {}, fillText(s, x, y) { texts.push({ s, x, y }); },
      beginPath() {}, moveTo() {}, lineTo() {}, setLineDash() {}, stroke() {},
      save() {}, restore() {},
    },
    bitmapSize: { width: 800, height: 600 }, horizontalPixelRatio: 1, verticalPixelRatio: 1,
  });
  draw(prim, ZP_PROFILE, target);
  const labels = texts.map((t) => t.s);
  assert.ok(labels.includes('POC* 101.00'), `POC* ラベル: ${labels}`);
  assert.ok(labels.includes('VAH 102.00'));
  assert.ok(labels.includes('VAL 100.00'));
  // 非 zp: ラベル無し（既存描画 byte 不変）
  const prim2 = new MarketProfileHistogramPrimitive();
  const texts2 = [];
  const target2 = fakeTarget();
  target2.useBitmapCoordinateSpace = (fn) => fn({
    context: {
      fillStyle: null, strokeStyle: null, globalAlpha: 1, lineWidth: 1, font: '', textBaseline: '',
      _dash: [],
      fillRect() {}, fillText(s) { texts2.push(s); },
      beginPath() {}, moveTo() {}, lineTo() {}, setLineDash() {}, stroke() {},
      save() {}, restore() {},
    },
    bitmapSize: { width: 800, height: 600 }, horizontalPixelRatio: 1, verticalPixelRatio: 1,
  });
  draw(prim2, { ...ZP_PROFILE, src: undefined }, target2);
  assert.equal(texts2.length, 0);
});

// --- 5) ISSUE-065: 増分（dwell ライブ）の初期描画フラッシュ回避 --------------- //
test('actor: 増分(growing×dwell×forming) の setEnabled は forming で初期描画し全期間 refresh を呼ばない', async () => {
  const client = fakeClient();               // /market_profile（全期間 refresh）の fetch を観測
  const forming = {
    calls: [],
    async fetchForming(args) {
      this.calls.push({ ...args });
      return {
        ok: true, formingStart: 1000, ticks: [[1010, 50000]],
        baseFine: [0, 0, 0], baseKmin: 4600, activeTable: [[1]],
        priceMin: 46000, priceMax: 46030, nBins: 3, gridW: 10, now: 1030,
      };
    },
  };
  const acc = { init() {}, addTick() {}, snapshot() { return { bins: [], poc: 1 }; } };
  const actor = new MarketProfileActor({
    client,
    primitive: fakePrimitive(),
    getContext: () => ({ datasetRef: 'jp225_tick', timeframe: '1D' }),
    getCandles: () => [{ time: 1700000000 }],
    formingClient: forming,
    makeAccumulator: () => acc,
  });
  actor.setParams({ src: 'dwell' });
  actor.applyGrowthState({ growing: true }); // FOLLOW＝growing（setEnabled 前・実際の呼び出し順と同じ）
  await actor.setEnabled(true);
  // 全期間 refresh の /market_profile fetch は 1 度も呼ばれない＝全期間フラッシュ無し
  assert.equal(client.calls.length, 0, 'setEnabled は全期間 refresh を呼ばない（フラッシュ回避）');
  // 初期描画は forming（当日 base+forming）経由
  assert.ok(forming.calls.length >= 1, '初期描画は forming（当日）経由');
  assert.equal(forming.calls[0].base, 1, '_enterTicklive の base=1 取得');
});

test('actor: 非増分(static dwell) の setEnabled は従来どおり全期間 refresh を呼ぶ（回帰ゼロ）', async () => {
  const client = fakeClient();
  const forming = { calls: [], async fetchForming() { return { ok: true, formingStart: 0, ticks: [] }; } };
  const actor = new MarketProfileActor({
    client, primitive: fakePrimitive(),
    getContext: () => ({ datasetRef: 'jp225_tick', timeframe: '1D' }),
    formingClient: forming, makeAccumulator: () => ({}),
  });
  actor.setParams({ src: 'dwell' });         // growing 未設定＝static
  await actor.setEnabled(true);
  assert.equal(client.calls.length, 1, 'static は従来どおり refresh（全期間）');
  assert.equal(forming.calls.length, 0);
});

// --- 6) ISSUE-066: setParams が onParamsChanged を発火（tf-period 即時再取得の起点） ------ //
test('actor: setParams（src/mode 変更）は onParamsChanged を発火する', () => {
  let count = 0;
  const actor = new MarketProfileActor({
    client: fakeClient(),
    primitive: fakePrimitive(),
    getContext: () => ({ datasetRef: 'jp225_tick', timeframe: '1h' }),
    onParamsChanged: () => { count += 1; },
  });
  actor.setParams({ src: 'dwell' });          // 通常パラメータ経路
  assert.equal(count, 1);
  actor.setParams({ mode: 'sessions' });      // mode 経路（早期 return 前に発火）
  assert.equal(count, 2);
  actor.setParams({ src: 'zp' });             // sessions のまま src 変更
  assert.equal(count, 3);
});

test('actor: onParamsChanged 未注入でも setParams は例外なく動作（後方互換）', () => {
  const actor = new MarketProfileActor({
    client: fakeClient(), primitive: fakePrimitive(),
    getContext: () => ({ datasetRef: 'jp225_tick', timeframe: '1h' }),
  });
  assert.doesNotThrow(() => actor.setParams({ src: 'dwell' }));
});

// --- 7) ISSUE-067: 日別×tf-period描画時は refresh が全期間 sessions フェッチを叩かない ------ //
test('actor: sessions×tfDraws の refresh は /market_profile を fetch しない（列は tf-period 供給）', async () => {
  const client = fakeClient();
  let focusCalled = 0;
  const actor = new MarketProfileActor({
    client,
    primitive: { setProfile() {}, setVisible() {}, setSessions() {} },
    getContext: () => ({ datasetRef: 'jp225_tick', timeframe: '1h' }),
    getCandles: () => [{ time: 1000 }, { time: 2000 }],
    renderer: {
      setSessionMP() {}, setCandleTransparency() {},
      focusTimeRange() { focusCalled += 1; },
    },
    sessionsDrawnByTfPeriod: () => true,   // tf-period が列を描くモード
  });
  actor._enabled = true;
  actor.setParams({ mode: 'sessions' });   // sessions ON（_sessionsFocusPending も立つ）
  const before = client.calls.length;
  await actor.refresh();
  assert.equal(client.calls.length, before, 'tfDraws では /market_profile を叩かない');
  assert.equal(focusCalled, 1, '初回のみ candle 範囲へ focus');
  await actor.refresh();
  assert.equal(focusCalled, 1, '2回目以降は focus しない（手動ズーム尊重）');
});

test('actor: sessions だが tfDraws=false（非対応tf等）は従来どおり /market_profile を fetch（回帰ゼロ）', async () => {
  const client = fakeClient();
  const actor = new MarketProfileActor({
    client,
    primitive: { setProfile() {}, setVisible() {}, setSessions() {} },
    getContext: () => ({ datasetRef: 'jp225_tick', timeframe: '1W' }),
    getCandles: () => [{ time: 1000 }],
    renderer: { setSessionMP() {}, setCandleTransparency() {}, focusTimeRange() {} },
    sessionsDrawnByTfPeriod: () => false,  // タイルは MP actor が描く
  });
  actor._enabled = true;
  actor.setParams({ mode: 'sessions' });
  const before = client.calls.length;
  await actor.refresh();
  assert.equal(client.calls.length, before + 1, 'tfDraws=false は従来どおり fetch');
});
