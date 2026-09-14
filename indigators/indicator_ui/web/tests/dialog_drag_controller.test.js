// dialog_drag_controller.test.js — ダイアログのドラッグ移動の単体検定（ISSUE-502 段階 4D）。
//
// 是正前は PropertiesDialog が _drag / _offset と 3 つのハンドラを直接持っていたため、
//   「離したら必ず解除される（document へリスナを残さない）」を単体で確かめられなかった。
//   ダイアログは都度生成・破棄されるので、解除漏れはそのまま常駐リスナの蓄積になる。
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { DialogDragController } from '../js/adapter/front/dialog_drag_controller.js';

// document の最小スタブ（登録中のリスナを型ごとに数える）。
function fakeDoc() {
  const listeners = new Map();
  return {
    listeners,
    count(type) { return (listeners.get(type) ?? []).length; },
    fire(type, ev) { for (const fn of [...(listeners.get(type) ?? [])]) fn(ev); },
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(fn);
    },
    removeEventListener(type, fn) {
      const arr = listeners.get(type) ?? [];
      const i = arr.indexOf(fn);
      if (i >= 0) arr.splice(i, 1);
    },
  };
}

const fakePanel = () => ({ style: {} });
const at = (x, y) => ({ clientX: x, clientY: y });

test('掴む対象がまだ無いときは何も登録しない（open 前の pointerdown で壊れない）', () => {
  const doc = fakeDoc();
  const drag = new DialogDragController({ document: doc, panel: () => null });
  drag.start(at(10, 10));
  assert.equal(doc.count('pointermove'), 0);
  assert.equal(doc.count('pointerup'), 0);
});

test('掴んだ点からの移動量がパネルの transform へ反映される', () => {
  const doc = fakeDoc();
  const panel = fakePanel();
  const drag = new DialogDragController({ document: doc, panel });
  drag.start(at(100, 50));
  doc.fire('pointermove', at(130, 70));
  assert.deepEqual(drag.offset, { x: 30, y: 20 });
  assert.equal(panel.style.transform, 'translate(30px, 20px)');
});

test('離したら move/up の両方を解除する（document にリスナを残さない）', () => {
  const doc = fakeDoc();
  const drag = new DialogDragController({ document: doc, panel: fakePanel() });
  drag.start(at(0, 0));
  assert.equal(doc.count('pointermove'), 1);
  assert.equal(doc.count('pointerup'), 1);
  doc.fire('pointerup', {});
  assert.equal(doc.count('pointermove'), 0);
  assert.equal(doc.count('pointerup'), 0);
});

test('離した後の pointermove は位置を動かさない（解除後の遅延イベントで飛ばない）', () => {
  const doc = fakeDoc();
  const panel = fakePanel();
  const drag = new DialogDragController({ document: doc, panel });
  drag.start(at(0, 0));
  const [move] = doc.listeners.get('pointermove');
  doc.fire('pointerup', {});
  move(at(999, 999)); // 解除後に届いた移動イベントを直接叩く。
  assert.deepEqual(drag.offset, { x: 0, y: 0 });
});

test('2 回目のドラッグは前回位置を基点に積み上がる（毎回原点へ戻らない）', () => {
  const doc = fakeDoc();
  const panel = fakePanel();
  const drag = new DialogDragController({ document: doc, panel });
  drag.start(at(0, 0));
  doc.fire('pointermove', at(10, 5));
  doc.fire('pointerup', {});
  drag.start(at(200, 200));
  doc.fire('pointermove', at(205, 195));
  assert.deepEqual(drag.offset, { x: 15, y: 0 });
  assert.equal(panel.style.transform, 'translate(15px, 0px)');
});

test('パネルは遅延解決できる（open で生成される要素を後から掴める）', () => {
  const doc = fakeDoc();
  let panel = null;
  const drag = new DialogDragController({ document: doc, panel: () => panel });
  drag.start(at(0, 0));
  assert.equal(doc.count('pointermove'), 0, 'パネル未生成では掴めない');
  panel = fakePanel();
  drag.start(at(0, 0));
  doc.fire('pointermove', at(7, 3));
  assert.equal(panel.style.transform, 'translate(7px, 3px)');
});
