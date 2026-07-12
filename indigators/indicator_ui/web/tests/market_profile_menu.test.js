// Market Profile を「インジケーター」メニュー（catalog 経由）から選択して表示できるように
//   した非破壊追加の検証。
//
// 設計入力: 依頼「MP を他指標と同様に catalog から選択 → actor へ委譲して描画。既存トグルは温存」。
//   観点:
//   - catalog に market_profile が登録され list/get で取得できる（tab:'profile'・computeId:'market_profile'）。
//   - params（bins/va/limit）が既定値・制約付きで定義されている。
//   - applyIndicator('market_profile') は /compute を呼ばず MarketProfileActor へ委譲する。
//   - params が actor.setParams へ渡る。
//   - 既存指標の applyIndicator は従来どおり compute を呼ぶ（回帰なし）。
//   - actor.setParams が fetch コンテキストへ bins/va/limit を反映する。
// 構造: Arrange-Act-Assert（AAA）。DOM/ネット非依存（全注入・recording fake）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { list, get } from '../js/usecase/catalog.js';
import { ParamType, ConstraintKind } from '../js/domain/constraint_eval.js';
import { IndicatorController } from '../js/adapter/front/indicator_controller.js';
import { MarketProfileActor } from '../js/adapter/front/market_profile_actor.js';

function paramOf(def, name) {
  return def.params.find((p) => p.name === name);
}

// --- catalog ---------------------------------------------------------------

test('catalog: market_profile is registered and retrievable via list/get', () => {
  // Act
  const ids = list().map((d) => d.id);
  const def = get('market_profile');
  // Assert
  assert.ok(ids.includes('market_profile'), 'market_profile が list に含まれる');
  assert.ok(def, 'get(market_profile) が非 null');
  assert.equal(def.tab, 'profile');
  assert.equal(def.compute.computeId, 'market_profile');
});

test('catalog: market_profile defines bins/va params (no limit param＝全期間集計固定)', () => {
  const def = get('market_profile');
  // bins: ENUM プリセット [30,60,100] 既定 '60'（数値自由入力→プリセット化・MIN_VALUE 制約撤去）。
  assert.equal(paramOf(def, 'bins').type, ParamType.ENUM);
  assert.equal(paramOf(def, 'bins').default, '60');
  assert.deepEqual(paramOf(def, 'bins').enumValues, ['30', '60', '100']);
  // va: FLOAT 既定0.70 0<va<1 RANGE_OPEN
  assert.equal(paramOf(def, 'va').type, ParamType.FLOAT);
  assert.equal(paramOf(def, 'va').default, 0.70);
  assert.ok(paramOf(def, 'va').constraints.some((c) => c.kind === ConstraintKind.RANGE_OPEN));
  // limit: 対象本数 param は削除済（全期間集計固定＝再追加を禁止する回帰）。
  assert.equal(paramOf(def, 'limit'), undefined, 'limit param は定義しない');
});

// --- controller 委譲 --------------------------------------------------------

// setParams / setEnabled の呼び出しを記録する Fake actor。
function fakeMarketProfile() {
  return {
    params: [], enables: [], refreshes: 0,
    setParams(p) { this.params.push(p); },
    async setEnabled(on) { this.enables.push(on); },
    async refresh() { this.refreshes += 1; },
    // 実 actor 契約: ライブ tick 入口は onLiveTick。非増分（ticklive OFF・既定）は refresh へ
    //   byte-identical 委譲するため、fake も同契約（onLiveTick→refresh）で反映する。
    async onLiveTick() { return this.refresh(); },
    detach() { this.detached = true; },
  };
}

function makeController({ marketProfile, computeCalls }) {
  const noop = () => {};
  return new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: {
      compute: async (req) => { computeCalls.push(req); return { ok: true, generation: req.generation ?? 0, series: [] }; },
    },
    persistence: {
      loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop,
      loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer: { renderLine: noop, renderHistogram: noop, renderHorizontal: noop, setData: noop, setVisible: noop, remove: noop },
    document: null,
    marketProfile,
  });
}

test('applyIndicator(market_profile) delegates to the actor and does NOT call /compute', async () => {
  // Arrange
  const marketProfile = fakeMarketProfile();
  const computeCalls = [];
  const ctrl = makeController({ marketProfile, computeCalls });
  // Act
  const inst = await ctrl.applyIndicator('market_profile', 'default');
  // Assert: compute（/compute）は呼ばれず、actor へ委譲される。
  assert.equal(computeCalls.length, 0, 'MP は /compute を呼ばない');
  assert.deepEqual(marketProfile.enables, [true], 'setEnabled(true) が呼ばれる');
  assert.ok(inst, 'applied インスタンスが返る');
  assert.equal(inst.indicatorId, 'market_profile');
});

test('applyIndicator(market_profile) forwards default params (resmode/bins/va/src) to actor.setParams', async () => {
  // Arrange
  const marketProfile = fakeMarketProfile();
  const computeCalls = [];
  const ctrl = makeController({ marketProfile, computeCalls });
  // Act
  await ctrl.applyIndicator('market_profile', 'default');
  // Assert: src は既定 zp（超過占有 z(p)・依頼者指示 2026-07-12 で candle から昇格）。
  //   resmode（解像度）既定 bins・range 既定 100（'auto' 撤去済）も
  //   bins/va と同様に転送される（client が resmode で bins/barw を排他化するため range 同送は無害）。
  //   mode（表示モード・統合トグル）既定 'normal' も転送される（actor が normal で両 OFF＝現状維持）。
  //   旧 replay/sessions は mode に統合されたため転送されない（catalog から撤去）。
  //   limit は転送しない（MP は全期間集計固定＝limit 非送信）。
  assert.equal(marketProfile.params.length, 1);
  assert.deepEqual(marketProfile.params[0], { bins: '60', va: 0.70, src: 'zp', mode: 'normal', resmode: 'bins', range: '100' });
});

test('applyIndicator(existing indicator) still calls /compute (no regression)', async () => {
  // Arrange
  const marketProfile = fakeMarketProfile();
  const computeCalls = [];
  const ctrl = makeController({ marketProfile, computeCalls });
  // Act
  await ctrl.applyIndicator('moving_averages', 'default');
  // Assert: 既存指標は従来どおり compute を呼び、MP actor へは委譲しない。
  assert.ok(computeCalls.length >= 1, '既存指標は /compute を呼ぶ');
  assert.equal(marketProfile.enables.length, 0, '既存指標は MP actor を触らない');
});

// --- restore（リロード復元）------------------------------------------------

test('restore() re-hydrates a saved market_profile via the actor and does NOT call /compute', async () => {
  // Arrange: 保存済み MP インスタンス（可視）を返す persistence を用意する。
  const marketProfile = fakeMarketProfile();
  const computeCalls = [];
  const noop = () => {};
  const savedMp = {
    instanceId: 'market_profile#1', indicatorId: 'market_profile', variant: 'default',
    params: [['bins', 80], ['va', 0.65], ['limit', 500], ['src', 'dwell']], visible: true,
    generation: 0, seq: 1, createdAt: '2026-06-07T00:00:00Z',
  };
  const ctrl = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async (req) => { computeCalls.push(req); return { ok: true, generation: 0, series: [] }; } },
    persistence: {
      loadApplied: () => [savedMp], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop,
      loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer: { renderLine: noop, renderHistogram: noop, renderHorizontal: noop, setData: noop, setVisible: noop, remove: noop, setCandles: noop },
    document: null,
    marketProfile,
  });
  // Act
  await ctrl.restore();
  // Assert: MP は /compute を介さず actor へ復元される（保存 params で setParams、可視なので setEnabled(true)）。
  assert.equal(computeCalls.length, 0, 'restore は MP を /compute で計算しない');
  // 保存 params に limit が残っていても _mpParams は転送しない（全期間集計固定）。
  assert.deepEqual(marketProfile.params.at(-1), { bins: 80, va: 0.65, src: 'dwell' });
  assert.deepEqual(marketProfile.enables, [true]);
});

// --- actor setParams -------------------------------------------------------

// --- 再計算経路（ライブ tick / 足切替）で MP を /compute へ流出させない（回帰）--------
//   背景: recomputeAllApplied（live_updater / forming_bar / setTimeframe の共通入口）に
//   MP ガードが無いと、MP が /compute へ流出し backend に compute が無いため例外。特に
//   setTimeframe では compute ループが preRender より前で例外→チャート更新が全スキップ。

// setCandles / mode:'b' / loadCandles を備えた B 方式コントローラ。
//   throwOnMp=true のとき market_profile の /compute は例外（backend に compute 無しを模擬）。
function makeControllerB({ marketProfile, computeCalls, setCandlesCalls, loadCandles, throwOnMp }) {
  const noop = () => {};
  return new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: {
      compute: async (req) => {
        computeCalls.push(req);
        if (throwOnMp && req.indicatorId === 'market_profile') {
          throw new Error('backend has no market_profile compute');
        }
        return { ok: true, generation: req.generation ?? 0, series: [] };
      },
    },
    persistence: {
      loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop,
      loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer: {
      renderLine: noop, renderHistogram: noop, renderHorizontal: noop, setData: noop,
      setVisible: noop, remove: noop, setCandles: (c) => setCandlesCalls.push(c),
    },
    document: null,
    mode: 'b',
    loadCandles,
    marketProfile,
  });
}

test('recomputeAllApplied does NOT route market_profile to /compute and refreshes the actor', async () => {
  // Arrange
  const marketProfile = fakeMarketProfile();
  const computeCalls = [];
  const ctrl = makeController({ marketProfile, computeCalls });
  await ctrl.applyIndicator('market_profile', 'default');
  assert.equal(computeCalls.length, 0, 'apply 時点で /compute は呼ばれない');
  const before = marketProfile.refreshes;
  // Act: ライブ更新／足切替と同じ再計算入口
  await ctrl.recomputeAllApplied({ mode: 'latest' });
  // Assert
  assert.ok(
    !computeCalls.some((r) => r.indicatorId === 'market_profile'),
    'MP は再計算経路で /compute へ流出しない',
  );
  assert.ok(marketProfile.refreshes > before, 'MP は actor.refresh で新時間足へ追従する');
});

test('setTimeframe still updates the chart candles when a market_profile is applied (does not throw)', async () => {
  // Arrange: market_profile の /compute は例外（backend 未実装を模擬）。
  const marketProfile = fakeMarketProfile();
  const computeCalls = [];
  const setCandlesCalls = [];
  const loadCandles = async () => ([{ time: 1, open: 1, high: 1, low: 1, close: 1 }]);
  const ctrl = makeControllerB({ marketProfile, computeCalls, setCandlesCalls, loadCandles, throwOnMp: true });
  await ctrl.applyIndicator('market_profile', 'default');
  // Act
  let threw = false;
  try {
    await ctrl.setTimeframe('1h');
  } catch {
    threw = true;
  }
  // Assert: MP が /compute へ流出しなければ preRender（setCandles）まで到達する。
  assert.equal(threw, false, 'MP 適用中でも setTimeframe は例外にならない');
  assert.equal(setCandlesCalls.length, 1, '足切替でチャート candles が更新される');
  assert.ok(
    !computeCalls.some((r) => r.indicatorId === 'market_profile'),
    'MP は足切替でも /compute へ流出しない',
  );
});

// --- MP 単一インスタンス制約（重複追加防止）--------------------------------------

test('applyIndicator(market_profile) twice keeps a single MP instance', async () => {
  // Arrange
  const marketProfile = fakeMarketProfile();
  const computeCalls = [];
  const ctrl = makeController({ marketProfile, computeCalls });
  // Act
  await ctrl.applyIndicator('market_profile', 'default');
  await ctrl.applyIndicator('market_profile', 'default');
  // Assert
  const mpInstances = ctrl._state.applied.filter((i) => i.indicatorId === 'market_profile');
  assert.equal(mpInstances.length, 1, 'MP は単一インスタンスに保たれる（二重 legend 行を作らない）');
});

// --- gear（設定）の async 取りこぼしを catch する ------------------------------

test('gear apply rejection on a market_profile does not surface as an unhandled rejection', async () => {
  // Arrange: gear の applyParams 内 await refresh() が reject するよう仕込む。
  const marketProfile = fakeMarketProfile();
  marketProfile.refresh = async () => { throw new Error('refresh failed'); };
  const computeCalls = [];
  const ctrl = makeController({ marketProfile, computeCalls });
  const inst = await ctrl.applyIndicator('market_profile', 'default');
  const def = get('market_profile');

  const unhandled = [];
  const onUnhandled = (reason) => unhandled.push(reason);
  process.on('unhandledRejection', onUnhandled);
  try {
    // document=null のため fallback パス applyParams(currentParams) が発火する。
    ctrl._onGearMarketProfile(inst, def);
    // unhandledRejection の発火機会を与える（次マクロタスクまで待つ）。
    await new Promise((resolve) => setTimeout(resolve, 20));
  } finally {
    process.removeListener('unhandledRejection', onUnhandled);
  }
  // Assert
  assert.equal(unhandled.length, 0, 'gear の拒否は catch され unhandledRejection にならない');
});

// gear（設定変更）で range（レンジpt）を actor へ転送する。auto/未指定は転送しない（従来 bins・
//   既定 apply の deepEqual を壊さない）。src=m1 も bins/va/limit/src と同様に転送される。
//   後方互換マイグレーション（修正1）: resmode 欠落かつ数値 range の旧 barw 保存インスタンスは
//   _mpParams が resmode='range' を導出する。これにより gear 経路でも保存レンジが barw として
//   送られる（無しだと bins= に化ける defect）。よって転送 params には resmode='range' が載る。
test('gear on market_profile forwards range to actor.setParams when set (non-auto)', async () => {
  // Arrange
  const marketProfile = fakeMarketProfile();
  const computeCalls = [];
  const ctrl = makeController({ marketProfile, computeCalls });
  const def = get('market_profile');
  const inst = {
    instanceId: 'market_profile#1', indicatorId: 'market_profile', variant: 'default',
    params: [['bins', 80], ['va', 0.65], ['limit', 500], ['src', 'm1'], ['range', '50']],
    visible: true, generation: 0, seq: 1, createdAt: '2026-06-07T00:00:00Z',
  };
  ctrl._state = { ...ctrl._state, applied: [inst] };
  // Act: document=null → applyParams(currentParams) が即時発火する。
  ctrl._onGearMarketProfile(inst, def);
  await new Promise((resolve) => setTimeout(resolve, 10));
  // Assert: 保存 params（range=50 含む）を actor へ転送する。resmode 欠落の旧 barw インスタンスは
  //   resmode='range' が導出される（後方互換マイグレーション・修正1）。
  assert.deepEqual(marketProfile.params.at(-1), { bins: 80, va: 0.65, src: 'm1', range: '50', resmode: 'range' });
});

test('gear on a legacy market_profile (sessions:true) forwards mode=sessions to actor.setParams (日別プロファイル・後方互換)', async () => {
  // Arrange: legacy sessions=true を保存 params に含む旧インスタンス（mode 統合前の永続データ）。
  const marketProfile = fakeMarketProfile();
  const computeCalls = [];
  const ctrl = makeController({ marketProfile, computeCalls });
  const def = get('market_profile');
  const inst = {
    instanceId: 'market_profile#1', indicatorId: 'market_profile', variant: 'default',
    params: [['bins', '60'], ['va', 0.70], ['src', 'candle'], ['sessions', true]],
    visible: true, generation: 0, seq: 1, createdAt: '2026-06-07T00:00:00Z',
  };
  ctrl._state = { ...ctrl._state, applied: [inst] };
  // Act
  ctrl._onGearMarketProfile(inst, def);
  await new Promise((resolve) => setTimeout(resolve, 10));
  // Assert: legacy sessions:true は _mpParams(_deriveMode) で mode='sessions' へ導出され、
  //   legacy キー（sessions）自体は actor へ送らない（mode に一本化）。
  assert.equal(marketProfile.params.at(-1).mode, 'sessions', 'mode=sessions が導出される');
  assert.equal('sessions' in marketProfile.params.at(-1), false, 'legacy sessions キーは送らない');
});

test('MarketProfileActor.setParams merges bins/va but DROPS limit (全期間集計固定)', async () => {
  // Arrange: getContext は limit を持たない構成。setParams に limit を渡しても転送されないことを固定する。
  const calls = [];
  const client = { async fetchProfile(ctx) { calls.push(ctx); return { bins: [] }; } };
  const primitive = { setProfile() {}, setVisible() {} };
  const actor = new MarketProfileActor({
    client, primitive, mainSeries: {},
    getContext: () => ({ datasetRef: 'sample', timeframe: '1D' }),
  });
  // Act
  actor.setParams({ bins: 80, va: 0.68, limit: 500 });
  await actor.setEnabled(true);
  // Assert: bins/va は重畳されるが limit は setParams で除外され fetch context に載らない。
  assert.deepEqual(calls[0], { datasetRef: 'sample', timeframe: '1D', bins: 80, va: 0.68 });
});
