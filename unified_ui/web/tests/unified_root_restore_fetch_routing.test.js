// 不変条件: `replayHandle.disable()` の全長復帰 fetch は、**必ず答えられる core** へ向かう。
//
// なぜこの不変条件が要るか:
//   `disable()` は reveal トリムを解いてライブ全長へ戻すため、`/candles` を再取得する
//   （`simulator/replay_ui/web/js/replay.js:532` の `catchUpToLiveTail()` →
//     `replay/replay_cursor.js:90-96` の `this._fetch('/candles?…')`）。この要求が
//   **その API を持たない core** へ向かうと 404 になり、チャートがライブ全長へ戻らない。
//   Phase 1 の sim core は静的配信しか持たない（`simulator/sim_ui/framework/serve_sim.py`）ので、
//   sim へ向けてはならない。
//
// 行き先を決めるのは誰か（結線の実測・ISSUE-362 以降）:
//   `unified_root.js` → `bootstrap({ fetch: routedFetch })` →
//   `composition_root_front.js:582` → `setupReplay({ fetchImpl: fetch })` →
//   `replay.js:72` → `new ReplayCursor({ fetchImpl })` → `this._fetch('/candles')`。
//   つまり prefix を付けるのは **front の routedFetch** であり、その参照するモードは
//   `modeController.getMode()`（= `activeMode`）である。Service Worker ではない。
//   `activeMode` は各遷移関数の**末尾**で更新されるため、遷移の途中で走る `disable()` は
//   「遷移前のモード」の prefix を得る。
//
// 本テストは、その結線を**実物の createRoutedFetch で組んで**行き先を観測する
//   （SW モードを幾つに設定しても、front 付与が既 prefix を作るため SW は素通しになる。
//    この冪等性は sw_rewrite.test.js が別途固定している）。
// 構造は AAA。

import { describe, test, expect } from 'vitest';
import { createModeController, MODE } from '../js/unified_root.js';
import { createRoutedFetch } from '../js/routed_fetch.js';

const ORIGIN = 'http://127.0.0.1:8000';

// unified_root.js の main() と**同じ形**で routedFetch を modeController へ結線する。
//   replayHandle.disable は本物と同じ条件で動く: enable 済み（wasEnabled）のときだけ
//   全長復帰 fetch を出し、未 enable なら早期 return（HTTP を出さない）。
function makeHarness() {
  const urls = [];
  const swModes = [];
  let mc = null;
  let wasEnabled = false;

  const routedFetch = createRoutedFetch({
    baseFetch: (input) => {
      urls.push(typeof input === 'string' ? input : input.url);
      return Promise.resolve({ ok: true });
    },
    getMode: () => (mc ? mc.getMode() : MODE.LIVE),   // unified_root.js:185 と同型
    origin: ORIGIN,
  });

  const replayHandle = {
    enable: async () => { wasEnabled = true; },
    disable: async () => {
      if (!wasEnabled) {
        return;                       // replay.js:513-515 の早期 return（HTTP なし）
      }
      wasEnabled = false;
      await routedFetch('/candles?datasetRef=jp225_tick');   // catchUpToLiveTail 相当
    },
  };

  mc = createModeController({
    controller: { clearRevealCache: () => {} },
    replayHandle,
    pollers: [{ start: () => {}, stop: () => {} }],
    setSwMode: (m) => { swModes.push(m); return Promise.resolve(true); },
    applyMode: () => {},
    initialMode: MODE.LIVE,
  });

  return { mc, urls, swModes };
}

describe('全長復帰 fetch の行き先（disable の不変条件）', () => {
  test('replay→sim: 復帰 fetch は sim core へ向かわない（sim は /candles を持たない）', async () => {
    // Arrange
    const { mc, urls } = makeHarness();
    await mc.toggle('replay');
    urls.length = 0;
    // Act
    await mc.toggle('sim');
    // Assert: 404 になる先（sim core）へ向けてはならない。
    expect(urls.some((u) => u.startsWith('/sim/'))).toBe(false);
    expect(urls).toEqual(['/replay/candles?datasetRef=jp225_tick']);
  });

  test('replay→live: 復帰 fetch は live core へは向かわず、遷移前モードの core が答える', async () => {
    // Arrange: 既存経路の実態を固定する（unified_root.js:100 のコメントが述べる意図との差）。
    const { mc, urls } = makeHarness();
    await mc.toggle('replay');
    urls.length = 0;
    // Act
    await mc.toggle('live');
    // Assert
    expect(urls).toEqual(['/replay/candles?datasetRef=jp225_tick']);
  });

  test('live→sim: replay 未 enable なら復帰 fetch を 1 本も出さない（早期 return）', async () => {
    // Arrange
    const { mc, urls } = makeHarness();
    // Act
    await mc.toggle('sim');
    // Assert
    expect(urls).toEqual([]);
  });

  test('sim→replay→sim: 往復しても sim core へ /candles を出さない', async () => {
    // Arrange
    const { mc, urls } = makeHarness();
    await mc.toggle('sim');
    await mc.toggle('replay');
    urls.length = 0;
    // Act
    await mc.toggle('sim');
    // Assert
    expect(urls.some((u) => u.startsWith('/sim/'))).toBe(false);
  });
});
