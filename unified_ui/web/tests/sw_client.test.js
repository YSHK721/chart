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

describe('registerServiceWorker — 登録とフェイルクローズ判定', () => {
  test('serviceWorker非対応_falseをresolveする', async () => {
    // Arrange: navigator はあるが serviceWorker を持たない。
    vi.stubGlobal('navigator', {});
    // Act
    const ok = await registerServiceWorker();
    // Assert
    expect(ok).toBe(false);
  });
});
