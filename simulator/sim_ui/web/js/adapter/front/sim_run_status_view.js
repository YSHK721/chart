// 実行状態の掲示面（View・Phase 9 段階 3 S1 M6・§19.6）。
//
// 役割: 投入と実行の**今の状態を画面に出すこと**だけを持つ。押す物は 1 つも作らない
//   （操作は M3 実行指示面が持つ）。HTTP も timer も知らない（それは M7）。
//
// なぜ M3 と別の面にするか（SRP・アクター単位）: 「sim ジョブ面 API の契約者」は
//   「実行操作者」と独立に変更要求を出す。応答に項目が増えた・状態語彙が増えた——という
//   要求でスタートボタンの面を書き換えると、押す物と読む物が同じ理由で動く。
//
// 語彙の単一ソース: `status` はサーバの語彙をそのまま出す。日本語ラベル表を front に
//   持たない（持てば列挙が増えたとき UI だけ古くなる）。終端かどうかも front では判定せず、
//   サーバが配る `terminal` をそのまま使う（domain 規則の第 2 実装を作らない・§19.6 R1）。
//
// DOM 更新は `textContent` と標準 DOM 生成だけで行う（ISSUE-425 の再演防止）。
//
// fake DOM 前提: querySelector は使わず、要素参照を JS 側で保持する。

/** 掲示の段階（front が所有する UI 文言）。状態語彙ではないので、サーバの列挙が
 *  増えてもここは変わらない（`status` は生値で別枠に出す）。 */
const PHASE_SUBMITTING = "投入中…";
const PHASE_ACCEPTED = "受付しました";
const PHASE_REJECTED = "投入できませんでした";
const PHASE_RUNNING = "実行中…";
const PHASE_TERMINAL = "終了しました";
const PHASE_FATAL = "画面を組み立てられませんでした";

export function createSimRunStatusView({ doc } = {}) {
  let root = null;
  let phaseNode = null;
  let jobNode = null;
  let stateNode = null;
  let reasonNode = null;
  // 掲示中の job（状態更新のたびに「どの run の話か」が消えないよう覚えておく）。
  let currentJobId = null;

  const el = (tag, props) => {
    const node = doc.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) node[k] = v;
    return node;
  };

  /** 掲示枠を書き換える（未 mount では何もしない）。値は必ず文字列にして入れる。 */
  function post({ phase, job, state, reason }) {
    if (!root) return;
    phaseNode.textContent = phase == null ? "" : String(phase);
    jobNode.textContent = job == null ? "" : String(job);
    stateNode.textContent = state == null ? "" : String(state);
    reasonNode.textContent = reason == null ? "" : String(reason);
  }

  return {
    elements: {},

    mount(host) {
      root = el("div", { id: "simRunStatusPanel", className: "run-status-panel" });
      phaseNode = el("div", { className: "run-status-phase" });
      jobNode = el("span", { className: "run-status-job" });
      stateNode = el("span", { className: "run-status-state" });
      reasonNode = el("div", { className: "run-status-reason" });
      for (const node of [phaseNode, jobNode, stateNode, reasonNode]) root.appendChild(node);
      host.appendChild(root);
      this.elements = { root, phaseNode, jobNode, stateNode, reasonNode };
      return root;
    },

    /** 投入の往路（応答待ち）。 */
    showSubmitting() {
      post({ phase: PHASE_SUBMITTING });
    },

    /** 投入が受理された（job_id と status はサーバの値をそのまま出す）。 */
    showAccepted({ job_id: jobId, status } = {}) {
      currentJobId = jobId == null ? null : String(jobId);
      post({ phase: PHASE_ACCEPTED, job: currentJobId, state: status });
    },

    /** 投入が拒まれた（サーバの理由文と HTTP 状態をそのまま出す・ISSUE-423）。 */
    showRejected({ message, status } = {}) {
      post({ phase: PHASE_REJECTED, state: status, reason: message });
    },

    /**
     * 実行状態を掲示する（`GET /sim/jobs/{id}` の応答）。
     *
     * 段階の文言は `terminal` **だけ**で決める。status の値から段階を決めると front が
     * 終端集合を持つことになる（§19.6 R1: 終端判定の権威はサーバ）。`terminal` を配らない
     * 応答は「まだ終わっていない」側へ倒す（勝手に終わったことにしない）。
     */
    showJobState({ status, failure_reason: failureReason, terminal } = {}) {
      post({
        phase: terminal === true ? PHASE_TERMINAL : PHASE_RUNNING,
        job: currentJobId,
        state: status,
        reason: failureReason,
      });
    },

    /** 器そのものを組めなかった（mount 段の失敗・B4）。押せる物が無い画面を無音にしない。 */
    showFatal(message) {
      post({ phase: PHASE_FATAL, job: currentJobId, reason: message });
    },
  };
}
