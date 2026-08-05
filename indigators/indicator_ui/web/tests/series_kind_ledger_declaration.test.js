// series_kind 台帳の「追記で完結」を **検定で強制する**（ISSUE-262 → ISSUE-270）。
//
// 経緯:
//   かつて router 側が `routed` の初期化と 4 分岐の dispatch を直書きしており、台帳へ種別を
//   1 行足しただけでは `routed[route]` が undefined になって **例外も出さず黙って捨てられた**
//   （描画されない）。経路の知識が台帳と router の 2 箇所に分かれていたのが原因。
//   ISSUE-270 で経路（順序・メソッド名・呼び方）を台帳 `RENDER_ROUTES` へ集約し、router は
//   本表を上から順に回すだけにした。
//
// 本検定が固定すること:
//   (1) 台帳の全 renderRoute が RENDER_ROUTES に宣言されている（追記漏れを落とす）
//   (2) RENDER_ROUTES の method が ChartRenderer に実在する
//   (3) router が経路名を 1 つも直書きしていない（知識が台帳側にある）
//   (4) 未宣言経路は黙って捨てず例外になる

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { SERIES_KINDS, RENDER_ROUTES, seriesKind } from '../js/domain/series_kind.js';
import { SeriesRenderRouter } from '../js/adapter/front/series_render_router.js';

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const ROUTER_SRC = readFileSync(join(WEB, 'js', 'adapter', 'front', 'series_render_router.js'), 'utf8');
const RENDERER_SRC = readFileSync(join(WEB, 'js', 'adapter', 'front', 'chart_renderer.js'), 'utf8');

const declaredRoutes = () =>
  [...new Set(Object.values(SERIES_KINDS).map((k) => k.renderRoute).filter(Boolean))];

test('台帳の全 renderRoute が RENDER_ROUTES に宣言されている', () => {
  const wired = new Set(RENDER_ROUTES.map((r) => r.route));
  const missing = declaredRoutes().filter((r) => !wired.has(r));
  assert.deepEqual(missing, [],
    `SERIES_KINDS が使う経路が RENDER_ROUTES に在りません: ${missing.join(', ')}。`
    + ' RENDER_ROUTES へ { route, method, perItem, opts } を追加してください。');
});

test('RENDER_ROUTES に、どの kind からも使われない経路が無い（死に経路を作らない）', () => {
  const used = new Set(declaredRoutes());
  const stale = RENDER_ROUTES.map((r) => r.route).filter((r) => !used.has(r));
  assert.deepEqual(stale, [], `使われない経路が残っています: ${stale.join(', ')}`);
});

test('RENDER_ROUTES の method が ChartRenderer に実在する', () => {
  const missing = RENDER_ROUTES
    .map((r) => r.method)
    .filter((m) => !new RegExp(`^\\s{2}${m}\\s*\\(`, 'm').test(RENDERER_SRC));
  assert.deepEqual(missing, [],
    `ChartRenderer に存在しないメソッドが宣言されています: ${missing.join(', ')}`);
});

test('router は経路名を直書きしていない（知識は台帳側にある）', () => {
  const offenders = declaredRoutes().filter((route) =>
    new RegExp(`['"\`]${route}['"\`]`).test(ROUTER_SRC.replace(/^\s*\/\/.*$/gm, '')));
  assert.deepEqual(offenders, [],
    `router に経路名が直書きされています: ${offenders.join(', ')}。RENDER_ROUTES から導いてください。`);
});

test('router は renderer のメソッド名を直書きしていない', () => {
  const code = ROUTER_SRC.replace(/^\s*\/\/.*$/gm, '');
  const offenders = RENDER_ROUTES.map((r) => r.method).filter((m) => code.includes(`.${m}(`));
  assert.deepEqual(offenders, [],
    `router に renderer のメソッド名が直書きされています: ${offenders.join(', ')}`);
});

test('未知 kind は従来どおり非描画（例外にしない）', () => {
  const host = { _validateSeriesNames: (x) => x, _label: () => 'x', _applyStoredStyles: () => {} };
  const router = new SeriesRenderRouter(host, {});
  assert.equal(seriesKind('__unknown__').renderRoute, null, '未知 kind の経路は null');
  assert.doesNotThrow(
    () => router.draw('i1', { placement: 'pane' }, [{ name: 'a', kind: '__unknown__', data: [] }]));
});

test('renderer に宣言メソッドが無いときは黙って捨てず例外になる', () => {
  // 「描かれないのに何も言わない」を作らないための固定点（かつての失敗モード）。
  const host = { _validateSeriesNames: (x) => x, _label: () => 'x', _applyStoredStyles: () => {} };
  const kind = Object.keys(SERIES_KINDS).find((k) => SERIES_KINDS[k].renderRoute);
  const router = new SeriesRenderRouter(host, {});   // メソッドを 1 つも持たない renderer
  assert.throws(
    () => router.draw('i1', { placement: 'pane' }, [{ name: 'a', kind, data: [] }]),
    /ChartRenderer に .* がありません/);
});

test('未宣言経路を捨てずに落とす分岐が router に在る（構造の固定）', () => {
  // RENDER_ROUTES への追記漏れを黙殺しないこと。凍結台帳のため実行時再現は不可なので構造で固定する。
  const code = ROUTER_SRC.replace(/^\s*\/\/.*$/gm, '');
  assert.match(code, /throw new Error\(`series_kind: 未宣言の描画経路/,
    'router に未宣言経路の例外送出が見当たりません');
});
