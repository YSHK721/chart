// mp_resmode_migration.test.js — 後方互換マイグレーション検証（レビュー指摘 修正1）。
//
// 背景: 中間版（range=auto/barw セレクタ）で「レンジ」を選んで保存した MP インスタンスは
//   永続 params が数値 range（'25'|'50'|'100'|'250'|'500'）を持つが resmode を持たない。
//   segmented 版適用後に restore/apply されると _mpParams が resmode を載せず、client の
//   排他送信が既定 bins 分岐へ落ちて &bins= を送り、保存したレンジ（barw）が無言で捨てられる。
// 修正: _mpParams（apply/gear/restore 共通入口）で resmode 欠落時に range から導出する。
//   - range が レンジ数値集合 → resmode='range'（保存したレンジを維持し client が &barw= を送る）
//   - range が 'auto' または未指定 → resmode='bins'（従来通り）
//   - 明示 resmode は導出で上書きしない（後方互換補完のみ）
// 構造: Arrange-Act-Assert（AAA）。DOM/ネット非依存（全注入・recording fake）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { get } from '../js/usecase/catalog.js';
import { IndicatorController } from '../js/adapter/front/indicator_controller.js';
import { registerMarketProfile } from './helpers/market_profile_rig.js';
import { buildMarketProfileUrl } from '../js/adapter/front/market_profile_client.js';

// setParams を記録する Fake actor。
function fakeMarketProfile() {
  return {
    params: [], enables: [],
    setParams(p) { this.params.push(p); },
    async setEnabled(on) { this.enables.push(on); },
    async refresh() {},
    detach() {},
  };
}

// S3: MP は ctor 引数ではなく**合成根と同じ登録経路**（registerActorController）で結線する。
function makeController({ marketProfile, applied = [] } = {}) {
  const noop = () => {};
  const ctrl = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async (req) => ({ ok: true, generation: req.generation ?? 0, series: [] }) },
    persistence: {
      loadApplied: () => applied, saveApplied: noop, loadFavorites: () => [], saveFavorites: noop,
      loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer: { renderLine: noop, renderHistogram: noop, renderHorizontal: noop, setData: noop, setVisible: noop, remove: noop, setCandles: noop },
    document: null,
  });
  registerMarketProfile(ctrl, { actor: marketProfile });
  return ctrl;
}

// --- 修正1: _mpParams で resmode を range から導出（apply/gear 経路）--------------

test('_mpParams derives resmode=range for a legacy barw params (resmode absent, numeric range) and the URL sends &barw= not bins=', () => {
  // Arrange: 旧 barw 保存インスタンス相当（resmode 無し・range 数値）。bins は既定が残存。
  const ctrl = makeController({ marketProfile: fakeMarketProfile() });
  const legacy = { bins: 60, va: 0.70, limit: 1500, src: 'candle', range: '100' };
  // Act
  const out = ctrl._mpParams(legacy);
  const url = buildMarketProfileUrl({ datasetRef: 'jp225_tick', ...out });
  // Assert: resmode='range' が導出され、URL は barw を送り bins は送らない。
  assert.equal(out.resmode, 'range', 'resmode が range へ導出される');
  assert.equal(out.range, '100', 'レンジが維持される');
  assert.ok(url.includes('&barw=100'), 'URL は &barw=100 を含む');
  assert.ok(!url.includes('bins='), 'URL は bins= を含まない');
});

test('_mpParams derives resmode=bins for range=auto (resmode absent) and the URL sends bins only', () => {
  // Arrange: range='auto'（resmode 無し）。従来 bins フォールバックが壊れないことを固定する。
  const ctrl = makeController({ marketProfile: fakeMarketProfile() });
  const legacyAuto = { bins: 60, va: 0.70, limit: 1500, src: 'candle', range: 'auto' };
  // Act
  const out = ctrl._mpParams(legacyAuto);
  const url = buildMarketProfileUrl({ datasetRef: 'jp225_tick', ...out });
  // Assert: resmode='bins'、range は 'auto' 除外、URL は bins のみ。
  assert.equal(out.resmode, 'bins', 'resmode が bins へ導出される');
  assert.equal(out.range, undefined, "range='auto' は out に載せない（従来通り）");
  assert.ok(url.includes('&bins=60'), 'URL は bins を含む');
  assert.ok(!url.includes('barw='), 'URL は barw を含まない');
});

test('_mpParams does NOT override an explicit resmode=bins even with a numeric range', () => {
  // Arrange: 明示 resmode='bins'（現行/新規インスタンス）。導出で上書きしない。
  const ctrl = makeController({ marketProfile: fakeMarketProfile() });
  // Act
  const out = ctrl._mpParams({ bins: 30, va: 0.7, limit: 500, src: 'candle', resmode: 'bins', range: '100' });
  // Assert
  assert.equal(out.resmode, 'bins', '明示 bins は保持される');
});

test('_mpParams does NOT override an explicit resmode=range', () => {
  // Arrange: 明示 resmode='range'。導出で上書きしない。
  const ctrl = makeController({ marketProfile: fakeMarketProfile() });
  // Act
  const out = ctrl._mpParams({ bins: 30, va: 0.7, limit: 500, src: 'candle', resmode: 'range', range: '50' });
  // Assert
  assert.equal(out.resmode, 'range', '明示 range は保持される');
  assert.equal(out.range, '50');
});

test('_mpParams leaves resmode absent when both resmode and range are absent (従来 bins 相当の非付与を維持)', () => {
  // Arrange: resmode も range も無い旧インスタンス（既存 restore テストの形）。
  const ctrl = makeController({ marketProfile: fakeMarketProfile() });
  // Act
  const out = ctrl._mpParams({ bins: 80, va: 0.65, limit: 500, src: 'dwell' });
  // Assert: range 不在時は導出せず resmode キーを付けない（client 既定 = bins）。
  assert.equal('resmode' in out, false, 'range 不在なら resmode を付与しない');
});

// --- 修正1: restore 経路（dialog を介さず persisted params を直接通す reload）でも効く ----

test('restore() derives resmode=range for a saved barw instance so the actor sends &barw=', async () => {
  // Arrange: 保存済み MP（可視・resmode 無し・range='100' の barw インスタンス）。
  const marketProfile = fakeMarketProfile();
  const savedBarw = {
    instanceId: 'market_profile#1', indicatorId: 'market_profile', variant: 'default',
    params: [['bins', 60], ['va', 0.70], ['limit', 1500], ['src', 'candle'], ['range', '100']],
    visible: true, generation: 0, seq: 1, createdAt: '2026-06-30T00:00:00Z',
  };
  const ctrl = makeController({ marketProfile, applied: [savedBarw] });
  // Act
  await ctrl.restore();
  const forwarded = marketProfile.params.at(-1);
  const url = buildMarketProfileUrl({ datasetRef: 'jp225_tick', ...forwarded });
  // Assert: restore でも resmode='range' が導出され barw が送られる。
  assert.equal(forwarded.resmode, 'range', 'restore で resmode=range が導出される');
  assert.ok(url.includes('&barw=100'), 'restore 後の URL は &barw=100 を含む');
  assert.ok(!url.includes('bins='), 'restore 後の URL は bins= を含まない');
});
