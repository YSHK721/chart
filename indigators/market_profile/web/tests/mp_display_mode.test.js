// mp_display_mode 表示モード台帳の検証（ISSUE-134 OCP）。
//
// 台帳の各属性が従来の mode 直接比較（=== 'normal' / === 'sessions'）と 1:1 一致すること（挙動変更ゼロ）と、
// 消費 3 ファイルが raw mode 文字列比較を台帳参照へ置換したこと（OCP）を構造的に固定する。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { MP_DISPLAY_MODES, mpDisplayMode } from '../js/domain/mp_display_mode.js';

const JS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'js');
const MODES = ['normal', 'sessions', 'replay', 'ticklive'];

test('attributes match legacy mode comparisons 1:1', () => {
  for (const mode of MODES) {
    const d = mpDisplayMode(mode);
    assert.equal(d.isNormal, mode === 'normal', mode); // 旧 mode === 'normal'
    assert.equal(d.splitByDay, mode === 'sessions', mode); // 旧 mode === 'sessions'
    assert.equal(d.transition, mode, mode); // 既知 mode の遷移経路は自身
  }
});

test('unknown mode falls back to legacy semantics and normal transition', () => {
  const d = mpDisplayMode('__unknown__');
  assert.equal(d.isNormal, false); // 旧 === 'normal' → false
  assert.equal(d.splitByDay, false); // 旧 === 'sessions' → false
  assert.equal(d.transition, 'normal'); // 未知の mode は 'normal' 扱い（安全側）
});

test('registry is the single mode ledger (consumers reference it, no raw mode literals for capability branches)', () => {
  // ISSUE-181: actor の mode 分岐（_applyMode の switch）は表示モード遷移ロール
  //   adapter/front/mp_mode_transition.js へ外出しした。台帳を import する「消費者」は移設先である。
  //   actor 側は raw 文字列比較が戻らないこと（非退行）を引き続き課す（下の noRawLiterals）。
  const files = {
    'domain/growth_window.js': J('domain', 'growth_window.js'),
    'usecase/catalog_entry.js': J('usecase', 'catalog_entry.js'),
    'adapter/front/mp_mode_transition.js': J('adapter', 'front', 'mp_mode_transition.js'),
  };
  const noRawLiterals = (label, src) => {
    // 表示モードの capability を表す raw 文字列比較が残存しないこと。
    assert.ok(!/mode\s*===\s*'sessions'/.test(src), `${label} に mode === 'sessions' が残存`);
    assert.ok(!/mode\s*!==\s*'sessions'/.test(src), `${label} に mode !== 'sessions' が残存`);
    assert.ok(!/mode\s*===\s*'normal'/.test(src), `${label} に mode === 'normal' が残存`);
    assert.ok(!/mode\s*===\s*'replay'/.test(src), `${label} に mode === 'replay' が残存`);
    assert.ok(!/mode\s*===\s*'ticklive'/.test(src), `${label} に mode === 'ticklive' が残存`);
  };
  for (const [label, src] of Object.entries(files)) {
    assert.ok(/mp_display_mode\.js'/.test(src), `${label} が mp_display_mode 台帳を import していない`);
    noRawLiterals(label, src);
  }
  // 分岐の移設元（actor）にも raw 文字列比較の非退行を課す（台帳参照は移設先が担う）。
  noRawLiterals('adapter/front/market_profile_actor.js', J('adapter', 'front', 'market_profile_actor.js'));
});

test('MP_DISPLAY_MODES is frozen and covers the four display modes', () => {
  assert.deepEqual(Object.keys(MP_DISPLAY_MODES).sort(), [...MODES].sort());
  assert.ok(Object.isFrozen(MP_DISPLAY_MODES));
});

function J(...parts) {
  return readFileSync(path.join(JS, ...parts), 'utf8');
}
