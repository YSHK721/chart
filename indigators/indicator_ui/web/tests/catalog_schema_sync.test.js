// catalog 既定値の単一情報源 同期テスト（ISSUE-092 ③・要件④）。
//
// back の single source（api/adapter/compute/catalog_schema.PARAM_DEFAULTS）を配布シリアライズ
// した契約 api/tests/golden/catalog_defaults.json と、front 静的フォールバック値
// （usecase/catalog.js の param 既定値）が一致することを固定する。乖離（back 既定値 != front
// 静的フォールバック値）を検出する。同一 JSON を back（test_catalog_schema.py）も読み、双方から
// 同期を固定する。JSON は fs で読む（import 属性非依存・ブラウザ実行に影響なし）。
//
// 構造: Arrange-Act-Assert（AAA）。読み取り専用（レジストリを変更しない）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { get } from '../js/usecase/catalog.js';

const FIXTURE = JSON.parse(
  readFileSync(new URL('../../api/tests/golden/catalog_defaults.json', import.meta.url), 'utf-8'),
);

test('front static defaults match back single-source contract (catalog_defaults.json)', () => {
  // Arrange / Act: 契約に含まれる各 compute_id の param 既定値を front レジストリから抽出する。
  const frontDefaults = {};
  for (const id of Object.keys(FIXTURE)) {
    const def = get(id);
    assert.ok(def, `front catalog is missing indicator ${id}`);
    const params = {};
    for (const name of Object.keys(FIXTURE[id])) {
      const p = def.params.find((q) => q.name === name);
      assert.ok(p, `front ${id} is missing param ${name}`);
      params[name] = p.default;
    }
    frontDefaults[id] = params;
  }
  // Assert: front 静的既定値 == back single source（乖離検出）。
  assert.deepStrictEqual(frontDefaults, FIXTURE);
});
