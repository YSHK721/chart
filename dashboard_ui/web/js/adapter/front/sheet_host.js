// sheet_host（adapter/front/sheet_host.js）— 水準到達シートのホスト要素の**所有規約**。
//
// 設計入力: indigators/indicator_ui/web/js/adapter/front/overlay_host.js:11-22 の規約に厳密に倣う。
//   - SRP: ホスト要素は「そこへ描く View」が所有する。ページが持つのは版面（注入されるアンカー）だけ。
//   - OCP: 表示系統の追加は View を 1 本足すだけで済む。要素名を列挙する中央 factory は置かない。
//   - DIP: View は「ページが宣言した id」ではなく、**注入されたアンカー要素**に依存する。
//   - ISP: View が触れるのは自分のホスト 1 要素だけ（document 全体の id 空間に依存しない）。
//   - フェイルクローズ: DOM はあるがアンカーが無ければ**例外**（無言 no-op にしない）。
//     ISSUE-276 の全滅は「要素不在なら no-op」が契約違反を無症状にしたため実 UI で気付けなかった。
//
// dashboard 固有の要件（unified_root.js:387-396 / arch-spec §7）: 渡される host は **sim と共有
//   する bottomPane の器**である。したがって `unmount()` は統合ページへ 1 要素も残してはならない。
//   残すと、sim モードの版面に dashboard の残骸が混ざる。CSS の link も同じ理由で View が
//   持ち込み、View が片付ける（残すと dashboard を出た後も規則が効き続ける）。
//
// id を付けない: 統合ページの id 空間は統合層のものであり、表示層が奪うと衝突の原因になる
//   （sim は移植元 CSS の都合で id を使うが、本表示層に移植元は無いのでクラスだけで足りる）。

/** ホスト要素のクラス名（＝所有者を一意に識別する名前）。 */
export const SHEET_HOST_CLASS = 'dash-sheet-host';

/** CSS の既定の置き場所（配信位置は合成根が導いて渡す）。 */
const DEFAULT_STYLE_HREF = null;

/**
 * ホスト要素の生成・破棄を持つ器を返す。
 *
 * @param {object}      opts
 * @param {object|null} opts.doc        DOM 実装（注入）。純ロジック環境は null 可。
 * @param {string|null} [opts.styleHref] 持ち込む stylesheet の URL（null なら持ち込まない）。
 * @returns {{mount: Function, unmount: Function, element: Function}}
 */
export function createSheetHost({ doc, styleHref = DEFAULT_STYLE_HREF } = {}) {
  let hostEl = null;
  let styleEl = null;

  /**
   * ホストをアンカー配下へ置く（すでに在れば再利用する＝再入で増やさない）。
   *
   * @param {object|null} anchor 置き先（統合層が渡す bottomPane の器）
   * @returns {object|null} ホスト要素。DOM 非対応環境では null（描画自体を行わない）。
   * @throws {Error} DOM はあるがアンカーが無い場合（契約違反・フェイルクローズ）。
   */
  function mount(anchor) {
    // DOM 非対応（SSR・純ロジック検定）は描画対象が存在しない＝縮退する。版面を「解決できない」
    //   環境と、版面が「無い」ページ（＝契約違反）とは厳密に区別する。
    if (!doc || typeof doc.createElement !== 'function') {
      return null;
    }
    if (!anchor || typeof anchor.appendChild !== 'function') {
      throw new Error(
        `sheet_host: アンカーが渡されていないため ${SHEET_HOST_CLASS} を配置できない`,
      );
    }
    if (hostEl && hostEl.parentNode === anchor) {
      return hostEl;   // 再入（再 mount）でホストを増やさない。
    }
    if (styleHref && !styleEl && doc.head && typeof doc.head.appendChild === 'function') {
      styleEl = doc.createElement('link');
      styleEl.rel = 'stylesheet';
      styleEl.href = styleHref;
      doc.head.appendChild(styleEl);
    }
    hostEl = doc.createElement('div');
    hostEl.className = SHEET_HOST_CLASS;
    anchor.appendChild(hostEl);
    return hostEl;
  }

  /** ホストと持ち込んだ stylesheet を取り除く（共有の器へ何も残さない）。 */
  function unmount() {
    if (hostEl && hostEl.parentNode && typeof hostEl.parentNode.removeChild === 'function') {
      hostEl.parentNode.removeChild(hostEl);
    }
    hostEl = null;
    if (styleEl && styleEl.parentNode && typeof styleEl.parentNode.removeChild === 'function') {
      styleEl.parentNode.removeChild(styleEl);
    }
    styleEl = null;
  }

  /** 現在のホスト要素（未 mount なら null）。 */
  function element() {
    return hostEl;
  }

  return { mount, unmount, element };
}
