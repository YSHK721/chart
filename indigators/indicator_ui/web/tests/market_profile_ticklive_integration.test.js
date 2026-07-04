// MP tick 逐次成長の統合配線検証（indicator_controller の 1 行置換＋composition の注入配線）。
//
// 設計入力: Phase2 設計 mp_ticklive_design.md「変更 統合」。
//   - indicator_controller.recomputeAllApplied の MP 分岐が actor.refresh でなく actor.onLiveTick を呼ぶ。
//     （非増分時 onLiveTick は refresh へ byte-identical 委譲＝回帰ゼロ。増分時のみ tick 逐次成長。）
//   - composition_root_front が MarketProfileFormingClient と DwellAccumulator factory を actor へ注入する。
//     （注入があると ticklive モードで onLiveTick が /market_profile_forming を叩ける。）
// 構造: Arrange-Act-Assert。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { IndicatorController } from '../js/adapter/front/indicator_controller.js';
import { get } from '../js/usecase/catalog.js';
import { bootstrap } from '../js/adapter/front/composition_root_front.js';
import { MarketProfileActor } from '../js/adapter/front/market_profile_actor.js';
import { DwellAccumulator } from '../js/domain/market_profile_dwell_accumulator.js';

// --- indicator_controller: MP 分岐は onLiveTick へ委譲する ---
function controller() {
  const noop = () => {};
  return new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async () => ({ ok: true, generation: 0, series: [] }) },
    persistence: {
      loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop,
      loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer: { renderLine: noop, renderHorizontal: noop, setData: noop, setVisible: noop, remove: noop },
    facade: {},
    document: null,
  });
}

test('recomputeAllApplied routes the MP branch to actor.onLiveTick (not refresh)', async () => {
  // Arrange: 可視の MP インスタンスを 1 件・fake actor（onLiveTick/refresh を計測）。
  const ctrl = controller();
  const calls = { onLiveTick: 0, refresh: 0 };
  ctrl._marketProfile = {
    onLiveTick: async () => { calls.onLiveTick += 1; },
    refresh: async () => { calls.refresh += 1; },
  };
  ctrl._state.applied = [{ instanceId: 'mp1', visible: true, params: {} }];
  ctrl._meta.set('mp1', { def: { compute: { computeId: 'market_profile' } } });
  // Act
  await ctrl.recomputeAllApplied();
  // Assert: onLiveTick に一本化される（refresh は直接呼ばない）。
  assert.equal(calls.onLiveTick, 1);
  assert.equal(calls.refresh, 0);
});

test('recomputeAllApplied skips the MP branch when the MP instance is hidden', async () => {
  // Arrange
  const ctrl = controller();
  const calls = { onLiveTick: 0 };
  ctrl._marketProfile = { onLiveTick: async () => { calls.onLiveTick += 1; }, refresh: async () => {} };
  ctrl._state.applied = [{ instanceId: 'mp1', visible: false, params: {} }];
  ctrl._meta.set('mp1', { def: { compute: { computeId: 'market_profile' } } });
  // Act
  await ctrl.recomputeAllApplied();
  // Assert: 非表示 MP は onLiveTick を呼ばない（既存 refresh 分岐の可視ガードを保存）。
  assert.equal(calls.onLiveTick, 0);
});

// --- MP-01 是正: 本番経路の到達性（UI が生成し得る ENUM 値で ticklive 機能が起動する） ---
//   直挿入 setParams({mode:'ticklive'}) の「見せかけ緑」ではなく、UI が実際に発行し得る値の連鎖を辿る:
//     catalog mode ENUM に 'ticklive' が存在（segmented トグルが描ける＝UI 発行可能）
//       → IndicatorController._mpParams/_deriveMode が 'ticklive' を素通しで転送
//       → MarketProfileActor.setParams → _applyMode('ticklive') → _ticklive=true（isTicklive）
//       → onLiveTick が /market_profile_forming を叩く（増分機能が起動）
//   この 1 本が「ENUM に ticklive が無ければ UI から到達不能＝dead code」を回帰的に禁止する。
test('production path: UI-emittable mode ENUM value ticklive reaches _ticklive and starts the forming feature', async () => {
  // Arrange: 本番 catalog def / 本番 _mpParams（controller）/ 本番 actor（forming client + accumulator 注入）。
  const def = get('market_profile');
  const modeParam = def.params.find((p) => p.name === 'mode');
  // (1) UI 発行可能性: segmented トグルは enumValues しか描けない。'ticklive' が列挙されていること。
  assert.ok(
    modeParam.enumValues.includes('ticklive'),
    'mode ENUM に ticklive が無いと本番 UI から mode:ticklive を発行できず _applyMode に到達不能',
  );

  const ctrl = controller();
  // (2) 転送経路: _mpParams/_deriveMode が UI 値 'ticklive' を actor へ素通しする（potentially 落とさない）。
  const forwarded = ctrl._mpParams({ mode: 'ticklive' });
  assert.equal(forwarded.mode, 'ticklive', '_mpParams/_deriveMode が ticklive を転送する');

  // (3)(4) 到達性: 転送 params を本番 actor.setParams へ → _applyMode('ticklive') → _ticklive=true。
  const urls = [];
  const fakeFetch = async (u) => {
    urls.push(String(u));
    return {
      ok: true,
      async json() {
        return {
          ok: true, formingStart: 1000, ticks: [], baseFine: [0], baseKmin: 100,
          activeTable: [[1]], priceMin: 1000, priceMax: 1100, nBins: 1, gridW: 10, now: 1010,
        };
      },
    };
  };
  const primitive = {
    setProfile() {}, setVisible() {}, setSessions() {},
  };
  const actor = new MarketProfileActor({
    client: { async fetchProfile() { return null; } },
    primitive,
    getContext: () => ({ datasetRef: 'jp225_tick', timeframe: '1h', limit: 1500 }),
    formingClient: { async fetchForming(args) { urls.push(await fakeFetch(`/market_profile_forming?base=${args.base}`)); return null; } },
    makeAccumulator: () => new DwellAccumulator(),
  });
  await actor.setEnabled(true);
  // Act: UI が生成した ENUM 値の転送結果を setParams（＝本番 apply/restore と同じ経路）。
  actor.setParams(forwarded);
  // Assert: _applyMode('ticklive') に到達し _ticklive=true。
  assert.equal(actor.isTicklive(), true, '本番経路で _ticklive=true に到達する（dead code でない）');
  // 増分機能が起動する（onLiveTick が forming エンドポイントを叩く＝機能本体が動く）。
  await actor.onLiveTick();
  assert.ok(
    urls.some((u) => String(u).includes('market_profile_forming')),
    'ticklive 到達後 onLiveTick が forming エンドポイントを叩く（機能起動を実証）',
  );
});

// --- composition_root_front: forming client / accumulator を注入する ---
function fakeLwc() {
  const mainSeries = { setData: () => {}, attachPrimitive: () => {} };
  const chart = {
    addSeries: () => mainSeries,
    timeScale: () => ({ fitContent: () => {} }),
    panes: () => [{ setStretchFactor: () => {}, paneIndex: () => 0 }],
    addPane: () => ({ addSeries: () => ({ setData: () => {} }), setStretchFactor: () => {}, paneIndex: () => 1 }),
    removePane: () => {}, removeSeries: () => {}, subscribeCrosshairMove: () => {},
  };
  return {
    createChart: () => chart,
    ColorType: { Solid: 'solid' },
    CandlestickSeries: {}, LineSeries: {}, HistogramSeries: {},
    createTextWatermark: () => ({ applyOptions: () => {}, detach: () => {} }),
  };
}

const noStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };

test('composition injects a forming client + accumulator so ticklive onLiveTick hits /market_profile_forming', async () => {
  // Arrange: URL を記録し、forming/profile/candles を切り分けて返す Fake fetch。
  const urls = [];
  const fakeFetch = async (u) => {
    urls.push(String(u));
    return {
      ok: true,
      async json() {
        if (String(u).includes('market_profile_forming')) {
          return {
            ok: true, formingStart: 1000, ticks: [], baseFine: [0], baseKmin: 100,
            activeTable: [[1]], priceMin: 1000, priceMax: 1100, nBins: 1, gridW: 10, now: 1010,
          };
        }
        if (String(u).includes('market_profile')) {
          return {
            ok: true,
            profile: {
              bins: [], poc: 0, va_low: 0, va_high: 0,
              price_min: 0, price_max: 0, tpo_units: 0, n_bins: 0,
            },
          };
        }
        return { ok: true, candles: [] };
      },
    };
  };
  const { marketProfile } = await bootstrap({
    lwc: fakeLwc(), container: {}, doc: null, storage: noStorage, protocol: 'http:', fetch: fakeFetch,
  });
  // Act: ticklive モードで onLiveTick → forming client 経由の取得が発火するはず。
  await marketProfile.setEnabled(true);
  marketProfile.setParams({ mode: 'ticklive' });
  await marketProfile.onLiveTick();
  // Assert: /market_profile_forming が叩かれる（注入があった証拠。無ければ refresh 委譲で叩かれない）。
  assert.ok(
    urls.some((u) => u.includes('market_profile_forming')),
    '注入配線があれば ticklive onLiveTick が forming エンドポイントを叩く',
  );
});
