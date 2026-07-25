// createModeController（モード切替ステートマシン）の単体検証。
//
// 設計意図: 単一 mount で chart/controller/pollers/replayHandle を 1 回生成した後、モード切替は
//   chart を **dispose も再生成もしない**。切替は「live pollers の start/stop」「リプレイ層ハンドルの
//   enable/disable」「controller.clearRevealCache」「SW モード通知」「body クラス」だけで行う。
// 構造: Arrange-Act-Assert。DOM/SW/lwc に依存しない純ロジックとして検証する（unified_root.js の
//   main() は document/window 不在の node では起動しないためインポートしても副作用がない）。

import { describe, it, expect, vi } from 'vitest';
import { createModeController, MODE } from '../js/unified_root.js';

// 呼び出し順序を 1 本の配列で記録する fake 一式を組む。
function makeHarness() {
  const calls = [];
  const controller = {
    clearRevealCache: () => calls.push('clearRevealCache'),
    // chart を破棄する API は controller には存在しない（構造的に再構築不能）。
  };
  const replayHandle = {
    enable: () => calls.push('replay.enable'),
    disable: () => calls.push('replay.disable'),
    // destroy は切替経路では絶対に呼ばれない（呼ばれたら記録して検出する）。
    destroy: () => calls.push('replay.destroy'),
  };
  const poller = () => ({
    start: () => calls.push('poller.start'),
    stop: () => calls.push('poller.stop'),
  });
  const pollers = [poller()];
  const setSwMode = (mode) => { calls.push(`sw:${mode}`); return Promise.resolve(true); };
  const applyMode = (mode) => calls.push(`ui:${mode}`);
  const mc = createModeController({
    controller, replayHandle, pollers, setSwMode, applyMode, initialMode: MODE.LIVE,
  });
  return { mc, calls };
}

describe('createModeController — 単一 mount の切替（chart 再構築なし）', () => {
  it('初期モードは live', () => {
    // Arrange / Act
    const { mc } = makeHarness();
    // Assert
    expect(mc.getMode()).toBe('live');
  });

  it('live→replay: pollers stop → clearRevealCache → SW=replay → replay.enable → ui=replay の順で切替、chart は破棄しない', async () => {
    // Arrange
    const { mc, calls } = makeHarness();
    // Act
    await mc.toggle('replay');
    // Assert: SW=replay を enable より前に切替（enable の再取得を /replay へ回す）。enable 前に pollers 停止。
    expect(calls).toEqual([
      'poller.stop', 'clearRevealCache', 'sw:replay', 'replay.enable', 'ui:replay',
    ]);
    // chart/リプレイ層の dispose 系は呼ばれない（再構築なしの構造的実証）。
    expect(calls).not.toContain('replay.destroy');
    expect(mc.getMode()).toBe('replay');
  });

  it('replay→live: SW=live → clearRevealCache → replay.disable → pollers start → ui=live の順で切替', async () => {
    // Arrange
    const { mc, calls } = makeHarness();
    await mc.toggle('replay');
    calls.length = 0; // replay 遷移分をクリアし、live 復帰だけを観測する。
    // Act
    await mc.toggle('live');
    // Assert: SW=live を disable より前（disable の全長復帰 fetch を /live へ回す）。disable→pollers 起動。
    expect(calls).toEqual([
      'sw:live', 'clearRevealCache', 'replay.disable', 'poller.start', 'ui:live',
    ]);
    expect(mc.getMode()).toBe('live');
  });

  it('同一モードへの toggle は no-op（切替を起こさない）', async () => {
    // Arrange
    const { mc, calls } = makeHarness();
    // Act: 既に live なので live 指定は無視される。
    await mc.toggle('live');
    // Assert
    expect(calls).toEqual([]);
    expect(mc.getMode()).toBe('live');
  });

  it('引数なし toggle は現モードの反対へ切替（live↔replay）', async () => {
    // Arrange
    const { mc } = makeHarness();
    // Act / Assert
    await mc.toggle();
    expect(mc.getMode()).toBe('replay');
    await mc.toggle();
    expect(mc.getMode()).toBe('live');
  });

  it('切替中（switching）に来た再入 toggle は無視される（二重切替の排他）', async () => {
    // Arrange: setSwMode を保留可能にして、遷移を途中で止める。
    const calls = [];
    let releaseSw;
    const gate = new Promise((r) => { releaseSw = r; });
    const controller = { clearRevealCache: () => calls.push('clearRevealCache') };
    const replayHandle = {
      enable: () => calls.push('replay.enable'),
      disable: () => calls.push('replay.disable'),
    };
    const pollers = [{ start: () => calls.push('poller.start'), stop: () => calls.push('poller.stop') }];
    const setSwMode = () => { calls.push('sw'); return gate; };
    const applyMode = (m) => calls.push(`ui:${m}`);
    const mc = createModeController({ controller, replayHandle, pollers, setSwMode, applyMode, initialMode: MODE.LIVE });
    // Act: 1 回目の toggle は sw 待ちで滞留。滞留中に 2 回目 toggle を投げる。
    const first = mc.toggle('replay');
    const second = mc.toggle('live'); // switching=true のため無視されるべき。
    await second; // 即 return（何もしない）。
    // Assert: 2 回目は enable/disable を増やしていない（sw までで滞留・enable 未達）。
    expect(calls).toEqual(['poller.stop', 'clearRevealCache', 'sw']);
    // 1 回目を解放して完了させる。
    releaseSw(true);
    await first;
    expect(calls).toEqual(['poller.stop', 'clearRevealCache', 'sw', 'replay.enable', 'ui:replay']);
    expect(mc.getMode()).toBe('replay');
  });
});
