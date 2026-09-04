// sw_client（Service Worker アダプタ）の契約テスト。
//
// 保証対象: unified_root.js から抽出した notifySwMode / registerServiceWorker の移設後契約を固定する。
//   - notifySwMode: controller 不在なら false / postMessage は {type:'set-mode', mode} + port2 転送 /
//     ack の ok を resolve 値へ写像。
//   - registerServiceWorker: serviceWorker 非対応なら false（フェイルクローズ）。
// 環境は node（DOM なし）。navigator / MessageChannel はテスト内で最小 fake に差し替える（vi.stubGlobal）。
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { describe, test, expect, vi, afterEach } from 'vitest';
import { notifySwMode, registerServiceWorker } from '../js/sw_client.js';

// port1↔port2 を相互リンクした最小 MessageChannel fake（port2.postMessage が port1.onmessage を発火）。
class FakePort {
  constructor() {
    this.onmessage = null;
    this._pair = null;
  }

  postMessage(data) {
    if (this._pair && typeof this._pair.onmessage === 'function') {
      this._pair.onmessage({ data });
    }
  }
}

class FakeMessageChannel {
  constructor() {
    this.port1 = new FakePort();
    this.port2 = new FakePort();
    this.port1._pair = this.port2;
    this.port2._pair = this.port1;
  }
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('notifySwMode — アクティブモード通知（ack 待ち）', () => {
  test('controller_不在_falseをresolveする', async () => {
    // Arrange
    vi.stubGlobal('navigator', { serviceWorker: { controller: null } });
    vi.stubGlobal('MessageChannel', FakeMessageChannel);
    // Act
    const ok = await notifySwMode('replay');
    // Assert
    expect(ok).toBe(false);
  });

  test('ack_ok_true_postMessageのペイロードとport転送を伴いtrueをresolveする', async () => {
    // Arrange
    const posted = [];
    const controller = {
      postMessage: (msg, transfer) => {
        posted.push({ msg, transfer });
        transfer[0].postMessage({ ok: true }); // SW からの ack を模擬。
      },
    };
    vi.stubGlobal('navigator', { serviceWorker: { controller } });
    vi.stubGlobal('MessageChannel', FakeMessageChannel);
    // Act
    const ok = await notifySwMode('replay');
    // Assert
    expect(ok).toBe(true);
    expect(posted).toHaveLength(1);
    expect(posted[0].msg).toEqual({ type: 'set-mode', mode: 'replay' });
    expect(posted[0].transfer).toHaveLength(1); // port2 を転送している。
  });

  test('ack_ok_false_falseをresolveする', async () => {
    // Arrange
    const controller = {
      postMessage: (_msg, transfer) => {
        transfer[0].postMessage({ ok: false });
      },
    };
    vi.stubGlobal('navigator', { serviceWorker: { controller } });
    vi.stubGlobal('MessageChannel', FakeMessageChannel);
    // Act
    const ok = await notifySwMode('live');
    // Assert
    expect(ok).toBe(false);
  });
});

// 最小 sessionStorage fake（getItem/setItem/removeItem のみ）。
function fakeSessionStorage(initial = {}) {
  const store = { ...initial };
  return {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
    _store: store,
  };
}

// 最小 navigator.serviceWorker fake。
//   claimAfterMs: activate 中の clients.claim() が controller を立てるまでの遅延（null=永久に立たない）。
function fakeServiceWorker({ claimAfterMs = null } = {}) {
  const listeners = { controllerchange: [] };
  const sw = {
    controller: null,
    register: async () => ({}),
    ready: Promise.resolve({}),
    addEventListener: (type, fn) => { (listeners[type] || (listeners[type] = [])).push(fn); },
    removeEventListener: (type, fn) => {
      const arr = listeners[type] || [];
      const i = arr.indexOf(fn);
      if (i >= 0) arr.splice(i, 1);
    },
  };
  if (claimAfterMs != null) {
    setTimeout(() => {
      sw.controller = {};
      for (const fn of [...(listeners.controllerchange || [])]) fn({});
    }, claimAfterMs);
  }
  return sw;
}

describe('registerServiceWorker — 登録とフェイルクローズ判定', () => {
  test('serviceWorker非対応_falseをresolveする', async () => {
    // Arrange: navigator はあるが serviceWorker を持たない。
    vi.stubGlobal('navigator', {});
    // Act
    const ok = await registerServiceWorker();
    // Assert
    expect(ok).toBe(false);
  });

  test('ready直後はcontroller不在_claim後のcontrollerchange_リロードせずtrueを返す', async () => {
    // Arrange: ready 解決時点では未制御。20ms 後に clients.claim() 相当で制御下へ入る。
    const reloads = [];
    vi.stubGlobal('navigator', { serviceWorker: fakeServiceWorker({ claimAfterMs: 20 }) });
    vi.stubGlobal('sessionStorage', fakeSessionStorage());
    vi.stubGlobal('location', { reload: () => reloads.push(1) });
    // Act
    const ok = await registerServiceWorker();
    // Assert: claim を待って true（リロード不要＝チラつき無し）。
    expect(ok).toBe(true);
    expect(reloads).toHaveLength(0);
  });

  test('リロード済みフラグ有り_claimで制御下に入る_trueを返しフラグを解除する', async () => {
    // Arrange: 同一セッションで既に 1 回リロード済み（SW 登録解除→再読込などで再び未制御になった状況）。
    const storage = fakeSessionStorage({ unified_sw_reloaded: '1' });
    vi.stubGlobal('navigator', { serviceWorker: fakeServiceWorker({ claimAfterMs: 20 }) });
    vi.stubGlobal('sessionStorage', storage);
    vi.stubGlobal('location', { reload: () => {} });
    // Act
    const ok = await registerServiceWorker();
    // Assert: 起動できる＋フラグは解除（次に未制御化しても再度 1 回リロードできる）。
    expect(ok).toBe(true);
    expect(storage.getItem('unified_sw_reloaded')).toBe(null);
  });

  test('claimが来ずリロード済みフラグ有り_falseを返す（フェイルクローズ維持）', async () => {
    // Arrange: 永久に未制御 かつ 既にリロード済み。
    const reloads = [];
    vi.stubGlobal('navigator', { serviceWorker: fakeServiceWorker({ claimAfterMs: null }) });
    vi.stubGlobal('sessionStorage', fakeSessionStorage({ unified_sw_reloaded: '1' }));
    vi.stubGlobal('location', { reload: () => reloads.push(1) });
    // Act
    const ok = await registerServiceWorker();
    // Assert: 無限リロードはせず false（呼び出し側でフェイルクローズ）。
    expect(ok).toBe(false);
    expect(reloads).toHaveLength(0);
  });
});
