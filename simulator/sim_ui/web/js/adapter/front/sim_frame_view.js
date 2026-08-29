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

    /**
     * 子文書の**中身が必要としている高さ**（px）。分からなければ null（ISSUE-442）。
     *
     * 何のためか: 統合ページの下部ペインの既定高さを「中身が収まる高さ」にするため
     *   （依頼者裁定 2026-08-22）。既定が版面の 45% 固定だと、投入フォームの下に余白が出る一方で
     *   チャート側は必要以上に削られ、指標ペインが狭くなって手で広げる作業が要った。
     *
     * 何を測るか: 投入フォームの版面（`#simRunForm`）の中身の高さだけ。結果ビューア
     *   （`?job=<id>`）では null を返す——結果は広いほど読みやすく、「収まる高さ」という
     *   概念が当てはまらない（狭める既定を勝手に決めない＝ビュー自動介入の禁止）。
     *   同一オリジンなので直接読める。まだ読み込まれていない・別オリジンなら null。
     */
    contentHeightPx() {
      const doc2 = frame ? (frame.contentDocument || null) : null;
      const form = doc2 && typeof doc2.getElementById === "function"
        ? doc2.getElementById("simRunForm") : null;
      if (!form) return null;
      const h = form.scrollHeight;
      return Number.isFinite(h) && h > 0 ? h : null;
    },
  };
}

/** 子文書の組み立て完了を待つ回数の上限（1 フレーム ≒ 16ms なので約 2 秒）。 */
const CONTENT_READY_MAX_FRAMES = 120;

/**
 * 子文書が組み上がってから中身の高さを 1 回だけ知らせる（ISSUE-442）。
 *
 * 子は `window.__simReportViewReady` で完了を表明する（`report_view.html` の finally）。
 * 表明を待たずに測ると組み立て途中の高さを掴む。上限まで待っても表明されない・高さが
 * 測れない（結果ビューア）ときは**何も知らせない**——分からない値で版面を動かさない。
 *
 * @param {object}   frame  器の View（contentHeightPx / childWindow を持つ）
 * @param {function} raf    次フレームの予約（注入・テストは自前の駆動を渡す）
 * @param {function} notify 高さの通知先
 */
export function waitForContent(frame, raf, notify) {
  if (typeof raf !== "function") return;
  let frames = 0;
  const tick = () => {
    const win = frame.childWindow();
    if (win && win.__simReportViewReady) {
      // 表明の直後はまだ描画が確定していないことがあるので、もう 1 フレーム置いて測る。
      raf(() => {
        const h = frame.contentHeightPx();
        if (h) notify(h);
      });
      return;
    }
    frames += 1;
    if (frames <= CONTENT_READY_MAX_FRAMES) raf(tick);
  };
  raf(tick);
}
