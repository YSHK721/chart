// 実行中のジョブを開いたときの完了待ち（ISSUE-540）。
//
// 実測（2026-09-27・実 UI）: 投入直後に「結果を見る」を押すと結果ビューアの report.json が
//   409（未完了）になり、「実行中のまま完了しても誰も読み直さない」＝取引明細 0 行・
//   売買履歴チャート空のまま残った。
//
// 役割: 「実行中」を掲示し、ジョブ状態を監視（M7 job_status_client）して、完了したら
//   **文書を読み直す**。結果ビューアの組み立ては「読み込み時に 1 回」であり、途中から
//   再開する第 2 の経路を作らない。親の売買履歴チャートも load イベントで読み直される
//   （結線は既存の 1 本のまま・監視も文書につき 1 本で済む）。
//
// DOM を知らない: 掲示は view.showMessage、読み直しは注入された reload だけを使う。

/**
 * 「実行中」を掲示し、完了まで監視する。
 *
 * @param {object}   deps
 * @param {object}   deps.view         showMessage(text) を持つ（結果ビューアの View）
 * @param {object}   deps.statusClient watch(jobId, onUpdate) を持つ（M7 job_status_client）
 * @param {string}   deps.jobId        監視するジョブ
 * @param {function} deps.reload       文書の読み直し（親側の再描画も load イベントで従う）
 * @param {object}  [deps.progress]    show(percent|null) / hide() を持つ（sim_progress_view）
 * @returns {function} 監視の停止関数（破棄時に必ず呼ぶ）
 */
export function showRunningAndWaitForCompletion({ view, statusClient, jobId, reload, progress = null }) {
  view.showMessage("ジョブは実行中です。完了すると自動で表示します。");
  if (progress) progress.show(null);
  return statusClient.watch(jobId, (update) => {
    if (progress) {
      // 終わっていない間だけバーと％を出す（値が無ければ「準備中」）。
      if (update && !update.error && update.terminal !== true) {
        progress.show(Number.isInteger(update.progress) ? update.progress : null);
      } else {
        progress.hide();
      }
    }
    if (update && update.error) {
      // 止まったのは監視であってジョブではない（無音で止まらない・§19.6 と同じ扱い）。
      view.showMessage(`実行状態の監視を継続できません: ${update.error}`);
      return;
    }
    if (!update || update.terminal !== true) return;
    if (update.status === "completed") {
      reload();
      return;
    }
    view.showMessage(update.failure_reason || `ジョブは ${update.status} で終わりました`);
  });
}
