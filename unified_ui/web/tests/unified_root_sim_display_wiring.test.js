// sim 表示層ハンドル（simHandle）の切替配線の単体検証（Phase 4 A）。
//
// 設計意図: sim モードは「再生」ではないのでリプレイ層は畳む（既存 enterSim の性質）。
//   そこへ **表示層の出し入れ**を足す。表示層は chart を持たない別の器（sim_display_view）で、
//   モードを出るときに必ず畳む（統合ページへ器・CSS を残さない）。
// 無波及: simHandle を注入しない既存の呼び出し（standalone / 既存検定）では 1 度も呼ばれず、
//   切替の呼び出し順も従来どおり（＝既存 4 本の検定が固定した順序を壊さない）。
import { describe, it, expect } from 'vitest';
import { createModeController, MODE } from '../js/unified_root.js';

function makeHarness({ withSim = true } = {}) {
  const calls = [];
  const controller = { clearRevealCache: () => calls.push('clearRevealCache') };
  const replayHandle = {
    enable: () => calls.push('replay.enable'),
    disable: () => calls.push('replay.disable'),
  };
  const pollers = [{
    start: () => calls.push('poller.start'),
    stop: () => calls.push('poller.stop'),
  }];
  const simHandle = {
    enable: () => { calls.push('sim.enable'); return Promise.resolve(); },
    disable: () => { calls.push('sim.disable'); return Promise.resolve(); },
  };
  const mc = createModeController({
    controller,
    replayHandle,
    simHandle: withSim ? simHandle : undefined,
    pollers,
    setSwMode: (mode) => { calls.push(`sw:${mode}`); return Promise.resolve(true); },
    applyMode: (mode) => calls.push(`ui:${mode}`),
    initialMode: MODE.LIVE,
  });
  return { mc, calls };
}

describe('createModeController — sim 表示層ハンドルの出し入れ', () => {
  it('live→sim: 表示層を enable する', async () => {
    const { mc, calls } = makeHarness();
    await mc.toggle(MODE.SIM);
    expect(calls).toContain('sim.enable');
    expect(mc.getMode()).toBe('sim');
  });

  it('live→sim: 表示層の enable は SW を sim へ向けた後（＝復帰 fetch の後）に行う', async () => {
    const { mc, calls } = makeHarness();
    await mc.toggle(MODE.SIM);
    expect(calls.indexOf('sim.enable')).toBeGreaterThan(calls.indexOf('sw:sim'));
    // 器の表示はモード反映（body クラス）より前に済ませる。
    expect(calls.indexOf('sim.enable')).toBeLessThan(calls.indexOf('ui:sim'));
  });

  it('live→sim: リプレイ層は従来どおり畳む（sim は「再生」ではない）', async () => {
    const { mc, calls } = makeHarness();
    await mc.toggle(MODE.SIM);
    expect(calls).toContain('replay.disable');
    expect(calls).not.toContain('replay.enable');
  });

  it('sim→live: 表示層を disable する（器・CSS を統合ページへ残さない）', async () => {
    const { mc, calls } = makeHarness();
    await mc.toggle(MODE.SIM);
    calls.length = 0;
    await mc.toggle(MODE.LIVE);
    expect(calls).toContain('sim.disable');
    expect(mc.getMode()).toBe('live');
  });

  it('sim→replay: 表示層を disable してからリプレイ層を enable する', async () => {
    const { mc, calls } = makeHarness();
    await mc.toggle(MODE.SIM);
    calls.length = 0;
    await mc.toggle(MODE.REPLAY);
    expect(calls.indexOf('sim.disable')).toBeGreaterThanOrEqual(0);
    expect(calls.indexOf('sim.disable')).toBeLessThan(calls.indexOf('replay.enable'));
  });

  it('sim→live→sim: 往復しても enable/disable が対で積み上がらない', async () => {
    const { mc, calls } = makeHarness();
    await mc.toggle(MODE.SIM);
    await mc.toggle(MODE.LIVE);
    await mc.toggle(MODE.SIM);
    expect(calls.filter((c) => c === 'sim.enable')).toHaveLength(2);
    expect(calls.filter((c) => c === 'sim.disable')).toHaveLength(1);
  });

  it('simHandle 未注入でも従来どおり動く（無波及）', async () => {
    const { mc, calls } = makeHarness({ withSim: false });
    await mc.toggle(MODE.SIM);
    await mc.toggle(MODE.LIVE);
    expect(calls.some((c) => c.startsWith('sim.'))).toBe(false);
    expect(mc.getMode()).toBe('live');
  });

  it('simHandle 未注入時の live→sim の呼び出し順は従来のまま', async () => {
    const { mc, calls } = makeHarness({ withSim: false });
    await mc.toggle(MODE.SIM);
    expect(calls).toEqual([
      'poller.stop', 'clearRevealCache', 'sw:live', 'replay.disable', 'sw:sim', 'ui:sim',
    ]);
  });
});
