// view_state.captureState / restoreState の契約テスト（Red フェーズ）。
//
// 保証対象（基本設計書 §3 切替動作 1./4.）: モード切替時に閲覧状態
// （timeframe・表示指標構成・可視レンジ）を capture → 反対モード再構築後に restore。
// 構造は AAA。テスト名は「対象_条件_期待結果」。
//
// Red: captureState / restoreState は未実装で throw するため全ケース失敗。

import { describe, test, expect } from 'vitest';
import { captureState, restoreState } from '../js/view_state.js';

// getter を持つ状態取得元スタブ（controller/chart 実体非依存）。
function fakeSource() {
  return {
    getTimeframe: () => '1D',
    getIndicators: () => [{ id: 'sma', window: 50 }],
    getVisibleRange: () => ({ from: 100, to: 200 }),
  };
}

// setter 呼び出し順・引数を記録する適用先スタブ。
function fakeTarget() {
  const calls = [];
  return {
    calls,
    setTimeframe: (v) => { calls.push(['timeframe', v]); },
    setIndicators: (v) => { calls.push(['indicators', v]); },
    setVisibleRange: (v) => { calls.push(['visibleRange', v]); },
  };
}

describe('captureState', () => {
  // --- E1: getter 群から {timeframe, indicators, visibleRange} を capture ---
  test('captures_timeframe_indicators_and_visible_range_from_source', () => {
    // Arrange
    const src = fakeSource();
    // Act
    const state = captureState(src);
    // Assert
    expect(state).toEqual({
      timeframe: '1D',
      indicators: [{ id: 'sma', window: 50 }],
      visibleRange: { from: 100, to: 200 },
    });
  });
});

describe('restoreState', () => {
  // --- E2: capture 値を target の setter へ適用（順序・引数を契約）---
  test('applies_state_to_target_setters_in_order', () => {
    // Arrange
    const target = fakeTarget();
    const state = {
      timeframe: '1D',
      indicators: [{ id: 'sma', window: 50 }],
      visibleRange: { from: 100, to: 200 },
    };
    // Act
    restoreState(target, state);
    // Assert: timeframe → indicators → visibleRange の順で各 setter が呼ばれる
    expect(target.calls).toEqual([
      ['timeframe', '1D'],
      ['indicators', [{ id: 'sma', window: 50 }]],
      ['visibleRange', { from: 100, to: 200 }],
    ]);
  });
});

describe('capture/restore roundtrip', () => {
  // --- E3: capture → restore ラウンドトリップで等価 ---
  test('roundtrip_restores_equivalent_state', () => {
    // Arrange
    const src = fakeSource();
    const target = fakeTarget();
    // Act
    const state = captureState(src);
    restoreState(target, state);
    // Assert: target へ適用された値が source の値と等価
    expect(target.calls).toEqual([
      ['timeframe', '1D'],
      ['indicators', [{ id: 'sma', window: 50 }]],
      ['visibleRange', { from: 100, to: 200 }],
    ]);
  });
});
