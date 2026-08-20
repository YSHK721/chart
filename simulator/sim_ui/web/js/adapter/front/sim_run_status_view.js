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
const PHASE_ABANDONED = "状態の取得を中止しました";
const PHASE_FATAL = "画面を組み立てられませんでした";

/**
 * 掲示枠の宣言表（唯一の宣言）。器（要素と class）も書き込み先も公開参照も**この表から
 * 導出する**。枠を増やすときはここへ 1 行足すだけであり、3 箇所（mount の生成・post の
 * 書き込み・elements の公開）へ別々に書き足さない——別々に書けば、足し忘れた側だけが
 * 黙って古いままになる。変化の軸が 1 本なら表も 1 本にする（OCP・M2 の EA_INPUT_FIELDS
 * と同じ流儀）。
 *
 *   key       : 掲示 1 件のキー（`post` に渡す名前。公開参照は `<key>Node`）
 *   tag       : 生成する要素（理由文だけ改行させるため div / span を使い分ける）
 *   className : CSS の選択子（`css/sim_run_form.css` が #simRunStatusPanel 配下で引く）
 */
const STATUS_SLOTS = Object.freeze([
  Object.freeze({ key: "phase", tag: "div", className: "run-status-phase" }),
  Object.freeze({ key: "job", tag: "span", className: "run-status-job" }),
  Object.freeze({ key: "state", tag: "span", className: "run-status-state" }),
  Object.freeze({ key: "reason", tag: "div", className: "run-status-reason" }),
]);

export function createSimRunStatusView({ doc } = {}) {
  let root = null;
  /** key → 掲示枠の要素（mount 前は空＝掲示は何もしない）。 */
  const slots = new Map();
  // 掲示中の job（状態更新のたびに「どの run の話か」が消えないよう覚えておく）。
  let currentJobId = null;

  const el = (tag, props) => {
    const node = doc.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) node[k] = v;
    return node;
  };

  /** 掲示枠を書き換える（未 mount では何もしない）。値は必ず文字列にして入れる。
   *  表に在る枠は**毎回すべて**書く（前の掲示の断片が残ると、今の状態と混ざって読める）。 */
  function post(update) {
    if (!root) return;
    for (const slot of STATUS_SLOTS) {
      const value = update[slot.key];
      slots.get(slot.key).textContent = value == null ? "" : String(value);
    }
  }

  return {
    elements: {},

    /**
     * 掲示面を host へ組む。
     *
     * `atTop` は「途中まで組めた面より前に出す」ためだけの選択肢である（§19.6 R2）。
     * 通常経路の掲示はスタートの直下（＝末尾）だが、器の組み立てに失敗したときは
     * **半端に組まれた面の下**に理由を置くと見つけられない。どちらにするかは器の組み方を
     * 知っている合成根が決め、この面は挿す位置を自分で判断しない。
     */
    mount(host, { atTop = false } = {}) {
      root = el("div", { id: "simRunStatusPanel", className: "run-status-panel" });
      const elements = { root };
      for (const slot of STATUS_SLOTS) {
        const node = el(slot.tag, { className: slot.className });
        root.appendChild(node);
        slots.set(slot.key, node);
        elements[`${slot.key}Node`] = node;
      }
      // 先客が居なければ `firstChild` は null＝insertBefore は末尾追加と同じ意味になる
      // （実 DOM の仕様。空の host でも分岐を増やさない）。
      if (atTop) host.insertBefore(root, host.firstChild);
      else host.appendChild(root);
      this.elements = elements;
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

    /**
     * 状態の取得を諦めた（連続失敗で監視を止めた）。
     *
     * 止まったのは**監視**であってジョブではない。段階を「実行中…」のままにすると利用者は
     * 来ない更新を待ち続け、「終了しました」にすると終わっていないジョブを終わったことに
     * してしまう（終端を決めるのはサーバ・§19.6 R1）。第 3 の段階として区別する。
     * 直近に見えていた状態は消さずに残す（どこまで見えていたかが判断材料になる）。
     */
    showWatchAbandoned({ status, failure_reason: failureReason } = {}) {
      post({
        phase: PHASE_ABANDONED, job: currentJobId, state: status, reason: failureReason,
      });
    },

    /** 器そのものを組めなかった（mount 段の失敗・B4）。押せる物が無い画面を無音にしない。 */
    showFatal(message) {
      post({ phase: PHASE_FATAL, job: currentJobId, reason: message });
    },
  };
}
