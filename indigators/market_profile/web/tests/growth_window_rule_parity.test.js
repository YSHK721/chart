// MP actor が複製している「成長窓の期間始端」規則を、唯一源との一致で **検定する**（ISSUE-262）。
//
// market_profile_actor.js の `_sessionFrom()` は domain の
//   GrowthWindow.forCurrent('normal', tf, now).from
// と同一規則を独自に算出している。複製の理由として actor は
//   「growth_window.js を取り込むと双方が持つ top-level `const TF_BAR_SEC` が二重宣言衝突を
//    起こす（bundle 破損）」
// と述べていたが、**この理由は現コードで成立しない**。`const TF_BAR_SEC` の宣言は
// domain/tf_meta.js の 1 箇所のみで、actor も growth_window も同じものを import している。
//
// 実際の阻害要因は別で、`growth_window.js` が indicator_ui の A方式バンドル
// （build.mjs の MODULE_ORDER）に登録されておらず、symlink も無いこと。よって現時点では
// 複製を残さざるを得ない。ただし**ずれたら落ちる**状態にする。
//
// 複製を消す手順（将来）: indicator_ui/web/js/domain へ growth_window.js の symlink を張り、
//   build.mjs の MODULE_ORDER へ market_profile_actor.js より前に登録し、`_sessionFrom()` を
//   GrowthWindow への委譲へ置換する。build_module_order.test.js が登録漏れを落とす。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { GrowthWindow } from '../js/domain/growth_window.js';
import { sessionDayStart } from '../js/domain/session_day.js';
import { TF_BAR_SEC, TF_CODES } from '../js/domain/tf_meta.js';

// market_profile_actor.js `_sessionFrom()` の式をそのまま写したもの（複製側の定義）。
//   actor 側を変更したらここも変える。両者がずれたら下のテストが落ちる。
function actorSessionFrom(tf, now) {
  const sessionStart = sessionDayStart(now);
  const formingStart = tf === '1D'
    ? sessionStart
    : Math.floor(now / (TF_BAR_SEC[tf] ?? 86400)) * (TF_BAR_SEC[tf] ?? 86400);
  return Math.min(sessionStart, formingStart);
}

// 境界を含む検定時刻（セッション境界前後・週跨ぎ・月末・DST 切替日）。
const CASES = [
  Date.UTC(2026, 7, 5, 11, 0, 0) / 1000,
  Date.UTC(2026, 7, 5, 20, 59, 59) / 1000,
  Date.UTC(2026, 7, 5, 21, 0, 0) / 1000,
  Date.UTC(2026, 7, 5, 22, 0, 0) / 1000,
  Date.UTC(2026, 6, 10, 3, 30, 0) / 1000,
  Date.UTC(2026, 6, 12, 23, 45, 0) / 1000,
  Date.UTC(2026, 0, 31, 12, 0, 0) / 1000,
  Date.UTC(2026, 2, 8, 6, 0, 0) / 1000,
  Date.UTC(2026, 10, 1, 6, 0, 0) / 1000,
];

for (const tf of TF_CODES) {
  test(`_sessionFrom の複製が GrowthWindow の規則と一致する（${tf}）`, () => {
    for (const now of CASES) {
      const authority = GrowthWindow.forCurrent('normal', tf, now).from;
      const copy = actorSessionFrom(tf, now);
      assert.equal(copy, authority,
        `tf=${tf} now=${now}: actor の複製(${copy}) が GrowthWindow(${authority}) と食い違っています。`
        + ' 規則を変えたなら market_profile_actor._sessionFrom と本テストの写しを同時に更新してください。');
    }
  });
}

test('複製の理由として述べられていた前提（TF_BAR_SEC 二重宣言）が成立しないことを固定する', () => {
  // 双方が同じ tf_meta.js の TF_BAR_SEC を参照している＝二重宣言は存在しない。
  // 将来 domain 側で値を持ち直したら（＝台帳の第 2 定義が復活したら）ここで落とす。
  assert.equal(typeof TF_BAR_SEC, 'object');
  assert.ok(Object.isFrozen(TF_BAR_SEC), 'TF_BAR_SEC は台帳由来の凍結オブジェクトである');
  assert.deepEqual(Object.keys(TF_BAR_SEC), [...TF_CODES], '台帳と同一のキー・順序である');
});
