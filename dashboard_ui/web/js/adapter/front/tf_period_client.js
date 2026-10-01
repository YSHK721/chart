// tf_period_client（adapter/front/tf_period_client.js）— ライブの tf-period 列を借りる唯一の口。
//
// 設計入力（水準到達シート基本設計書 §3.5.2・依頼者裁定 2026-09-29）:
//   価格ラダーの MP の期間水準は live core の `/tf_period_profile` の応答 columns[] をそのまま使う。
//   dashboard core は MP を計算しない。
//
// **URL を組まない**（mp_profile_client と同じ規約）: クエリの唯一源はライブの `buildTfPeriodUrl`
//   であり、注入で受け取って**戻り値の前に prefix を足すだけ**にする。綴っていないことは
//   tests/mp_period_levels.test.js のソース走査が固定する。
//
// 失敗は掲示できる形へ倒す（例外で落とさない・無言 no-op にもしない）。掲示文を組み立てるのは
//   失敗を観測したここだけで、どの失敗も**それだけ読めば MP の期間水準のどの足の話か分かる形**で返す。

/** 失敗を掲示できる形で返す。 */
function failure(timeframe, reason, type = 'TransportError') {
  return { ok: false, error: { type, message: `MP の期間水準（${timeframe}）を借用できません: ${reason}` } };
}

/**
 * tf-period 列の借用クライアントを作る。
 *
 * @param {object}   deps
 * @param {Function} deps.fetch     fetch 実装（注入必須）
 * @param {string}   deps.apiPrefix live core の prefix（統合ページなら '/live'）
 * @param {Function} deps.buildUrl  ライブの `buildTfPeriodUrl`（URL の唯一源）
 * @returns {{fetchColumns: (query: object) => Promise<object>}}
 */
export function createTfPeriodClient({ fetch: fetchFn, apiPrefix, buildUrl } = {}) {
  if (typeof fetchFn !== 'function') {
    throw new TypeError('createTfPeriodClient: fetch の注入は必須');
  }
  if (typeof apiPrefix !== 'string') {
    throw new TypeError('createTfPeriodClient: apiPrefix の注入は必須');
  }
  if (typeof buildUrl !== 'function') {
    throw new TypeError('createTfPeriodClient: buildUrl（ライブの唯一源）の注入は必須');
  }

  /**
   * 取得窓 1 本ぶんの列を借りる（1 要求 = 1 往復）。
   *
   * @param {object} query `buildTfPeriodUrl` の引数（datasetRef / timeframe / from / to / src / va）
   * @returns {Promise<object>} `{ ok:true, columns }` または `{ ok:false, error }`
   */
  async function fetchColumns(query) {
    const timeframe = query && query.timeframe;
    let response;
    try {
      response = await fetchFn(`${apiPrefix}${buildUrl(query)}`);
    } catch (err) {
      return failure(timeframe, err && err.message ? err.message : String(err));
    }
    if (!response || response.ok !== true) {
      const status = response && response.status !== undefined ? response.status : '不明';
      return failure(timeframe, `供給元が応答しません（HTTP ${status}）`);
    }
    let payload;
    try {
      payload = await response.json();
    } catch (err) {
      return failure(timeframe, `応答を読めません: ${err && err.message ? err.message : err}`, 'ProtocolError');
    }
    if (!payload || payload.ok !== true || !Array.isArray(payload.columns)) {
      const reason = payload && payload.error && payload.error.message
        ? payload.error.message : '応答が契約の形ではありません';
      return failure(timeframe, reason, 'ProtocolError');
    }
    return { ok: true, columns: payload.columns };
  }

  return { fetchColumns };
}
