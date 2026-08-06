// OverlayHost（adapter/front/overlay_host.js）— チャートに重ねる表示系統の**ホスト要素の所有規約**。
//
// 解決する問題（実測 2026-08-06）:
//   表示要素を index.html へ直書きしていたため、配信される 3 ページ（indicator_ui / replay_ui /
//   unified_ui）へ同じマークアップが手書き複製され、表示系統を足すたび 3 枚を同期する義務が
//   生まれていた。取り残しは実際に 3 回起きている:
//     - リプレイ #rp-mode の option 5 件が丸ごと欠落（commit 4079461）
//     - カテゴリボタンの二重表示（ISSUE-221・実際に配信される unified_ui の取り残し）
//     - #pane-legends の欠落でペイン別凡例が全滅（ISSUE-276・:8000 で無症状のまま全滅）
//
// 規約（SOLID）:
//   - SRP: ホスト要素は「そこへ描く View」が所有する。ページが持つのは版面（.chart-wrap）だけ。
//          表示系統の構成は描画側の関心であり、配信ページの関心ではない。
//   - OCP: 表示系統の追加は View を 1 本足すだけで済む。HTML も本モジュールも改変不要
//          （要素名を列挙する中央 factory は置かない＝追加のたび改変が要る＝OCP 違反の移設）。
//   - DIP: View は「ページが宣言した id」ではなく、注入されたアンカー要素に依存する。
//   - ISP: View が触れるのは自分のホスト 1 要素だけ（document 全体の id 空間に依存しない）。
//   - LSP: .chart-wrap を持つページはどれも等価な宿主。要素の取り残しという部分実装が起こり得ない。
//
// フェイルクローズ: DOM がある環境でアンカーが無ければ**例外**を投げる（無言 no-op にしない）。
//   ISSUE-276 の全滅は「要素不在なら no-op」が契約違反を無症状にしたため、実 UI で気付けなかった。

// チャート版面のアンカー。配信 3 ページすべてが持つ唯一の共通土台（CSS も position:relative を
//   ここに置いている）。ページ側に要求するのはこの 1 要素だけに保つ。
export const CHART_ANCHOR_SELECTOR = '.chart-wrap';

/**
 * 表示系統のホスト要素を、アンカー配下に「無ければ作り・あれば再利用して」返す。
 *
 * @param {object|null} doc               DOM 実装（注入）。DOM 非対応環境（SSR/純ロジックテスト）は null 可。
 * @param {object} opts
 * @param {string} opts.className         ホスト要素のクラス名（＝所有者を一意に識別する名前）。
 * @param {string} [opts.anchorSelector]  アンカーの選択子（既定 .chart-wrap）。
 * @param {object} [opts.anchor]          アンカー要素の直接注入（テスト・多版面での明示指定用）。
 * @returns {object|null}                 ホスト要素。DOM 非対応環境では null（描画自体を行わない）。
 * @throws {Error}                        DOM はあるがアンカーが無い場合（契約違反・フェイルクローズ）。
 */
export function ensureOverlayHost(doc, { className, anchorSelector = CHART_ANCHOR_SELECTOR, anchor = null } = {}) {
  if (!className) {
    throw new Error('ensureOverlayHost: className は必須（ホスト要素の所有者名）');
  }
  // DOM 非対応（SSR・要素生成しか持たないスタブ document）は描画対象が存在しない＝縮退する。
  //   版面を「解決できない」環境と、版面が「無い」ページ（＝契約違反）とは厳密に区別する:
  //   実ブラウザの document は必ず querySelector を持つため、実配信ページの取り残しは必ず例外になる。
  if (!doc || typeof doc.createElement !== 'function') {
    return null;
  }
  if (!anchor && typeof doc.querySelector !== 'function') {
    return null;
  }
  const root = anchor ?? doc.querySelector(anchorSelector);
  if (!root) {
    throw new Error(`ensureOverlayHost: アンカー ${anchorSelector} がページに無いため ${className} を配置できない`);
  }
  const existing = typeof root.querySelector === 'function' ? root.querySelector(`.${className}`) : null;
  if (existing) {
    return existing;   // 再入（再 mount・再描画）でホストを増やさない。
  }
  const el = doc.createElement('div');
  el.className = className;
  root.appendChild(el);
  return el;
}
