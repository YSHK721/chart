// report.json の取得元（F-4）。
//
// 取得先は sim core の結果配信面 `GET /sim/data/{job_id}/report.json`（既存 Phase 2 の口・
// 新設なし）。**絶対パス**で書くのは、統合 UI の routedFetch / Service Worker が
// 「既にモード prefix の付いたパスは書き換えない」＝冪等だからである（sw_rewrite.js の
// MODE_PREFIXES 走査で確認済み）。よって fetch を注入して回す必要がない。
//
// job_id は `?job=<id>` からのみ得る。**一覧から自動で選ばない**（ビュー自動介入の禁止）。
// 取得できないときは例外にする。半端に描くと「古い結果が今の結果に見える」状態を作る
// （部分描画しない・fail-stop）。

/** 結果ペイロードのファイル名（sim core の /data/{job_id}/{file} が受ける名前）。 */
export const REPORT_FILENAME = "report.json";
/** 結果配信面の根（統合 UI から見た絶対パス）。 */
export const SIM_DATA_BASE = "/sim/data";

/** 取得失敗の理由コードつきエラー（表示メッセージの出し分けに使う）。 */
export class ReportSourceError extends Error {
  constructor(message, code, status) {
    super(message);
    this.name = "ReportSourceError";
    this.code = code;      // no_job / not_ready / broken / unreachable
    this.status = status;  // HTTP status（あれば）
  }
}

/** `?job=<id>` から job_id を読む。無ければ null（自動選択しない）。 */
export function readJobId(search) {
  if (typeof search !== "string" || search === "") return null;
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  const raw = params.get("job");
  if (raw === null) return null;
  const id = raw.trim();
  return id === "" ? null : id;
}

/** 表示する区間を選ぶ＝**先頭の区間**（承認 G の規則: `Object.keys(segments)[0]`）。
 *
 * キー名で分岐しない。供給元は 2 通りある（report_ui の {"is","oos"} と sim 単一 run の
 * {"single"}）が、どちらも先頭が表示対象である。"is" を特別扱いすると、供給元が増える
 * たびに front を直す義務が生まれる（規則が 2 本になる）。
 * 非整数キーのオブジェクトは挿入順が保たれるため、この選択は決定的である
 * （数値キーの segments には適用しない）。
 */
export function firstSegment(payload) {
  const segments = payload && payload.segments;
  if (!segments) return null;
  const keys = Object.keys(segments);
  return keys.length ? segments[keys[0]] : null;
}

/** report.json を取りに行くクライアントを作る。 */
export function createReportSourceClient({ fetch: fetchFn, base = SIM_DATA_BASE } = {}) {
  const doFetch = fetchFn || ((...args) => globalThis.fetch(...args));

  return {
    /** job_id の report.json を取得する。取得できなければ理由コードつきで throw する。 */
    async load(jobId) {
      if (!jobId) {
        throw new ReportSourceError("ジョブ未指定", "no_job");
      }
      const url = `${base}/${encodeURIComponent(jobId)}/${REPORT_FILENAME}?v=${Date.now()}`;
      let res;
      try {
        res = await doFetch(url, { cache: "no-store" });
      } catch (e) {
        throw new ReportSourceError(`結果を取得できません: ${e && e.message ? e.message : e}`, "unreachable");
      }
      if (!res || !res.ok) {
        throw new ReportSourceError("結果未生成", "not_ready", res && res.status);
      }
      try {
        return await res.json();
      } catch (e) {
        throw new ReportSourceError("結果ファイルを読めません（JSON 不正）", "broken", res.status);
      }
    },
  };
}
