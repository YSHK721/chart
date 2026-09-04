// Service Worker（unified_ui/web/sw.js）のモード受理契約テスト。
//
// 保証対象（基本設計書 §3.5.6 #8）: `postMessage({type:'set-mode', mode})` の受理判定が
//   **モード定義表の許可集合**であること。旧実装は `data.mode === 'live' || data.mode === 'replay'`
//   の 2 値ハードコードで、'sim' を送っても activeMode が更新されず ack も返らない。
//   ack を待つ側（sw_client.notifySwMode）は「反映済み」と誤認できないまま先へ進み、
//   SW を通る要求だけが**前のモードの core へ流れ続ける**（front 付与と食い違う）。
//
// 方式: sw.js は `self` 上の addEventListener で登録するだけの素の ESM なので、`self` を
//   最小 fake へ差し替えてから import し、登録されたリスナを直接叩いて契約を観測する
//   （実装の内部状態を覗かず、外から見える ack と fetch の行き先だけで判定する）。
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { describe, test, expect, beforeEach, afterEach, vi } from 'vitest';
import { MODE_IDS, prefixOf } from '../js/mode_table.js';

const ORIGIN = 'http://127.0.0.1:8000';

// self（ServiceWorkerGlobalScope）の最小 fake。登録されたリスナを種別ごとに保持する。
function fakeSelf() {
  const listeners = new Map();
  return {
    listeners,
    addEventListener: (type, fn) => listeners.set(type, fn),
    skipWaiting: () => {},
    clients: { claim: () => Promise.resolve() },
    location: { origin: ORIGIN },
  };
}

// set-mode を送り、ack（送信側が反映完了を待つための応答）を受け取る。
function sendSetMode(sw, mode) {
  const acks = [];
  sw.listeners.get('message')({
    data: { type: 'set-mode', mode },
    ports: [{ postMessage: (m) => acks.push(m) }],
  });
  return acks;
}

// GET 要求を 1 本流し、SW が実際に叩いた URL を返す（respondWith されなければ null）。
async function fetchThrough(sw, path) {
  const targets = [];
  vi.stubGlobal('fetch', (target) => {
    targets.push(target);
    return Promise.resolve({ ok: true });
  });
  let responded = null;
  const handler = sw.listeners.get('fetch');
  handler({
    request: {
      url: ORIGIN + path,
      method: 'GET',
      headers: {},
      credentials: 'same-origin',
      redirect: 'follow',
    },
    respondWith: (p) => { responded = p; },
  });
  if (responded) {
    await responded;
  }
  return targets.length ? targets[0] : null;
}

let sw;

beforeEach(async () => {
  sw = fakeSelf();
  vi.stubGlobal('self', sw);
  // sw.js は import 時に self へリスナを登録する。fake を差し替えるたびに再評価させる。
  vi.resetModules();
  await import('../sw.js');
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('sw.js set-mode — 受理モードの表駆動化', () => {
  // --- 既存挙動の固定（2 値時代からの回帰壁）---
  test('set_mode_replay_is_accepted_and_acked', () => {
    // Arrange / Act
    const acks = sendSetMode(sw, 'replay');
    // Assert
    expect(acks).toEqual([{ ok: true, mode: 'replay' }]);
  });

  // --- ★Red★ 第 3 モード ---
  test('set_mode_sim_is_accepted_and_acked', () => {
    // Arrange / Act
    const acks = sendSetMode(sw, 'sim');
    // Assert: 受理されなければ ack が 1 件も返らない（送信側は反映を確認できない）。
    expect(acks).toEqual([{ ok: true, mode: 'sim' }]);
  });

  test('set_mode_sim_makes_subsequent_api_fetch_go_to_sim_core', async () => {
    // Arrange
    sendSetMode(sw, 'sim');
    // Act
    const target = await fetchThrough(sw, '/candles?tf=1D');
    // Assert: SW を通る要求も sim core へ回る（front 付与と食い違わない）。
    expect(target).toBe(`${ORIGIN}/sim/candles?tf=1D`);
  });

  test('every_mode_in_the_table_is_accepted', () => {
    // Arrange / Act / Assert: 表に載っている全モードが受理される（第 4 モードも自動で覆う）。
    for (const id of MODE_IDS) {
      expect(sendSetMode(sw, id)).toEqual([{ ok: true, mode: id }]);
    }
  });

  test('every_mode_in_the_table_routes_api_fetch_to_its_own_prefix', async () => {
    for (const id of MODE_IDS) {
      sendSetMode(sw, id);
      const target = await fetchThrough(sw, '/candles');
      expect(target).toBe(`${ORIGIN}${prefixOf(id)}/candles`);
    }
  });

  // --- 異常系: 表に無いモードは受理しない（activeMode を汚さない）---
  test('set_mode_with_unknown_mode_is_rejected_without_ack', async () => {
    // Arrange: いったん sim を確定させてから、未知値を投げる。
    sendSetMode(sw, 'sim');
    // Act
    const acks = sendSetMode(sw, 'nope');
    // Assert: ack なし。直前の sim が保たれる（未知値で上書きされない）。
    expect(acks).toEqual([]);
    expect(await fetchThrough(sw, '/candles')).toBe(`${ORIGIN}/sim/candles`);
  });
});
