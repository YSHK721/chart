// usecase 層に console（副作用）が 1 つも存在しないことの固定（R-4）。
//
// 設計入力: 基本設計_指標カラーテーマ §7.8（依存は内向き）／clean-architecture の層責務。
//   usecase は「入力 → 出力の写像」だけを持つ純ロジック層で、DOM・Storage・I/O と同様に
//   console も外界への副作用である。純関数が console を呼ぶと、
//     (1) 呼び出し側は「何が無視されたか」を戻り値から知れず、UI へ写像できない、
//     (2) テストは console を差し替えないと純関数を観測できない（F.I.R.S.T の Independent を壊す）、
//     (3) 出力先（開発者コンソール / 収集基盤 / 無出力）の選択が内側に固定される、
//   の 3 点が同時に起きる。事実は戻り値で返し、報告先の決定は adapter が持つ。
//
// 本ファイルが必要な理由（実測 2026-08-09）: R-4 着手時点で
//   `grep -Rn "console\." js/usecase/` の一致は `color_themes.js:99-100` の 1 件だけだった。
//   同じ規律を守る場所は 1 ファイルではなく層全体なので、判定も層全体に対して行う
//   （個別ファイルの検定にすると、次に console を足したファイルを検出できない）。
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const USECASE_DIR = resolve(HERE, '../js/usecase');

// symlink 共有（ISSUE-304）でも実体を読む。`withFileTypes` の isFile は symlink を false にするため、
//   名前で .js を拾い readFileSync（symlink を辿る）で内容を得る。
function usecaseSources() {
  return readdirSync(USECASE_DIR)
    .filter((name) => name.endsWith('.js'))
    .map((name) => ({ name, src: readFileSync(resolve(USECASE_DIR, name), 'utf8') }));
}

test('TC-R4-01 usecase 層のどのファイルも console を参照しない（副作用は adapter が持つ）', () => {
  // Arrange
  const sources = usecaseSources();
  assert.ok(sources.length > 0, '前提: usecase 層に走査対象が在る');
  // Act
  const offenders = [];
  for (const { name, src } of sources) {
    src.split('\n').forEach((line, i) => {
      if (/console\s*\./.test(line)) {
        offenders.push(`${name}:${i + 1}`);
      }
    });
  }
  // Assert
  assert.deepEqual(offenders, [], `usecase は純ロジック層（console は副作用）: ${offenders.join(', ')}`);
});
