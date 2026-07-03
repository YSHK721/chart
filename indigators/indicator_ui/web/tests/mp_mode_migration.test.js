// mp_mode_migration.test.js — 表示モード統合(mode)の転送・後方互換マイグレーション検証。
//
// 背景: 旧版は gear に replay(BOOL)/sessions(BOOL) の 2 チェックがあり、それぞれ永続 params に
//   replay:true / sessions:true として保存された。統合版は 1 つの mode ENUM
//   ['normal','replay','sessions'] へ一本化する。永続 params に mode が無く legacy replay/sessions
//   が残るインスタンスを restore/apply したとき、_mpParams が legacy → mode を導出して actor へ渡す
//   （legacy キー自体は actor へ送らない）。resmode の _deriveResmode と同方針。
// 規則: 両方 true の旧データは sessions 優先（排他統合のため一方に確定させる）。
// 構造: Arrange-Act-Assert（AAA）。DOM/ネット非依存（全注入・recording fake）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { get } from '../js/usecase/catalog.js';
import { IndicatorController } from '../js/adapter/front/indicator_controller.js';

function fakeMarketProfile() {
  return {
    params: [], enables: [],
    setParams(p) { this.params.push(p); },
    async setEnabled(on) { this.enables.push(on); },
    async refresh() {},
    detach() {},
  };
}

function makeController({ marketProfile, applied = [] } = {}) {
  const noop = () => {};
  return new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async (req) => ({ ok: true, generation: req.generation ?? 0, series: [] }) },
    persistence: {
      loadApplied: () => applied, saveApplied: noop, loadFavorites: () => [], saveFavorites: noop,
      loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer: { renderLine: noop, renderHistogram: noop, renderHorizontal: noop, setData: noop, setVisible: noop, remove: noop, setCandles: noop },
    document: null,
    marketProfile,
  });
}

// --- mode の転送（新規/現行インスタンス）--------------------------------------

test('_mpParams forwards an explicit mode to the actor', () => {
  const ctrl = makeController({ marketProfile: fakeMarketProfile() });
  const out = ctrl._mpParams({ bins: 60, va: 0.7, src: 'candle', mode: 'replay' });
  assert.equal(out.mode, 'replay', 'mode を actor へ転送する');
});

test('_mpParams does not forward legacy replay/sessions keys to the actor', () => {
  const ctrl = makeController({ marketProfile: fakeMarketProfile() });
  const out = ctrl._mpParams({ bins: 60, va: 0.7, src: 'candle', mode: 'sessions' });
  assert.equal('replay' in out, false, 'legacy replay キーは actor へ送らない');
  assert.equal('sessions' in out, false, 'legacy sessions キーは actor へ送らない');
});

// --- 後方互換: legacy replay/sessions → mode 導出 ------------------------------

test('_mpParams derives mode=replay from legacy replay:true (mode absent)', () => {
  const ctrl = makeController({ marketProfile: fakeMarketProfile() });
  const out = ctrl._mpParams({ bins: 60, va: 0.7, src: 'candle', replay: true });
  assert.equal(out.mode, 'replay', 'legacy replay:true → mode=replay');
  assert.equal('replay' in out, false, 'legacy replay キーは送らない');
});

test('_mpParams derives mode=sessions from legacy sessions:true (mode absent)', () => {
  const ctrl = makeController({ marketProfile: fakeMarketProfile() });
  const out = ctrl._mpParams({ bins: 60, va: 0.7, src: 'candle', sessions: true });
  assert.equal(out.mode, 'sessions', 'legacy sessions:true → mode=sessions');
  assert.equal('sessions' in out, false, 'legacy sessions キーは送らない');
});

test('_mpParams prefers sessions over replay when both legacy flags are true (規則: sessions 優先)', () => {
  const ctrl = makeController({ marketProfile: fakeMarketProfile() });
  const out = ctrl._mpParams({ bins: 60, va: 0.7, src: 'candle', replay: true, sessions: true });
  assert.equal(out.mode, 'sessions', '両 true は sessions 優先');
});

test('_mpParams leaves mode absent when neither mode nor legacy flags are present (actor 既定=通常)', () => {
  const ctrl = makeController({ marketProfile: fakeMarketProfile() });
  const out = ctrl._mpParams({ bins: 60, va: 0.7, src: 'candle' });
  assert.equal('mode' in out, false, 'mode も legacy も無ければ mode を付けない');
});

test('_mpParams does not derive mode from legacy false flags (replay:false/sessions:false → normal 相当)', () => {
  const ctrl = makeController({ marketProfile: fakeMarketProfile() });
  const out = ctrl._mpParams({ bins: 60, va: 0.7, src: 'candle', replay: false, sessions: false });
  // 明示 false は「通常」= mode='normal'（既定と一致）を導出する（restore 時に両 OFF を再現）。
  assert.equal(out.mode, 'normal', 'legacy 両 false → mode=normal');
});

test('an explicit mode wins over conflicting legacy flags', () => {
  const ctrl = makeController({ marketProfile: fakeMarketProfile() });
  const out = ctrl._mpParams({ bins: 60, va: 0.7, src: 'candle', mode: 'replay', sessions: true });
  assert.equal(out.mode, 'replay', 'mode 明示は legacy に優先する');
});

// --- restore 経路（persisted params を直接通す reload）でも効く -----------------

test('restore() derives mode=sessions for a legacy sessions instance and forwards it', async () => {
  const marketProfile = fakeMarketProfile();
  const savedSessions = {
    instanceId: 'market_profile#1', indicatorId: 'market_profile', variant: 'default',
    params: [['bins', 60], ['va', 0.70], ['src', 'candle'], ['sessions', true]],
    visible: true, generation: 0, seq: 1, createdAt: '2026-06-30T00:00:00Z',
  };
  const ctrl = makeController({ marketProfile, applied: [savedSessions] });
  await ctrl.restore();
  const forwarded = marketProfile.params.at(-1);
  assert.equal(forwarded.mode, 'sessions', 'restore で mode=sessions が導出される');
  assert.equal('sessions' in forwarded, false, 'legacy sessions キーは actor へ送らない');
});
