// menu_document_close.js — ドロップダウンの「外側クリックで閉じる」を担う共有レジストリ。
//
// ISSUE-169: document スコープのリスナが mount 毎に線形蓄積するのを止める。
//
// 統合 UI（`unified_ui/`）のモードトグルは `#mode-ui` サブツリーを pristine innerHTML へ復元する。
// **要素スコープ**のリスナは新ノード置換で根絶されるが、**document スコープ**のリスナは残る。
// 各メニューは mount 毎に `install()` されるため、トグルのたびに document の click リスナが
// +1 蓄積していた。本モジュールは document あたり **click リスナを常に 1 個**だけ張り、
// メニューは「閉じ方（root と close）」をキーで登録する。同一キーの再登録は前回を置換するので、
// 蓄積は構造的に起こらない（`removeMenuCloseHandler` は明示 teardown 経路）。
//
// ISSUE-366（複数メニューの排他）: 旧実装はメニューごとに「自分を閉じる」リスナを document へ
//   張り、トリガーは `stopPropagation()` でそれを回避していた。この stopPropagation は
//   **他メニューの close リスナも同時に止める**ため、テンプレートを開いたままテーマを開く、
//   の順に押すとテンプレートが開きっぱなしになった（逆順も同じ）。原因は「閉じる条件」が
//   `イベントが document へ届いたか` という**伝播の有無**に置かれていたことにある。
//   本モジュールは条件を `クリック位置が自分の root の中か` という**位置**へ置き換える。
//   位置は伝播を止められても変わらないので、どのメニューを押しても他は必ず閉じる。
//   その結果トリガー側の `stopPropagation()` は不要（かつ有害）になり、各メニューから撤去した。

//: document ごとの登録簿。{ listener, entries: Map<key, {root, close}> }。
//  キーを持つのは、複数のメニュー（時間足／テンプレート／テーマ／右クリック）が同じ document へ
//  それぞれ 1 個ずつ登録できるようにするため（互いを外し合わない）。
const _REGISTRY = new WeakMap();

//: 登録された root を要素へ解決する。関数を許すのは、右クリックメニューのように
//  ホスト要素を開くときまで生成しない（遅延生成する）メニューがあるため。
function _resolveRoot(root) {
  const el = typeof root === 'function' ? root() : root;
  return el ?? null;
}

//: クリック位置がそのメニューの内側か。root が無い／`contains` を持たない（最小 fake）ときは
//  「外側」とみなす＝閉じる（従来の document クリック＝必ず閉じる、と同じ結果になる）。
function _containsTarget(root, target) {
  const el = _resolveRoot(root);
  if (!el || typeof el.contains !== 'function' || !target) {
    return false;
  }
  return el.contains(target);
}

/**
 * 「外側クリックで閉じる」を登録する（同一キーの前回登録は置換する）。
 *
 * @param {Document} doc 対象 document（`addEventListener` を持たなければ no-op）。
 * @param {string} key メニュー種別の識別子（例 'timeframe'）。
 * @param {object} entry
 * @param {Element|function} entry.root トリガーとポップを含む要素（または、それを返す関数）。
 * @param {function} entry.close 閉じる操作。
 */
export function installMenuCloseHandler(doc, key, { root = null, close } = {}) {
  if (!doc || typeof doc.addEventListener !== 'function' || typeof close !== 'function') {
    return;
  }
  let reg = _REGISTRY.get(doc);
  if (!reg) {
    reg = { listener: null, entries: new Map() };
    _REGISTRY.set(doc, reg);
  }
  reg.entries.set(key, { root, close });
  if (!reg.listener) {
    reg.listener = (e) => {
      const target = e && e.target;
      // 反復中に close 側が登録簿を触っても壊れないよう、スナップショットを回す。
      for (const entry of [...reg.entries.values()]) {
        if (!_containsTarget(entry.root, target)) {
          entry.close();
        }
      }
    };
    doc.addEventListener('click', reg.listener);
  }
}

/**
 * 登録を外す（明示 teardown 経路）。最後の 1 件を外したら document リスナも外す。
 * 二重呼び出し・未登録キーは no-op。
 *
 * @param {Document} doc 対象 document。
 * @param {string} key メニュー種別の識別子。
 */
export function removeMenuCloseHandler(doc, key) {
  if (!doc) {
    return;
  }
  const reg = _REGISTRY.get(doc);
  if (!reg) {
    return;
  }
  reg.entries.delete(key);
  if (reg.entries.size === 0 && reg.listener) {
    if (typeof doc.removeEventListener === 'function') {
      doc.removeEventListener('click', reg.listener);
    }
    reg.listener = null;
  }
}
