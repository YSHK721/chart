// live_chart_kit_api.js — 売買履歴チャートへ貸す「最初に読む足の本数」がライブチャートの値そのものであること
//   （ISSUE-552/554 段階 2-2）。借り手が値を書き写すと、ライブチャートの本数を変えたときに食い違う。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import * as kit from '../js/public/live_chart_kit_api.js';
import { RECENT_BARS } from '../js/adapter/front/composition_root_front.js';

test('公開面の RECENT_BARS はライブチャートの合成根の値そのもの（正の整数）', () => {
  assert.equal(kit.RECENT_BARS, RECENT_BARS);
  assert.ok(Number.isInteger(kit.RECENT_BARS) && kit.RECENT_BARS > 0);
});

test('公開面は値を持たない（再輸出だけ・数値を書き写していない）', () => {
  const src = readFileSync(fileURLToPath(new URL('../js/public/live_chart_kit_api.js', import.meta.url)), 'utf-8');
  const code = src.split('\n').filter((line) => !line.trim().startsWith('//')).join('\n');
  assert.ok(!/\d/.test(code), '公開面のコードに数値があります（値は出所から再輸出する）');
  assert.ok(!/\bconst\b|\blet\b|\bfunction\b/.test(code), '公開面が中身を持っています');
});
