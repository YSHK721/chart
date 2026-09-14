// mp_va_pct_propagation.test.js — ISSUE-260: VA 比率（va）が front の全経路へ届くことの検定。
//
// 破れていた不変条件（是正前）:
//   UI は「バリューエリア」を常時操作可能なパラメータとして出すのに、
//     - tf-period 列経路: client が URL に va を載せず、列の VA は既定比率に固定されていた。
//     - 増分成長経路: DwellAccumulator が自前のリテラル（VA_PCT）で VA を再計算していた。
//   ＝設定と表示が一致しない（効かないツマミ）。
//
// 本テストが固定する不変条件:
//   1. domain（DwellAccumulator）は比率を**所有しない**。init({vaPct}) の注入値に従い、
//      比率が変われば snapshot の VA も変わる（既定リテラルへ黙って落ちない）。
//   2. 取得経路（client / jitter buffer / actor）が va を透過し、変更でキャッシュを破棄する。
//   3. catalog の既定は Python 唯一源の生成物（front に第 2 定義を作らない）。
//   4. サーバ応答に vaPct が無ければ増分成長へ入らない（前回描画を保持＝自前既定で描かない）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  DwellAccumulator, valueArea,
} from '../js/domain/market_profile_dwell_accumulator.js';
import * as accumulatorModule from '../js/domain/market_profile_dwell_accumulator.js';
import { VA_PCT_DEFAULT } from '../js/domain/mp_param_defaults_generated.js';
import { buildTfPeriodUrl } from '../js/adapter/front/tf_period_profile_client.js';
import { TfPeriodJitterBuffer } from '../js/adapter/front/tf_period_jitter_buffer.js';
import { TfPeriodProfileActor } from '../js/adapter/front/tf_period_profile_actor.js';
import { MpFetchParams } from '../js/adapter/front/mp_fetch_params.js';
import { MpTickGrowth } from '../js/adapter/front/mp_tick_growth.js';
import { makeMarketProfileDef } from '../js/usecase/catalog_entry.js';

const DAY0 = 1704067200; // 2024-01-01 00:00 UTC（月曜）。
const ALL_ACTIVE = Array.from({ length: 7 }, () => Array.from({ length: 24 }, () => true));

// --------------------------------------------------------------------------- //
// 1. domain: 比率は注入値に従う
// --------------------------------------------------------------------------- //
function makeAcc(vaPct) {
  const acc = new DwellAccumulator();
  acc.init({
    baseFine: [10, 30, 80, 20, 10],   // 非対称な分布（比率で拡張幅が変わる）。
    baseKmin: 100,
    activeTable: ALL_ACTIVE,
    priceMin: 1000,
    priceMax: 1050,
    nBins: 5,
    gridW: 10,
    formingStart: DAY0,
    vaPct,
  });
  return acc;
}

test('DwellAccumulator: snapshot の VA は init で注入された比率に従う（既定へ落ちない）', () => {
  // Arrange / Act
  const narrow = makeAcc(0.30).snapshot();
  const wide = makeAcc(0.95).snapshot();
  // Assert: 是正前は VA_PCT=0.70 固定で両者が一致し Red。
  assert.notDeepEqual(
    [narrow.va_low, narrow.va_high], [wide.va_low, wide.va_high],
    'vaPct を変えても snapshot の VA が変わらない（比率が届いていない）',
  );
  assert.ok((wide.va_high - wide.va_low) > (narrow.va_high - narrow.va_low));
});

test('DwellAccumulator: snapshot の VA は同一比率の valueArea（単一規則）と一致する', () => {
  for (const vaPct of [0.30, 0.55, VA_PCT_DEFAULT, 0.95]) {
    const snap = makeAcc(vaPct).snapshot();
    const centers = snap.bins.map((b) => b.price);
    const tpo = snap.bins.map((b) => b.tpo);
    const [lo, hi] = valueArea(centers, tpo, vaPct);
    assert.deepEqual(
      [snap.va_low, snap.va_high],
      [Math.round(lo * 100) / 100, Math.round(hi * 100) / 100],
      `vaPct=${vaPct}`,
    );
  }
});

test('domain は VA 比率の既定を export しない（第 2 定義の不在＝単一情報源）', () => {
  assert.equal('VA_PCT' in accumulatorModule, false,
    'domain が VA 比率のリテラルを再び所有している（決定権の分散）');
});

// --------------------------------------------------------------------------- //
// 2. 取得経路: client / jitter buffer / actor
// --------------------------------------------------------------------------- //
test('buildTfPeriodUrl: va を付与する／未指定は従来 URL（byte 不変）', () => {
  const base = buildTfPeriodUrl({ datasetRef: 'jp225_tick', timeframe: '1h', from: 1, to: 2 });
  assert.ok(!base.includes('va='), '未指定で va を載せない（サーバ既定へ委ねる）');
  const withVa = buildTfPeriodUrl({
    datasetRef: 'jp225_tick', timeframe: '1h', from: 1, to: 2, va: 0.55,
  });
  assert.ok(withVa.endsWith('&va=0.55'));
  assert.equal(withVa.replace('&va=0.55', ''), base);
});

test('TfPeriodJitterBuffer: va 変更でキャッシュ破棄＋再取得し、fetch へ透過する', async () => {
  // Arrange
  const calls = [];
  const client = {
    async fetchWindow(args) {
      calls.push({ ...args });
      return {
        tf: args.timeframe, unit: 1, from: args.from, to: args.to,
        columns: [{ time: args.from, levels: [] }],
      };
    },
  };
  const buf = new TfPeriodJitterBuffer({
    client, datasetRef: 'jp225_tick', windowSec: 100, prefetch: 0,
  });
  const settle = () => new Promise((r) => setTimeout(r, 0));
  // Act / Assert
  buf.ensure('1h', 0, 50, { src: null, va: 0.7 });
  await settle();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].va, 0.7);
  buf.ensure('1h', 0, 50, { src: null, va: 0.7 });   // 同キー → ヒット
  await settle();
  assert.equal(calls.length, 1);
  buf.ensure('1h', 0, 50, { src: null, va: 0.55 });  // va 変更 → 破棄＋再取得
  await settle();
  assert.equal(calls.length, 2, 'va を変えても再取得されない（列が古い比率のまま残る）');
  assert.equal(calls[1].va, 0.55);
  buf.ensure('1h', 0, 50);                            // 未指定へ戻す → 破棄＋再取得
  await settle();
  assert.equal(calls.length, 3);
  assert.ok(!('va' in calls[2]));
});

test('TfPeriodProfileActor: getQuery の取得パラメータ（src/va）を ensure へ渡す', () => {
  // Arrange
  const seen = [];
  const buf = {
    ensure(tf, from, to, query) { seen.push({ tf, from, to, query }); },
    allReady: () => true,
    getColumns: () => [],
    unit: () => 1,
  };
  const actor = new TfPeriodProfileActor({
    jitterBuffer: buf,
    primitive: { setTfPeriods() {} },
    getTimeframe: () => '1h',
    getVisibleRange: () => ({ from: 0, to: 100 }),
    getQuery: () => ({ src: 'zp', va: 0.55 }),
  });
  // Act
  actor.setEnabled(true);
  // Assert
  assert.equal(seen.length, 1);
  assert.deepEqual(seen[0].query, { src: 'zp', va: 0.55 });
});

test('MpFetchParams.va(): setParams の va を公開する（未設定は null＝サーバ既定へ委ねる）', () => {
  const host = { _sessions: false, _getCandles: () => [], _nowSec: () => 0,
    _replayScrub: { isReplay: () => false } };
  const params = new MpFetchParams(host);
  assert.equal(params.va(), null);
  params.set({ va: 0.55 });
  assert.equal(params.va(), 0.55);
});

// --------------------------------------------------------------------------- //
// 3. catalog: 既定は Python 唯一源の生成物
// --------------------------------------------------------------------------- //
test('catalog の va 既定は生成物 VA_PCT_DEFAULT と同一（front に第 2 定義を持たない）', () => {
  // Arrange: catalog.js のローカル helper と同型の最小 param / IndicatorDef を注入する。
  const params = [];
  const param = (name, type, def, constraints, enumValues, meta) => {
    params.push({ name, type, def, constraints, enumValues, meta });
    return { name, def };
  };
  makeMarketProfileDef({
    IndicatorDef: class { constructor(spec) { Object.assign(this, spec); } },
    SeriesDef: class { constructor(spec) { Object.assign(this, spec); } },
    SeriesKind: { HORIZONTAL_LINE: 'h' },
    ParamType: { ENUM: 'enum', FLOAT: 'float' },
    ConstraintKind: { RANGE_OPEN: 'range_open', MIN_VALUE: 'min' },
    param,
    OHLC: [],
  });
  // Assert
  const va = params.find((p) => p.name === 'va');
  assert.ok(va, 'va パラメータが存在する');
  assert.equal(va.def, VA_PCT_DEFAULT);
});

// --------------------------------------------------------------------------- //
// 4. 増分成長: サーバ解決値に従う／欠損時は増分に入らない
// --------------------------------------------------------------------------- //
function growthHost(forming) {
  const drawn = [];
  const initArgs = [];
  const host = {
    _enabled: true,
    _sessions: false,
    _params: { src: 'dwell' },
    _primitive: { setProfile(p) { drawn.push(p); } },
    _getContext: () => ({}),
    refresh: async () => { drawn.push('refresh'); },
    _buildFormingArgs: () => ({ base: 1 }),
  };
  const growth = new MpTickGrowth(host, {
    formingClient: { async fetchForming() { return forming; } },
    makeAccumulator: () => ({
      init(args) { initArgs.push(args); },
      addTick() {},
      snapshot: () => ({ ok: true }),
    }),
  });
  host._isIncremental = () => growth.isIncremental();
  host._enterTicklive = () => growth.enter();
  growth.setGrowing(true);
  return { growth, drawn, initArgs };
}

const _FORMING = {
  formingStart: DAY0,
  ticks: [],
  baseFine: [0, 0, 0],
  baseKmin: 100,
  activeTable: ALL_ACTIVE,
  priceMin: 1000,
  priceMax: 1030,
  nBins: 3,
  gridW: 10,
};

test('MpTickGrowth: 応答の vaPct（サーバ解決値）を accumulator へ注入する', async () => {
  const { growth, initArgs } = growthHost({ ..._FORMING, vaPct: 0.55 });
  await growth.enter();
  assert.equal(initArgs.length, 1);
  assert.equal(initArgs[0].vaPct, 0.55, 'サーバ解決済み比率が accumulator へ届いていない');
});

test('MpTickGrowth: vaPct 欠損の応答では増分に入らない（前回描画を保持）', async () => {
  const { growth, drawn, initArgs } = growthHost({ ..._FORMING }); // vaPct なし
  await growth.enter();
  assert.equal(initArgs.length, 0, '比率不明のまま自前の既定で描いている');
  assert.equal(drawn.length, 0);
});
