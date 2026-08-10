// chart_template_controller.js（テンプレート協働子）の Red テスト。
//
// 設計入力（唯一の仕様源）: .doc/indicator-management-ui/基本設計_チャートテンプレート.md v0.1.1
//   §5.1（UC-T01 保存＝写像・activeTemplateId 設定・チェック ON で現在足へ紐付け）、
//   §5.2（UC-T02 適用＝現構成の全件除去 → 宣言順に適用 → 現在足で計算 → activeTemplateId 更新＋
//        永続化 → 凡例再描画。MP は宣言順の先頭 1 件のみ・除去は MP 経路）、
//   §5.3（UC-T03 紐付けは構成を変更しない）、
//   §5.4（UC-T04 切替時自動適用の発火条件・順序（除去 → 切替 → 適用）・再入防止・
//        紐付けが無い足は現行挙動維持）、
//   §5.5（UC-T05 改名・削除＝紐付けと activeTemplateId の掃除・構成は変更しない）、
//   §5.6 F-T3（dangling は適用せず当該紐付けを削除して永続化）、
//   §7.1（ホスト契約 TemplateHost にのみ依存する協働子）。
// 参照実装（同型元）: timeframe_controller.js / market_profile_controller.js（host 契約に依存する協働子）、
//   indicator_controller.js:101-128（凍結ロール契約の記法）。
// 構造: Arrange-Act-Assert（AAA）。DOM 非依存の host スタブ。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  ChartTemplateController,
  TEMPLATE_HOST_CONTRACT,
} from '../js/adapter/front/chart_template_controller.js';

const VALID_TIMEFRAMES = ['1m', '5m', '15m', '30m', '1h', '4h', '1D', '1W', '1M'];

const DEF_LINE = { id: 'tgp_btlm', compute: { computeId: 'tgp_btlm' } };
const DEF_BAND = { id: 'profit_band', compute: { computeId: 'profit_band' } };
const DEF_MP = { id: 'market_profile', compute: { computeId: 'market_profile' } };
const CATALOG = { tgp_btlm: DEF_LINE, profit_band: DEF_BAND, market_profile: DEF_MP };

function tplInstance({ indicatorId, variant = 'default', params = {}, visible = true, styles = null }) {
  return { indicatorId, variant, params, visible, styles };
}

function appliedJson({ indicatorId, seq = 1, variant = 'default', params = {}, visible = true, styles = null }) {
  return {
    instanceId: `${indicatorId}#${seq}`,
    indicatorId,
    variant,
    params,
    visible,
    generation: 0,
    seq,
    createdAt: 't0',
    styles,
  };
}

// TemplateHost 契約（12 面）のみを備えた最小スタブ。
function fakeHost({ applied = [], timeframe = '1D', seqCounters = {} } = {}) {
  const log = [];
  const host = {
    log,
    _datasetRef: 'jp225:1m',
    _timeframe: timeframe,
    _meta: new Map(),
    _catalog: { get: (id) => CATALOG[id] ?? null },
    _state: { applied, favorites: [], seqCounters, uiState: { timeframe, activeTemplateId: null } },
    _store: {
      rebuildApplied: async (list) => {
        log.push(`rebuildApplied:${list.map((i) => i.instanceId).join(',')}`);
      },
    },
    _isMarketProfile: (def) => def?.compute?.computeId === 'market_profile',
    _removeMarketProfile: async (inst) => {
      log.push(`removeMp:${inst.instanceId}`);
      host._state = { ...host._state, applied: host._state.applied.filter((i) => i.instanceId !== inst.instanceId) };
    },
    removeInstance: (instanceId) => {
      log.push(`remove:${instanceId}`);
      host._state = { ...host._state, applied: host._state.applied.filter((i) => i.instanceId !== instanceId) };
    },
    _commitState: (s) => { host._state = s; log.push('commitState'); },
    _persistAll: () => log.push('persistAll'),
    _renderLegend: () => log.push('renderLegend'),
  };
  return host;
}

// gateway スタブ（LocalStorageTemplateGateway と同一面）。
function fakeGateway({ templates = [], bindings = {}, lastSeq = 0 } = {}) {
  return {
    saved: { templates: null, bindings: null, lastSeq: null },
    loadTemplates: () => templates,
    loadBindings: () => bindings,
    loadTemplateSeq: () => lastSeq,
    saveTemplates(list) { this.saved.templates = list; },
    saveBindings(obj) { this.saved.bindings = obj; },
    saveTemplateSeq(n) { this.saved.lastSeq = n; },
  };
}

function fakeMenu() {
  return { renders: [], render(vm) { this.renders.push(vm); } };
}

function build({ host, gateway, menu = fakeMenu(), now = () => 2000 } = {}) {
  const ctl = new ChartTemplateController(host, {
    gateway, menu, validTimeframes: VALID_TIMEFRAMES, now,
  });
  return { ctl, menu };
}

const TPL_A = {
  templateId: 'tpl#1',
  name: 'スイング',
  instances: [
    tplInstance({ indicatorId: 'tgp_btlm', params: { window: 25 } }),
    tplInstance({ indicatorId: 'profit_band', visible: false, styles: { band: { color: '#f00' } } }),
  ],
  createdAt: 1000,
  updatedAt: 1000,
};

// ---------------------------------------------------------------------------
// ホスト契約（§7.1・U9）
// ---------------------------------------------------------------------------

test('TC-C01 契約: TEMPLATE_HOST_CONTRACT は凍結され、既存 host が構造的に満たす 12 面を列挙する（§7.1）', () => {
  // Arrange / Act
  const host = fakeHost();
  // Assert
  assert.equal(Object.isFrozen(TEMPLATE_HOST_CONTRACT), true, '契約は Object.freeze で宣言する（既存記法と一致）');
  assert.deepEqual(
    [...TEMPLATE_HOST_CONTRACT.methods].sort(),
    ['_commitState', '_isMarketProfile', '_persistAll', '_removeMarketProfile', '_renderLegend', 'removeInstance'].sort(),
  );
  assert.deepEqual(
    [...TEMPLATE_HOST_CONTRACT.fields].sort(),
    ['_catalog', '_datasetRef', '_meta', '_state', '_store', '_timeframe'].sort(),
  );
  for (const m of TEMPLATE_HOST_CONTRACT.methods) {
    assert.equal(typeof host[m], 'function', `${m} は host のメソッド面`);
  }
  for (const f of TEMPLATE_HOST_CONTRACT.fields) {
    assert.ok(f in host, `${f} は host のフィールド面`);
  }
});

// ---------------------------------------------------------------------------
// UC-T01 保存（§5.1）
// ---------------------------------------------------------------------------

test('TC-C02 保存: 現構成を写像して永続化し、activeTemplateId を設定し現在足へ紐付ける（§5.1）', () => {
  // Arrange
  const host = fakeHost({ applied: [appliedJson({ indicatorId: 'tgp_btlm', params: [['window', 25]] })], timeframe: '1D' });
  const gateway = fakeGateway();
  const { ctl, menu } = build({ host, gateway });
  // Act
  const res = ctl.saveCurrent({ name: 'スイング', bindCurrentTimeframe: true });
  // Assert
  assert.equal(res.ok, true);
  assert.equal(gateway.saved.templates.length, 1);
  assert.deepEqual(gateway.saved.templates[0].instances, [
    { indicatorId: 'tgp_btlm', variant: 'default', params: { window: 25 }, visible: true, styles: null },
  ], 'AppliedInstance -> TEMPLATE_INSTANCE 写像（§4.1）');
  assert.equal(gateway.saved.lastSeq, 1, '採番と同時に templateSeq を永続化する（§4.3）');
  assert.deepEqual(gateway.saved.bindings, { '1D': 'tpl#1' }, '現在足へ紐付ける（既定 ON）');
  assert.equal(host._state.uiState.activeTemplateId, 'tpl#1', 'activeTemplateId を設定する');
  assert.ok(host.log.includes('persistAll'), 'uiState を永続化する');
  assert.ok(menu.renders.length > 0, '保存後にメニューを再描画する（U3）');
});

test('TC-C03 保存: 紐付けチェック OFF なら紐付けを作らない（§5.1 処理 4）', () => {
  // Arrange
  const host = fakeHost({ applied: [], timeframe: '1D' });
  const gateway = fakeGateway();
  const { ctl } = build({ host, gateway });
  // Act
  ctl.saveCurrent({ name: '空構成', bindCurrentTimeframe: false });
  // Assert
  assert.equal(gateway.saved.bindings, null, '紐付けは書き込まない');
});

test('TC-C04 保存: 名前が不正なら保存せず既存データを変更しない（F-T1）', () => {
  // Arrange
  const host = fakeHost({ applied: [] });
  const gateway = fakeGateway();
  const { ctl } = build({ host, gateway });
  // Act
  const res = ctl.saveCurrent({ name: '   ', bindCurrentTimeframe: true });
  // Assert
  assert.equal(res.ok, false);
  assert.equal(gateway.saved.templates, null, 'templates を書き込まない');
  assert.equal(host._state.uiState.activeTemplateId, null, 'activeTemplateId は不変');
});

// ---------------------------------------------------------------------------
// UC-T02 適用（§5.2）
// ---------------------------------------------------------------------------

test('TC-C05 適用: 現構成を全件除去してから宣言順に適用し、activeTemplateId 更新・永続化・凡例再描画を行う（§5.2）', async () => {
  // Arrange
  // seqCounters は既適用インスタンスの採番済み状態（facade.apply 後の実状態）を再現する。
  const host = fakeHost({
    applied: [appliedJson({ indicatorId: 'tgp_btlm', seq: 1 })], timeframe: '1D', seqCounters: { tgp_btlm: 1 },
  });
  const gateway = fakeGateway({ templates: [TPL_A], lastSeq: 1 });
  const { ctl } = build({ host, gateway });
  // Act
  await ctl.applyTemplate('tpl#1');
  // Assert: 除去 → 再構築 の順序
  const removeIdx = host.log.indexOf('remove:tgp_btlm#1');
  const rebuildIdx = host.log.findIndex((l) => l.startsWith('rebuildApplied:'));
  assert.ok(removeIdx >= 0 && rebuildIdx > removeIdx, `除去 → 適用の順序（実際: ${JSON.stringify(host.log)}）`);
  // 再構築は **1 回で全件** 渡す（一括適用・ユーザー指示 2026-08-10）。共有ベースはこのとき初めて
  //   compute を並列発行できる（1 件ずつ渡すと呼び出し側で並列性が消え Σ(compute) になる）。
  //   F-T4 の失敗局所化は共有ベース側（compute/描画の 1 件ごと try/catch・MP は catch 済み）が担う。
  //   宣言順と再採番はそのまま固定する。
  assert.deepEqual(
    host.log.filter((l) => l.startsWith('rebuildApplied:')),
    ['rebuildApplied:tgp_btlm#2,profit_band#1'],
    '宣言順のまま 1 回で全件渡す（一括適用）・instanceId は seqCounters で再採番する（§4.1）',
  );
  assert.deepEqual(
    host._state.applied.map((i) => i.instanceId), ['tgp_btlm#2', 'profit_band#1'],
    '再構築の前に state へ在席させる（styles 再適用の事前条件）',
  );
  assert.equal(host._state.applied[1].visible, false, 'visible を復元する');
  assert.deepEqual(host._state.applied[1].styles, { band: { color: '#f00' } }, 'styles を復元する');
  assert.equal(host._state.uiState.activeTemplateId, 'tpl#1', 'activeTemplateId を更新する（手順 5）');
  const legendIdx = host.log.lastIndexOf('renderLegend');
  assert.ok(legendIdx > rebuildIdx, '凡例再描画は協働子が最後に行う（手順 6）');
  assert.ok(host.log.lastIndexOf('persistAll') > rebuildIdx, '適用後に永続化する（手順 5）');
});

test('TC-C23 適用: 再構築の呼び出しは 1 回だけ（一括適用＝呼び出し側で並列性を打ち消さない）', async () => {
  // Arrange: 4 件のテンプレート（1 件ずつ呼ぶ旧実装なら 4 回・一括なら 1 回）
  const tpl = {
    templateId: 'tpl#7',
    name: '4 件',
    instances: [
      tplInstance({ indicatorId: 'tgp_btlm' }),
      tplInstance({ indicatorId: 'profit_band' }),
      tplInstance({ indicatorId: 'tgp_btlm' }),
      tplInstance({ indicatorId: 'profit_band' }),
    ],
    createdAt: 1, updatedAt: 1,
  };
  const host = fakeHost({ applied: [] });
  const { ctl } = build({ host, gateway: fakeGateway({ templates: [tpl], lastSeq: 7 }) });
  // Act
  await ctl.applyTemplate('tpl#7');
  // Assert: 呼び出しは 1 回・渡す配列は宣言順の全件（共有ベースはこれで compute を並列発行できる。
  //   1 件ずつ渡すと適用所要が Σ(compute) になる＝実測 8 件で直列 29.8 秒 / 並列 8.3 秒）
  const calls = host.log.filter((l) => l.startsWith('rebuildApplied:'));
  assert.equal(calls.length, 1, '再構築は 1 回だけ呼ぶ（一括適用）');
  assert.deepEqual(
    calls, ['rebuildApplied:tgp_btlm#1,profit_band#1,tgp_btlm#2,profit_band#2'],
    '宣言順の全件を 1 回で渡す',
  );
});

test('TC-C24 適用: 再構築が失敗しても永続化と凡例再描画は実行する（D-1 の構成消失を作らない）', async () => {
  // Arrange: 再構築が reject する host（共有ベースは reject しない設計だが、協働子側の
  //   完遂保証がそれに依存していないことを固定する）
  const host = fakeHost({ applied: [appliedJson({ indicatorId: 'tgp_btlm', seq: 1 })], seqCounters: { tgp_btlm: 1 } });
  host._store.rebuildApplied = async () => { throw new Error('boom'); };
  const { ctl } = build({ host, gateway: fakeGateway({ templates: [TPL_A], lastSeq: 1 }) });
  const warns = [];
  ctl._warn = (m) => warns.push(m);
  // Act
  const ok = await ctl.applyTemplate('tpl#1');
  // Assert
  assert.equal(ok, true, '適用は完遂する（再構築の失敗で中止しない）');
  assert.equal(host._state.uiState.activeTemplateId, 'tpl#1', '手順 5: activeTemplateId を更新する');
  assert.ok(host.log.includes('persistAll'), '手順 5: 永続化する（空構成 [] を最終値にしない）');
  assert.ok(host.log.includes('renderLegend'), '手順 6: 凡例を再描画する');
  assert.equal(warns.length, 1, '失敗は警告で残す');
});

test('TC-C06 適用: MP は宣言順の先頭 1 件のみ適用する（§5.2・E-8）', async () => {
  // Arrange
  const tpl = {
    templateId: 'tpl#9',
    name: 'MP 2 件',
    instances: [
      tplInstance({ indicatorId: 'market_profile' }),
      tplInstance({ indicatorId: 'market_profile' }),
      tplInstance({ indicatorId: 'tgp_btlm' }),
    ],
    createdAt: 1, updatedAt: 1,
  };
  const host = fakeHost({ applied: [] });
  const { ctl } = build({ host, gateway: fakeGateway({ templates: [tpl], lastSeq: 9 }) });
  // Act
  await ctl.applyTemplate('tpl#9');
  // Assert
  assert.deepEqual(
    host._state.applied.map((i) => i.instanceId), ['market_profile#1', 'tgp_btlm#1'],
    '2 件目以降の MP は無視する（単一インスタンス制約）',
  );
});

test('TC-C07 適用: 除去は MP 経路と通常経路へ協働子自身が分岐する（§5.2 手順 1・多態に依存しない）', async () => {
  // Arrange
  const host = fakeHost({
    applied: [
      appliedJson({ indicatorId: 'market_profile' }),
      appliedJson({ indicatorId: 'tgp_btlm' }),
    ],
  });
  const { ctl } = build({ host, gateway: fakeGateway({ templates: [TPL_A], lastSeq: 1 }) });
  // Act
  await ctl.applyTemplate('tpl#1');
  // Assert
  assert.ok(host.log.includes('removeMp:market_profile#1'), 'MP は _removeMarketProfile へ');
  assert.ok(host.log.includes('remove:tgp_btlm#1'), '非 MP は removeInstance へ');
});

test('TC-C08 適用: 対象 templateId が不在なら何もせず、当該紐付けを削除して永続化する（F-T3）', async () => {
  // Arrange
  const host = fakeHost({ applied: [appliedJson({ indicatorId: 'tgp_btlm' })] });
  const gateway = fakeGateway({ templates: [], bindings: { '1D': 'tpl#9' } });
  const { ctl } = build({ host, gateway });
  // Act
  await ctl.applyTemplate('tpl#9');
  // Assert
  assert.equal(host.log.some((l) => l.startsWith('remove')), false, '構成は変更しない');
  assert.deepEqual(gateway.saved.bindings, {}, 'dangling 紐付けを削除して永続化する');
});

// ---------------------------------------------------------------------------
// UC-T03 紐付け（§5.3）
// ---------------------------------------------------------------------------

test('TC-C09 紐付け: 設定・解除は永続化するが構成を変更しない（§5.3）', () => {
  // Arrange
  const host = fakeHost({ applied: [appliedJson({ indicatorId: 'tgp_btlm' })], timeframe: '5m' });
  const gateway = fakeGateway({ templates: [TPL_A], lastSeq: 1 });
  const { ctl } = build({ host, gateway });
  // Act
  ctl.bindCurrentTimeframe('tpl#1');
  // Assert
  assert.deepEqual(gateway.saved.bindings, { '5m': 'tpl#1' });
  assert.equal(host.log.some((l) => l.startsWith('rebuildApplied')), false, '適用は行わない');
  // Act: 解除
  ctl.bindCurrentTimeframe(null);
  // Assert
  assert.deepEqual(gateway.saved.bindings, {}, '解除は当該キーを削除する');
});

// ---------------------------------------------------------------------------
// UC-T04 切替時自動適用（§5.4）
// ---------------------------------------------------------------------------

test('TC-C10 切替: 紐付けが無い足は現行挙動を維持する（除去も適用もせず切替のみ）（§5.4）', async () => {
  // Arrange
  const host = fakeHost({ applied: [appliedJson({ indicatorId: 'tgp_btlm' })], timeframe: '1D' });
  const { ctl } = build({ host, gateway: fakeGateway({ templates: [TPL_A] }) });
  const proceeded = [];
  // Act
  await ctl.onTimeframeChange('5m', async (tf) => { proceeded.push(tf); host.log.push(`proceed:${tf}`); });
  // Assert
  assert.deepEqual(proceeded, ['5m'], '既存の時間足切替は実行する');
  assert.deepEqual(host.log, ['proceed:5m'], '除去も適用も行わない（現行挙動不変）');
});

test('TC-C11 切替: 紐付けありは 除去 → 切替 → 適用 の順で 1 回だけ実行する（§5.4 適用手順）', async () => {
  // Arrange
  const host = fakeHost({
    applied: [appliedJson({ indicatorId: 'tgp_btlm', seq: 1 })], timeframe: '1D', seqCounters: { tgp_btlm: 1 },
  });
  const gateway = fakeGateway({ templates: [TPL_A], bindings: { '5m': 'tpl#1' }, lastSeq: 1 });
  const { ctl } = build({ host, gateway });
  // Act
  await ctl.onTimeframeChange('5m', async (tf) => { host._timeframe = tf; host.log.push(`proceed:${tf}`); });
  // Assert
  const order = host.log.filter((l) => l.startsWith('remove:') || l.startsWith('proceed:') || l.startsWith('rebuildApplied:'));
  assert.deepEqual(
    order,
    ['remove:tgp_btlm#1', 'proceed:5m', 'rebuildApplied:tgp_btlm#2,profit_band#1'],
    '除去（計算なし）→ 切替 → 新構成を新しい足で適用（再構築は 1 回で全件＝一括適用）',
  );
  const rebuilt = order.filter((l) => l.startsWith('rebuildApplied:'));
  assert.equal(new Set(rebuilt).size, rebuilt.length, '同一インスタンスを二重に適用しない（適用は 1 回だけ）');
  // 「新構成に対して計算は 1 回のみ」（受入基準 3）の計算回数そのものは、実 controller を通す
  //   結線テスト（chart_template_persistence_integration.test.js TC-P02）が compute 呼び出しで固定する。
  assert.equal(host._state.uiState.activeTemplateId, 'tpl#1');
});

test('TC-C20 切替: 現在足と同じ項目のクリックは既存挙動どおり no-op（除去も適用もしない）（§5.4 発火条件 1）', async () => {
  // Arrange: 現在足 5m に紐付けが在り、手動適用の構成が在席（activeTemplateId は未設定）
  const host = fakeHost({ applied: [appliedJson({ indicatorId: 'profit_band' })], timeframe: '5m' });
  const gateway = fakeGateway({ templates: [TPL_A], bindings: { '5m': 'tpl#1' }, lastSeq: 1 });
  const { ctl } = build({ host, gateway });
  // Act: 時間足メニューで現在足（5m）をクリックする（indicator_controller.js:787 が全項目へ配線）
  await ctl.onTimeframeChange('5m', async (tf) => { host.log.push(`proceed:${tf}`); });
  // Assert: 切替が発生しない操作は発火条件 1 を満たさない＝既存挙動（timeframe_controller.js:63-65 の
  //   同一性ガードで no-op）へ委譲するだけで、構成の除去・置換・永続化は起きない
  assert.deepEqual(host.log, ['proceed:5m'], '除去も適用も永続化もしない');
  assert.deepEqual(
    host._state.applied.map((i) => i.instanceId), ['profit_band#1'],
    '現構成が置換されない（§5.3「紐付け操作そのものは構成を変更しない」の迂回を作らない）',
  );
  assert.equal(host._state.uiState.activeTemplateId, null, 'activeTemplateId も変えない');
});

test('TC-C12 切替: 紐付け先が activeTemplateId と同一なら適用しない（§5.4 発火条件 3）', async () => {
  // Arrange
  const host = fakeHost({ applied: [appliedJson({ indicatorId: 'tgp_btlm' })], timeframe: '1D' });
  host._state.uiState.activeTemplateId = 'tpl#1';
  const gateway = fakeGateway({ templates: [TPL_A], bindings: { '5m': 'tpl#1' }, lastSeq: 1 });
  const { ctl } = build({ host, gateway });
  // Act
  await ctl.onTimeframeChange('5m', async (tf) => { host.log.push(`proceed:${tf}`); });
  // Assert
  assert.deepEqual(host.log, ['proceed:5m'], '同一テンプレート由来のままなら構成を置換しない（決定論性）');
});

test('TC-C13 切替: 自動適用の実行中に発生した切替要求は無視する（§5.4 再入防止）', async () => {
  // Arrange
  const host = fakeHost({ applied: [], timeframe: '1D' });
  const gateway = fakeGateway({ templates: [TPL_A], bindings: { '5m': 'tpl#1', '15m': 'tpl#1' }, lastSeq: 1 });
  const { ctl } = build({ host, gateway });
  const proceeded = [];
  let reentrant = null;
  // Act: proceed の最中に別の切替要求を出す（再入）。
  await ctl.onTimeframeChange('5m', async (tf) => {
    proceeded.push(tf);
    reentrant = await ctl.onTimeframeChange('15m', async (tf2) => { proceeded.push(tf2); });
  });
  // Assert
  assert.deepEqual(proceeded, ['5m'], '再入した切替要求は無視する（proceed も呼ばない）');
  assert.equal(reentrant, undefined, '再入時は何も返さず即 return する');
});

test('TC-C14 切替: 集合外の時間足キーは解決対象にせず削除もしない（F-T6）', async () => {
  // Arrange
  const host = fakeHost({ applied: [], timeframe: '1D' });
  const gateway = fakeGateway({ templates: [TPL_A], bindings: { '2h': 'tpl#1' }, lastSeq: 1 });
  const { ctl } = build({ host, gateway });
  // Act
  await ctl.onTimeframeChange('2h', async (tf) => { host.log.push(`proceed:${tf}`); });
  // Assert
  assert.deepEqual(host.log, ['proceed:2h'], '集合外キーでは自動適用しない');
  assert.equal(gateway.saved.bindings, null, '集合外キーは削除しない（将来足の温存）');
});

// ---------------------------------------------------------------------------
// UC-T05 改名・削除（§5.5）
// ---------------------------------------------------------------------------

test('TC-C15 改名: 検証を通れば永続化し、重複は拒否して既存を変更しない（§5.5）', () => {
  // Arrange
  const other = { templateId: 'tpl#2', name: 'デイトレ', instances: [], createdAt: 1, updatedAt: 1 };
  const host = fakeHost();
  const gateway = fakeGateway({ templates: [TPL_A, other], lastSeq: 2 });
  const { ctl } = build({ host, gateway });
  // Act
  const ok = ctl.renameTemplate('tpl#1', 'スイング改');
  const ng = ctl.renameTemplate('tpl#1', 'デイトレ');
  // Assert
  assert.equal(ok.ok, true);
  assert.equal(gateway.saved.templates[0].name, 'スイング改');
  assert.equal(gateway.saved.templates[0].updatedAt, 2000, 'updatedAt を進める');
  assert.equal(ng.ok, false, '他テンプレートとの正規化名重複は拒否する');
  assert.equal(gateway.saved.templates[0].name, 'スイング改', '拒否時は既存を変更しない');
});

test('TC-C16 削除: 紐付けと activeTemplateId を掃除し、現在の構成は変更しない（§5.5）', () => {
  // Arrange
  const host = fakeHost({ applied: [appliedJson({ indicatorId: 'tgp_btlm' })], timeframe: '1D' });
  host._state.uiState.activeTemplateId = 'tpl#1';
  const gateway = fakeGateway({ templates: [TPL_A], bindings: { '1D': 'tpl#1', '5m': 'tpl#2' }, lastSeq: 1 });
  const { ctl } = build({ host, gateway });
  // Act
  ctl.deleteTemplate('tpl#1');
  // Assert
  assert.deepEqual(gateway.saved.templates, [], 'テンプレートを削除する');
  assert.deepEqual(gateway.saved.bindings, { '5m': 'tpl#2' }, '当該 id を参照する紐付けを削除する');
  assert.equal(host._state.uiState.activeTemplateId, null, 'activeTemplateId を null にする');
  assert.deepEqual(host._state.applied.map((i) => i.instanceId), ['tgp_btlm#1'], '現在チャート上の構成は変更しない');
  assert.equal(gateway.saved.lastSeq, null, 'lastSeq は減算・再発行しない（§4.3）');
});

// ---------------------------------------------------------------------------
// ビューモデル（U3・U6）
// ---------------------------------------------------------------------------

test('TC-C21 上書き: 保存ダイアログへ既存テンプレート判定器（usecase 純関数）を渡す（上書き確認の判定源）', () => {
  // Arrange
  const host = fakeHost({ applied: [], timeframe: '1D' });
  const opened = [];
  const dialogs = { openSave: (arg) => opened.push(arg), openManage: () => {} };
  const ctl = new ChartTemplateController(host, {
    gateway: fakeGateway({ templates: [TPL_A], lastSeq: 1 }), dialogs,
    validTimeframes: VALID_TIMEFRAMES, now: () => 2000,
  });
  // Act
  ctl.openSaveDialog();
  // Assert
  assert.equal(typeof opened[0].findExisting, 'function', '判定器を注入する（ダイアログは文字列比較を持たない）');
  assert.equal(opened[0].findExisting('  すいんぐ  '), null, '正規化名が一致しなければ null');
  assert.equal(opened[0].findExisting('  スイング  ').templateId, 'tpl#1', '前後空白を吸収して既存を返す');
});

test('TC-C22 上書き: templateId と紐付けが保持され createdAt 不変・updatedAt が進む（§5.1 処理 2・値の扱いは不変）', () => {
  // Arrange: tpl#1 が 1D へ紐付け済み・現構成は 1 件
  const host = fakeHost({ applied: [appliedJson({ indicatorId: 'tgp_btlm' })], timeframe: '1D' });
  const gateway = fakeGateway({ templates: [TPL_A], bindings: { '1D': 'tpl#1' }, lastSeq: 1 });
  const { ctl } = build({ host, gateway });
  // Act: 同名（正規化一致）で保存＝上書き
  const res = ctl.saveCurrent({ name: '  すいんぐ  '.replace('すいんぐ', 'スイング'), bindCurrentTimeframe: true });
  // Assert
  assert.equal(res.ok, true);
  assert.equal(res.templateId, 'tpl#1', 'templateId を保持する');
  assert.equal(gateway.saved.templates.length, 1, '新規追加ではなく上書き');
  assert.equal(gateway.saved.templates[0].createdAt, 1000, 'createdAt は不変');
  assert.equal(gateway.saved.templates[0].updatedAt, 2000, 'updatedAt を更新する');
  assert.deepEqual(
    gateway.saved.templates[0].instances.map((i) => i.indicatorId), ['tgp_btlm'],
    'instances は現在の構成で置換する',
  );
  assert.deepEqual(gateway.saved.bindings, { '1D': 'tpl#1' }, '紐付けは保持される');
  assert.equal(gateway.saved.lastSeq, null, '上書きでは採番しない');
  assert.equal(host._state.uiState.activeTemplateId, 'tpl#1', 'activeTemplateId は当該 id に設定する');
});

test('TC-C19 保存ダイアログ入口: 現在足と保存対象の指標名（凡例と同一表記）を dialogs へ渡す（§6.2）', () => {
  // Arrange
  const host = fakeHost({
    applied: [
      appliedJson({ indicatorId: 'tgp_btlm' }),
      appliedJson({ indicatorId: 'profit_band', variant: 'robust' }),
    ],
    timeframe: '1D',
  });
  host._catalog = { get: (id) => (id === 'tgp_btlm' ? { ...DEF_LINE, displayNameKey: 'ind.tgp_btlm' } : { ...DEF_BAND, displayNameKey: 'ind.profit_band' }) };
  const opened = [];
  const dialogs = { openSave: (arg) => opened.push(arg), openManage: () => {} };
  const ctl = new ChartTemplateController(host, {
    gateway: fakeGateway(), dialogs, validTimeframes: VALID_TIMEFRAMES, now: () => 2000,
  });
  // Act
  ctl.openSaveDialog();
  // Assert
  assert.equal(opened.length, 1);
  assert.equal(opened[0].timeframeLabel, '1D');
  assert.deepEqual(opened[0].indicatorNames, ['tgp_btlm', 'profit_band (robust)'],
    'displayNameKey の末尾＝凡例（_label）と同一表記・非既定 variant を併記');
});

test('TC-C18 結線: attachUi は後から注入されたメニューへ現在のビューモデルを描画する（生成順の吸収）', () => {
  // Arrange: menu 未注入で生成（replay composition root は協働子がメニューより先に生成される）。
  const host = fakeHost();
  const gateway = fakeGateway({ templates: [TPL_A], bindings: { '1D': 'tpl#1' }, lastSeq: 1 });
  const ctl = new ChartTemplateController(host, { gateway, validTimeframes: VALID_TIMEFRAMES });
  const menu = fakeMenu();
  // Act
  ctl.attachUi({ menu });
  // Assert
  assert.deepEqual(menu.renders, [{ templates: [TPL_A], bindings: { '1D': 'tpl#1' }, activeTemplateId: null, timeframe: '1D' }]);
});

test('TC-C17 ビューモデル: templates / bindings / activeTemplateId を返す（メニューの再描画元）', () => {
  // Arrange
  const host = fakeHost();
  host._state.uiState.activeTemplateId = 'tpl#1';
  const { ctl } = build({ host, gateway: fakeGateway({ templates: [TPL_A], bindings: { '1D': 'tpl#1' }, lastSeq: 1 }) });
  // Act
  const vm = ctl.viewModel();
  // Assert
  assert.deepEqual(vm.templates, [TPL_A]);
  assert.deepEqual(vm.bindings, { '1D': 'tpl#1' });
  assert.equal(vm.activeTemplateId, 'tpl#1');
  assert.equal(vm.timeframe, '1D', '「● = 現在足に紐付け」印の判定に使う現在足（§6.2）');
});
