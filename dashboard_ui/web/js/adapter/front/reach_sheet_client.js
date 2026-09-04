// reach_sheet_client（adapter/front/reach_sheet_client.js）— `/reach_sheet` を叩く唯一の口。
//
// 設計入力: arch-spec §9（JSON 契約。単一ソースは dashboard_ui/usecase/sheet_models.py。
//   **フィールド名の別名を発明しない**）／arch-spec §7（`sw_rewrite.js` は改変不要＝View が
//   apiPrefix 注入で `/dashboard/reach_sheet` を直接叩く）。
//
// フロントは数値を再計算しない（arch-spec §9）。`p` の算出・並び替え・到達判定はすべて
//   サーバ側が単一ソースであり、ここが行うのは往復 1 本だけである。
//
// prefix を文字列で書き写さない理由: モードの prefix の唯一源は unified_ui の `mode_table.js`
//   だが、そこから import すると依存が逆流する（arch-spec §1 の依存方向: dashboard_ui は
//   unified_ui を知らない）。代わりに**自分が配信されている場所**から導く。本モジュールは
//   `<prefix>/js/adapter/front/reach_sheet_client.js` として配信されるので、`/js/` の手前が
//   そのまま prefix である。写しではなく実際の配信位置なので、定義がズレようがない。
//
// 失敗は**掲示できる形**へ倒す（例外で落とさない・無言 no-op にもしない）。表示側が理由を
//   出せなければ、利用者からは「空の表」と区別が付かない。

/** 面（arch-spec §9）。 */
const REACH_SHEET_PATH = '/reach_sheet';

/** 配信位置の目印。この手前までが prefix。 */
const FRONT_TREE_MARK = '/js/';

/**
 * モジュールの配信 URL から API prefix を導く。
 *
 * @param {string} moduleUrl `import.meta.url` 相当
 * @returns {string} prefix（root 直下配信なら空文字）
 * @throws {TypeError} `/js/` を含まない位置から呼ばれたとき（導出不能を黙って '' に倒さない）
 */
export function deriveApiPrefix(moduleUrl) {
  const path = String(moduleUrl).replace(/^[a-z]+:\/\/[^/]*/i, '');
  const at = path.indexOf(FRONT_TREE_MARK);
  if (at < 0) {
    throw new TypeError(
      `reach_sheet_client: 配信位置から API prefix を導けません（${moduleUrl}）`,
    );
  }
  return path.slice(0, at);
}

/** 失敗を掲示できる形で返す。 */
function failure(message, type = 'TransportError') {
  return { ok: false, error: { type, message } };
}

/**
 * `/reach_sheet` クライアントを作る。
 *
 * @param {object} deps
 * @param {Function} deps.fetch      fetch 実装（注入必須。既定で globalThis を掴むと、
 *                                   検定が実ネットワークへ出る経路が残る）
 * @param {string}   deps.apiPrefix  API prefix（統合ページなら '/dashboard'）
 * @returns {{fetchSheet: (body: object) => Promise<object>}}
 */
export function createReachSheetClient({ fetch: fetchFn, apiPrefix } = {}) {
  if (typeof fetchFn !== 'function') {
    throw new TypeError('createReachSheetClient: fetch の注入は必須');
  }
  if (typeof apiPrefix !== 'string') {
    throw new TypeError('createReachSheetClient: apiPrefix の注入は必須');
  }
  const endpoint = `${apiPrefix}${REACH_SHEET_PATH}`;

  /**
   * シートを 1 回だけ取りに行く（1 要求 = 1 往復）。
   *
   * @param {object} body arch-spec §9 の要求ボディ
   * @returns {Promise<object>} 応答（成功・失敗いずれも `ok` を持つ形）
   */
  async function fetchSheet(body) {
    let response;
    try {
      response = await fetchFn(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
    } catch (err) {
      return failure(`ダッシュボードに接続できません: ${err && err.message ? err.message : err}`);
    }
    if (!response || response.ok !== true) {
      const status = response && response.status !== undefined ? response.status : '不明';
      return failure(`ダッシュボードが応答しません（HTTP ${status}）`);
    }
    try {
      return await response.json();
    } catch (err) {
      return failure(`応答を読めません: ${err && err.message ? err.message : err}`, 'ProtocolError');
    }
  }

  return { fetchSheet };
}
