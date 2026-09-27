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

/** ジョブ状態の照会先（sim core のジョブ面）。``waitMs`` を渡すと保留照会（long-poll）。 */
export function jobStatusUrl(jobId, waitMs = null) {
  const base = `/sim/jobs/${encodeURIComponent(jobId)}`;
  return waitMs == null ? base : `${base}?wait_ms=${encodeURIComponent(waitMs)}`;
}

/** 保留照会の保留上限（ms）。サーバ側の上限（serve_sim_jobs の _WAIT_MS_MAX）と同値。
 *  権威は基本設計書 NFR-04（2026-09-27 改訂: 状態照会は完了まで応答を保留できる）。 */
export const WAIT_MS = 25000;

/** **失敗時の再試行**間隔（ms）。NFR-04 改訂（2026-09-27）で周期ポーリングは廃止し、
 *  成功応答の間は保留照会を直列に張る（タイマーを使わない）。タイマーを使うのは
 *  照会が失敗したときの再試行だけである。 */
export const POLL_INTERVAL_MS = 1000;

/** 連続で照会に失敗したときに監視を諦める回数。
 *  無限に叩き続けると、サーバが落ちている間ずっと通信を出し続ける。定数は 1 箇所に置く。 */
export const MAX_CONSECUTIVE_FAILURES = 3;

/**
 * 購読者へ 1 件渡す（監視ループと購読者の**唯一の接点**）。
 *
 * 監視ループの生存は購読者の成否と独立でなければならない。購読者（掲示側）が例外を投げると
 * `poll` の promise が reject するが、timer コールバックの戻り値は誰も待っていないため
 * unhandled rejection として消える——次の照会が予約されないまま監視が**無音で死ぬ**
 * （実行中のジョブの状態が二度と更新されない＝ISSUE-423 が是正したはずの沈黙の再発）。
 *
 * 例外はここで止め、周期・終端停止・諦めの判断（ループの制御）へ持ち込まない。理由は
 * 開発者コンソールへ残す（握り潰し禁止）。購読者の契約は**同期**であり、返り値は使わない。
 */
function notifySubscriber(onUpdate, update) {
  if (!onUpdate) return;
  try {
    onUpdate(update);
  } catch (e) {
    console.error(`実行状態の購読者が例外を投げました: ${(e && e.message) || e}`);
  }
}

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
  async function fetchStatus(jobId, waitMs = null, signal = null) {
    const res = await doFetch(jobStatusUrl(jobId, waitMs), { cache: "no-store", signal });
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
   * ジョブが終わるまで**保留照会（long-poll）を直列に張る**（NFR-04 改訂 2026-09-27）。
   * 戻り値は停止関数（`stop()`）。
   *
   * 周期タイマーは持たない: サーバが完了まで（上限 :data:`WAIT_MS`）応答を保留するため、
   * 成功応答を受けたら**直ちに**次の保留照会を張る。旧 1 秒周期は完了の検知に平均
   * 0.5 秒の待ちを足していた（ISSUE-541 の実測）。タイマーを使うのは失敗時の再試行
   * （:data:`POLL_INTERVAL_MS`）だけである。
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
    // 保留照会は最長 WAIT_MS 接続を握る。stop() で切らないと、再投入を繰り返したときに
    // 停止済みの監視の接続が溜まる（ブラウザの同時接続上限を食う）。無い環境（テストの
    // 素の fake fetch / 旧実行系）では従来どおり＝応答が返った時点で stopped ガードが捨てる。
    const aborter = typeof AbortController !== "undefined" ? new AbortController() : null;

    const retryLater = () => {
      if (stopped) return;
      timerId = later(poll, POLL_INTERVAL_MS);
    };

    async function poll() {
      if (stopped) return;
      let payload = null;
      try {
        payload = await fetchStatus(jobId, WAIT_MS, aborter && aborter.signal);
        failures = 0;
      } catch (e) {
        if (stopped) return;   // 停止後に切れた保留照会を失敗として数えない
        failures += 1;
        if (failures >= MAX_CONSECUTIVE_FAILURES) {
          stopped = true;
          notifySubscriber(onUpdate, { error: (e && e.message) || String(e), status: e && e.status });
          return;
        }
        retryLater();
        return;
      }
      if (stopped) return;   // 応答を待っている間に停止されていたら掲示もしない
      notifySubscriber(onUpdate, payload);
      if (payload && payload.terminal === true) {
        stopped = true;
        return;
      }
      poll();   // 保留照会の直列（サーバが待つのでタイマーは要らない）
    }

    poll();
    return function stop() {
      stopped = true;
      if (timerId !== null) cancel(timerId);
      if (aborter !== null) aborter.abort();
    };
  }

  return { fetchStatus, watch };
}
