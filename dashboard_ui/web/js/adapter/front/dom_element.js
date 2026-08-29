// dom_element（adapter/front/dom_element.js）— 要素を 1 つ作るだけの葉ユーティリティ。
//
// これは**中央 factory ではない**（overlay_host.js の禁止対象と混同しないこと）。禁止されるのは
//   「表示系統ごとの要素名を列挙し、追加のたびに改変が要る」中央の生成器である。本関数は
//   どの要素を作るかを一切知らず、呼び出し側（各 View）が自分の DOM を所有する構造は変わらない。
//
// なぜ切り出すか: 第 1 表と第 2 表で同じ 6 行を手書き複製していた。複製は必ず取り残しを生む
//   （MEMORY: no-hand-duplication-single-source）。

/**
 * 要素を 1 つ作る。
 *
 * @param {object} doc          DOM 実装（注入）
 * @param {string} tag          タグ名
 * @param {object} [props]      設定する属性。`dataset` は個別に merge する
 * @returns {object} 生成した要素
 */
export function createElementWith(doc, tag, props = {}) {
  const node = doc.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === 'dataset') Object.assign(node.dataset, value);
    else node[key] = value;
  }
  return node;
}
