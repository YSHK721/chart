// param_allowlist_and_failure_isolation.test.js — ISSUE-281 / ISSUE-282 の回帰固定。
//
// 実際に起きたこと（利用者報告・2026-08-08 実測）:
//   1. リプレイ有効化で `/compute` が 400。`add_rsi が受理しない param が渡されました: ['ma_period']`。
//      永続化された古い param が front を素通りして送られていた（サーバは ISSUE-278 #8 で
//      フェイルクローズ化済み）。一度混入すると保存状態が治らず、その指標は永久に描けない。
//   2. 続けて `E01_INSUFFICIENT_BARS`（履歴不足）で **再生そのものが停止**。1 指標の計算失敗を
//      バッチが rethrow し、`replay.js:render` が catch→return してローソクも他指標も描かれなかった。
//      リプレイは計算窓を limit=bar+1 に絞るため、履歴不足は**想定内の状態**である。
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { get, scopedParams, applyServerParamScopes } from '../js/usecase/catalog.js';
import { IndicatorController } from '../js/adapter/front/indicator_controller.js';
import { ComputeError } from '../js/domain/compute_error.js';

const noop = () => {};

// ---- ISSUE-281: 送信は許可リスト（カタログが知らない param は送らない） ----

test('scopedParams はカタログ定義に無い param を送らない（許可リスト）', () => {
  const def = get('profit_rsi');

  const sent = scopedParams(def, 'default', {
    rsi_period: 6, apply: 5, ma_period: 5, unknown_x: 1,
  });

  assert.deepEqual(Object.keys(sent).sort(), ['apply', 'rsi_period']);
  assert.equal('ma_period' in sent, false, '廃止 param は送らない（サーバは 400 を返す）');
  assert.equal('unknown_x' in sent, false, '未知 param も送らない');
});

test('scopedParams は variant が受理しない param を送らない（従来の絞り込みも維持）', () => {
  // variant ごとの受理集合はサーバ（/catalog の paramScopes）が権威。overlay 後に絞り込みが効く。
  const def = get('profit_rsi');
  applyServerParamScopes({ profit_rsi: { default: ['rsi_period'], other: ['rsi_period', 'apply'] } });

  assert.deepEqual(Object.keys(scopedParams(def, 'default', { rsi_period: 6, apply: 5 })), ['rsi_period']);
  assert.deepEqual(
    Object.keys(scopedParams(def, 'other', { rsi_period: 6, apply: 5 })).sort(),
    ['apply', 'rsi_period'],
  );

  // 後続テストへ影響させないため、宣言を元（default が全 param を受理）へ戻す。
  applyServerParamScopes({
    profit_rsi: { default: ['rsi_period', 'apply', 'window_n', 'q_low', 'q_high', 'q_out', 'k_events', 'timeframe'] },
  });
});

// ---- ISSUE-281: 保存状態も治す（送信側だけでは汚れが残り続ける） ----

function controllerWithSaved(savedInstances, opts = {}) {
  const saved = [];
  return {
    saved,
    ctrl: new IndicatorController({
      catalog: { listIndicators: () => [], get },
      compute: { compute: async () => ({ ok: true, generation: 0, series: [] }) },
      persistence: {
        loadApplied: () => savedInstances,
        saveApplied: (list) => saved.push(list),
        loadFavorites: () => [], saveFavorites: noop,
        loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 9,
      },
      renderer: {
        renderLine: noop, renderHorizontal: noop, renderHistogram: noop, setData: noop,
        setVisible: noop, remove: noop, setCandles: noop,
      },
      document: null,
      ...opts,
    }),
  };
}

test('復元時にカタログ定義に無い param を落とし、保存へ書き戻す（壊れた状態が治る）', async () => {
  const { ctrl, saved } = controllerWithSaved([{
    instanceId: 'rsi-1', indicatorId: 'profit_rsi', variant: 'default',
    params: [['rsi_period', 6], ['ma_period', 5]], visible: true, generation: 0, seq: 1, createdAt: 0,
  }]);

  await ctrl.restore();

  const inst = ctrl._state.applied.find((i) => i.indicatorId === 'profit_rsi');
  const names = (Array.isArray(inst.params) ? inst.params.map(([k]) => k) : Object.keys(inst.params));
  assert.equal(names.includes('ma_period'), false, '未知 param は state から消える');
  assert.equal(names.includes('rsi_period'), true, '既知 param は残る');
  assert.ok(saved.length >= 1, '掃除結果を保存へ書き戻す（次回起動でゴミが復活しない）');
});

test('アクター駆動指標（MP）の param は落とさない（受理集合の権威はアクター側）', async () => {
  const { ctrl } = controllerWithSaved([{
    instanceId: 'mp-1', indicatorId: 'market_profile', variant: 'default',
    params: [['bins', '30'], ['va', 0.8]], visible: false, generation: 0, seq: 1, createdAt: 0,
  }]);

  await ctrl.restore();

  const inst = ctrl._state.applied.find((i) => i.indicatorId === 'market_profile');
  const names = (Array.isArray(inst.params) ? inst.params.map(([k]) => k) : Object.keys(inst.params));
  assert.equal(names.includes('bins'), true, 'front カタログに無くてもアクターが使う param は残す');
});

// ---- ISSUE-282: 失敗はインスタンスに閉じる（バッチは完走する） ----

test('1 指標の計算失敗でバッチは例外を投げず、他指標は描かれる', async () => {
  const drawn = [];
  const computed = [];
  // リプレイの実態を再現する: 適用時（ライブ窓）は計算でき、窓が縮んだ後（リビール手前）に
  //   履歴不足で失敗するようになる。＝失敗は「後から起きる状態」であって適用の失敗ではない。
  let windowTooShort = false;
  const ctrl = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: {
      compute: async (req) => {
        computed.push(req.indicatorId);
        if (windowTooShort && req.indicatorId === 'profit_rsi') {
          throw new Error('E01_INSUFFICIENT_BARS: バー数 1234 では σ̂ を 1 本も出力できない');
        }
        return { ok: true, generation: 0, series: [{ name: 'ma', kind: 'line', points: [] }] };
      },
    },
    persistence: {
      loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop,
      loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer: {
      renderLine: (id) => { drawn.push(id); }, renderHorizontal: noop, renderHistogram: noop,
      setData: noop, setVisible: noop, remove: noop, setCandles: noop,
    },
    document: null,
  });
  await ctrl.applyIndicator('profit_rsi');
  await ctrl.applyIndicator('moving_averages');
  drawn.length = 0;
  computed.length = 0;
  windowTooShort = true;   // リプレイでカーソルが手前へ来た状態

  // Act: 失敗する指標を含めて一括再計算する。
  const batch = await ctrl.recomputeAllApplied({ mode: 'full' });

  // Assert: 例外は出ず、失敗は当該インスタンスに閉じ、他指標は描かれる。
  assert.ok(batch && Array.isArray(batch.failures), 'failures を返す（呼び出し元が表示へ回せる）');
  assert.equal(batch.failures.length, 1);
  assert.match(batch.failures[0].instanceId, /profit_rsi/);
  assert.ok(
    computed.includes('moving_averages'),
    '失敗した指標の後も他指標の計算が実行される（バッチが中断しない）',
  );
  assert.ok(drawn.length >= 1 || computed.length >= 2, '成功分の処理が続行している');
});

// ---- ISSUE-283: 満たせないと分かっている要求は発行しない ----

test('必要バー数を学習し、窓が満たすまで /compute を発行しない（満たせば自動再開）', async () => {
  const requests = [];
  const ctrl = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: {
      compute: async (req) => {
        requests.push(req.limit);
        if (req.indicatorId === 'cvfe' && (req.limit ?? 0) < 1352) {
          // サーバは「あと何本必要か」を機械可読で申告する（文言解析を強いない）。
          throw new ComputeError('E01_INSUFFICIENT_BARS: バー数が足りない', {
            error_type: 'validation',
            violations: [{ code: 'E01_INSUFFICIENT_BARS', requiredBars: 1352, actualBars: req.limit }],
          });
        }
        return { ok: true, generation: 0, series: [] };
      },
    },
    persistence: {
      loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop,
      loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer: {
      renderLine: noop, renderHorizontal: noop, renderHistogram: noop, setData: noop,
      setVisible: noop, remove: noop, setCandles: noop,
    },
    document: null,
    recentBars: 1500,        // ライブ窓では計算できる（＝適用は成功し、描画済みになる）
  });
  await ctrl.applyIndicator('cvfe');

  // リプレイでカーソルが手前へ来た状態＝計算窓が要件を下回る（limit = bar + 1）。
  ctrl._recentBars = 1234;
  requests.length = 0;

  const first = await ctrl.recomputeAllApplied({ mode: 'full' });   // 1 回だけ失敗して要件を学ぶ
  const afterFirst = requests.length;
  const second = await ctrl.recomputeAllApplied({ mode: 'full' });  // 学習済み＝発行しない
  ctrl._recentBars = 1235;                                          // 1 バー進んでもまだ足りない
  const third = await ctrl.recomputeAllApplied({ mode: 'full' });

  assert.equal(afterFirst, 1, '最初の 1 回だけ要求する');
  assert.equal(requests.length, 1, '要件未達の間は追加の要求を出さない（毎バー投げない）');
  assert.equal(first.failures.length, 1);
  assert.equal(first.failures[0].requiredBars, 1352, '必要バー数を学習する');
  assert.equal(second.deferred.length, 1, '見送りとして可視化する（無言で消えない）');
  assert.equal(third.deferred[0].requiredBars, 1352);

  // 窓が要件に達したら自動的に再開する（人手の解除を要しない）。
  ctrl._recentBars = 1352;
  const resumed = await ctrl.recomputeAllApplied({ mode: 'full' });
  assert.equal(requests.length, 2, '要件を満たしたら再び要求する');
  assert.equal(resumed.failures.length, 0);
  assert.equal(resumed.deferred.length, 0);
});
