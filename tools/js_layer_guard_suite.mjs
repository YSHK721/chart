// js_layer_guard_suite.mjs — 依存方向ゲート（G-1 / G-3）の**検定本体の唯一の実装**。
//
// なぜ共有モジュールなのか:
//   走査の実装（js_layer_guard.mjs）は既に 1 つだが、「何を assert するか」を各 core の
//   テストファイルへ手書きで複製すると、規則が 1 つで検定が複数ある状態になる。複製は必ず
//   取り残しを生み、片方だけ直された日に片方の core だけ検査が緩む（本リポジトリの既往型）。
//   assert はここだけが持ち、各 core のテストは「どの根を見るか」だけを渡す。
//
// 収録するもの（各 core で同一）:
//   - 前提の非空振り（層が構造的に見つかる／走査対象が少なすぎない）
//   - G-1 層方向・G-3 越境 URL の回帰錨
//   - 検出器の自己検定（合成ソースで実際に捕捉する／自 core 除外が他 core まで緩めない）
//   - 計算量（検定 1 巡の読取 − 対象ファイル数 = 0・項目数を増やしても読取が増えない・変異で赤）

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

import {
  collectSources,
  crossCoreModuleUrlOffenders,
  discoverLayers,
  layerDirectionOffenders,
} from './js_layer_guard.mjs';

/** 読取の発行を数える io（走査の実装は無改変のまま、発行だけを外から観測する）。 */
function countingIo(counter) {
  return {
    readFile: (p) => { counter.reads.push(p); return readFileSync(p, 'utf8'); },
    readDir: (p) => readdirSync(p),
    statOf: (p) => statSync(p),
  };
}

/**
 * 1 つの core（配信根 `web/js`）に対して依存方向ゲートの検定一式を登録する。
 *
 * @param {object} spec
 * @param {string} spec.label            報告用の core 名（テスト名に出す）。
 * @param {string} spec.jsRoot           配信根 `.../web/js` の絶対パス。
 * @param {string} spec.repoRoot         報告の相対化根。
 * @param {string} spec.ownCore          自 core の配信名（`live|replay|sim|dashboard`）。
 * @param {string} spec.otherCore        自 core 除外が緩めていないことを測る**他** core の名前。
 * @param {string[]} spec.requiredLayers 構造的に見つかるべき層（前提の非空振り）。
 * @param {number} spec.minFiles         走査対象の下限（前提崩壊の検出）。
 */
export function registerLayerDirectionSuite({
  label, jsRoot, repoRoot, ownCore, otherCore, requiredLayers, minFiles,
}) {
  const sources = collectSources([jsRoot]);

  test(`[${label}] 走査根から層が構造的に見つかる（検定が空振りしていない）`, () => {
    const layers = discoverLayers(jsRoot);
    for (const layer of requiredLayers) {
      assert.ok(layers.includes(layer), `${layer} 層が見つからない: ${layers}`);
    }
    assert.ok(sources.size > minFiles, `走査対象が少なすぎる（前提崩壊）: ${sources.size}`);
  });

  test(`[${label}] G-1 内側の層が外側を import していない`, () => {
    const offenders = layerDirectionOffenders(sources, jsRoot, repoRoot);
    assert.deepEqual(offenders, [],
      `依存方向の逆流（内側 → 外側）:\n${offenders.join('\n')}`);
  });

  test(`[${label}] G-3 他 core のモジュール URL は public 面のみを名指す`, () => {
    const offenders = crossCoreModuleUrlOffenders(sources, repoRoot, ownCore);
    assert.deepEqual(offenders, [],
      `他 core の内部階層を名指している（public/ の面へ寄せること）:\n${offenders.join('\n')}`);
  });

  // ------------------------------------------------------------------------- //
  // 検出器の自己検定 — 合成ソースで違反を実際に捕捉する
  // ------------------------------------------------------------------------- //
  const syntheticTree = () => {
    const root = mkdtempSync(path.join(tmpdir(), `jslayer-${ownCore}-`));
    const js = path.join(root, 'js');
    for (const layer of ['domain', 'usecase', 'adapter', 'public']) {
      mkdirSync(path.join(js, layer), { recursive: true });
    }
    return { root, js };
  };

  test(`[${label}] 検出器は usecase → adapter の逆流を捕捉する`, () => {
    const { root, js } = syntheticTree();
    writeFileSync(path.join(js, 'adapter', 'client.js'), 'export const client = 1;\n');
    writeFileSync(
      path.join(js, 'usecase', 'poller.js'),
      "import { client } from '../adapter/client.js';\nexport const poll = () => client;\n",
    );
    const offenders = layerDirectionOffenders(collectSources([js]), js, root);
    assert.equal(offenders.length, 1, `捕捉できていない: ${offenders}`);
    assert.match(offenders[0], /usecase → adapter/);
  });

  test(`[${label}] 検出器は domain → usecase の逆流も捕捉する（最内層の規則が緩んでいない）`, () => {
    const { root, js } = syntheticTree();
    writeFileSync(path.join(js, 'usecase', 'rule.js'), 'export const rule = 1;\n');
    writeFileSync(
      path.join(js, 'domain', 'model.js'),
      "export { rule } from '../usecase/rule.js';\n",
    );
    const offenders = layerDirectionOffenders(collectSources([js]), js, root);
    assert.match(offenders[0] ?? '', /domain → usecase/);
  });

  test(`[${label}] 検出器は他 core の内部階層の名指しを捕捉する（自 core 除外が全体を緩めていない）`, () => {
    // 自 core 除外の最大の危険は「除外が広すぎて全部通る」ことである。他 core は必ず落ちる。
    const { root, js } = syntheticTree();
    writeFileSync(
      path.join(js, 'adapter', 'root.js'),
      `const P = '/${otherCore}/js/usecase/period_presets.js';\nexport const load = () => import(P);\n`,
    );
    const offenders = crossCoreModuleUrlOffenders(collectSources([js]), root, ownCore);
    assert.equal(offenders.length, 1, `他 core の名指しを見逃している: ${offenders}`);
    assert.match(offenders[0], new RegExp(`/${otherCore}/js/usecase/period_presets\\.js`));
  });

  test(`[${label}] 検出器は自 core（${ownCore}）の名指しを offender にしない`, () => {
    // なぜ在るか（実測 2026-09-04）: sim の合成根は表示部品を `/sim/report-js/chart.js` の
    //   **配信 URL** で読み込む（report 側の資源と配信根を共有するため）。これは「他 core の
    //   内部階層を名指す」越境ではなく、自 core 内の参照である。ここを越境として落とすと、
    //   検定は誤検出だけを出し続ける器になる。
    const { root, js } = syntheticTree();
    writeFileSync(
      path.join(js, 'adapter', 'root.js'),
      `import { c } from "/${ownCore}/report-js/chart.js";\nexport const chart = c;\n`,
    );
    const sources2 = collectSources([js]);
    assert.deepEqual(crossCoreModuleUrlOffenders(sources2, root, ownCore), []);
    // 自 core を渡さなければ従来どおり offender（加法であること＝既存呼出の挙動は不変）。
    assert.equal(crossCoreModuleUrlOffenders(sources2, root).length, 1,
      '自 core 除外が既定になっている（既存 core の検定を無言で緩めている）');
  });

  test(`[${label}] 検出器は他 core の public 面の名指しを offender にしない（許可の側も効いている）`, () => {
    const { root, js } = syntheticTree();
    writeFileSync(
      path.join(js, 'adapter', 'root.js'),
      `const API = '/${otherCore}/js/public/${otherCore}_public_api.js';\n`
      + 'export const load = () => import(API);\n',
    );
    assert.deepEqual(crossCoreModuleUrlOffenders(collectSources([js]), root, ownCore), []);
  });

  test(`[${label}] 検出器は相対指定子を offender にしない（G-3 が見るのは絶対 URL だけ）`, () => {
    const { root, js } = syntheticTree();
    writeFileSync(
      path.join(js, 'adapter', 'view.js'),
      `import { t } from '../../${otherCore}/timing.js';\nexport const v = t;\n`,
    );
    assert.deepEqual(crossCoreModuleUrlOffenders(collectSources([js]), root, ownCore), []);
  });

  // ------------------------------------------------------------------------- //
  // 計算量: 検定 1 回あたりの読取 − 対象ファイル数 = 0
  // ------------------------------------------------------------------------- //
  const treeWithFiles = (count) => {
    const { root, js } = syntheticTree();
    for (let i = 0; i < count; i += 1) {
      writeFileSync(path.join(js, 'domain', `m${i}.js`), `export const m${i} = ${i};\n`);
    }
    return { root, js };
  };

  for (const fileCount of [30, 60]) {
    test(`[${label}] 検定 1 巡の読取 − 対象ファイル数 = 0（対象 ${fileCount} 本）`, () => {
      const { root, js } = treeWithFiles(fileCount);
      const counter = { reads: [] };
      const scanned = collectSources([js], countingIo(counter));

      // 3 項目を同じ Map の上で走らせる（項目ごとに読み直さない）。
      layerDirectionOffenders(scanned, js, root);
      crossCoreModuleUrlOffenders(scanned, root, ownCore);
      layerDirectionOffenders(scanned, js, root);

      assert.equal(counter.reads.length - scanned.size, 0,
        `同じファイルを読み直している: reads=${counter.reads.length} files=${scanned.size}`);
      assert.equal(scanned.size, fileCount);
    });
  }

  for (const itemCount of [3, 6]) {
    test(`[${label}] 検査項目を ${itemCount} 件に増やしても読取は増えない（オーダーの表明）`, () => {
      const { root, js } = treeWithFiles(30);
      const counter = { reads: [] };
      const scanned = collectSources([js], countingIo(counter));
      for (let i = 0; i < itemCount; i += 1) {
        layerDirectionOffenders(scanned, js, root);
        crossCoreModuleUrlOffenders(scanned, root, ownCore);
      }
      assert.equal(counter.reads.length - scanned.size, 0);
    });
  }

  test(`[${label}] 計算量ゲートの検出力: 項目ごとに読み直す変異で赤になる`, () => {
    const { root, js } = treeWithFiles(30);
    const counter = { reads: [] };
    const io = countingIo(counter);
    // 捨てられる読取を混ぜる（項目ごとに collectSources を呼び直す浪費の再現）。
    const first = collectSources([js], io);
    collectSources([js], io);
    assert.notEqual(counter.reads.length - first.size, 0, '変異を検出できていない（検査が空振り）');
  });
}
