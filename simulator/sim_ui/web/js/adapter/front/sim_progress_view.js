// 結果待ちの進み具合の表示（バーと％・2026-09-27 依頼者指示「終わりが分からない」）。
//
// 役割: 進み具合（サーバの `progress`＝0〜100 の整数％・null）を**バーの長さ**と**％の文字**の
//   両方で出すことだけ。HTTP も timer も知らない（値は M7 job_status_client の応答を渡される）。
//
// 単一ソース: 実行状態の掲示（M6 sim_run_status_view）と結果ビューアの完了待ち
//   （job_completion_wait）の 2 か所が、この部品を 1 つずつ組んで使う（見た目も文言も写さない）。
//
// 値の意味（サーバの定義をそのまま使う）:
//   null      … まだ記録が無い（データ読み込み・組み立て中）。0% を名乗らない。
//   0〜99     … 処理を終えた足の割合。
//   100       … 足はすべて処理した。結果を書き出している（終端はサーバの `terminal` が決める）。
//
// DOM は標準の生成と textContent だけで組む（fake DOM で検定できる形・ISSUE-425 の再演防止）。
// 見た目は css/sim_progress.css が持つ。

/** 表示の文言（front が所有する UI 文言）。 */
export const PROGRESS_TEXT = Object.freeze({
  preparing: "準備中…",
  writing: "結果を書き出し中…",
});

/** 値から文言を決める（純関数・DOM 非依存）。 */
export function progressLabel(percent) {
  if (!Number.isInteger(percent)) return PROGRESS_TEXT.preparing;
  if (percent >= 100) return `100%（${PROGRESS_TEXT.writing}）`;
  return `${percent}%`;
}

export function createSimProgressView({ doc } = {}) {
  let root = null;
  let fill = null;
  let label = null;

  const el = (tag, className) => {
    const node = doc.createElement(tag);
    node.className = className;
    return node;
  };

  return {
    elements: {},

    /** host の末尾へ組む（初めは隠しておく）。二重 mount は無視。 */
    mount(host) {
      if (root) return root;
      root = el("div", "sim-progress sim-progress--hidden");
      // ARIA は属性ではなくプロパティで付ける（ARIA 反映・標準 DOM）。fake DOM でも同じ形で検定できる。
      root.role = "progressbar";
      root.ariaValueMin = "0";
      root.ariaValueMax = "100";
      const track = el("div", "sim-progress-track");
      fill = el("div", "sim-progress-fill");
      track.appendChild(fill);
      label = el("span", "sim-progress-label");
      root.appendChild(track);
      root.appendChild(label);
      host.appendChild(root);
      this.elements = { root, fill, label };
      return root;
    },

    /** 進み具合を出す（null は「準備中」＝長さの決まらないバー）。 */
    show(percent) {
      if (!root) return;
      const known = Number.isInteger(percent);
      const value = known ? Math.max(0, Math.min(100, percent)) : null;
      root.className = known ? "sim-progress" : "sim-progress sim-progress--indeterminate";
      fill.style.width = known ? `${value}%` : "";
      label.textContent = progressLabel(value);
      root.ariaValueNow = known ? String(value) : null;
    },

    /** 隠す（終端・失敗・監視の中止）。 */
    hide() {
      if (!root) return;
      root.className = "sim-progress sim-progress--hidden";
    },
  };
}
