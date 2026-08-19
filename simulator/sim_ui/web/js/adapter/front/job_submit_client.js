// ジョブ投入・実行条件取得のクライアント（Phase 6 F-8 / Phase 9 S1）。
//
// 投入先は sim core のジョブ面 `POST /sim/jobs`（既存 Phase 2 の口・新設なし）。
// **同一オリジンの相対パス**で書く（統合 UI の routedFetch / Service Worker は既に
// mode prefix の付いたパスを冪等に扱う＝report_source_client と同じ理由）。fetch は注入して
// 実行とテストを分ける。
//
// 本文の byte 等価（§12.1 の流儀）: strategy / sizing は**不在なら本文に載せない**。サーバは
// body.get("strategy") / body.get("sizing") を読むため、キー不在は OFF（None）と等価であり、
// 戦略なしの投入は既存の {backtest[, sizing]} 本文と byte 等価になる。

/** ジョブ投入先（sim core のジョブ面）。 */
export const JOBS_URL = "/sim/jobs";
/** run config フォームの選択肢（データセット profile＋ea_name 一覧）の取得先（Phase 6 拡張の口）。 */
export const RUN_OPTIONS_URL = "/sim/run-options";

/** 投入失敗（非 2xx）のエラー。サーバの error 文言を握って上位へ届ける。 */
export class JobSubmitError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "JobSubmitError";
    this.status = status;
  }
}

/** 空でない object か（strategy を本文へ載せるかの判定）。 */
function nonEmptyObject(v) {
  return v != null && typeof v === "object" && Object.keys(v).length > 0;
}

/** ジョブ投入・指標候補取得クライアントを作る。 */
export function createJobSubmitClient({ fetch: fetchFn } = {}) {
  const doFetch = fetchFn || ((...args) => globalThis.fetch(...args));

  return {
    /**
     * バックテストジョブを投入する。strategy / sizing / settings は不在なら本文に載せない。
     * 2xx なら parse 済み JSON（job_id / status）を返す。非 2xx は JobSubmitError。
     *
     * settings（Phase 8 §18・第 4 ブロック）は Tester Settings の生トークン Mapping。
     * strategy と同型に「空なら載せない」ため、Tester パネルを結線していない構成の本文は
     * 従来と byte 等価であり、旧フォーム投入がそのまま併存する。
     */
    async submit({ backtest, strategy, sizing, settings } = {}) {
      const body = { backtest: backtest || {} };
      if (nonEmptyObject(strategy)) body.strategy = strategy;
      if (sizing != null) body.sizing = sizing;
      if (nonEmptyObject(settings)) body.settings = settings;

      const res = await doFetch(JOBS_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      let payload = null;
      try {
        payload = res && res.json ? await res.json() : null;
      } catch (_e) {
        payload = null;
      }
      if (!res || !res.ok) {
        const reason = (payload && payload.error) || `投入に失敗しました (HTTP ${res && res.status})`;
        throw new JobSubmitError(reason, res && res.status);
      }
      return payload;
    },

    /**
     * run config フォームの選択肢（GET /sim/run-options）の payload を返す。
     * {ok, datasets:[{dataset, data_path, symbol, ...銘柄仕様}], ea_names:[...]}。非 2xx は throw。
     */
    async loadRunOptions() {
      const res = await doFetch(RUN_OPTIONS_URL, { cache: "no-store" });
      let payload = null;
      try {
        payload = res && res.json ? await res.json() : null;
      } catch (_e) {
        payload = null;
      }
      if (!res || !res.ok) {
        const reason = (payload && payload.error) || `実行条件を取得できません (HTTP ${res && res.status})`;
        throw new JobSubmitError(reason, res && res.status);
      }
      return payload;
    },
  };
}
