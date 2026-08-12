// 統合ページ側の器（View・F-2b）。
//
// 役割: 統合ページに置くのは **#sim-display と iframe の 2 つだけ**にする。表示の中身
//   （3 窓・取引明細）は子文書 `/sim/report_view.html` が持つ。
//
// なぜ別文書（iframe）か — 実 UI 実測（2026-08-11）と裁定 B:
//   移植元 report_ui の style.css は `body { background / color / font / height:100vh /
//   display:flex }` を持つ**全画面ページのための CSS** である。これを統合ページへ link
//   すると、sim とは無関係な既存 UI の見た目まで変わる（実測: body 背景 19,23,34 →
//   14,17,23／font "Helvetica Neue",Arial → Tahoma／文字色 209,212,220 → 201,209,217）。
//   セレクタを書き換えれば「見た目の複製」になり、単一ソースが壊れる。**同じ CSS を
//   そのまま使いつつ波及だけを止める**唯一の構造が、別文書に載せることである。
//   Shadow DOM は style.css:1-13 の `:root` カスタムプロパティが shadow 内で解決される
//   保証を実証できていないため採らない（裁定 B）。
//
// 統合ページへ持ち込む CSS は **sim 所有の器 CSS 1 枚だけ**（寸法のみ）。移植元 style.css
//   は子文書の中でしか読まない。
//
// job_id は URL クエリで子へ渡す（子は自分で `?job=` を読む＝親子で読み方を二重化しない）。

/** 子文書（表示の実体）のパス。 */
export const SIM_REPORT_VIEW_PATH = "/sim/report_view.html";
/** sim 所有 CSS（器の寸法だけを持つ）。統合ページと子文書の両方が読む。 */
export const SIM_FRAME_CSS = "/sim/css/sim_display.css";
/** 器の id（統合ページ側で唯一の sim 由来要素）。 */
export const SIM_FRAME_ID = "sim-display";

/** `?job=<id>` を付けた子文書 URL を作る（id 不在ならクエリなし）。 */
export function reportViewUrl(jobId) {
  return jobId ? `${SIM_REPORT_VIEW_PATH}?job=${encodeURIComponent(jobId)}` : SIM_REPORT_VIEW_PATH;
}

/** 統合ページ側の器（#sim-display ＋ iframe）を生成・破棄する View を返す。 */
export function createSimFrameView({ doc } = {}) {
  let root = null;
  let frame = null;
  let link = null;
  let host = null;

  return {
    isMounted() { return root !== null; },

    /** 器を `target` の下へ挿し、子文書を読み込む（二重 mount は無視）。 */
    mount(target, jobId) {
      if (root) return root;
      host = target;
      root = doc.createElement("div");
      root.id = SIM_FRAME_ID;
      frame = doc.createElement("iframe");
      frame.title = "シミュレーション結果";
      frame.src = reportViewUrl(jobId);
      root.appendChild(frame);

      link = doc.createElement("link");
      link.rel = "stylesheet";
      link.href = SIM_FRAME_CSS;
      doc.head.appendChild(link);

      host.appendChild(root);
      return root;
    },

    /** 器と sim 所有 CSS を外す（統合ページへ何も残さない）。二重 unmount は無視。 */
    unmount() {
      if (link && doc.head) { doc.head.removeChild(link); }
      if (root && host) { host.removeChild(root); }
      root = frame = link = host = null;
    },

    /** 子文書の window（同一オリジン直参照）。未 mount なら null。 */
    childWindow() { return frame ? (frame.contentWindow || null) : null; },

    /** 子文書の iframe 要素（load 待ちの購読点）。未 mount なら null。 */
    frameElement() { return frame; },
  };
}
