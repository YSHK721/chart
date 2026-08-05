// series_kind 台帳の「追記で完結」宣言を **検定で強制する**（ISSUE-262）。
//
// series_render_router.js は「kind → 描画経路は series_kind 台帳（renderRoute）で一元化
//   （新種別は台帳追記で完結・OCP）」と宣言していた。しかし router は routed の初期化と
//   dispatch を直書きしており、台帳へ 1 行足しただけでは `routed[route]` が undefined になって
//   **黙って捨てられる**（描画されない）。宣言が施行されていなかった。
//
// 本検定は「台帳に在る全 renderRoute が router で実際に処理される」ことを固定する。
//   台帳へ種別を足して router を忘れたら落ちる。落ちたら router 側にも経路を足す
//   （＝宣言どおり「台帳追記で完結」にできない構造なら、宣言を先に正す）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { SERIES_KINDS } from '../js/domain/series_kind.js';

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const ROUTER = join(WEB, 'js', 'adapter', 'front', 'series_render_router.js');

function declaredRoutes() {
  return [...new Set(Object.values(SERIES_KINDS).map((k) => k.renderRoute).filter(Boolean))];
}

function routerHandledRoutes(source) {
  // `const routed = { line: [], histogram: [], ... }` の初期化キーを経路集合とみなす。
  const m = source.match(/const\s+routed\s*=\s*\{([^}]*)\}/);
  if (!m) return [];
  return [...m[1].matchAll(/([a-z_]+)\s*:/g)].map((x) => x[1]);
}

test('台帳の全 renderRoute が router で処理される（台帳追記で完結を保証）', () => {
  const source = readFileSync(ROUTER, 'utf8');
  const handled = new Set(routerHandledRoutes(source));
  const missing = declaredRoutes().filter((r) => !handled.has(r));
  assert.deepEqual(missing, [],
    `台帳に在る renderRoute を router が処理していません: ${missing.join(', ')}。`
    + ' router の routed 初期化と dispatch に経路を足してください'
    + '（足せない構造なら series_render_router.js の OCP 宣言を先に正すこと）。');
});

test('router が台帳に無い経路を持っていない（死に経路を作らない）', () => {
  const source = readFileSync(ROUTER, 'utf8');
  const declared = new Set(declaredRoutes());
  const stale = routerHandledRoutes(source).filter((r) => !declared.has(r));
  assert.deepEqual(stale, [],
    `router に台帳外の経路が残っています: ${stale.join(', ')}。台帳から消えた種別は router からも消す。`);
});

test('各 renderRoute に対応する renderer メソッドが router から呼ばれている', () => {
  // routed に入れるだけで dispatch を忘れると、やはり黙って捨てられる（描画されない）。
  const source = readFileSync(ROUTER, 'utf8');
  const missing = declaredRoutes().filter((route) => {
    const camel = route.replace(/_([a-z])/g, (_, c) => c.toUpperCase());
    const method = `render${camel.charAt(0).toUpperCase()}${camel.slice(1)}`;
    return !new RegExp(`_renderer\\.${method}\\s*\\(`).test(source);
  });
  assert.deepEqual(missing, [],
    `router が renderer を呼んでいない経路があります: ${missing.join(', ')}（routed に入れただけで描画されない）。`);
});
