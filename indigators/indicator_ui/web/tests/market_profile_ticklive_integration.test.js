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
// ISSUE-260: VA 比率の既定は Python 唯一源の生成物（テストも第 2 定義を持たない）。
import { VA_PCT_DEFAULT } from '../js/domain/mp_param_defaults_generated.js';

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

// --- Phase5（統一成長）: 成長経路は grow 軸で存続する（表示選択肢 'ticklive' のみ撤去） ---
//   旧 MP-01 は「'ticklive' が UI 発行可能な ENUM 値」を強制したが、Phase5 で表示選択肢から撤去した。
//   代わりに本 1 本が「成長エンジン（forming 増分）は表示モードでなく成長軸（growing 信号）で起動する」ことを
//   回帰的に固定する:
//     (1) catalog mode ENUM に 'ticklive' は無い（表示選択肢として撤去済）。
//     (2) normal 表示 + applyGrowthState({growing:true}) → onLiveTick が /market_profile_forming を叩く
//         （＝成長機能が grow 軸で起動する・dead code でない）。
//   これにより「ticklive 撤去で成長が no-op 化する退行」を禁止する（gate3: 成長経路存続）。
test('production path: forming growth starts via the grow axis (growing) in normal mode — ticklive segment removed but engine survives', async () => {
  // (1) 表示選択肢からの撤去: segmented トグルに 'ticklive' は無い。
  const def = get('market_profile');
  const modeParam = def.params.find((p) => p.name === 'mode');
  assert.ok(
    !modeParam.enumValues.includes('ticklive'),
    'Phase5: mode ENUM から ticklive セグメント（表示選択肢）を撤去済',
  );

  // (2) 成長エンジンの grow 軸起動: normal 表示 + growing=true で onLiveTick が forming を叩く。
  const urls = [];
  const fakeFetch = async (u) => {
    urls.push(String(u));
    return {
      ok: true,
      async json() {
        return {
          ok: true, formingStart: 1000, ticks: [], baseFine: [0], baseKmin: 100,
          activeTable: [[1]], priceMin: 1000, priceMax: 1100, nBins: 1, gridW: 10, vaPct: VA_PCT_DEFAULT, now: 1010,
        };
      },
    };
  };
  const primitive = { setProfile() {}, setVisible() {}, setSessions() {} };
  const actor = new MarketProfileActor({
    client: { async fetchProfile() { return null; } },
    primitive,
    getContext: () => ({ datasetRef: 'jp225_tick', timeframe: '1h', limit: 1500 }),
    formingClient: { async fetchForming(args) { urls.push(await fakeFetch(`/market_profile_forming?base=${args.base}`)); return null; } },
    makeAccumulator: () => new DwellAccumulator(),
  });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'normal' });          // 表示モードは通常（ticklive でない）。
  actor.applyGrowthState({ growing: true });     // 成長軸で成長 ON（FOLLOW/reveal 相当）。
  // Act: 成長状態の onLiveTick（present の pull 成長）。
  await actor.onLiveTick();
  // Assert: 成長エンジンが grow 軸で起動する（forming エンドポイントを叩く＝dead code でない）。
  assert.ok(
    !actor.isTicklive(),
    'normal 表示＝ticklive 表示モードではない（成長は表示モードでなく grow 軸が担う）',
  );
  assert.ok(
    urls.some((u) => String(u).includes('market_profile_forming')),
    'normal + growing で onLiveTick が forming を叩く（成長経路が grow 軸で存続）',
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
            activeTable: [[1]], priceMin: 1000, priceMax: 1100, nBins: 1, gridW: 10, vaPct: VA_PCT_DEFAULT, now: 1010,
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
    lwc: fakeLwc(), container: {}, doc: null, storage: noStorage, fetch: fakeFetch,
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
