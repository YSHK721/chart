// 右クリックメニューの項目一覧が**開くたびに読み直される**ことの検証（ISSUE-435 実装 1）。
//
// 設計入力（唯一の仕様源）: 依頼者指示（2026-08-21）「解除項目は**その水準が設定済みのときだけ**
//   出す（未設定なら項目自体を出さない）」。設定・解除はメニューを開いていない間に起きるので、
//   一覧は install 時点で確定できない。
//
// 実測（本検定を書く動機）: `chart_context_menu.js:122` は `for (const item of this._items)` で
//   **構築時に受け取った配列参照**を開くたびに読む。一方 `installSharedUi` は
//   `[copyBarInfo, ...contextMenuItems]` と新しい配列へ写していたため、注入側で項目が増減しても
//   install 時点の内容が焼き付いていた（本検定の Red がそれ）。`ChartContextMenu` は 1 バイトも
//   変えない制約（設計「ピッカー経路の実測検証」3）があるため、**読み直しの契機（反復）を保つ**
//   一覧を渡すことで解く。
//
// 観点: 「開くたびに」なので、同じメニューを 2 回開いて**間に起きた変化**が反映されるかを見る。
// 構造: Arrange-Act-Assert。最小 DOM は support/position_sizing_boot.js の El を使う（複製しない）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { installSharedUi, createPositionSizingContextItems } from '../js/adapter/front/chart_app_wiring.js';
import { liveMenuItems } from '../js/adapter/front/position_sizing_context_items.js';
import { El } from './support/position_sizing_boot.js';

function rig(contextMenuItems) {
  const wrap = new El();
  const app = new El();
  const doc = {
    body: new El(),
    createElement: () => new El(),
    querySelector: (sel) => (sel === '.chart-wrap' ? wrap : (sel === '#app' ? app : null)),
    getElementById: () => new El(),
    addEventListener: () => {},
    removeEventListener: () => {},
  };
  const container = new El();
  installSharedUi({
    container,
    renderer: {
      panPriceByPixels() {}, handlePriceWheel: () => false, isOverPriceAxis: () => false,
      resetPriceZoom() {}, setPaneHeight() {}, isLatestBarVisible: () => true,
      scrollToLatest() {}, barInfoAt: () => null,
    },
    doc,
    getController: () => null,
    updatePaneHeight: () => {},
    contextMenuItems,
  });
  // 右クリックして出た項目のラベル一覧（版面ホスト配下のボタン）。
  //   `open.select(label)` で同じ項目を押せる（一覧は開くたびに作り直される）。
  const open = () => {
    container.fire('contextmenu', { clientX: 120, clientY: 200, preventDefault() {} });
    return wrap.children[wrap.children.length - 1];
  };
  const labelsOf = () => open().children.map((b) => b.textContent);
  labelsOf.select = (label) => {
    const host = open();
    host.children[host.children.map((b) => b.textContent).indexOf(label)].fire('click');
  };
  return labelsOf;
}

test('TC-LV01 生きた一覧なら、開くたびに現在の項目が出る（install 時点の内容が焼き付かない）', () => {
  // Arrange: 「いま解除できる水準」に相当する可変状態を 1 つ持つ生きた一覧。
  let setPrices = [];
  const open = rig(liveMenuItems(() => [
    { label: 'この価格を損切りに設定', onSelect: () => {} },
    ...setPrices.map((name) => ({ label: `${name}を解除`, onSelect: () => {} })),
  ]));
  // Act / Assert 1: 未設定なら解除項目は出ない。
  assert.deepEqual(open(), ['情報をコピーする', 'この価格を損切りに設定']);
  // Act / Assert 2: メニューを閉じている間に設定された水準が、次に開いたとき出る。
  setPrices = ['損切り'];
  assert.deepEqual(open(), ['情報をコピーする', 'この価格を損切りに設定', '損切りを解除']);
  // Act / Assert 3: 解除されたら次に開いたとき消える。
  setPrices = [];
  assert.deepEqual(open(), ['情報をコピーする', 'この価格を損切りに設定']);
});

test('TC-LV02 静的な配列を渡す従来の呼び出しは 1 バイトも変わらない（後方互換）', () => {
  // Arrange
  const open = rig([{ label: '静的項目', onSelect: () => {} }]);
  // Act / Assert
  assert.deepEqual(open(), ['情報をコピーする', '静的項目']);
  assert.deepEqual(open(), ['情報をコピーする', '静的項目']);
});

test('TC-LV03 生きた一覧は配列として振る舞う（既存の呼び出し側が添字で触っても壊れない）', () => {
  // Arrange: 既存検定・boot 補助は `items[0].onSelect(...)` の形で項目を掴む。
  const items = liveMenuItems(() => [{ label: 'a', onSelect: () => 1 }, { label: 'b', onSelect: () => 2 }]);
  // Act / Assert
  assert.equal(Array.isArray(items), true, '配列でない（ChartContextMenu が items を捨てる）');
  assert.equal(items[0].label, 'a');
  assert.equal(items.map((i) => i.label).join(','), 'a,b');
});

test('TC-LV04 実物の項目生成 × 実物のメニューで、設定済みの水準だけが解除項目として出る', () => {
  // 合成点の検定（Pre-mortem: 各リンクが緑でも**繋ぎ目**が抜けると実 UI で出ない）。
  //   root がやることと同じ形にする: `createPositionSizingContextItems(...)` を**1 回**呼び、
  //   その戻り値を `installSharedUi({ contextMenuItems })` へ渡し、以後は水準だけが変わる。
  // Arrange: 協働子は最小 fake（水準の保持者は usecase だが、ここで見たいのは配線）。
  const cleared = [];
  let levels = { entryPrices: [null], stopPrice: null, takePrice: null };
  const positionSizing = {
    levels: () => levels,
    symbolSpec: () => ({ tick: 1, digits: 0 }),
    clearPrice: (t) => cleared.push(t),
    setStopPrice() {}, addEntryPrice() {}, setTakePrice() {},
  };
  const open = rig(createPositionSizingContextItems({
    renderer: { priceAtCoordinate: () => 58650, paneIndexAtCoordinate: () => 0, snapCandidatesAt: () => [] },
    getPositionSizing: () => positionSizing,
  }));
  // Act / Assert 1: 未設定なら既存 3 項目だけ。
  assert.deepEqual(open(), [
    '情報をコピーする', 'この価格を損切りに設定', 'この価格を建値に追加', 'この価格を利確に設定',
  ]);
  // Act / Assert 2: 損切りが入ったら、次に開いたとき解除項目が増える。
  levels = { entryPrices: [null], stopPrice: 58340, takePrice: null };
  assert.deepEqual(open().slice(4), ['損切りを解除'], '実 UI の経路で解除項目が出ない');
  // Act / Assert 3: 押すと協働子の解除が対象名で呼ばれる（押しても何も起きない項目にしない）。
  open.select('損切りを解除');
  assert.deepEqual(cleared, ['stop'], 'メニューから解除が呼ばれていない');
});
