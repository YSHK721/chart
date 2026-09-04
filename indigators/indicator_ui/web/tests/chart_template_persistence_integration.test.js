// テンプレート適用の永続化（結線レベル）— 実 IndicatorController を用いた回帰テスト。
//
// 設計入力（唯一の仕様源）: .doc/indicator-management-ui/基本設計_チャートテンプレート.md v0.1.1
//   §5.2 手順 5「`activeTemplateId` を当該テンプレート id に更新し、**`applied.v1` を永続化する**
//        （＝以後の通常復元でこの構成が復元される）」、§7.4 受入基準 6（リロード後に構成が保持される）、
//   §5.4（切替時自動適用の順序：除去 → 切替 → 適用）。
//
// 本ファイルの追加理由（実 UI 検証 D-1 の回帰固定）:
//   既存の協働子テストは host スタブの `_persistAll` をログ記録に置換していたため、
//   「実 controller の永続化経路（IndicatorStateStore.persistAll → StatePersistencePort.saveApplied）に
//   何が書かれるか」を一切検証できていなかった。本ファイルは実 IndicatorController・実
//   IndicatorStateStore・実 facade を通し、**永続化ポートへ渡る実データ**を固定する。
// 構造: Arrange-Act-Assert（AAA）。DOM 非依存（document=null）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { IndicatorController } from '../js/adapter/front/indicator_controller.js';
import { registerMarketProfile } from './helpers/market_profile_rig.js';
import { ChartTemplateController } from '../js/adapter/front/chart_template_controller.js';
import { get, list } from '../js/usecase/catalog.js';
import { timeframeLabels } from '../js/adapter/front/timeframe_menu.js';

const VALID_TIMEFRAMES = ['1m', '5m', '15m', '30m', '1h', '4h', '1D', '1W', '1M'];

// StatePersistencePort の記録用フェイク（LocalStorageGateway と同一面）。
function fakePersistence(initial = {}) {
  const store = {
    applied: initial.applied ?? [],
    favorites: [],
    uiState: initial.uiState ?? { timeframe: '5m' },
    seqCounters: {},
    savedAppliedCalls: [],
    savedUiStateCalls: [],
  };
  return {
    store,
    loadApplied: () => store.applied,
    saveApplied: (list_) => { store.applied = list_; store.savedAppliedCalls.push(list_); },
    loadFavorites: () => store.favorites,
    saveFavorites: (ids) => { store.favorites = ids; },
    loadUiState: () => store.uiState,
    saveUiState: (s) => { store.uiState = s; store.savedUiStateCalls.push(s); },
    nextSeq: (id) => { store.seqCounters[id] = (store.seqCounters[id] ?? 0) + 1; return store.seqCounters[id]; },
  };
}

function fakeRenderer() {
  const log = [];
  return {
    log,
    renderLine: (id) => log.push(`renderLine:${id}`),
    renderHistogram: (id) => log.push(`renderHistogram:${id}`),
    renderHorizontal: (id) => log.push(`renderHorizontal:${id}`),
    setData: () => {},
    setCandles: () => log.push('setCandles'),
    setVisible: (id, on) => log.push(`setVisible:${id}:${on}`),
    remove: (id) => log.push(`remove:${id}`),
    resetPriceZoom: () => {},
    resetPaneScales: () => {},
  };
}

// ma_marod の実 def に一致する系列名で応答する compute フェイク（F3 照合を通すため）。
//   T-2: 受入基準 3（旧構成を新しい足で計算しない・新構成に対して 1 回のみ）を固定するため
//   呼び出し（indicatorId / timeframe）を記録する。
function fakeCompute(calls = []) {
  return {
    calls,
    compute: async ({ indicatorId, generation, timeframe }) => {
      calls.push({ indicatorId, timeframe });
      const def = get(indicatorId);
      const names = (def?.series ?? []).map((s) => s.seriesName).filter(Boolean);
      return {
        ok: true,
        generation,
        series: names.map((name) => ({ name, kind: 'line', data: [{ time: 1, value: 1 }] })),
      };
    },
  };
}

const TEMPLATE_MA = {
  templateId: 'tpl#1',
  name: 'スキャル',
  instances: [{ indicatorId: 'ma_marod', variant: 'default', params: { window: 50 }, visible: true, styles: null }],
  createdAt: 1000,
  updatedAt: 1000,
};

function fakeGateway({ templates = [TEMPLATE_MA], bindings = {}, lastSeq = 1 } = {}) {
  return {
    saved: { templates: null, bindings: null, lastSeq: null },
    loadTemplates: () => templates,
    loadBindings: () => bindings,
    loadTemplateSeq: () => lastSeq,
    saveTemplates(l) { this.saved.templates = l; },
    saveBindings(o) { this.saved.bindings = o; },
    saveTemplateSeq(n) { this.saved.lastSeq = n; },
  };
}

// 実 controller ＋ 協働子（composition root と同一の setTimeframe 差し替え）を組む。
async function buildWiring({
  bindings = {},
  loadCandles = async () => [{ time: 1, open: 1, high: 1, low: 1, close: 1 }],
  marketProfile = null,
  templateSet = undefined,
} = {}) {
  const persistence = fakePersistence();
  const renderer = fakeRenderer();
  const computeCalls = [];
  const controller = new IndicatorController({
    catalog: { listIndicators: list, get },
    compute: fakeCompute(computeCalls),
    persistence,
    renderer,
    document: null,
    mode: 'b',
    datasetRef: 'jp225_tick',
    timeframe: '5m',
    loadCandles,
  });
  // S3: MP は ctor 引数ではなく**合成根と同じ登録経路**で結線する。ctor へ渡し続けると
  //   キーは黙って無視され、fakeMpActor({ failOnEnable: true }) が一度も発火しない
  //   （「注入したつもりで何も届いていない」テストになる）。
  registerMarketProfile(controller, { actor: marketProfile });
  const gateway = fakeGateway(templateSet ? { bindings, templates: templateSet } : { bindings });
  const templates = new ChartTemplateController(controller, {
    gateway, validTimeframes: VALID_TIMEFRAMES, now: () => 2000,
  });
  const proceed = controller.setTimeframe.bind(controller);
  controller.setTimeframe = (tf) => templates.onTimeframeChange(tf, proceed);
  return { controller, templates, persistence, renderer, gateway, computeCalls };
}

// 永続化ポートへ最後に渡された applied 配列。
function lastSavedApplied(persistence) {
  const calls = persistence.store.savedAppliedCalls;
  return calls.length > 0 ? calls[calls.length - 1] : null;
}

// ---------------------------------------------------------------------------
// D-1 回帰: 適用後に applied.v1 が永続化される（§5.2 手順 5・受入基準 6）
// ---------------------------------------------------------------------------

test('TC-P01 手動適用（UC-T02）後、applied.v1 に適用後の構成が永続化される（§5.2 手順 5）', async () => {
  // Arrange
  const { controller, templates, persistence } = await buildWiring();
  await controller.applyIndicator('profit_band', 'robust'); // 適用前の別構成
  // Act
  await templates.applyTemplate('tpl#1');
  // Assert
  const saved = lastSavedApplied(persistence);
  assert.deepEqual(
    saved.map((i) => i.indicatorId), ['ma_marod'],
    `適用後の構成が永続化される（実際: ${JSON.stringify(saved)}）`,
  );
  assert.equal(controller._state.uiState.activeTemplateId, 'tpl#1');
});

test('TC-P02 自動適用（UC-T04）後、applied.v1 に適用後の構成が永続化される（受入基準 6・D-1 回帰）', async () => {
  // Arrange: 5m に別構成が在り、1m に tpl#1 が紐付いている
  const { controller, persistence, computeCalls } = await buildWiring({ bindings: { '1m': 'tpl#1' } });
  await controller.applyIndicator('profit_band', 'robust');
  computeCalls.length = 0; // 切替以降の計算だけを観測する
  // Act: 時間足メニュー相当の切替（composition root が差し替えた setTimeframe を通る）
  await controller.setTimeframe('1m');
  // Assert
  const saved = lastSavedApplied(persistence);
  assert.deepEqual(
    saved.map((i) => i.indicatorId), ['ma_marod'],
    `自動適用後の構成が applied.v1 へ永続化される（実際: ${JSON.stringify(saved)}）`,
  );
  assert.equal(controller._state.uiState.activeTemplateId, 'tpl#1', 'activeTemplateId も更新される');
  assert.equal(persistence.store.uiState.timeframe, '1m', '時間足も永続化される');
  // 受入基準 3（T-2）: 旧構成（profit_band）は新しい足で計算されず、新構成の計算は 1 回だけ。
  assert.deepEqual(
    computeCalls.map((c) => c.indicatorId), ['ma_marod'],
    `旧構成は新しい足で計算されない（実際: ${JSON.stringify(computeCalls)}）`,
  );
  assert.equal(computeCalls.length, 1, '新構成に対して計算は 1 回のみ（受入基準 3）');
  assert.equal(computeCalls[0].timeframe, '1m', '計算は切替後の新しい足で行う（§5.4 ステップ 3）');
});

test('TC-P03 永続化された applied.v1 は restore() で同じ構成へ復元できる（決定論性・受入基準 6）', async () => {
  // Arrange: 自動適用まで実施し、その永続化内容で新しい controller を起こす
  const { controller, persistence } = await buildWiring({ bindings: { '1m': 'tpl#1' } });
  await controller.applyIndicator('profit_band', 'robust');
  await controller.setTimeframe('1m');
  const renderer2 = fakeRenderer();
  const revived = new IndicatorController({
    catalog: { listIndicators: list, get },
    compute: fakeCompute(),
    persistence, // 同じ永続化内容を読む
    renderer: renderer2,
    document: null,
    mode: 'b',
    datasetRef: 'jp225_tick',
    timeframe: '1m',
    loadCandles: async () => [{ time: 1, open: 1, high: 1, low: 1, close: 1 }],
  });
  // Act
  await revived.restore();
  // Assert
  assert.deepEqual(
    revived._state.applied.map((i) => i.indicatorId), ['ma_marod'],
    'リロード相当（restore）で構成が戻る（凡例が空にならない）',
  );
});

// ---------------------------------------------------------------------------
// D-2 回帰: 切替処理が失敗しても「除去済み・未適用」で終わらない（§5.4）
// ---------------------------------------------------------------------------

test('TC-P04 切替処理が例外を投げても、テンプレートの適用と永続化は実行される（D-2 回帰）', async () => {
  // Arrange: proceed（既存の時間足切替）が throw する状況を作る
  const { controller, persistence } = await buildWiring({
    bindings: { '1m': 'tpl#1' },
    loadCandles: async () => { throw new Error('Value is null'); },
  });
  await controller.applyIndicator('profit_band', 'robust');
  // Act / Assert: 例外は呼び出し元へ従来どおり伝播する
  await assert.rejects(() => controller.setTimeframe('1m'), /Value is null/);
  // Assert: 「除去済み・未適用」（全指標消失）で終わらない
  assert.deepEqual(
    controller._state.applied.map((i) => i.indicatorId), ['ma_marod'],
    '除去だけ実行されて構成が失われる状態にしない',
  );
  const saved = lastSavedApplied(persistence);
  assert.deepEqual(saved.map((i) => i.indicatorId), ['ma_marod'], '永続化も実行され、リロードで消えない');
  assert.equal(controller._state.uiState.activeTemplateId, 'tpl#1');
});

// ---------------------------------------------------------------------------
// D-1 根本原因の回帰: 再構築（MP 復元経路）が失敗しても永続化は完遂する
// ---------------------------------------------------------------------------

// MP を含むテンプレート（実 UI 検証の tpl#1 と同型）。
const TEMPLATE_MA_MP = {
  templateId: 'tpl#1',
  name: 'スキャル',
  instances: [
    { indicatorId: 'ma_marod', variant: 'default', params: {}, visible: true, styles: null },
    { indicatorId: 'market_profile', variant: 'default', params: {}, visible: true, styles: null },
  ],
  createdAt: 1000,
  updatedAt: 1000,
};

// MP アクター（setEnabled(true) で失敗させられる）。
function fakeMpActor({ failOnEnable = false } = {}) {
  return {
    enabled: false,
    setParams: () => {},
    applyGrowthState: () => {},
    isEnabled() { return this.enabled; },
    async setEnabled(on) {
      if (on && failOnEnable) { throw new Error('MP fetch failed'); }
      this.enabled = on;
    },
    detach: () => {},
    async refresh() {},
  };
}

test('TC-P05 MP 復元が失敗しても applied.v1 は空のままにならない（D-1 根本原因の回帰・F-T4）', async () => {
  // Arrange: 実 UI 検証と同型（MP 入りテンプレートが 1m へ紐付け・MP 復元が失敗する）
  const { controller, persistence } = await buildWiring({
    bindings: { '1m': 'tpl#1' },
    templateSet: [TEMPLATE_MA_MP],
    marketProfile: fakeMpActor({ failOnEnable: true }),
  });
  await controller.applyIndicator('ma_marod', 'default'); // 5m の既存構成
  // Act
  await controller.setTimeframe('1m');
  // Assert: 除去が永続化した [] が最終値として残らない（リロードで構成が消えない）
  const saved = lastSavedApplied(persistence);
  assert.deepEqual(
    saved.map((i) => i.indicatorId), ['ma_marod', 'market_profile'],
    `MP 復元の失敗で全体を中止せず、適用済み構成を永続化する（実際: ${JSON.stringify(saved)}）`,
  );
  assert.equal(controller._state.uiState.activeTemplateId, 'tpl#1', 'activeTemplateId も手順 5 どおり更新される');
});

// ---------------------------------------------------------------------------
// D-3 回帰: 保存ダイアログの時間足表記はラベル（キーではない）
// ---------------------------------------------------------------------------

test('TC-P06 保存ダイアログの時間足表記は時間足メニューのラベル（1m→1分 / 1D→日）（§6.2・D-3 回帰）', async () => {
  // Arrange: composition root と同一の注入（ラベルの単一情報源＝timeframe_menu.js の groups）
  const { controller } = await buildWiring();
  const opened = [];
  const templates = new ChartTemplateController(controller, {
    gateway: fakeGateway(),
    dialogs: { openSave: (a) => opened.push(a), openManage: () => {} },
    validTimeframes: VALID_TIMEFRAMES,
    timeframeLabels: timeframeLabels(),
    now: () => 2000,
  });
  // Act
  templates.openSaveDialog();                 // 5m
  await controller.setTimeframe('1D');
  templates.openSaveDialog();                 // 1D
  // Assert
  assert.deepEqual(opened.map((o) => o.timeframeLabel), ['5分', '日'], 'キー（5m/1D）ではなくラベルを渡す');
});

test('TC-P07 ラベル写像は時間足メニューの groups から導出される（キーとラベルの二重定義がない）', () => {
  // Arrange / Act
  const labels = timeframeLabels();
  // Assert: 既定 9 足すべてにラベルが在る
  assert.deepEqual(labels, {
    '1m': '1分', '5m': '5分', '15m': '15分', '30m': '30分',
    '1h': '1時間', '4h': '4時間', '1D': '日', '1W': '週', '1M': '月',
  });
});

// ---------------------------------------------------------------------------
// C-2 回帰: MP の失敗を当該 1 件に局所化する（F-T4「当該 1 件のみスキップ」）
// ---------------------------------------------------------------------------

// MP を先頭に持つテンプレート（後続指標が MP 失敗の巻き添えになるかを検出する宣言順）。
const TEMPLATE_MP_FIRST = {
  templateId: 'tpl#1',
  name: 'MP 先頭',
  instances: [
    { indicatorId: 'market_profile', variant: 'default', params: {}, visible: true, styles: null },
    { indicatorId: 'ma_marod', variant: 'default', params: {}, visible: true, styles: null },
  ],
  createdAt: 1000,
  updatedAt: 1000,
};

test('TC-P08 MP 復元が失敗しても宣言順で後続の指標は計算・描画される（C-2 回帰・F-T4）', async () => {
  // Arrange: MP が先頭・その setEnabled(true) が失敗する
  const { controller, persistence, renderer, computeCalls } = await buildWiring({
    bindings: { '1m': 'tpl#1' },
    templateSet: [TEMPLATE_MP_FIRST],
    marketProfile: fakeMpActor({ failOnEnable: true }),
  });
  computeCalls.length = 0;
  renderer.log.length = 0;
  // Act
  await controller.setTimeframe('1m');
  // Assert: 失敗は MP の 1 件に閉じ、後続の ma_marod は計算も描画もされる
  assert.deepEqual(
    computeCalls.map((c) => c.indicatorId), ['ma_marod'],
    `MP の後続指標が巻き添えでスキップされない（実際: ${JSON.stringify(computeCalls)}）`,
  );
  assert.ok(
    renderer.log.some((l) => l.startsWith('renderLine:ma_marod')),
    `後続指標が描画される（実際: ${JSON.stringify(renderer.log)}）`,
  );
  // 「state と凡例には在席するが系列が描かれていない」不整合を残さない
  const saved = lastSavedApplied(persistence);
  assert.deepEqual(saved.map((i) => i.indicatorId), ['market_profile', 'ma_marod'], '永続化は従来どおり完遂する');
});
