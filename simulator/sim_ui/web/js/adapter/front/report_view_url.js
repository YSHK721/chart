// 結果ビューア URL の単一ソース（ISSUE-421）。
//
// 同一概念（結果ビューアの URL）の導出規則（`?job=<id>` クエリの組み立て）はこの 1 箇所
// だけが持つ。以前は sim_frame_view.js（絶対パス版）と composition_root_execution.js
// （相対クエリ版）に同名 `reportViewUrl` が別実装で併存し、値も規則も分岐していた
// （規約「同一概念に複数の呼び名を作らない」）。第 2 実装の再発は
// tests/import_source.test.js の走査（2c）が機械的に遮断する。
//
// base の使い分け（規則は 1 つ・表記の基準だけが違う）:
//   - 既定（SIM_REPORT_VIEW_PATH）: 別文書（統合ページの iframe など）から子文書を名指す形。
//   - ""                          : report_view.html **自身の中**で dispatch する形（相対
//     クエリ。現在の文書に対して解決されるため、配信パスが変わっても壊れない）。

/** 子文書（表示の実体）のパス。 */
export const SIM_REPORT_VIEW_PATH = "/sim/report_view.html";

/** `?job=<id>` を付けた結果ビューア URL を作る（id 不在ならクエリなし）。 */
export function reportViewUrl(jobId, base = SIM_REPORT_VIEW_PATH) {
  return jobId ? `${base}?job=${encodeURIComponent(jobId)}` : base;
}
