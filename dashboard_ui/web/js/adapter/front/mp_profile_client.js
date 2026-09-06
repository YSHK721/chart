// mp_profile_client（adapter/front/mp_profile_client.js）— ライブ MP を借りる唯一の口。
//
// 設計入力（依頼者承認 2026-09-06「MP 列＝ライブ MP の借用」）:
//   ラダーの MP 列の供給元は **live core の `/market_profile`** である。dashboard core は MP の
//   配信面も計算も複製しない——同じデータの供給口を 2 つ作ると、片方だけ直したときの取り違えが
//   無症状で残る（ISSUE-348 と同型）。live 側から借りる形は candles_client（ISSUE-470）と
//   同じ規約であり、prefix は合成根が注入する（本モジュールは場所を知らない）。
//
// **URL を組まない**（本モジュールの最重要の制約）:
//   クエリの唯一源はライブの `buildMarketProfileUrl`（market_profile_client.js:31・純関数）で
//   あり、それを注入で受け取って**戻り値の前に prefix を足すだけ**にする。ここで組み直すと、
//   ライブ側がパラメータを 1 つ足した瞬間にラダーだけが古い URL を投げ、サーバ側メモの共有も
//   仕様の同期も無言で壊れる（版面には「それらしい MP」が出続けるので状態検証では落ちない）。
//   自分で綴っていないことは tests/mp_profile_client.test.js のソース走査が機械的に固定する。
//
// 失敗は**掲示できる形**へ倒す（candles_client.js と同じ規約。例外で落とさず、無言 no-op にも
//   しない——MP 列が空のとき「密度が無い相場」と「借りられなかった」を版面で区別する必要がある）。
//   掲示文を組み立てるのは**失敗を観測したここだけ**である（SRP・candles_client → chartsView と
//   同じ受け渡し: 呼び出し側は `error.message` をそのまま掲示し、頭に文を足さない）。文を 2 か所で
//   足すと版面に同じ主語が二重に出る。したがってどの失敗も**それだけ読めば MP の話だと分かる形**
//   で返す（掲示先はラダーの掲示欄＝他の注記と同居する 1 か所である）。

/** 失敗を掲示できる形で返す。 */
function failure(message, type = 'TransportError') {
  return { ok: false, error: { type, message } };
}

/**
 * ライブ MP の借用クライアントを作る。
 *
 * @param {object}   deps
 * @param {Function} deps.fetch     fetch 実装（注入必須。既定で globalThis を掴むと検定が
 *                                  実ネットワークへ出る経路が残る——candles_client と同じ理由）
 * @param {string}   deps.apiPrefix live core の prefix（統合ページなら '/live'）
 * @param {Function} deps.buildUrl  ライブの `buildMarketProfileUrl`（URL の唯一源）
 * @returns {{fetchProfile: (context: object) => Promise<object>}}
 */
export function createMpProfileClient({ fetch: fetchFn, apiPrefix, buildUrl } = {}) {
  if (typeof fetchFn !== 'function') {
    throw new TypeError('createMpProfileClient: fetch の注入は必須');
  }
  if (typeof apiPrefix !== 'string') {
    throw new TypeError('createMpProfileClient: apiPrefix の注入は必須');
  }
  if (typeof buildUrl !== 'function') {
    throw new TypeError('createMpProfileClient: buildUrl（ライブの唯一源）の注入は必須');
  }

  /**
   * 取得文脈 1 本ぶんのプロファイルを借りる（1 要求 = 1 往復）。
   *
   * @param {object} context ライブと同一の取得文脈（mp_fetch_context が組む）
   * @returns {Promise<object>} `{ ok:true, profile }` または `{ ok:false, error }`
   */
  async function fetchProfile(context) {
    const url = `${apiPrefix}${buildUrl(context)}`;
    let response;
    try {
      response = await fetchFn(url);
    } catch (err) {
      return failure(`MP を借用できません: ${err && err.message ? err.message : err}`);
    }
    if (!response || response.ok !== true) {
      const status = response && response.status !== undefined ? response.status : '不明';
      return failure(`MP の供給元が応答しません（HTTP ${status}）`);
    }
    let payload;
    try {
      payload = await response.json();
    } catch (err) {
      return failure(`MP の応答を読めません: ${err && err.message ? err.message : err}`, 'ProtocolError');
    }
    if (!payload || payload.ok !== true
        || !payload.profile || !Array.isArray(payload.profile.bins)) {
      // 供給元の理由（例 'zp は 1m を支えません'）はそれ単独では何の話か読めない。主語を
      //   ここで 1 度だけ足す（呼び出し側は足さない）。
      const reason = payload && payload.error && payload.error.message
        ? payload.error.message : '応答が契約の形ではありません';
      return failure(`MP を借用できません: ${reason}`, 'ProtocolError');
    }
    return { ok: true, profile: payload.profile };
  }

  return { fetchProfile };
}
