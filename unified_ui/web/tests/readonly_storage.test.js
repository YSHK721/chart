// readonly_storage — storage ポートの読み取り専用ラッパの単体検証（arch-spec §0 T-2）。
//
// なぜ在るか: ダッシュボードの「テンプレート束」は **live スコープに保存された既存の資産**である。
//   ダッシュボード側はそれを**読むだけ**で、書き換えてはならない（書ける口を渡すと、第 4 モードの
//   不具合が live のテンプレートを壊す経路になる＝相互不可視の契約が片側から破れる）。
//   スコープの選択は Composition Root（unified_root）が行い、View は自分でスコープを選ばない。
//
// 契約:
//   - getItem / key / length は下層へ委譲する（読みは素通し）
//   - setItem / removeItem / clear は**下層へ 1 度も届かない**。かつ黙って捨てない
//     （無音で書けたつもりになるのが最悪の壊れ方なので、その場で落とす）
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { describe, test, expect } from 'vitest';
import { readOnlyStorage } from '../js/readonly_storage.js';

function fakeStorage(initial = {}) {
  const data = { ...initial };
  const writes = [];
  return {
    writes,
    data,
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => { writes.push(['set', k, v]); data[k] = v; },
    removeItem: (k) => { writes.push(['remove', k]); delete data[k]; },
    key: (i) => Object.keys(data)[i] ?? null,
    get length() { return Object.keys(data).length; },
    clear: () => { writes.push(['clear']); },
  };
}

describe('readOnlyStorage — 読み取り専用の storage ポート', () => {
  test('get_item_is_delegated_to_the_underlying_storage', () => {
    // Arrange
    const base = fakeStorage({ 'live:chartTemplates': '[{"name":"A"}]' });
    // Act
    const ro = readOnlyStorage(base);
    // Assert
    expect(ro.getItem('live:chartTemplates')).toBe('[{"name":"A"}]');
    expect(ro.getItem('absent')).toBe(null);
  });

  test('key_and_length_are_delegated_to_the_underlying_storage', () => {
    // Arrange
    const base = fakeStorage({ a: '1', b: '2' });
    // Act
    const ro = readOnlyStorage(base);
    // Assert
    expect(ro.length).toBe(2);
    expect(ro.key(0)).toBe('a');
  });

  test('set_item_throws_and_never_reaches_the_underlying_storage', () => {
    // Arrange
    const base = fakeStorage({ a: '1' });
    const ro = readOnlyStorage(base);
    // Act / Assert: 黙って捨てると「保存したつもり」が残る（無音の失敗）。
    expect(() => ro.setItem('a', 'x')).toThrow();
    expect(base.writes).toEqual([]);
    expect(base.data.a).toBe('1');
  });

  test('remove_item_throws_and_never_reaches_the_underlying_storage', () => {
    // Arrange
    const base = fakeStorage({ a: '1' });
    const ro = readOnlyStorage(base);
    // Act / Assert
    expect(() => ro.removeItem('a')).toThrow();
    expect(base.writes).toEqual([]);
    expect(base.data.a).toBe('1');
  });

  test('clear_throws_and_never_reaches_the_underlying_storage', () => {
    // Arrange
    const base = fakeStorage({ a: '1' });
    const ro = readOnlyStorage(base);
    // Act / Assert
    expect(() => ro.clear()).toThrow();
    expect(base.writes).toEqual([]);
  });

  test('reading_never_issues_a_write_to_the_underlying_storage', () => {
    // Arrange: 読み取り経路が書き込みを 1 度も発行しないこと（発行 − 使用 = 0 の表明）。
    //   入力（読む回数）を変えても書き込み発行が増えないことを 2 点で固定する。
    const base = fakeStorage({ a: '1', b: '2' });
    const ro = readOnlyStorage(base);
    // Act
    for (let i = 0; i < 2; i += 1) { ro.getItem('a'); ro.key(0); }
    const afterTwo = base.writes.length;
    for (let i = 0; i < 10; i += 1) { ro.getItem('a'); ro.key(0); }
    // Assert
    expect(afterTwo).toBe(0);
    expect(base.writes.length).toBe(afterTwo);
  });
});
