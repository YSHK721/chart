// ジョブ状態の照会クライアント（Phase 9 段階 3 S2 M7・§19.6）。
//
// 役割: `GET /sim/jobs/{id}` を叩くことと、その繰り返し（watch）だけを持つ。DOM は
//   一切知らない（掲示は M6）。投入（`POST /sim/jobs`）は M5/job_submit_client の側であり、
//   ここは**読むだけ**である。
//
// なぜ投入クライアントと別モジュールか（SRP・アクター単位）: 投入は「実行操作者」の要求で
//   動き、照会は「状態を見たい」要求で動く。周期・停止条件・連続失敗の扱いが投入側の
//   ファイルに入ると、投入の検定に timer のダブルが要るようになる。
//
// **同一オリジンの相対パス**で書く（統合 UI の routedFetch / Service Worker は mode prefix の
//   付いたパスを冪等に扱う＝job_submit_client と同じ理由）。fetch は注入して実行とテストを分ける。
//
// 依存 0（import しない）: 通信と時計だけの面を、器も本文も無しで素のまま確かめられる状態に
//   保つ（`import_source.test.js` が機械強制する）。

/** ジョブ状態の照会先（sim core のジョブ面）。 */
export function jobStatusUrl(jobId) {
  return `/sim/jobs/${encodeURIComponent(jobId)}`;
}

/** 照会失敗（非 2xx / 本文が読めない）のエラー。サーバの error 文言を握って上位へ届ける。 */
export class JobStatusError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "JobStatusError";
    this.status = status;
  }
}

/** ジョブ状態の照会クライアントを作る。 */
export function createJobStatusClient({ fetch: fetchFn } = {}) {
  const doFetch = fetchFn || ((...args) => globalThis.fetch(...args));

  return {
    /**
     * ジョブ状態（{job_id, status, failure_reason, terminal}）を返す。
     * 応答は**組み替えずそのまま**返す（front で語彙を作らない）。非 2xx は JobStatusError。
     */
    async fetchStatus(jobId) {
      const res = await doFetch(jobStatusUrl(jobId), { cache: "no-store" });
      let payload = null;
      try {
        payload = res && res.json ? await res.json() : null;
      } catch (_e) {
        payload = null;
      }
      if (!res || !res.ok || payload === null) {
        const reason = (payload && payload.error) || `状態を取得できません (HTTP ${res && res.status})`;
        throw new JobStatusError(reason, res && res.status);
      }
      return payload;
    },
  };
}
