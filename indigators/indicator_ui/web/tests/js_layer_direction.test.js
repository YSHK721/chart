// js_layer_direction.test.js — フロント（JS）の依存方向ゲート（ISSUE-479 Wave2 J-4a）。
//
// Python 側には内側 4 層が Composition Root を import しないことを構文木で固定するゲートが
// あるが（simulator/tests/unit/test_layer_dependency_direction.py）、**フロントには無かった**。
// JS でも層（domain / usecase / adapter）は同じ Dependency Rule に従う——内側は外側を知らない。
//
// 本ファイルが固定するもの:
//   G-1 層方向: domain → domain のみ / usecase → domain + 同層 / adapter → usecase + domain + 同層。
//   G-3 越境 URL: 他 core のモジュール URL は、その core の `public/` 配下だけを名指してよい。
//   検出器の自己検定: 合成ソースで違反を実際に捕捉すること（空振りのゲートを置かない）。
//   計算量: 検定 1 回あたりの読取 − 対象ファイル数 = 0（項目ごとに読み直さない）。
//
// 走査対象は列挙しない。配信根 `web/js` 直下の層ディレクトリを**構造から**見つける
// （列挙に載っていない新規ディレクトリが永久に検出されない穴を作らない）。
// 走査の実装は tools/js_layer_guard.mjs だけが持つ（各 core へ書き写さない）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import {
  collectSources,
  crossCoreModuleUrlOffenders,
  discoverLayers,
  importSpecifiers,
  layerDirectionOffenders,
} from '../../../../tools/js_layer_guard.mjs';

const WEB = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const JS_ROOT = path.join(WEB, 'js');
const REPO_ROOT = path.resolve(WEB, '..', '..', '..');

const SOURCES = collectSources([JS_ROOT]);

test('走査根から層が構造的に見つかる（検定が空振りしていない）', () => {
  const layers = discoverLayers(JS_ROOT);
  assert.ok(layers.includes('domain'), `domain 層が見つからない: ${layers}`);
  assert.ok(layers.includes('usecase'), `usecase 層が見つからない: ${layers}`);
  assert.ok(layers.includes('adapter'), `adapter 層が見つからない: ${layers}`);
  assert.ok(SOURCES.size > 50, `走査対象が少なすぎる（前提崩壊）: ${SOURCES.size}`);
});

test('G-1 内側の層が外側を import していない', () => {
  const offenders = layerDirectionOffenders(SOURCES, JS_ROOT, REPO_ROOT);
  assert.deepEqual(offenders, [],
    `依存方向の逆流（内側 → 外側）:\n${offenders.join('\n')}`);
});

test('G-3 他 core のモジュール URL は public 面のみを名指す', () => {
  const offenders = crossCoreModuleUrlOffenders(SOURCES, REPO_ROOT);
  assert.deepEqual(offenders, [],
    `他 core の内部階層を名指している（public/ の面へ寄せること）:\n${offenders.join('\n')}`);
});

// --------------------------------------------------------------------------- //
// 検出器の自己検定 — 合成ソースで違反を実際に捕捉する
// --------------------------------------------------------------------------- //
function syntheticTree() {
  const root = mkdtempSync(path.join(tmpdir(), 'jslayer-'));
  const js = path.join(root, 'js');
  for (const layer of ['domain', 'usecase', 'adapter']) {
    mkdirSync(path.join(js, layer), { recursive: true });
  }
  return { root, js };
}

test('検出器は usecase → adapter の逆流を捕捉する', () => {
  const { root, js } = syntheticTree();
  writeFileSync(path.join(js, 'adapter', 'client.js'), 'export const client = 1;\n');
  writeFileSync(
    path.join(js, 'usecase', 'poller.js'),
    "import { client } from '../adapter/client.js';\nexport const poll = () => client;\n",
  );
  const sources = collectSources([js]);
  const offenders = layerDirectionOffenders(sources, js, root);
  assert.equal(offenders.length, 1, `捕捉できていない: ${offenders}`);
  assert.match(offenders[0], /usecase → adapter/);
});

test('検出器は domain → usecase の逆流も捕捉する（最内層の規則が緩んでいない）', () => {
  const { root, js } = syntheticTree();
  writeFileSync(path.join(js, 'usecase', 'rule.js'), 'export const rule = 1;\n');
  writeFileSync(
    path.join(js, 'domain', 'model.js'),
    "export { rule } from '../usecase/rule.js';\n",
  );
  const sources = collectSources([js]);
  assert.match(layerDirectionOffenders(sources, js, root)[0] ?? '', /domain → usecase/);
});

test('検出器は許された向き（adapter → usecase → domain）を offender にしない', () => {
  const { root, js } = syntheticTree();
  writeFileSync(path.join(js, 'domain', 'model.js'), 'export const model = 1;\n');
  writeFileSync(
    path.join(js, 'usecase', 'rule.js'),
    "import { model } from '../domain/model.js';\nexport const rule = model;\n",
  );
  writeFileSync(
    path.join(js, 'adapter', 'view.js'),
    "import { rule } from '../usecase/rule.js';\nimport { model } from '../domain/model.js';\n"
    + 'export const view = [rule, model];\n',
  );
  const sources = collectSources([js]);
  assert.deepEqual(layerDirectionOffenders(sources, js, root), []);
});

test('検出器は識別子渡しの動的 import（import 走査に映らない越境）を文字列で捕捉する', () => {
  // D-1 の実測形。import 文の走査には**原理的に現れない**ため、文字列を見るしかない。
  const { root, js } = syntheticTree();
  const source = [
    "const PERIOD_PRESETS_PATH = '/live/js/usecase/period_presets.js';",
    'export async function load() {',
    '  return import(PERIOD_PRESETS_PATH);',
    '}',
  ].join('\n');
  writeFileSync(path.join(js, 'adapter', 'root.js'), source);
  const sources = collectSources([js]);

  assert.deepEqual(importSpecifiers(source), [],
    'import 走査に映ってしまっている（この検定の前提が崩れている）');
  const offenders = crossCoreModuleUrlOffenders(sources, root);
  assert.equal(offenders.length, 1, `越境 URL を捕捉できていない: ${offenders}`);
  assert.match(offenders[0], /\/live\/js\/usecase\/period_presets\.js/);
});

test('検出器は public 面の名指しを offender にしない（許可の側も効いている）', () => {
  const { root, js } = syntheticTree();
  writeFileSync(
    path.join(js, 'adapter', 'root.js'),
    "const API = '/live/js/public/live_public_api.js';\nexport const load = () => import(API);\n",
  );
  const sources = collectSources([js]);
  assert.deepEqual(crossCoreModuleUrlOffenders(sources, root), []);
});

test('検出器は相対指定子を offender にしない（G-3 が見るのは絶対 URL だけ）', () => {
  // なぜ在るか（実測 2026-09-04）: `import { t } from '../../replay/timing.js';` の指定子には
  //   `/replay/timing.js` という**部分文字列**が含まれる。左端を縛らない走査はこれを「他 core の
  //   モジュール URL」と誤認する。相対指定子は G-1（層方向）が解決して見る担当であり、G-3 の
  //   担当は配信 URL（`/` 始まり）だけである。誤検出が残る検定は信用されず、いずれ無効化される。
  const { root, js } = syntheticTree();
  writeFileSync(
    path.join(js, 'adapter', 'view.js'),
    "import { timing } from '../../replay/timing.js';\n"
    + "import { calendar } from './replay/calendar.js';\nexport const v = [timing, calendar];\n",
  );
  const sources = collectSources([js]);
  assert.deepEqual(crossCoreModuleUrlOffenders(sources, root), []);
});

test('検出器はテンプレート合成の越境 URL を捕捉する（左端を縛っても取りこぼさない）', () => {
  // 左端を縛る是正が「クォート直後だけ」に狭まると、`${PREFIX}/js/...` の合成が抜ける。
  const { root, js } = syntheticTree();
  writeFileSync(
    path.join(js, 'adapter', 'root.js'),
    'const PREFIX = "";\nexport const load = () => import(`${PREFIX}/live/js/usecase/period_presets.js`);\n',
  );
  const sources = collectSources([js]);
  const offenders = crossCoreModuleUrlOffenders(sources, root);
  assert.equal(offenders.length, 1, `合成された越境 URL を取りこぼしている: ${offenders}`);
});

test('検出器は API prefix と JSON 資源を offender にしない（公開契約は対象外）', () => {
  const { root, js } = syntheticTree();
  writeFileSync(
    path.join(js, 'adapter', 'api.js'),
    "const PREFIX = '/live';\nconst DATA = '/live/data/trade_markers.json';\n"
    + 'export const urls = [PREFIX, DATA];\n',
  );
  const sources = collectSources([js]);
  assert.deepEqual(crossCoreModuleUrlOffenders(sources, root), []);
});

test('検出器はコメント内の記述を offender にしない（文章と宣言を混ぜない）', () => {
  const { root, js } = syntheticTree();
  writeFileSync(
    path.join(js, 'adapter', 'doc.js'),
    "// 旧経路は '/live/js/usecase/period_presets.js' だった。\n"
    + "/* 併記: '/replay/js/replay.js' */\nexport const x = 1;\n",
  );
  const sources = collectSources([js]);
  assert.deepEqual(crossCoreModuleUrlOffenders(sources, root), []);
});

// --------------------------------------------------------------------------- //
// 計算量: 検定 1 回あたりの読取 − 対象ファイル数 = 0
// --------------------------------------------------------------------------- //
// 読取の発行を数える io（走査の実装は無改変のまま、発行だけを外から観測する）。
function countingIo(counter) {
  return {
    readFile: (p) => { counter.reads.push(p); return readFileSync(p, 'utf8'); },
    readDir: (p) => readdirSync(p),
    statOf: (p) => statSync(p),
  };
}

function treeWithFiles(count) {
  const { root, js } = syntheticTree();
  for (let i = 0; i < count; i += 1) {
    writeFileSync(path.join(js, 'domain', `m${i}.js`), `export const m${i} = ${i};\n`);
  }
  return { root, js };
}

for (const fileCount of [30, 60]) {
  test(`検定 1 巡の読取 − 対象ファイル数 = 0（対象 ${fileCount} 本）`, () => {
    const { root, js } = treeWithFiles(fileCount);
    const counter = { reads: [] };
    const sources = collectSources([js], countingIo(counter));

    // 3 項目を同じ Map の上で走らせる（項目ごとに読み直さない）。
    layerDirectionOffenders(sources, js, root);
    crossCoreModuleUrlOffenders(sources, root);
    layerDirectionOffenders(sources, js, root);

    assert.equal(counter.reads.length - sources.size, 0,
      `同じファイルを読み直している: reads=${counter.reads.length} files=${sources.size}`);
    assert.equal(sources.size, fileCount);
  });
}

for (const itemCount of [3, 6]) {
  test(`検査項目を ${itemCount} 件に増やしても読取は増えない（オーダーの表明）`, () => {
    const { root, js } = treeWithFiles(30);
    const counter = { reads: [] };
    const sources = collectSources([js], countingIo(counter));
    for (let i = 0; i < itemCount; i += 1) {
      layerDirectionOffenders(sources, js, root);
      crossCoreModuleUrlOffenders(sources, root);
    }
    assert.equal(counter.reads.length - sources.size, 0);
  });
}

test('計算量ゲートの検出力: 項目ごとに読み直す変異で赤になる', () => {
  const { root, js } = treeWithFiles(30);
  const counter = { reads: [] };
  const io = countingIo(counter);
  // 捨てられる読取を混ぜる（項目ごとに collectSources を呼び直す浪費の再現）。
  const first = collectSources([js], io);
  collectSources([js], io);
  assert.notEqual(counter.reads.length - first.size, 0, '変異を検出できていない（検査が空振り）');
});
