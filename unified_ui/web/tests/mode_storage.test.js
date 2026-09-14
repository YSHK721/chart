// mode_storage.scopedStorage の契約テスト（実配線版）。
//
// 保証対象（基本設計書 §3 R4 / code-review 🟡-5）: 単一オリジン下で live/replay の
// localStorage キー衝突を防ぐ storage ポートラッパ。下層 storage（localStorage 互換）へ
// `${mode}:${key}` の物理キーで getItem/setItem/removeItem を委譲する。
// unified_root.js が既存 bootstrap の `storage` 注入口へ渡す実体そのものを検証する。
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { describe, test, expect } from 'vitest';
import { scopedStorage } from '../js/mode_storage.js';

// Map を裏に持つ最小 localStorage 互換スタブ（物理キーを直接観測できる）。
function fakeStorage() {
  const store = new Map();
  return {
    store,
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => { store.set(key, value); },
    removeItem: (key) => { store.delete(key); },
  };
}

describe('scopedStorage', () => {
  // --- C1: setItem/getItem が物理キーへ mode prefix を付与 ---
  test('set_item_writes_physical_key_with_mode_prefix', () => {
    // Arrange
    const st = fakeStorage();
    const ns = scopedStorage(st, 'live');
    // Act
    ns.setItem('applied', 'v1');
    // Assert: 下層 storage は prefix 付き物理キーを見る
    expect(st.store.has('live:applied')).toBe(true);
    expect(st.store.has('applied')).toBe(false);
    expect(ns.getItem('applied')).toBe('v1');
  });

  // --- C2: live と replay で同一論理キーが衝突しない ---
  test('live_and_replay_same_logical_key_do_not_collide', () => {
    // Arrange
    const st = fakeStorage();
    const live = scopedStorage(st, 'live');
    const replay = scopedStorage(st, 'replay');
    // Act
    live.setItem('uiState', 'L');
    replay.setItem('uiState', 'R');
    // Assert: 相互不可視
    expect(live.getItem('uiState')).toBe('L');
    expect(replay.getItem('uiState')).toBe('R');
    expect(st.store.get('live:uiState')).toBe('L');
    expect(st.store.get('replay:uiState')).toBe('R');
  });

  // --- C3: 下層 storage の既存 prefix 無しキーには触れない ---
  test('existing_unprefixed_keys_are_left_untouched', () => {
    // Arrange: 既存キー（prefix 無し）を事前投入
    const st = fakeStorage();
    st.setItem('legacy', 'keep');
    const ns = scopedStorage(st, 'live');
    // Act
    ns.setItem('applied', 'v1');
    // Assert: 既存キーは不変・scoped からは不可視
    expect(st.store.get('legacy')).toBe('keep');
    expect(ns.getItem('legacy')).toBe(null);
  });

  // --- C4: removeItem が prefix 付き物理キーのみを削除 ---
  test('remove_item_deletes_only_prefixed_physical_key', () => {
    // Arrange
    const st = fakeStorage();
    st.setItem('legacy', 'keep');
    const ns = scopedStorage(st, 'replay');
    ns.setItem('applied', 'v1');
    // Act
    ns.removeItem('applied');
    // Assert: 自モードの物理キーのみ消え、既存 prefix 無しキーは残る
    expect(st.store.has('replay:applied')).toBe(false);
    expect(st.store.get('legacy')).toBe('keep');
  });
});
