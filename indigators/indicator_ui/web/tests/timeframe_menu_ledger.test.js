// 時間足メニューが台帳（唯一の定義）から導出されることの検定（ISSUE-254）。
//
// 「新しい時間足を足すとき何箇所直すのか誰にも分からない」状態を構造的に潰す。台帳へ 1 行足せば
// メニューにも必ず出る／ラベルの付け忘れは本検定が落とす、という 2 方向を固定する。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { TimeframeMenu, timeframeLabels } from '../js/adapter/front/timeframe_menu.js';
import { TF_CODES } from '../js/domain/tf_meta.js';

function menuCodes() {
  const menu = new TimeframeMenu({});
  return menu._groups.flatMap((g) => g.items.map(([code]) => code));
}

test('既定メニューの時間足集合と順序は台帳と完全一致する（台帳が唯一の定義）', () => {
  assert.deepEqual(menuCodes(), [...TF_CODES]);
});

test('台帳の全時間足にラベルが定義されている（コード素出しの取りこぼしが無い）', () => {
  const labels = timeframeLabels();
  for (const code of TF_CODES) {
    assert.ok(labels[code], `${code} のラベル未定義`);
    assert.notEqual(labels[code], code, `${code} がコード素出しになっている`);
  }
});

test('ラベル写像は台帳外の時間足を含まない（消えた足の残骸を持たない）', () => {
  for (const code of Object.keys(timeframeLabels())) {
    assert.ok(TF_CODES.includes(code), `${code} は台帳に存在しない`);
  }
});
