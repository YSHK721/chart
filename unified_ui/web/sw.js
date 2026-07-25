// Service Worker（統合層）。基本設計書 §2 / §3。
//
// 役割: 既存フロントが出す root 相対 API fetch（`/compute`・`/candles` 等）を、
//   アクティブモード（live/replay）に応じて `/live/*` `/replay/*` へリライトして
//   ルータへ渡す。これにより既存 core は「自分宛の素パス要求のみ」を受ける（無編集）。
//
// リライトの純ロジックは js/sw_rewrite.js に分離（vitest 単体検証済み）。
// モジュール SW（type:'module'）として登録される（import 可）。

import { rewritePath } from './js/sw_rewrite.js';

// アクティブモード。unified_root.js が postMessage({type:'set-mode'}) で更新する。
let activeMode = 'live';

self.addEventListener('install', () => {
  // 新 SW を即時有効化（初回訪問で待機させない）。
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  // 有効化直後から既存クライアントを制御下に置く（初回訪問でも fetch を捕捉する）。
  event.waitUntil(self.clients.claim());
});

self.addEventListener('message', (event) => {
  const data = event.data || {};
  if (data.type === 'set-mode' && (data.mode === 'live' || data.mode === 'replay')) {
    activeMode = data.mode;
    // MessageChannel 経由の要求には ack を返す（送信側が反映完了を待てる）。
    if (event.ports && event.ports[0]) {
      event.ports[0].postMessage({ ok: true, mode: activeMode });
    }
  }
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  let url;
  try {
    url = new URL(req.url);
  } catch {
    return; // 解析不能な URL は素通し。
  }
  // 同一オリジンのみ対象（クロスオリジンは不変）。
  if (url.origin !== self.location.origin) {
    return;
  }
  const original = url.pathname + url.search;
  const rewritten = rewritePath(activeMode, original);
  if (rewritten === original) {
    return; // API パスでない（静的資産・既 prefix）＝素通し。
  }
  event.respondWith(proxyRewritten(req, url.origin + rewritten));
});

// 元要求の method/headers/body/credentials を保ったままリライト先 URL へ再 fetch する。
async function proxyRewritten(req, target) {
  const init = {
    method: req.method,
    headers: req.headers,
    credentials: req.credentials,
    mode: 'same-origin',
    redirect: req.redirect,
  };
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    init.body = await req.blob();
  }
  return fetch(target, init);
}
