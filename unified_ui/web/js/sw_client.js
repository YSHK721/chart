// Service Worker アダプタ（統合ルートから抽出・葉モジュール）。
//
// 役割:
//   - Service Worker（/sw.js）の登録とフェイルクローズ判定（registerServiceWorker）。
//   - アクティブモード（live/replay）の通知と ack 待ち（notifySwMode）。
//
// 無波及順守: 本モジュールは unified_root を import しない（葉モジュール＝循環依存なし）。
//   関数実体は unified_root.js から**ロジック無改変で移設**したもの（DOM/SW メッセージ・タイマは不変）。

// ---- Service Worker: アクティブモード通知（ack 待ち）--------------------------
export function notifySwMode(mode) {
  return new Promise((resolve) => {
    const ctrl =
      typeof navigator !== 'undefined' && navigator.serviceWorker
        ? navigator.serviceWorker.controller
        : null;
    if (!ctrl) {
      resolve(false);
      return;
    }
    const channel = new MessageChannel();
    let settled = false;
    const done = (ok) => {
      if (!settled) {
        settled = true;
        resolve(ok);
      }
    };
    channel.port1.onmessage = (event) => {
      done(!!(event.data && event.data.ok));
    };
    try {
      ctrl.postMessage({ type: 'set-mode', mode }, [channel.port2]);
    } catch {
      done(false);
      return;
    }
    setTimeout(() => done(false), 500); // ack が来ない環境でも進行を止めない（保険）。
  });
}

// 一度だけリロードしたことを示すセッションフラグ（無限リロード防止）。
const RELOAD_GUARD = 'unified_sw_reloaded';

// SW の activate 中 `clients.claim()` が完了して制御下へ入るまでの待ち上限（ms）。
//   `navigator.serviceWorker.ready` は「アクティブな登録がある」時点で解決するため、claim が
//   まだ届かず controller が null のことがある（競合）。ここで controllerchange を待たないと
//   「制御下でない」と誤判定してリロード（or フェイルクローズ）に落ちる。
const CLAIM_WAIT_MS = 3000;

// controllerchange（＝clients.claim() 到達）を上限つきで待つ。
function waitForController(timeoutMs) {
  return new Promise((resolve) => {
    const sw = navigator.serviceWorker;
    if (sw.controller) {
      resolve(true);
      return;
    }
    let timer = null;
    const onChange = () => {
      if (timer !== null) clearTimeout(timer);
      sw.removeEventListener('controllerchange', onChange);
      resolve(!!sw.controller);
    };
    sw.addEventListener('controllerchange', onChange);
    timer = setTimeout(() => {
      sw.removeEventListener('controllerchange', onChange);
      resolve(!!sw.controller);
    }, timeoutMs);
  });
}

// ---- Service Worker 登録（フェイルクローズ判定つき）--------------------------
export async function registerServiceWorker() {
  if (typeof navigator === 'undefined' || !navigator.serviceWorker) {
    return false; // 非対応ブラウザ。呼び出し側でフェイルクローズ。
  }
  try {
    await navigator.serviceWorker.register('/sw.js', { type: 'module', scope: '/' });
    await navigator.serviceWorker.ready;
  } catch {
    return false;
  }
  // ready 直後に未制御でも、claim が届けば制御下に入る（リロード不要）。
  if (await waitForController(CLAIM_WAIT_MS)) {
    // 制御下に入れたのでリロードフラグを解除する。これが無いと、SW 登録解除や更新で
    // 同一タブが再び未制御になったとき「既にリロード済み」と見なされ、タブを閉じるまで
    // 二度と起動できない（＝報告された起動中止の再現条件）。
    sessionStorage.removeItem(RELOAD_GUARD);
    return true;
  }
  // まだ制御下でない（初回訪問）。一度だけリロードして制御下に入る。
  if (!sessionStorage.getItem(RELOAD_GUARD)) {
    sessionStorage.setItem(RELOAD_GUARD, '1');
    location.reload();
    await new Promise(() => {}); // reload 後に再実行されるため待機。
  }
  return !!navigator.serviceWorker.controller;
}
