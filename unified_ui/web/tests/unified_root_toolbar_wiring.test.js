// 統合ルートが「3 モードのツールバー構成」を実際に bootstrap へ渡していることの固定。
//
// なぜソースを読むのか: `unified_root.js` の `main()` はブラウザ（document/window あり）でしか
//   起動しないため、node のテストからは実行できない。一方で本件は**受け口だけ作って呼び出し側が
//   送っていない**という壊れ方が実際に起きる箇所である（ISSUE-291 の実測: サーバ側に分岐を作っても
//   front が送らなければ無言で死ぬ）。合成根の呼び出し側とモード定義表の結線を、
//   `composition_roots_share_wiring.test.js` と同じ流儀でソース上に固定する。
//
// 固定するのは 2 点だけ:
//   1. bootstrap 呼び出しに toolbar 構成が渡っている（注入口が実際に使われている）
//   2. その構成がモード定義表由来である（モード名・ラベルを統合ルートに直書きしていない）
// 構造は AAA。

import { describe, test, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { MODE_TOGGLE_BUTTONS } from '../js/mode_table.js';

const SRC = readFileSync(fileURLToPath(new URL('../js/unified_root.js', import.meta.url)), 'utf8');

describe('unified_root — ツールバー構成の注入結線（L-1 の呼び出し側）', () => {
  test('bootstrap_call_passes_a_toolbar_config', () => {
    // Assert: 注入口（bootstrap の toolbar 引数）を実際に使っている。
    expect(SRC).toMatch(/toolbar:\s*\{/);
  });

  test('toolbar_config_is_derived_from_the_mode_table', () => {
    // Assert: モードの集合は定義表由来。統合ルートが第 2 の定義を持たない。
    expect(SRC).toMatch(/MODE_TOGGLE_BUTTONS/);
    expect(SRC).toMatch(/modeButtons:\s*MODE_TOGGLE_BUTTONS/);
  });

  test('button_ids_are_not_hardcoded_in_the_root', () => {
    // Assert: ボタン id の文字列リテラルが無い（表を迂回した第 2 定義の検出）。
    //   ラベルは日本語で、コメント本文にも自然に現れるため id だけを見る。
    for (const b of MODE_TOGGLE_BUTTONS) {
      expect(SRC).not.toContain(`'${b.id}'`);
      expect(SRC).not.toContain(`"${b.id}"`);
    }
  });
});
