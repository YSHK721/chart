// createModeController — T-4 拡張点（`layers: Map<modeId,{enable,disable}>`）と
// 第 4 モード dashboard の表示層配線の単体検証。
//
// なぜ在るか（arch-spec §0 T-4 / ISSUE-452）: 従来の createModeController は表示層を
//   **モード別の名前付き引数**（`simHandle`）で受け、遷移手続きを `TRANSITIONS` へ手で並べていた。
//   この形ではモードを 1 つ足すたびに引数・遷移関数・表の 3 箇所を同時に直すことになり、
//   1 箇所でも取り残すと**無症状で誤動作する**（押しても何も出ない／前のモードの器が残る）。
//   よって表示層は `layers`（モード名 → {enable, disable}）で受け、遷移はモード定義表の走査で
//   組み立てる。第 4 モードの追加は**表の 1 行と layers の 1 エントリ**で完結する。
//
// 遷移の形はモード名ではなく**表の属性**から決まる:
//   - 既定モード（live）        … SW を戻し、層を畳み、live pollers を起動する
//   - chartApi を持つ層（replay）… pollers を止め、SW を向けてから層を enable する
//   - chartApi を持たない層（sim / dashboard）… pollers を止め、SW を一度**既定モード**へ
//     向けてリプレイ層の全長復帰 fetch を `/candles` を持つ core へ届かせ、そのあと SW を
//     自モードへ向けて層を enable する
//   dashboard は 3 番目に該当する（sim の前例に厳密に倣う）。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { describe, test, expect } from 'vitest';
import { createModeController, MODE } from '../js/unified_root.js';

function makeLayer(name, calls) {
  return {
    enable: () => { calls.push(`${name}.enable`); return Promise.resolve(); },
    disable: () => { calls.push(`${name}.disable`); return Promise.resolve(); },
  };
}

// dashboard 層だけを `layers` で注入する（sim 層は注入しない＝呼び出し列を読みやすくする）。
function makeHarness({ layerModes = ['dashboard'] } = {}) {
  const calls = [];
  const controller = { clearRevealCache: () => calls.push('clearRevealCache') };
  const replayHandle = {
    enable: () => { calls.push('replay.enable'); return Promise.resolve(); },
    disable: () => { calls.push('replay.disable'); return Promise.resolve(); },
  };
  const pollers = [{
    start: () => calls.push('poller.start'),
    stop: () => calls.push('poller.stop'),
  }];
  const layers = new Map(layerModes.map((id) => [id, makeLayer(id, calls)]));
  const mc = createModeController({
    controller,
    replayHandle,
    layers,
    pollers,
    setSwMode: (mode) => { calls.push(`sw:${mode}`); return Promise.resolve(true); },
    applyMode: (mode) => calls.push(`ui:${mode}`),
    initialMode: MODE.LIVE,
  });
  return { mc, calls };
}

describe('createModeController — layers 化と第 4 モード dashboard', () => {
  // --- 拡張点そのもの（layers Map で層を受ける）-------------------------------------
  test('layers_map_registers_the_display_layer_of_a_mode_and_the_transition_enables_it', () => {
    // Arrange / Act は makeHarness が実施済み。
    const { mc, calls } = makeHarness();
    // Act
    return mc.toggle('dashboard').then(() => {
      // Assert: 名前付き引数を足さずに層が結線される（第 4 モードで本体を改変しない）。
      expect(calls).toContain('dashboard.enable');
      expect(mc.getMode()).toBe('dashboard');
    });
  });

  test('dashboard_transition_follows_the_same_shape_as_the_sim_precedent', async () => {
    // Arrange
    const { mc, calls } = makeHarness();
    // Act
    await mc.toggle('dashboard');
    // Assert: sim と同形（SW を一度 live へ向けてからリプレイ層を畳み、そのあと自モードへ）。
    expect(calls).toEqual([
      'poller.stop', 'clearRevealCache', 'sw:live', 'replay.disable',
      'sw:dashboard', 'dashboard.enable', 'ui:dashboard',
    ]);
  });

  test('dashboard_layer_is_enabled_only_after_the_sw_points_at_the_dashboard_core', async () => {
    // Arrange: 並びの比較ではなく「enable の瞬間に SW がどこを向いているか」を直接見る。
    const { mc, calls } = makeHarness();
    // Act
    await mc.toggle('dashboard');
    // Assert
    const swBeforeEnable = calls.slice(0, calls.indexOf('dashboard.enable'))
      .filter((c) => c.startsWith('sw:')).pop();
    expect(swBeforeEnable).toBe('sw:dashboard');
    // 器の表示はモード反映（body クラス）より前に済ませる（sim と同じ）。
    expect(calls.indexOf('dashboard.enable')).toBeLessThan(calls.indexOf('ui:dashboard'));
  });

  test('replay_layer_is_unwound_while_the_sw_still_points_at_a_core_that_serves_candles', async () => {
    // Arrange: 不変条件そのものの表明。`replay.disable()` の全長復帰 fetch は `/candles` を
    //   持つ core へ届かなければならない。dashboard core は持たない（chartApi:false）。
    const { mc, calls } = makeHarness();
    // Act
    await mc.toggle('dashboard');
    // Assert
    const swBeforeDisable = calls.slice(0, calls.indexOf('replay.disable'))
      .filter((c) => c.startsWith('sw:')).pop();
    expect(swBeforeDisable).toBe('sw:live');
  });

  test('leaving_dashboard_for_live_folds_the_dashboard_layer', async () => {
    // Arrange
    const { mc, calls } = makeHarness();
    await mc.toggle('dashboard');
    calls.length = 0;
    // Act
    await mc.toggle(MODE.LIVE);
    // Assert: 器を統合ページへ残さない（sim と同じ規律）。
    expect(calls).toEqual([
      'sw:live', 'clearRevealCache', 'dashboard.disable', 'replay.disable', 'poller.start', 'ui:live',
    ]);
  });

  test('leaving_dashboard_for_replay_folds_it_before_the_replay_layer_is_enabled', async () => {
    // Arrange
    const { mc, calls } = makeHarness();
    await mc.toggle('dashboard');
    calls.length = 0;
    // Act
    await mc.toggle(MODE.REPLAY);
    // Assert
    expect(calls).toEqual([
      'poller.stop', 'clearRevealCache', 'dashboard.disable', 'sw:replay', 'replay.enable', 'ui:replay',
    ]);
  });

  // --- 層が 2 つ在るとき（sim ⇄ dashboard）------------------------------------------
  test('moving_between_two_layers_folds_the_one_being_left_and_never_the_target', async () => {
    // Arrange: chartApi を持たない層が 2 つ在る構成（sim と dashboard）。
    const { mc, calls } = makeHarness({ layerModes: ['sim', 'dashboard'] });
    await mc.toggle(MODE.SIM);
    calls.length = 0;
    // Act
    await mc.toggle('dashboard');
    // Assert: 出る側だけを畳む。目標の層を畳んでから開き直すような無駄をしない。
    expect(calls).toEqual([
      'poller.stop', 'clearRevealCache', 'sim.disable', 'sw:live', 'replay.disable',
      'sw:dashboard', 'dashboard.enable', 'ui:dashboard',
    ]);
    expect(calls).not.toContain('dashboard.disable');
  });

  test('moving_back_from_dashboard_to_sim_folds_the_dashboard_layer', async () => {
    // Arrange
    const { mc, calls } = makeHarness({ layerModes: ['sim', 'dashboard'] });
    await mc.toggle('dashboard');
    calls.length = 0;
    // Act
    await mc.toggle(MODE.SIM);
    // Assert
    expect(calls).toEqual([
      'poller.stop', 'clearRevealCache', 'dashboard.disable', 'sw:live', 'replay.disable',
      'sw:sim', 'sim.enable', 'ui:sim',
    ]);
  });

  test('round_trip_between_the_two_layers_does_not_stack_enable_calls', async () => {
    // Arrange
    const { mc, calls } = makeHarness({ layerModes: ['sim', 'dashboard'] });
    // Act
    await mc.toggle('dashboard');
    await mc.toggle(MODE.SIM);
    await mc.toggle('dashboard');
    // Assert: 開いた回数と畳んだ回数が対で積み上がらない（器の二重生成を作らない）。
    expect(calls.filter((c) => c === 'dashboard.enable')).toHaveLength(2);
    expect(calls.filter((c) => c === 'dashboard.disable')).toHaveLength(1);
    expect(calls.filter((c) => c === 'sim.enable')).toHaveLength(1);
  });

  // --- 無波及（層を注入しない構成）--------------------------------------------------
  test('a_mode_without_a_registered_layer_still_transitions', async () => {
    // Arrange: dashboard の層を注入しない（core 未起動・standalone・既存検定と同じ状況）。
    const { mc, calls } = makeHarness({ layerModes: [] });
    // Act
    await mc.toggle('dashboard');
    // Assert: 遷移そのものは成立し、層の呼び出しは 1 度も起きない。
    expect(mc.getMode()).toBe('dashboard');
    expect(calls.some((c) => c.startsWith('dashboard.'))).toBe(false);
    expect(calls).toEqual([
      'poller.stop', 'clearRevealCache', 'sw:live', 'replay.disable', 'sw:dashboard', 'ui:dashboard',
    ]);
  });

  test('the_named_sim_handle_argument_is_still_honoured_as_the_sim_layer', async () => {
    // Arrange: 既存の呼び出し形（`simHandle`）は layers の sim エントリと等価に扱う
    //   （一般化は加法であり、既存の注入形を壊さない）。
    const calls = [];
    const mc = createModeController({
      controller: { clearRevealCache: () => calls.push('clearRevealCache') },
      replayHandle: {
        enable: () => calls.push('replay.enable'),
        disable: () => calls.push('replay.disable'),
      },
      simHandle: makeLayer('sim', calls),
      pollers: [{ start: () => calls.push('poller.start'), stop: () => calls.push('poller.stop') }],
      setSwMode: (mode) => { calls.push(`sw:${mode}`); return Promise.resolve(true); },
      applyMode: (mode) => calls.push(`ui:${mode}`),
      initialMode: MODE.LIVE,
    });
    // Act
    await mc.toggle(MODE.SIM);
    // Assert
    expect(calls).toEqual([
      'poller.stop', 'clearRevealCache', 'sw:live', 'replay.disable', 'sw:sim', 'sim.enable', 'ui:sim',
    ]);
  });

  // --- 遷移表がモード定義表由来であること（第 5 モードで本体を改変しないことの土台）------
  test('every_mode_in_the_table_has_a_transition_and_unknown_values_have_none', async () => {
    // Arrange
    const { mc } = makeHarness({ layerModes: [] });
    // Act / Assert: 表に載っているモードへは遷移できる。
    for (const id of ['replay', 'sim', 'dashboard', 'live']) {
      await mc.toggle(id);
      expect(mc.getMode()).toBe(id);
    }
    // 表に無い値は状態を壊さない（全域性）。
    await mc.toggle('nope');
    expect(mc.getMode()).toBe('live');
  });
});
