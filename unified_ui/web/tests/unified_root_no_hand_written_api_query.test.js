// 統合層が core の API の問い合わせを手書きしないことの固定（2026-09-27・手書き複製の解消）。
//
// 由来: unified_root.js が simチャートの足を読むために `/live/candles?datasetRef=…` を手書きしていた。
//   同じ問い合わせは live core の chart_app_wiring.js（fetchCandles）が持っており、片方だけ直ると
//   食い違う。問い合わせは live core の公開面（fetchCandleRange）から借り、行き先は routedFetch の
//   規則で決める。
//
// 散文の引用は別扱い: コメントを取り除いたコードだけを見る（コメントで名前に触れても赤にしない）。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

function codeOnly(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .map((line) => line.replace(/(^|[^:])\/\/.*$/, '$1'))
    .join('\n');
}

test('unified_root_code_has_no_candles_query', () => {
  // Arrange
  const src = readFileSync(fileURLToPath(new URL('../js/unified_root.js', import.meta.url)), 'utf8');

  // Act
  const code = codeOnly(src);

  // Assert
  expect(code).not.toMatch(/\/candles\?/);
  expect(code).toMatch(/fetchCandleRange\(/);
});

test('code_only_strips_prose_but_keeps_code', () => {
  // Arrange / Act
  const stripped = codeOnly("// `/candles?x` は散文\nconst u = '/candles?x';");

  // Assert: 検査が空振りしない（コードの文字列は残る）。
  expect(stripped).toMatch(/\/candles\?/);
  expect(stripped.split('\n')[0]).not.toMatch(/candles/);
});
