// ジョブ投入・指標候補取得のクライアント（Phase 6 F-8）。
//
// 投入先は sim core のジョブ面 `POST /sim/jobs`（既存 Phase 2 の口・新設なし）、指標候補は
// `GET /sim/ea-series/{ea_name}`（Phase 6 の口・**選択中の ea_name の registry 系列名**）。
// どちらも**同一オリジンの相対パス**で書く（統合 UI の routedFetch / Service Worker は既に
// mode prefix の付いたパスを冪等に扱う＝report_source_client と同じ理由）。fetch は注入して
// 実行とテストを分ける。
//
// 候補源の単一ソース（名前空間結線・依頼者承認 2026-08-12）: 指標候補は必ず /sim/ea-series 由来。
// 因果カタログの /sim/indicators は別名前空間（MA / hl_range ...）かつ ea_name 非依存で、候補源に
// 使うと投入時の受付検証（E-5）で全て弾かれる（実測済み）。/sim/ea-series は E-5 と
// GenericConditionStrategy が実際に参照する ea_name 別の registry 系列名を返す。
//
// 本文の byte 等価（§12.1 の流儀）: strategy / sizing は**不在なら本文に載せない**。サーバは
// body.get("strategy") / body.get("sizing") を読むため、キー不在は OFF（None）と等価であり、
// 戦略なしの投入は既存の {backtest[, sizing]} 本文と byte 等価になる。

/** ジョブ投入先（sim core のジョブ面）。 */
export const JOBS_URL = "/sim/jobs";
/** ea_name 別 registry 系列一覧の取得先接頭辞（Phase 6 の口）。実際は `${EA_SERIES_URL}/{ea_name}`。 */
export const EA_SERIES_URL = "/sim/ea-series";
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
     * バックテストジョブを投入する。strategy / sizing は不在なら本文に載せない。
     * 2xx なら parse 済み JSON（job_id / status）を返す。非 2xx は JobSubmitError。
     */
    async submit({ backtest, strategy, sizing } = {}) {
      const body = { backtest: backtest || {} };
      if (nonEmptyObject(strategy)) body.strategy = strategy;
      if (sizing != null) body.sizing = sizing;

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
     * 指定 ea_name の registry 系列名一覧（GET /sim/ea-series/{ea_name}）の payload を返す。
     * ea_name はパスセグメントなので URL エンコードする。非 2xx はサーバ文言つきで throw。
     */
    async loadEaSeries(eaName) {
      const url = `${EA_SERIES_URL}/${encodeURIComponent(eaName)}`;
      const res = await doFetch(url, { cache: "no-store" });
      let payload = null;
      try {
        payload = res && res.json ? await res.json() : null;
      } catch (_e) {
        payload = null;
      }
      if (!res || !res.ok) {
        const reason = (payload && payload.error) || `指標候補を取得できません (HTTP ${res && res.status})`;
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
