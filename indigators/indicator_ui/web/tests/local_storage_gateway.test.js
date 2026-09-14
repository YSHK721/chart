// local_storage_gateway.js（StatePersistencePort 実装）の仕様検証。
//
// 設計入力: 内部設計書 §6.1（物理スキーマ: indicatorUi.* / .vN）、§6.2（破損は当該キーのみ初期化・他温存）、
//   §6.2 seq 採番（next = (counters[id] ?? 0)+1・単調増加 §5.7）、QuotaExceeded(F5) は当該書込み中止。
// Storage は注入（Fake localStorage）でテスト。
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { LocalStorageGateway } from '../js/adapter/front/local_storage_gateway.js';

// Fake localStorage（getItem/setItem/removeItem）。quota フラグで setItem を失敗させられる。
function fakeStorage(initial = {}) {
  const map = new Map(Object.entries(initial));
  return {
    map,
    quotaExceeded: false,
    getItem(k) { return map.has(k) ? map.get(k) : null; },
    setItem(k, v) {
      if (this.quotaExceeded) { const e = new Error('quota'); e.name = 'QuotaExceededError'; throw e; }
      map.set(k, String(v));
    },
    removeItem(k) { map.delete(k); },
  };
}

test('save/load favorites roundtrips the id list', () => {
  // Arrange
  const storage = fakeStorage();
  const gw = new LocalStorageGateway(storage);
  // Act
  gw.saveFavorites(['tgp_btlm', 'profit_band']);
  // Assert
  assert.deepEqual(gw.loadFavorites(), ['tgp_btlm', 'profit_band']);
});

test('loadFavorites returns [] when key absent', () => {
  const gw = new LocalStorageGateway(fakeStorage());
  assert.deepEqual(gw.loadFavorites(), []);
});

test('save/load applied roundtrips instances (plain JSON)', () => {
  const gw = new LocalStorageGateway(fakeStorage());
  const instances = [{ instanceId: 'profit_band#3', indicatorId: 'profit_band', variant: 'global', params: {}, visible: true, generation: 2, seq: 3, createdAt: 't' }];
  gw.saveApplied(instances);
  assert.deepEqual(gw.loadApplied(), instances);
});

test('save/load uiState roundtrips', () => {
  const gw = new LocalStorageGateway(fakeStorage());
  const ui = { lastTab: 'indicator', lastCategory: 'cat.statistics', dialogOpen: false };
  gw.saveUiState(ui);
  assert.deepEqual(gw.loadUiState(), ui);
});

test('nextSeq is monotonic per indicator and persists the counter (§5.7)', () => {
  const gw = new LocalStorageGateway(fakeStorage());
  assert.equal(gw.nextSeq('tgp_btlm'), 1);
  assert.equal(gw.nextSeq('tgp_btlm'), 2);
  assert.equal(gw.nextSeq('profit_band'), 1);
  // a fresh gateway over the same storage continues monotonically
});

test('nextSeq stays monotonic across gateway re-creation (counter persisted)', () => {
  const storage = fakeStorage();
  const gw1 = new LocalStorageGateway(storage);
  gw1.nextSeq('tgp_btlm');
  gw1.nextSeq('tgp_btlm');
  const gw2 = new LocalStorageGateway(storage);
  assert.equal(gw2.nextSeq('tgp_btlm'), 3);
});

test('corrupt key initializes only that key and preserves other keys (§6.2)', () => {
  // Arrange: favorites is corrupt JSON, uiState is valid
  const storage = fakeStorage({
    'indicatorUi.favorites.v1': '{ broken json',
    'indicatorUi.uiState.v1': JSON.stringify({ lastTab: 'indicator', lastCategory: '', dialogOpen: false }),
  });
  const gw = new LocalStorageGateway(storage);
  // Act
  const favs = gw.loadFavorites();
  const ui = gw.loadUiState();
  // Assert: corrupt favorites -> [], uiState preserved
  assert.deepEqual(favs, []);
  assert.equal(ui.lastTab, 'indicator');
});

test('QuotaExceeded during save aborts that write without throwing (F5)', () => {
  const storage = fakeStorage();
  const gw = new LocalStorageGateway(storage);
  storage.quotaExceeded = true;
  // Should not throw; write is aborted, in-memory caller state unaffected.
  assert.doesNotThrow(() => gw.saveFavorites(['x']));
});
