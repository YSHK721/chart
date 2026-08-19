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

/** 照会の周期（ms）。権威は基本設計書 NFR-04「ポーリング間隔 1 秒」。 */
export const POLL_INTERVAL_MS = 1000;

/** 連続で照会に失敗したときに監視を諦める回数。
 *  無限に叩き続けると、サーバが落ちている間ずっと通信を出し続ける。定数は 1 箇所に置く。 */
export const MAX_CONSECUTIVE_FAILURES = 3;

/** 照会失敗（非 2xx / 本文が読めない）のエラー。サーバの error 文言を握って上位へ届ける。 */
export class JobStatusError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "JobStatusError";
    this.status = status;
  }
}

/** ジョブ状態の照会クライアントを作る（fetch と timer は注入・既定は globalThis）。 */
export function createJobStatusClient({
  fetch: fetchFn, setTimeout: setTimeoutFn, clearTimeout: clearTimeoutFn,
} = {}) {
  const doFetch = fetchFn || ((...args) => globalThis.fetch(...args));
  const later = setTimeoutFn || ((...args) => globalThis.setTimeout(...args));
  const cancel = clearTimeoutFn || ((...args) => globalThis.clearTimeout(...args));

  /**
   * ジョブ状態（{job_id, status, failure_reason, terminal}）を返す。
   * 応答は**組み替えずそのまま**返す（front で語彙を作らない）。非 2xx は JobStatusError。
   */
  async function fetchStatus(jobId) {
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
  }

  /**
   * ジョブが終わるまで周期照会する。戻り値は停止関数（`stop()`）。
   *
   * 停止条件は 3 つだけである:
   *   1. 応答の `terminal` が真（**終端判定の権威はサーバ**・§19.6 R1。front は
   *      completed / failed / cancelled のどれであるかを見ない）。
   *   2. 連続失敗が :data:`MAX_CONSECUTIVE_FAILURES` に達した（諦めたことを購読者へ渡す
   *      ＝無音で止まらない）。成功したら連続失敗の数は 0 に戻る。
   *   3. `stop()` が呼ばれた（合成根が再投入時に前の監視を落とすのに使う＝同時 1 本）。
   *
   * 購読者へ渡すのは、成功なら応答そのもの、諦めたときは `{error, status}` である。
   */
  function watch(jobId, onUpdate) {
    let stopped = false;
    let failures = 0;
    let timerId = null;

    const schedule = () => {
      if (stopped) return;
      timerId = later(poll, POLL_INTERVAL_MS);
    };

    async function poll() {
      if (stopped) return;
      let payload = null;
      try {
        payload = await fetchStatus(jobId);
        failures = 0;
      } catch (e) {
        failures += 1;
        if (failures >= MAX_CONSECUTIVE_FAILURES) {
          stopped = true;
          if (onUpdate) onUpdate({ error: (e && e.message) || String(e), status: e && e.status });
          return;
        }
        schedule();
        return;
      }
      if (stopped) return;   // 応答を待っている間に停止されていたら掲示もしない
      if (onUpdate) onUpdate(payload);
      if (payload && payload.terminal === true) {
        stopped = true;
        return;
      }
      schedule();
    }

    schedule();
    return function stop() {
      stopped = true;
      if (timerId !== null) cancel(timerId);
    };
  }

  return { fetchStatus, watch };
}
