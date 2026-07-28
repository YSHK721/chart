// IndicatorStateStore への公開入口追加（§7.2 S2 承認済み）の Red テスト。
//
// 設計入力（唯一の仕様源）: .doc/indicator-management-ui/基本設計_チャートテンプレート.md v0.1.1
//   §7.2（_restoreRun を「保存状態の読込・時間足復元」部（indicator_state_store.js:73-94）と
//        「applied 配列からの再構築ループ」部（:96-121）へ分割し、後者を applied 配列を引数に取る
//        公開入口として切り出す。抽出範囲は再構築ループのみで、時間足復元部（:81-90）は含めない。
//        既存の呼び出し面（restore / setTimeframe / controller._persistAll / 既存テスト）は不変＝挙動不変）、
//   §5.2（MP 種別は MP 復元経路へ委譲）、§5.6 F-T4（カタログ非在席・compute 例外は当該 1 件のみ
//        スキップし残りの適用と描画を継続する）、§4.1（params はペア配列→オブジェクトへ正規化）。
// 参照実装: js/adapter/front/indicator_state_store.js:96-121（現行 _restoreRun の再構築ループ）。
// 構造: Arrange-Act-Assert（AAA）。DOM 非依存の host スタブ。
//
// ★ 本ファイルは Red フェーズ専用。公開入口は未実装。
//
// ★ 仮名: 入口名は設計書 §7.2 が「例 rebuildApplied(appliedList)」と示すのみで確定していない。
//   実装フェーズで確定する。本テストは ENTRY 定数 1 箇所で入口名を束ねる。
//
// ★ 本テストが検証しないもの（設計書に定義が無い／挙動不変抽出と衝突するため・報告書参照）:
//   - MP 単一インスタンス制約「宣言順の先頭 1 件のみ適用」（§5.2）。現行ループは MP 全件に
//     restoreInstance を呼ぶため、この絞り込みを入口へ入れると §7.2 の「挙動不変」抽出に反する。
//     絞り込みの所在（usecase 側の写像でフィルタするか入口側か）が設計書で未定義。
//   - 入口が _renderLegend / _renderDialogList（:122-123）を含むか（§7.2 の分割範囲外・未定義）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { IndicatorStateStore } from '../js/adapter/front/indicator_state_store.js';

// 公開入口の仮名（§7.2 の例示）。実装フェーズで確定する。
const ENTRY = 'rebuildApplied';

// AppliedInstance の保存形（indicator_state_store.js:43-55 toJson と同形）。
function inst({ instanceId, indicatorId, variant = 'default', params = {}, visible = true, generation = 3, styles = null }) {
  return { instanceId, indicatorId, variant, params, visible, generation, seq: 1, createdAt: 't0', styles };
}

// 再構築ループが参照する host 面のみを備えた最小スタブ（indicator_state_store.js の host 契約）。
function fakeHost({ defs = {}, computeFails = new Set(), savedTimeframe = '1m', timeframe = '1D' } = {}) {
  const log = [];
  const host = {
    log,
    _datasetRef: 'jp225:1m',
    _timeframe: timeframe,
    _meta: new Map(),
    _state: { applied: [], favorites: [], uiState: { timeframe: savedTimeframe } },
    _catalog: { get: (id) => defs[id] ?? null },
    _persistence: {
      loadApplied: () => [], loadFavorites: () => [], loadUiState: () => ({ timeframe: savedTimeframe }),
      saveApplied: () => {}, saveFavorites: () => {}, saveUiState: () => {},
    },
    _renderer: {
      setVisible: (id, on) => log.push(`setVisible:${id}:${on}`),
      setCandles: () => log.push('setCandles'),
    },
    _mp: { restoreInstance: async (i) => { log.push(`mp.restoreInstance:${i.instanceId}`); } },
    _isMarketProfile: (def) => !!(def && def.isMp),
    _paramsObject: (params) => (Array.isArray(params) ? Object.fromEntries(params) : (params ?? {})),
    _gatewayAdapter: (variant) => ({
      compute: async (req) => {
        log.push(`compute:${req.indicatorId}:${variant}:${JSON.stringify(req.params)}`);
        if (computeFails.has(req.indicatorId)) {
          throw new Error(`compute failed: ${req.indicatorId}`);
        }
        return { ok: true, generation: req.generation, series: [{ name: `${req.indicatorId}_s`, kind: 'line', data: [] }] };
      },
    }),
    _commitLastSeries: (series) => log.push(`commitLastSeries:${series.map((s) => s.name).join(',')}`),
    _commitState: (s) => { host._state = s; log.push('commitState'); },
    _commitTimeframe: (tf) => log.push(`commitTimeframe:${tf}`),
    _loadCandles: async (ref, tf) => { log.push(`loadCandles:${tf}`); return []; },
    _draw: (instanceId, def, series, params) => log.push(`draw:${instanceId}:${JSON.stringify(params)}`),
    _syncTimeframeButtons: () => log.push('syncTimeframeButtons'),
    _timeframeObserver: (tf) => log.push(`observer:${tf}`),
    _renderLegend: () => log.push('renderLegend'),
    _renderDialogList: () => log.push('renderDialogList'),
  };
  return host;
}

const DEF_LINE = { id: 'tgp_btlm', compute: { computeId: 'tgp_btlm' } };
const DEF_BAND = { id: 'profit_band', compute: { computeId: 'profit_band' } };
const DEF_MP = { id: 'market_profile', isMp: true, compute: { computeId: 'market_profile' } };

// 入口の在席を明示的に失敗させる（Red の理由を「未実装」と一意に示す）。
function entryOf(store) {
  assert.equal(typeof store[ENTRY], 'function',
    `公開入口 ${ENTRY}(appliedList) が未実装（§7.2 S2: 再構築ループの抽出＋公開入口 1 個の追加）`);
  return store[ENTRY].bind(store);
}

// ---------------------------------------------------------------------------
// 入口の契約（§7.2）
// ---------------------------------------------------------------------------

test('TC-R01 入口は引数の applied 配列を再構築する（host._state.applied を読まない）（§7.2）', async () => {
  // Arrange
  const host = fakeHost({ defs: { tgp_btlm: DEF_LINE, profit_band: DEF_BAND } });
  host._state.applied = [inst({ instanceId: 'stale#1', indicatorId: 'profit_band' })];
  const store = new IndicatorStateStore(host);
  const list = [inst({ instanceId: 'tgp_btlm#1', indicatorId: 'tgp_btlm', params: [['window', 25]] })];
  // Act
  await entryOf(store)(list);
  // Assert
  assert.ok(host.log.some((l) => l.startsWith('draw:tgp_btlm#1')), '引数の構成を描画する');
  assert.equal(host.log.some((l) => l.includes('stale#1')), false, 'host._state.applied は参照しない');
  assert.deepEqual(
    host.log.filter((l) => l.startsWith('compute:')),
    ['compute:tgp_btlm:default:{"window":25}'],
    'params はペア配列→オブジェクトへ正規化して compute へ渡す（§4.1）',
  );
});

test('TC-R02 入口は時間足復元を含まない（_commitTimeframe / _loadCandles / setCandles を呼ばない）（§7.2）', async () => {
  // Arrange: 保存時間足（1m）が現在足（1D）と異なる＝_restoreRun なら時間足復元が走る条件
  const host = fakeHost({ defs: { tgp_btlm: DEF_LINE }, savedTimeframe: '1m', timeframe: '1D' });
  const store = new IndicatorStateStore(host);
  // Act
  await entryOf(store)([inst({ instanceId: 'tgp_btlm#1', indicatorId: 'tgp_btlm' })]);
  // Assert
  assert.equal(host.log.some((l) => l.startsWith('commitTimeframe')), false, '時間足を確定しない');
  assert.equal(host.log.some((l) => l.startsWith('loadCandles')), false, 'candles を再取得しない');
  assert.equal(host.log.includes('setCandles'), false, 'メイン系列を差し替えない');
});

test('TC-R03 MP 種別は compute を通さず _mp.restoreInstance へ委譲する（§5.2・E-8）', async () => {
  // Arrange
  const host = fakeHost({ defs: { market_profile: DEF_MP, tgp_btlm: DEF_LINE } });
  const store = new IndicatorStateStore(host);
  const list = [
    inst({ instanceId: 'market_profile#1', indicatorId: 'market_profile' }),
    inst({ instanceId: 'tgp_btlm#1', indicatorId: 'tgp_btlm' }),
  ];
  // Act
  await entryOf(store)(list);
  // Assert
  assert.ok(host.log.includes('mp.restoreInstance:market_profile#1'), 'MP は専用復元経路へ委譲する');
  assert.equal(host.log.some((l) => l.startsWith('compute:market_profile')), false, 'MP は /compute を通さない');
  assert.ok(host.log.some((l) => l.startsWith('compute:tgp_btlm')), '非 MP は従来どおり compute する');
  assert.ok(host._meta.has('market_profile#1') && host._meta.has('tgp_btlm#1'), 'meta は両種別で登録される');
});

// ---------------------------------------------------------------------------
// 個別失敗の局所化（F-T4）
// ---------------------------------------------------------------------------

test('TC-R04 カタログ非在席は当該 1 件のみスキップし残りを適用継続する（F-T4）', async () => {
  // Arrange
  const host = fakeHost({ defs: { tgp_btlm: DEF_LINE, profit_band: DEF_BAND } });
  const store = new IndicatorStateStore(host);
  const list = [
    inst({ instanceId: 'tgp_btlm#1', indicatorId: 'tgp_btlm' }),
    inst({ instanceId: 'ghost#1', indicatorId: 'not_in_catalog' }),
    inst({ instanceId: 'profit_band#1', indicatorId: 'profit_band' }),
  ];
  // Act
  await entryOf(store)(list);
  // Assert
  const drawn = host.log.filter((l) => l.startsWith('draw:')).map((l) => l.split(':')[1]);
  assert.deepEqual(drawn, ['tgp_btlm#1', 'profit_band#1'], '非在席の 1 件だけを飛ばし残りは描画する');
  assert.equal(host._meta.has('ghost#1'), false, '非在席は meta へ登録しない');
});

test('TC-R05 個別 compute の例外は当該 1 件のみスキップし残りを適用継続する（F-T4）', async () => {
  // Arrange
  const host = fakeHost({ defs: { tgp_btlm: DEF_LINE, profit_band: DEF_BAND }, computeFails: new Set(['tgp_btlm']) });
  const store = new IndicatorStateStore(host);
  const list = [
    inst({ instanceId: 'tgp_btlm#1', indicatorId: 'tgp_btlm' }),
    inst({ instanceId: 'profit_band#1', indicatorId: 'profit_band' }),
  ];
  // Act / Assert: 全体を中止しない（例外を伝播させない）
  await assert.doesNotReject(() => entryOf(store)(list));
  const drawn = host.log.filter((l) => l.startsWith('draw:')).map((l) => l.split(':')[1]);
  assert.deepEqual(drawn, ['profit_band#1'], '失敗した 1 件のみスキップし残りの適用と描画は継続する');
});

// ---------------------------------------------------------------------------
// visible の復元（§5.2 手順 4）
// ---------------------------------------------------------------------------

test('TC-R06 visible=false は描画後に非表示へ戻す（§5.2 手順 4）', async () => {
  // Arrange
  const host = fakeHost({ defs: { tgp_btlm: DEF_LINE, profit_band: DEF_BAND } });
  const store = new IndicatorStateStore(host);
  const list = [
    inst({ instanceId: 'tgp_btlm#1', indicatorId: 'tgp_btlm', visible: false }),
    inst({ instanceId: 'profit_band#1', indicatorId: 'profit_band', visible: true }),
  ];
  // Act
  await entryOf(store)(list);
  // Assert
  const drawIdx = host.log.indexOf('draw:tgp_btlm#1:{}');
  const hideIdx = host.log.indexOf('setVisible:tgp_btlm#1:false');
  assert.ok(drawIdx >= 0 && hideIdx > drawIdx, `非表示化は描画の後に行う（実際: ${JSON.stringify(host.log)}）`);
  assert.equal(host.log.some((l) => l.startsWith('setVisible:profit_band#1')), false, 'visible=true には触らない');
});
