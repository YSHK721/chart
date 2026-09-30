// 足の成果物の取得クライアント（売買履歴チャートの足・口座・指標を位置の区間で読む・ISSUE-552/554 段階 2-2）。
//
// 取得先は sim core の足の API（`simulator/sim_ui/adapter/chart_bars_api_controller.py`）:
//   GET /sim/chart-bars/{job}/extent               行数・列・指標と列の対応・1 回の上限（宣言）
//   GET /sim/chart-bars/{job}/rows/{start}/{end}   位置の半開区間 [start, end) の列
// 同一オリジンの絶対パスで書く理由と、区間をクエリでなくパスで運ぶ理由は
// `trace_analysis_client.js` と同じ（sim core の GET はハンドラでクエリを落とす）。
// サーバ側の綴りとの一致は `tests/chart_bars_client.test.js` がサーバのソースを読んで固定する。
//
// 責務（SRP）: HTTP だけ。どの区間を読むか（`usecase/chart_window.js`）も、何を描くか
// （`usecase/result_chart_model.js`）も持たない。
//
// 非 2xx は throw にする（状態を `status` に載せる）。半端な応答を成功にすると、足の無いチャートが
// 沈黙で出来上がる。呼び手は状態で掲示を分ける（409＝未完了・404＝足の成果物が無い・413＝区間が広すぎる）。

/** 足の API の根（統合 UI から見た絶対パス）。 */
export const CHART_BARS_BASE = "/sim/chart-bars";

/** 宣言を問う URL。 */
export function extentUrl(jobId) {
  return `${CHART_BARS_BASE}/${encodeURIComponent(jobId)}/extent`;
}

/** 位置の半開区間 [start, end) の列を問う URL。 */
export function rowsUrl(jobId, start, end) {
  return `${CHART_BARS_BASE}/${encodeURIComponent(jobId)}/rows/${start}/${end}`;
}

/** 取得失敗。サーバの error 文言と状態を握って上位へ届ける。 */
export class ChartBarsError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ChartBarsError";
    this.status = status;
  }
}

/** 足の API のクライアントを作る。 */
export function createChartBarsClient({ fetch: fetchFn } = {}) {
  const doFetch = fetchFn || ((...args) => globalThis.fetch(...args));

  /** 1 本取ってくる。返せないときは必ず throw する（形の不正を成功にしない）。 */
  async function get(url) {
    const res = await doFetch(url, { cache: "no-store" });
    let payload = null;
    try {
      payload = res && res.json ? await res.json() : null;
    } catch (_e) {
      payload = null;
    }
    if (!res || !res.ok) {
      const reason = (payload && payload.error) || `取得できませんでした (${url})`;
      throw new ChartBarsError(reason, res ? res.status : 0);
    }
    if (!payload || payload.ok !== true) {
      throw new ChartBarsError(`足の API の応答が契約を満たしません (${url})`, res.status);
    }
    return payload;
  }

  return {
    /** 行数・列・指標と列の対応・1 回の上限（宣言）。 */
    extent(jobId) {
      return get(extentUrl(jobId));
    },
    /** 位置の半開区間 [start, end) の列。 */
    rows(jobId, start, end) {
      return get(rowsUrl(jobId, start, end));
    },
  };
}
