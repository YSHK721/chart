// 実行トレース分析 API の取得クライアント（RUN_TRACE_BASIC_DESIGN §9.2）。
//
// 取得先は sim core の分析面（`simulator/sim_ui/adapter/trace_api_controller.py`）。
// **同一オリジンの相対パス**で書くのは settings_schema_client / job_submit_client と同じ
// 理由で、統合 UI の routedFetch / Service Worker が mode prefix 付きのパスを冪等に扱う
// ためである。fetch は注入して実行とテストを分ける。
//
// 責務（SRP）: HTTP だけ。payload の解釈（どの列を描くか・何が事象か）は持たない
// ——それはサーバ側が宣言から導く単一ソースであり、front はその写しを作らない。
//
// **なぜ窓をクエリでなくパスで運ぶか（実測）**: sim core の GET は
// `serve_sim.make_handler` が `urlparse(self.path).path` を渡すため、**クエリは
// ハンドラで落ちる**。既存の GET JSON ルート 3 本もクエリを受け取っていない。
// サーバ側の綴りは `trace_api_controller.TRACE_PATH_PREFIX` /
// `UNBOUNDED_TOKEN` と同一であり、その一致は
// `simulator/sim_ui/tests/integration/test_serve_sim_trace.py` が機械的に固定する
// （ISSUE-291「サーバ分岐を作っても front が送らなければ無言で死ぬ」の再発防止）。
//
// 非 2xx を throw にする理由: 半端な payload を返すと「点が 1 つも無いチャート」が沈黙で
// 出来上がり、「記録が無い run」と区別できなくなる。取れなかったことは呼出側の判断材料である。

/** 分析 API の根（統合 UI 経由の相対パス）。 */
export const TRACE_ANALYSIS_BASE = "/sim/trace";

/** 窓の片側が無制限であることを表すトークン（サーバの `UNBOUNDED_TOKEN` と同綴り）。 */
export const UNBOUNDED = "-";

/** 記録の範囲を問う URL。 */
export function extentUrl(jobId) {
  return `${TRACE_ANALYSIS_BASE}/${jobId}/extent`;
}

/** 窓の点列を問う URL。境界は epoch **ミリ秒**の整数、`null` は無制限。 */
export function pointsUrl(jobId, start, end) {
  const bound = (v) => (v === null || v === undefined ? UNBOUNDED : String(v));
  return `${TRACE_ANALYSIS_BASE}/${jobId}/points/${bound(start)}/${bound(end)}`;
}

/** 取得失敗。サーバの error 文言と状態を握って上位へ届ける。 */
export class TraceAnalysisError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "TraceAnalysisError";
    this.status = status;
  }
}

/** 分析 API のクライアントを作る。 */
export function createTraceAnalysisClient({ fetch: fetchFn } = {}) {
  const doFetch = fetchFn || ((...args) => globalThis.fetch(...args));

  /**
   * 1 本取ってくる。**返せないときは必ず throw する**（`null` や形不正を成功にしない）。
   *
   * 判定は 3 点:
   *   1. 応答が無い / 非 2xx（413 = 窓が広すぎる・409 = 未完了・404 = 記録が無い）
   *   2. 本文を JSON として読めない（プロキシのエラーページ等・HTTP は 200 になり得る）
   *   3. payload が契約（`ok: true`）を満たさない
   */
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
      throw new TraceAnalysisError(reason, res ? res.status : 0);
    }
    if (!payload || payload.ok !== true) {
      throw new TraceAnalysisError(
        `分析 API の応答が契約を満たしません (${url})`, res.status,
      );
    }
    return payload;
  }

  return {
    /** 記録の範囲・run の設定値・サーバが配る宣言（列・事象種別・上限）。 */
    extent(jobId) {
      return get(extentUrl(jobId));
    },
    /** 窓 `[start, end)`（epoch ミリ秒）の点列・事象・DD。 */
    points(jobId, start, end) {
      return get(pointsUrl(jobId, start, end));
    },
  };
}
