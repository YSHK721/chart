// timeframes — バー周期は時間足台帳からの導出であって写しではない（ISSUE-502 後続・残件 1）。
//
// なぜ要るか（是正前の実測）: `TIMEFRAME_REFRESH_MS` は 8 本の `barSec × 1000` を手で書き
//   写したものであり、時間足台帳（`js/domain/tf_ledger_generated.js` ← marketdata/tf_meta.py
//   の TF_BAR_SEC）と突き合わせる検定が **1 本も無かった**。台帳の barSec だけを直しても
//   写しは古い周期のまま残り、チャートは表示され続けたまま「確定より前に取り直す（浪費）」
//   または「確定しても取り直さない（古い足を出し続ける）」へ静かに倒れる。応答も表示も
//   正しく見えるので、出力を見る検査では**原理的に落ちない**
//   （ISSUE-253 の floorable の写しのずれ・ISSUE-254 の台帳と同型の「静かなずれ」）。
//
// 本検定が固定するもの（**値そのものは焼き込まない**——焼き込めば写しをテスト側へ移すだけ）:
//   - 導出の同一性: 各足の周期 = その足の台帳 barSec × 1000（第 2 定義が無い）
//   - 絞りの同一性: 鍵の集合と順序 = 表示する足（台帳だけが持つ足は入らない・欠けない）
//   - 絞りが空振りでない: 台帳には表示しない足が実在する（フィルタが仕事をしている）
//   - 無言の縮退の禁止: 表示する足が台帳に無ければ `undefined` を配らず落ちる
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import {
  DASHBOARD_TIMEFRAMES,
  TIMEFRAME_REFRESH_MS,
  refreshMsOf,
} from '../js/adapter/front/timeframes.js';
import { TF_LEDGER } from '../js/domain/tf_ledger_generated.js';

const MS_PER_SEC = 1000;

describe('timeframes — バー周期は台帳からの導出（写しの不在）', () => {
  test('every_refresh_period_equals_the_ledger_bar_length_of_that_timeframe', () => {
    // Arrange: 台帳を唯一の正とする（周期の値をこちらへ書き写さない）。
    const barSecByCode = new Map(TF_LEDGER.map((entry) => [entry.code, entry.barSec]));

    // Act / Assert: 表示する 8 本すべてが台帳の barSec × 1000 と一致する。
    for (const code of DASHBOARD_TIMEFRAMES) {
      assert.equal(
        TIMEFRAME_REFRESH_MS[code],
        barSecByCode.get(code) * MS_PER_SEC,
        `時間足 ${code} の周期が台帳の barSec と食い違っています`,
      );
    }
  });

  test('the_period_keys_are_exactly_the_displayed_timeframes_in_order', () => {
    // 台帳だけが持つ足（30m）が紛れ込まず、表示する足が 1 本も欠けない。
    assert.deepEqual(Object.keys(TIMEFRAME_REFRESH_MS), [...DASHBOARD_TIMEFRAMES]);
  });

  test('the_ledger_holds_timeframes_the_dashboard_does_not_display', () => {
    // 絞りの生存確認: 台帳 = 表示足なら「絞り」は恒真になり、上の検定は空振りする。
    const displayed = new Set(DASHBOARD_TIMEFRAMES);
    const ledgerOnly = TF_LEDGER.filter((entry) => !displayed.has(entry.code));
    assert.ok(
      ledgerOnly.length > 0,
      '台帳が表示足と同一集合になっています（絞りを検定できていません）',
    );
    for (const entry of ledgerOnly) {
      assert.equal(TIMEFRAME_REFRESH_MS[entry.code], undefined);
    }
  });

  test('a_displayed_timeframe_missing_from_the_ledger_throws_instead_of_yielding_undefined', () => {
    // 無言の縮退の禁止: 周期の引けない 1 本を素通しすると candle_poller はその足だけ
    //   毎 tick 発行へ倒れる（表示は正しいまま＝出力の検査では落ちない）。
    assert.throws(() => refreshMsOf(['9x'], TF_LEDGER), /9x/);
    assert.throws(() => refreshMsOf(['1m'], []), /1m/);
  });

  test('the_derivation_does_not_depend_on_ledger_order_or_on_undisplayed_entries', () => {
    // 導出は集合演算である（台帳の並び・表示しない足の有無で結果が変わらない）。
    const displayed = new Set(DASHBOARD_TIMEFRAMES);
    const shuffled = [...TF_LEDGER].reverse();
    const trimmed = TF_LEDGER.filter((entry) => displayed.has(entry.code));

    assert.deepEqual(refreshMsOf(DASHBOARD_TIMEFRAMES, shuffled), TIMEFRAME_REFRESH_MS);
    assert.deepEqual(refreshMsOf(DASHBOARD_TIMEFRAMES, trimmed), TIMEFRAME_REFRESH_MS);
  });
});
