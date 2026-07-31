// menu_document_close.js — ドロップダウンの「外側クリックで閉じる」を document へ張る共有ヘルパ。
//
// ISSUE-169: document スコープのリスナが mount 毎に線形蓄積するのを止める。
//
// 統合 UI（`unified_ui/`）のモードトグルは `#mode-ui` サブツリーを pristine innerHTML へ復元する。
// **要素スコープ**のリスナは新ノード置換で根絶されるが、**document スコープ**のリスナは残る。
// 各メニューは mount 毎に `install()` されるため、トグルのたびに document の click リスナが
// +1 蓄積していた（各リスナは「閉じる」の冪等操作なので実害は軽微だが、蓄積は無限に続く）。
//
// 対策は 2 段構え:
//   1. **自己修復**: install 時に、同一 document へ同一キーで張った前回のリスナを自分で外す。
//      呼び出し側（統合 UI の teardown）が本モジュールを知らなくても効き、蓄積を
//      「document × キーあたり 1 個」に有界化する。
//   2. **明示 teardown**: `removeDocumentCloseHandler` を提供し、dispose を書ける呼び出し側は
//      そちらで確実に外す。
//
// 単一モジュールに置くのは A方式バンドル（全モジュールをトップレベルで連結する配信形態）で
// **同名 const の二重宣言＝SyntaxError** になるため。各メニューへコピーすると
// `pair_dim_alpha_single_source.test.js` の二重宣言ガードが落ちる（実際に検出された）。

//: document ごと・キーごとに、現在張っている click ハンドラを保持する。
//  キーを持つのは、複数のメニュー（時間足／チャートテンプレート）が同じ document に
//  それぞれ 1 個ずつリスナを持てるようにするため（互いを外し合わない）。
const _HANDLERS = new WeakMap();

/**
 * 外側クリッククローズのハンドラを document へ張る（同一キーの前回ぶんは外す）。
 *
 * @param {Document} doc 対象 document（`addEventListener` を持たなければ no-op）。
 * @param {string} key メニュー種別の識別子（例 'timeframe'）。
 * @param {Function} handler 張るハンドラ。
 */
export function installDocumentCloseHandler(doc, key, handler) {
  if (!doc || typeof doc.addEventListener !== 'function' || typeof handler !== 'function') {
    return;
  }
  let byKey = _HANDLERS.get(doc);
  if (!byKey) {
    byKey = new Map();
    _HANDLERS.set(doc, byKey);
  }
  const previous = byKey.get(key);
  if (previous && typeof doc.removeEventListener === 'function') {
    doc.removeEventListener('click', previous);   // 前 mount ぶんを外す（蓄積の停止）。
  }
  doc.addEventListener('click', handler);
  byKey.set(key, handler);
}

/**
 * 張ったハンドラを外す（明示 teardown 経路）。二重呼び出しは no-op。
 *
 * @param {Document} doc 対象 document。
 * @param {string} key メニュー種別の識別子。
 * @param {Function} handler 外すハンドラ。
 */
export function removeDocumentCloseHandler(doc, key, handler) {
  if (!doc || typeof doc.removeEventListener !== 'function' || !handler) {
    return;
  }
  doc.removeEventListener('click', handler);
  const byKey = _HANDLERS.get(doc);
  if (byKey && byKey.get(key) === handler) {
    byKey.delete(key);
  }
}
