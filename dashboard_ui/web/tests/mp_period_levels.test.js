// MP の期間水準（設計書 §3.5.3）— 当期の列の選び方・行の名前・取得窓・借用 URL の唯一源。
//
// 参照実装（設計書が名指すライブの規則）:
//   - 当期の列: `market_profile_primitive.js` の tfPeriodLevelAt が `c.time === t` で列を引く。
//   - 行の名前: `mpSourceCapability(src).poc === 'star'` なら POC*、それ以外は POC（VAH / VAL は
//     そのまま）。いずれもライブの描画ラベルと同じ語。
//   - URL: ライブの `buildTfPeriodUrl` が唯一源（借り手は綴らない）。live の公開面から借りる。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import {
  MP_PERIOD_TIMEFRAMES, currentColumnOf, levelsOfColumn, periodWindowOf, pocLabelOf,
} from '../js/domain/mp_period_levels.js';
import { createTfPeriodClient } from '../js/adapter/front/tf_period_client.js';
// live core の**公開面**から借りる実物（再輸出が消えたらここが赤になる）。
import {
  buildTfPeriodUrl, mpSourceCapability, mpTfPeriodSrc, MP_DEFAULT_SOURCE,
} from '../../../indigators/indicator_ui/web/js/public/live_public_api.js';

const DAY = 1_790_640_000;

function column(time, poc, vaHigh, vaLow) {
  return { time, levels: [], poc, va_high: vaHigh, va_low: vaLow };
}

describe('MP の期間水準 — 規則（domain）', () => {
  test('the_targets_are_the_day_week_and_month_timeframes', () => {
    assert.deepEqual([...MP_PERIOD_TIMEFRAMES], ['1D', '1W', '1M']);
  });

  test('the_current_column_is_the_one_whose_time_equals_the_current_bar_time', () => {
    // Arrange: 過去の列・当期の列・未来の列（取り違えると値が変わる）。
    const columns = [column(DAY - 86_400, 1, 2, 0), column(DAY, 10, 20, 5), column(DAY + 86_400, 7, 8, 6)];
    // Act
    const hit = currentColumnOf(columns, DAY);
    // Assert
    assert.equal(hit, columns[1]);
  });

  test('no_column_with_the_current_time_answers_null_instead_of_the_nearest', () => {
    const columns = [column(DAY - 86_400, 1, 2, 0)];
    assert.equal(currentColumnOf(columns, DAY), null);
    assert.equal(currentColumnOf([], DAY), null);
    assert.equal(currentColumnOf(null, DAY), null);
  });

  test('a_star_poc_source_names_the_row_poc_star_and_others_poc', () => {
    // 語はライブの記述子（mpSourceCapability）から導く。'zp' を版面側に書かない。
    assert.equal(pocLabelOf(mpSourceCapability(MP_DEFAULT_SOURCE)), 'POC*');
    assert.equal(pocLabelOf(mpSourceCapability('dwell')), 'POC');
    assert.equal(pocLabelOf(mpSourceCapability(null)), 'POC');
  });

  test('one_column_gives_three_rows_with_the_values_as_is', () => {
    // Act
    const { levels, missing } = levelsOfColumn({
      timeframe: '1D', column: column(DAY, 104.2, 107.0, 101.0), pocLabel: 'POC*',
    });
    // Assert
    assert.deepEqual(levels, [
      { timeframe: '1D', level: 'POC*', price: 104.2 },
      { timeframe: '1D', level: 'VAH', price: 107.0 },
      { timeframe: '1D', level: 'VAL', price: 101.0 },
    ]);
    assert.deepEqual(missing, []);
  });

  test('a_value_the_column_does_not_have_is_reported_as_missing_not_sent', () => {
    // サーバは有限でない価格を要求ごと拒む（シート全体が落ちる）。値の無い水準は送らず名前を返す。
    const { levels, missing } = levelsOfColumn({
      timeframe: '1W', column: column(DAY, null, 107.0, Number.NaN), pocLabel: 'POC',
    });
    assert.deepEqual(levels, [{ timeframe: '1W', level: 'VAH', price: 107.0 }]);
    assert.deepEqual(missing, ['POC', 'VAL']);
  });

  test('the_window_contains_only_the_current_column', () => {
    assert.deepEqual(periodWindowOf(DAY), { from: DAY, to: DAY + 1 });
  });
});

describe('MP の期間水準 — 借用クライアント', () => {
  function spyFetch(answer) {
    const urls = [];
    const fetchFn = (url) => {
      urls.push(String(url));
      if (answer === null) return Promise.reject(new Error('切断'));
      return Promise.resolve({
        ok: answer.httpOk !== false, status: answer.httpOk === false ? 503 : 200,
        json: () => Promise.resolve(answer),
      });
    };
    return { urls, fetchFn };
  }

  test('the_url_is_the_live_builder_output_behind_the_prefix', async () => {
    // Arrange
    const spy = spyFetch({ ok: true, columns: [] });
    const client = createTfPeriodClient({ fetch: spy.fetchFn, apiPrefix: '/live', buildUrl: buildTfPeriodUrl });
    const query = { datasetRef: 'jp225_mt5', timeframe: '1D', from: DAY, to: DAY + 1, src: mpTfPeriodSrc('zp'), va: 0.7 };
    // Act
    await client.fetchColumns(query);
    // Assert
    assert.deepEqual(spy.urls, [`/live${buildTfPeriodUrl(query)}`]);
  });

  test('a_success_returns_the_columns', async () => {
    const columns = [column(DAY, 1, 2, 0)];
    const client = createTfPeriodClient({
      fetch: spyFetch({ ok: true, columns }).fetchFn, apiPrefix: '/live', buildUrl: buildTfPeriodUrl,
    });
    const result = await client.fetchColumns({ datasetRef: 'r', timeframe: '1D', from: DAY, to: DAY + 1 });
    assert.deepEqual(result, { ok: true, columns });
  });

  test('every_failure_is_a_postable_message_naming_mp_and_the_timeframe', async () => {
    for (const answer of [null, { httpOk: false }, { ok: false, error: { message: 'zp は 1m を支えません' } }, { ok: true }]) {
      const client = createTfPeriodClient({
        fetch: spyFetch(answer).fetchFn, apiPrefix: '/live', buildUrl: buildTfPeriodUrl,
      });
      const result = await client.fetchColumns({ datasetRef: 'r', timeframe: '1W', from: DAY, to: DAY + 1 });
      assert.equal(result.ok, false);
      assert.match(result.error.message, /MP の期間水準/);
      assert.match(result.error.message, /1W/);
    }
  });

  test('the_client_source_never_spells_the_query_itself', () => {
    // URL の唯一源はライブの buildTfPeriodUrl。借り手がクエリを綴るとライブの変更に追随しない。
    const source = readFileSync(
      fileURLToPath(new URL('../js/adapter/front/tf_period_client.js', import.meta.url)), 'utf8',
    );
    const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/[^\n]*/g, '$1');
    assert.doesNotMatch(code, /tf_period_profile\?|datasetRef=|&from=|&to=/);
  });
});
