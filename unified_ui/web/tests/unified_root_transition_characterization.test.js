// createModeController — 既存 3 モードの遷移手順を**そのまま**固定する特性テスト
// （characterization test）。
//
// なぜ在るか（T-4 の安全網）: `arch-spec §0 T-4` で createModeController をモード別名前付き
//   引数（`simHandle` 等）から `layers: Map<modeId,{enable,disable}>` へ一般化する。この作業は
//   リファクタリングであり、**live / replay / sim の遷移は 1 手も変わってはならない**。
//   ところが既存の検定は「simHandle を注入しない場合」と「注入する場合」で別ファイルに分かれ、
//   注入した状態の**完全な呼び出し列**はどこにも固定されていない（順序の一部だけを見ている）。
//   一般化のときに層の畳み方の順序が変わっても既存検定は緑のまま通る。
//
// よって本ファイルは、全ハンドルを注入した状態で **6 つの順序対すべての呼び出し列を完全一致で**
//   固定する。リファクタ前に緑であることを確認してから着手し、リファクタ後も緑であることが
//   「挙動不変」の機械的証拠になる（宣言ではなく検査で強制する）。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { describe, test, expect } from 'vitest';
import { createModeController, MODE } from '../js/unified_root.js';

// 全ハンドル（controller / replayHandle / simHandle / pollers）を注入した実配線相当のハーネス。
//   呼び出しは 1 本の配列へ順に記録する（順序そのものが契約）。
function makeHarness() {
  const calls = [];
  const controller = { clearRevealCache: () => calls.push('clearRevealCache') };
  const replayHandle = {
    enable: () => { calls.push('replay.enable'); return Promise.resolve(); },
    disable: () => { calls.push('replay.disable'); return Promise.resolve(); },
    destroy: () => calls.push('replay.destroy'),
  };
  const simHandle = {
    enable: () => { calls.push('sim.enable'); return Promise.resolve(); },
    disable: () => { calls.push('sim.disable'); return Promise.resolve(); },
  };
  const pollers = [{
    start: () => calls.push('poller.start'),
    stop: () => calls.push('poller.stop'),
  }];
  const mc = createModeController({
    controller,
    replayHandle,
    simHandle,
    pollers,
    setSwMode: (mode) => { calls.push(`sw:${mode}`); return Promise.resolve(true); },
    applyMode: (mode) => calls.push(`ui:${mode}`),
    initialMode: MODE.LIVE,
  });
  return { mc, calls };
}

// 遷移先ごとの手順（develop 実装の実測列）。行き先が同じなら、どこから来ても同じ列になる。
const TO_LIVE = [
  'sw:live', 'clearRevealCache', 'sim.disable', 'replay.disable', 'poller.start', 'ui:live',
];
const TO_REPLAY = [
  'poller.stop', 'clearRevealCache', 'sim.disable', 'sw:replay', 'replay.enable', 'ui:replay',
];
// sim は「再生」ではないのでリプレイ層を畳む。SW を一度 **既定モード** へ向けてから畳むのは、
//   `replay.disable()` の全長復帰 fetch（/candles 再取得）が **/candles を持つ core** へ届く
//   ようにするため（sim core は持たない＝404 でチャートがライブ全長へ戻らない）。
const TO_SIM = [
  'poller.stop', 'clearRevealCache', 'sw:live', 'replay.disable', 'sw:sim', 'sim.enable', 'ui:sim',
];

async function goto(mc, calls, ...modes) {
  for (const m of modes) {
    await mc.toggle(m);
  }
  calls.length = 0; // 直前までの遷移を捨て、最後の 1 手だけを観測する。
}

describe('createModeController — 既存 3 モード遷移の特性（T-4 リファクタの不変条件）', () => {
  test('live_to_replay_follows_the_recorded_sequence', async () => {
    // Arrange
    const { mc, calls } = makeHarness();
    // Act
    await mc.toggle(MODE.REPLAY);
    // Assert
    expect(calls).toEqual(TO_REPLAY);
    expect(mc.getMode()).toBe('replay');
  });

  test('live_to_sim_follows_the_recorded_sequence', async () => {
    // Arrange
    const { mc, calls } = makeHarness();
    // Act
    await mc.toggle(MODE.SIM);
    // Assert
    expect(calls).toEqual(TO_SIM);
    expect(mc.getMode()).toBe('sim');
  });

  test('replay_to_live_follows_the_recorded_sequence', async () => {
    // Arrange
    const { mc, calls } = makeHarness();
    await goto(mc, calls, MODE.REPLAY);
    // Act
    await mc.toggle(MODE.LIVE);
    // Assert
    expect(calls).toEqual(TO_LIVE);
  });

  test('replay_to_sim_follows_the_recorded_sequence', async () => {
    // Arrange
    const { mc, calls } = makeHarness();
    await goto(mc, calls, MODE.REPLAY);
    // Act
    await mc.toggle(MODE.SIM);
    // Assert
    expect(calls).toEqual(TO_SIM);
  });

  test('sim_to_live_follows_the_recorded_sequence', async () => {
    // Arrange
    const { mc, calls } = makeHarness();
    await goto(mc, calls, MODE.SIM);
    // Act
    await mc.toggle(MODE.LIVE);
    // Assert
    expect(calls).toEqual(TO_LIVE);
  });

  test('sim_to_replay_follows_the_recorded_sequence', async () => {
    // Arrange
    const { mc, calls } = makeHarness();
    await goto(mc, calls, MODE.SIM);
    // Act
    await mc.toggle(MODE.REPLAY);
    // Assert
    expect(calls).toEqual(TO_REPLAY);
  });

  test('no_transition_ever_destroys_the_chart_layer', async () => {
    // Arrange: 単一 mount の要（chart を dispose も再生成もしない）を全経路で確かめる。
    const { mc, calls } = makeHarness();
    // Act
    await mc.toggle(MODE.REPLAY);
    await mc.toggle(MODE.SIM);
    await mc.toggle(MODE.LIVE);
    // Assert
    expect(calls).not.toContain('replay.destroy');
  });

  test('named_entry_points_stay_available_for_the_existing_three_modes', async () => {
    // Arrange: 公開 API（enterLive / enterReplay / enterSim）は一般化後も残る。
    const { mc, calls } = makeHarness();
    // Act
    await mc.enterReplay();
    // Assert
    expect(calls).toEqual(TO_REPLAY);
    expect(mc.getMode()).toBe('replay');
  });
});
