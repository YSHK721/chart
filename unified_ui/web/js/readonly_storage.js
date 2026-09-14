// storage ポートの読み取り専用ラッパ（葉モジュール・arch-spec §0 T-2）。
//
// なぜ在るか: ダッシュボード（第 4 モード）が読む「テンプレート束」は live スコープに保存された
//   既存の資産である。ダッシュボードは**読むだけ**でよく、書ける口を渡す理由が無い。渡すと、
//   第 4 モードの不具合が live のテンプレートを壊す経路になり、`mode_storage.js` が立てた
//   「モード間で相互不可視」という契約が片側から破れる。
//
// どのスコープを読むかを決めるのは Composition Root（`unified_root.js`）であり、View ではない
//   （View が自分でスコープを選ぶと、束の出所がページごとに散る）。本モジュールは
//   `scopedStorage(...)` が返す storage ポートを、そのまま読み取り専用へ落とすだけの器である。
//
// 書き込みは**その場で落とす**。黙って捨てると「保存したつもり」の状態が残り、原因の分からない
//   欠落として現れる（無音の失敗を作らない）。

/** 書き込み操作を拒む理由（メッセージは呼び出し元の特定に足る形にする）。 */
function refuse(op) {
  throw new TypeError(
    `readOnlyStorage: ${op} は許可されていない（読み取り専用の注入。書き込みは所有者側で行う）`,
  );
}

/**
 * storage ポート（getItem/setItem/removeItem 互換）を読み取り専用にする。
 *
 * @param {{getItem:Function, key?:Function, length?:number}} base 下層 storage ポート
 * @returns {{getItem:Function, key:Function, length:number, setItem:Function, removeItem:Function, clear:Function}}
 */
export function readOnlyStorage(base) {
  return {
    getItem: (key) => base.getItem(key),
    key: (index) => (typeof base.key === 'function' ? base.key(index) : null),
    get length() {
      return base.length;
    },
    setItem: () => refuse('setItem'),
    removeItem: () => refuse('removeItem'),
    clear: () => refuse('clear'),
  };
}
