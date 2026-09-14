// timer_registry.wrap の契約テスト（Red フェーズ）。
//
// 保証対象（基本設計書 §3 R5）: モード切替 teardown 時に旧モードの setInterval を
// 一括停止するための登録簿。下層 setInterval/clearInterval をラップし id を追跡する。
// 構造は AAA。テスト名は「対象_条件_期待結果」。
//
// Red: wrap は未実装で throw するため全ケース失敗。

import { describe, test, expect } from 'vitest';
import { wrap } from '../js/timer_registry.js';

// 決定論的な id を返す下層タイマスタブ（実タイマ非依存＝F.I.R.S.T Fast/Repeatable）。
function fakeBase() {
  let seq = 0;
  const cleared = [];
  return {
    cleared,
    setInterval: (_fn, _ms) => { seq += 1; return seq; },
    clearInterval: (id) => { cleared.push(id); },
  };
}

describe('wrap', () => {
  // --- D1: wrap() が返す setInterval/clearInterval は id を追跡し委譲する ---
  test('wrapped_set_and_clear_delegate_and_track_ids', () => {
    // Arrange
    const base = fakeBase();
    const reg = wrap(base);
    // Act
    const id1 = reg.setInterval(() => {}, 1000);
    reg.clearInterval(id1);
    // Assert: 下層へ委譲されている
    expect(id1).toBe(1);
    expect(base.cleared).toEqual([1]);
  });

  // --- D2: clearAll() で未 clear の全 interval を停止 ---
  test('clear_all_stops_every_uncleared_interval', () => {
    // Arrange
    const base = fakeBase();
    const reg = wrap(base);
    // Act
    reg.setInterval(() => {}, 1000); // id 1
    reg.setInterval(() => {}, 2000); // id 2
    reg.clearAll();
    // Assert: 両方が下層 clearInterval される
    expect(base.cleared.sort()).toEqual([1, 2]);
  });

  // --- D3: 個別 clear 済みは clearAll で二重 clear しない ---
  test('individually_cleared_interval_is_not_cleared_again_by_clear_all', () => {
    // Arrange
    const base = fakeBase();
    const reg = wrap(base);
    // Act
    const id1 = reg.setInterval(() => {}, 1000); // id 1
    reg.setInterval(() => {}, 2000);             // id 2
    reg.clearInterval(id1);                       // 個別 clear
    reg.clearAll();                               // 残り(2)のみ clear されるはず
    // Assert: id1 は一度だけ、id2 も一度だけ
    expect(base.cleared).toEqual([1, 2]);
  });
});
