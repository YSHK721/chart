// js_layer_direction.test.js — dashboard core の依存方向ゲート（ISSUE-479 Wave2 J-4a）。
//
// 本ファイルが固定するもの:
//   G-1 層方向: usecase → domain + 同層 / adapter → usecase + domain + 同層。
//   G-2 統合層への逆流: dashboard core は統合層（unified_ui）を一切参照しない。
//        統合層は各 core を束ねる**外側**であり、core から名指すのは依存方向の反転である。
//   G-3 越境 URL: 他 core のモジュール URL は、その core の `public/` 配下だけを名指してよい。
//        D-1 の実測: `composition_root_front.js` は live core の内部階層
//        （`/live/js/usecase/...`・`/live/js/adapter/front/...`）を文字列で名指し、
//        識別子渡しの動的 import で読み込んでいた。import 走査には原理的に映らない形である。
//        live core の内部配置が変われば dashboard は無言で 404 になる。
//
// 走査の実装は tools/js_layer_guard.mjs だけが持つ（各 core へ書き写さない）。
// API prefix（`CANDLES_API_PREFIX = '/live'`）は公開契約であり本ゲートの対象ではない
// （モジュール URL＝`.js` で終わる絶対パスだけを見る）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import {
  collectSources,
  crossCoreModuleUrlOffenders,
  discoverLayers,
  foreignReferenceOffenders,
  layerDirectionOffenders,
} from '../../../tools/js_layer_guard.mjs';

const WEB = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const JS_ROOT = path.join(WEB, 'js');
const REPO_ROOT = path.resolve(WEB, '..', '..');

const SOURCES = collectSources([JS_ROOT]);

test('走査根から層が構造的に見つかる（検定が空振りしていない）', () => {
  const layers = discoverLayers(JS_ROOT);
  assert.ok(layers.includes('usecase'), `usecase 層が見つからない: ${layers}`);
  assert.ok(layers.includes('adapter'), `adapter 層が見つからない: ${layers}`);
  assert.ok(SOURCES.size > 5, `走査対象が少なすぎる（前提崩壊）: ${SOURCES.size}`);
});

test('G-1 内側の層が外側を import していない', () => {
  const offenders = layerDirectionOffenders(SOURCES, JS_ROOT, REPO_ROOT);
  assert.deepEqual(offenders, [],
    `依存方向の逆流（内側 → 外側）:\n${offenders.join('\n')}`);
});

test('G-2 dashboard core は統合層（unified_ui）を参照しない', () => {
  const offenders = foreignReferenceOffenders(SOURCES, ['unified_ui', '/unified/'], REPO_ROOT);
  assert.deepEqual(offenders, [],
    `core が統合層を名指している（依存方向の反転）:\n${offenders.join('\n')}`);
});

test('G-3 他 core のモジュール URL は public 面のみを名指す', () => {
  const offenders = crossCoreModuleUrlOffenders(SOURCES, REPO_ROOT);
  assert.deepEqual(offenders, [],
    `他 core の内部階層を名指している（public/ の面へ寄せること）:\n${offenders.join('\n')}`);
});
