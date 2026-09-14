// candles_client（adapter/front/candles_client.js）— ローソク取得（GET /candles）の唯一の口。
//
// 設計入力: ISSUE-452 内容 2（各時間足のチャート一覧）。ローソクの供給元は **live core の
//   `/candles`** である（`indigators/indicator_ui/api/usecase/serve_candles.py` が正）。
//   dashboard core は水準の計算だけを持ち、ローソクの配信面を複製しない——同じデータの
//   供給口を 2 つ作ると、片方だけ直したときの取り違えが無症状で残る（ISSUE-348 と同型）。
//   live 側から借りる形は period_presets の実行時 import（composition_root_front.js）と
//   同じ規約であり、prefix は合成根が注入する（本モジュールは場所を知らない）。
//
// クエリの契約は live フロントの参照実装（chart_app_wiring.js:74-96）と同一:
//   GET <prefix>/candles?datasetRef=&timeframe=&limit=  →  { ok, candles } | { ok:false, error }
//
// 失敗は**掲示できる形**へ倒す（reach_sheet_client.js と同じ規約。例外で落とさず、
//   無言 no-op にもしない——タイルに理由が出なければ「空のチャート」と区別が付かない）。

/** 面（live core の配信 API）。 */
const CANDLES_PATH = '/candles';

/** 失敗を掲示できる形で返す。 */
function failure(message, type = 'TransportError') {
  return { ok: false, error: { type, message } };
}

/**
 * `/candles` クライアントを作る。
 *
 * @param {object}   deps
 * @param {Function} deps.fetch      fetch 実装（注入必須。既定で globalThis を掴むと検定が
 *                                   実ネットワークへ出る経路が残る——reach_sheet_client と同じ理由）
 * @param {string}   deps.apiPrefix  live core の prefix（統合ページなら '/live'）
 * @returns {{fetchCandles: (opts: object) => Promise<object>}}
 */
export function createCandlesClient({ fetch: fetchFn, apiPrefix } = {}) {
  if (typeof fetchFn !== 'function') {
    throw new TypeError('createCandlesClient: fetch の注入は必須');
  }
  if (typeof apiPrefix !== 'string') {
    throw new TypeError('createCandlesClient: apiPrefix の注入は必須');
  }

  /**
   * ローソクを 1 時間足ぶん取りに行く（1 要求 = 1 往復）。
   *
   * @param {object} opts
   * @param {string} opts.datasetRef 素材（T-10: live と同一データセット固定）
   * @param {string} opts.timeframe  時間足コード
   * @param {number} opts.limit      末尾からの本数
   * @returns {Promise<object>} `{ ok:true, candles }` または `{ ok:false, error }`
   */
  async function fetchCandles({ datasetRef, timeframe, limit } = {}) {
    const url = `${apiPrefix}${CANDLES_PATH}`
      + `?datasetRef=${encodeURIComponent(String(datasetRef))}`
      + `&timeframe=${encodeURIComponent(String(timeframe))}`
      + `&limit=${encodeURIComponent(String(limit))}`;
    let response;
    try {
      response = await fetchFn(url);
    } catch (err) {
      return failure(`ローソクを取得できません: ${err && err.message ? err.message : err}`);
    }
    if (!response || response.ok !== true) {
      const status = response && response.status !== undefined ? response.status : '不明';
      return failure(`ローソクの供給元が応答しません（HTTP ${status}）`);
    }
    let payload;
    try {
      payload = await response.json();
    } catch (err) {
      return failure(`応答を読めません: ${err && err.message ? err.message : err}`, 'ProtocolError');
    }
    if (!payload || payload.ok !== true || !Array.isArray(payload.candles)) {
      const reason = payload && payload.error && payload.error.message
        ? payload.error.message : 'ローソクの応答が契約の形ではありません';
      return failure(reason, 'ProtocolError');
    }
    return { ok: true, candles: payload.candles };
  }

  return { fetchCandles };
}
