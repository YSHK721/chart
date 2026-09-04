// 指標ペインの上下並べ替え（ドラッグ&ドロップ・ユーザー指示 2026-08-09）の仕様検証。
//
// 設計入力:
//   - 操作 → 「どのペインを何番へ」の翻訳は PaneReorderDrag が持つ（upstream に触れない）。
//   - 実際の並べ替えは ChartRenderer.movePane（upstream 隔離点）が行う。
//   - 価格ペインは移動元にも移動先にもしない（overlay 指標は chart.addSeries＝既定 pane 0 へ
//     追加されるため、価格ペインが 0 番から動くと以後の overlay が別ペインへ落ちる）。
//   - 凡例のチップが掴み手（畳んでいても在る唯一の要素）。動かした直後の click は開閉にしない。
//   - ドラッグ中は凡例を再構築しない（掴んでいる要素が作り直されると掴みが外れる）。
//
// 構造: Arrange-Act-Assert（AAA）。実 DOM・lightweight-charts 非依存（Fake 注入）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { PaneReorderDrag } from '../js/adapter/front/pane_reorder_drag.js';
import { PaneLegendView } from '../js/adapter/front/pane_legend_view.js';
import { ChartRenderer } from '../js/adapter/front/chart_renderer.js';

// ---- Fake DOM ---- //

function fakeElement(tagName = 'div', className = '') {
  return {
    tagName,
    className,
    textContent: '',
    title: '',
    type: '',
    style: {},
    dataset: {},
    children: [],
    listeners: {},
    _rectTop: 0,
    // ポインタ捕捉（掴んだ要素へ以後のポインタ事象を配送する DOM の仕組み）。
    //   捕捉した／解いた pointerId を記録するだけの Fake。
    captured: [],
    released: [],
    setPointerCapture(id) { this.captured.push(id); },
    // 実 DOM の契約: 捕捉を解くと lostpointercapture が **必ず** 配送される。これを写さないと
    //   「解除が誘発する再入」がテストで一度も起きず、再入対策（状態を畳んでから解く／購読を
    //   先に外す）を壊しても赤くならない（🟡-4-3）。
    releasePointerCapture(id) {
      this.released.push(id);
      this.fire('lostpointercapture', { pointerId: id });
    },
    get innerHTML() { return this._innerHTML ?? ''; },
    set innerHTML(v) { this._innerHTML = v; if (v === '') { this.children = []; } },
    append(...nodes) { for (const n of nodes) { this.children.push(n); } },
    appendChild(n) { this.children.push(n); return n; },
    removeChild(n) { this.children = this.children.filter((c) => c !== n); return n; },
    getBoundingClientRect() { return { top: this._rectTop, height: 0 }; },
    addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); },
    removeEventListener(type, fn) {
      this.listeners[type] = (this.listeners[type] ?? []).filter((f) => f !== fn);
    },
    fire(type, ev) { for (const fn of [...(this.listeners[type] ?? [])]) { fn(ev); } },
    querySelector(sel) {
      const want = sel.startsWith('.') ? sel.slice(1) : sel;
      return this.children.find((c) => c.className === want) ?? null;
    },
  };
}

// document 実装。掴んでいる間の pointermove/up は document で拾う契約を写す。
function fakeDoc(anchor = null) {
  const listeners = {};
  return {
    listeners,
    createElement(tag) { return fakeElement(tag); },
    getElementById() { return null; },
    querySelector(sel) { return (anchor && sel === '.chart-wrap') ? anchor : null; },
    addEventListener(type, fn) { (listeners[type] ??= []).push(fn); },
    removeEventListener(type, fn) {
      listeners[type] = (listeners[type] ?? []).filter((f) => f !== fn);
    },
    fire(type, ev) { for (const fn of [...(listeners[type] ?? [])]) { fn(ev); } },
  };
}

// ペイン 3 枚（価格 0 / 指標 1 / 指標 2）。top/height は凡例 DTO と同じ座標系（器の上端起点）。
function fakeGroups() {
  const mk = (paneIndex, movable, top, height) => ({
    paneIndex, movable, top, height, box: fakeElement('div', 'pane-legend'), handle: fakeElement('button'),
  });
  return [mk(0, false, 0, 400), mk(1, true, 402, 160), mk(2, true, 564, 160)];
}

function setup() {
  const doc = fakeDoc();
  const root = fakeElement('div', 'pane-legends');
  const moves = [];
  const drag = new PaneReorderDrag({ document: doc, movePane: (from, to) => { moves.push([from, to]); return true; } });
  const groups = fakeGroups();
  drag.sync(root, groups);
  return { doc, root, drag, groups, moves };
}

// ---- PaneReorderDrag: 操作 → 並べ替え指示 ---- //

test('しきい値未満の動きは並べ替えにしない（チップのクリック＝開閉として通す）', () => {
  const { doc, drag, groups, moves } = setup();

  groups[1].handle.fire('pointerdown', { button: 0, clientY: 500 });
  doc.fire('pointermove', { clientY: 502 });   // 2px＝しきい値 4px 未満
  doc.fire('pointerup', { clientY: 502 });

  assert.deepEqual(moves, []);
  assert.equal(drag.consumeClickSuppression(groups[1].handle), false, 'クリックを握りつぶしてはいけない');
});

test('掴んで別ペインの上で離すと、そのペインの番号へ移動を指示する', () => {
  const { doc, groups, moves } = setup();

  groups[1].handle.fire('pointerdown', { button: 0, clientY: 450 });   // pane1 を掴む
  doc.fire('pointermove', { clientY: 600 });                           // pane2 の領域（564..724）へ
  doc.fire('pointerup', { clientY: 600 });

  assert.deepEqual(moves, [[1, 2]]);
});

test('上へ動かす向きも同じ規約（乗っているペインの番号へ入る）', () => {
  const { doc, groups, moves } = setup();

  groups[2].handle.fire('pointerdown', { button: 0, clientY: 600 });
  doc.fire('pointermove', { clientY: 450 });   // pane1 の領域（402..562）
  doc.fire('pointerup', { clientY: 450 });

  assert.deepEqual(moves, [[2, 1]]);
});

test('価格ペインは掴めない（掴み手に操作を結ばない）', () => {
  const { doc, groups, moves } = setup();

  groups[0].handle.fire('pointerdown', { button: 0, clientY: 100 });
  doc.fire('pointermove', { clientY: 600 });
  doc.fire('pointerup', { clientY: 600 });

  assert.deepEqual(moves, [], '価格ペインが動いてはいけない');
});

test('価格ペインの領域へ落としても、最寄りの指標ペインへ丸める（価格ペインを移動先にしない）', () => {
  const { doc, groups, moves } = setup();

  groups[2].handle.fire('pointerdown', { button: 0, clientY: 600 });
  doc.fire('pointermove', { clientY: 50 });    // 価格ペイン（0..400）の上
  doc.fire('pointerup', { clientY: 50 });

  assert.deepEqual(moves, [[2, 1]], '最上段の指標ペインへ丸まらなかった');
});

test('同じペインの上で離したときは並べ替えを指示しない', () => {
  const { doc, groups, moves } = setup();

  groups[1].handle.fire('pointerdown', { button: 0, clientY: 450 });
  doc.fire('pointermove', { clientY: 500 });   // まだ pane1 の領域（402..562）
  doc.fire('pointerup', { clientY: 500 });

  assert.deepEqual(moves, []);
});

test('掴んでいる間だけ isDragging＝true（凡例の再構築を止める合図）', () => {
  const { doc, drag, groups } = setup();

  assert.equal(drag.isDragging(), false);
  groups[1].handle.fire('pointerdown', { button: 0, clientY: 450 });
  assert.equal(drag.isDragging(), true);
  doc.fire('pointermove', { clientY: 600 });
  assert.equal(drag.isDragging(), true);
  doc.fire('pointerup', { clientY: 600 });
  assert.equal(drag.isDragging(), false, '離した後も止めたままだと凡例が更新されない');
});

test('ドラッグ直後の click は 1 回だけ握りつぶす（掴み手＝開閉チップのため）', () => {
  const { doc, drag, groups } = setup();

  groups[1].handle.fire('pointerdown', { button: 0, clientY: 450 });
  doc.fire('pointermove', { clientY: 600 });
  doc.fire('pointerup', { clientY: 600 });

  assert.equal(drag.consumeClickSuppression(groups[1].handle), true);
  assert.equal(drag.consumeClickSuppression(groups[1].handle), false, '2 回目以降まで握りつぶすと開閉できなくなる');
});

// 握りつぶしの対象は「掴んだ当の要素」であって、真偽値のグローバル状態ではない（🔴-1）。
//   真偽値だと、click が飛ばずに残った印が **次に押した無関係な要素**の click を食う。
//   掴み手の同一性で持てば、そもそも別要素と一致しない＝食えない（構造的に起こり得ない）。
test('握りつぶしは掴んだ当の要素にだけ効く（別の掴み手の click は素通しする）', () => {
  const { doc, drag, groups } = setup();

  groups[1].handle.fire('pointerdown', { button: 0, clientY: 450 });
  doc.fire('pointermove', { clientY: 600 });
  doc.fire('pointerup', { clientY: 600 });

  assert.equal(drag.consumeClickSuppression(groups[2].handle), false,
    '掴んでいない要素の click まで握りつぶした（真偽値のグローバル状態が漏れている）');
});

test('離したら見た目を必ず戻す（追従の transform・掴み中クラス・予告線を残さない）', () => {
  const { doc, root, groups } = setup();

  groups[1].handle.fire('pointerdown', { button: 0, clientY: 450 });
  doc.fire('pointermove', { clientY: 600 });
  assert.equal(groups[1].box.className.includes('is-dragging'), true);
  assert.equal(root.children.some((c) => c.className === 'pane-drop-indicator'), true, '予告線が出ていない');

  doc.fire('pointerup', { clientY: 600 });

  assert.equal(groups[1].box.style.transform, '');
  assert.equal(groups[1].box.className.includes('is-dragging'), false);
  assert.equal(root.children.some((c) => c.className === 'pane-drop-indicator'), false, '予告線が残っている');
});

// ---- 掴みの終了保証（フェイルオープンの遮断） ---- //
//
// ドラッグ中は PaneLegendView が凡例の再描画を丸ごと止める（isDragging）。止めた再描画を
//   再開する条件が「pointerup が届くこと」だけだと、届かない経路が 1 つでもあれば凡例が
//   恒久的に固まる（値更新も指標の追加削除の反映も全停止）。停止は **終了が保証された寿命**
//   ＝掴み手へのポインタ捕捉に紐づける。捕捉が失われれば lostpointercapture が必ず届く。

test('掴んだら掴み手へポインタを捕捉し、離したら必ず解く', () => {
  const { doc, groups } = setup();

  groups[1].handle.fire('pointerdown', { button: 0, clientY: 450, pointerId: 7 });
  assert.deepEqual(groups[1].handle.captured, [7], '捕捉していない＝終了イベントが要素へ届く保証が無い');

  doc.fire('pointerup', { clientY: 450, pointerId: 7 });

  assert.deepEqual(groups[1].handle.released, [7], '捕捉を解いていない＝次の操作へ持ち越す');
});

test('掴みを終えたら掴み手側の lostpointercapture 購読も残さない（次の掴みへ持ち越さない）', () => {
  const { doc, groups } = setup();

  groups[1].handle.fire('pointerdown', { button: 0, clientY: 450, pointerId: 7 });
  assert.equal((groups[1].handle.listeners.lostpointercapture ?? []).length, 1, '前提: 捕捉喪失を購読しているはず');

  doc.fire('pointerup', { clientY: 450, pointerId: 7 });

  assert.equal((groups[1].handle.listeners.lostpointercapture ?? []).length, 0,
    '購読が残ると、解除が誘発する捕捉喪失や次の掴みの事象を二重に拾う');
});

test('pointerup が届かなくても掴みは必ず終わる（捕捉喪失＝lostpointercapture）', () => {
  const { doc, drag, groups } = setup();

  groups[1].handle.fire('pointerdown', { button: 0, clientY: 450, pointerId: 7 });
  doc.fire('pointermove', { clientY: 600 });
  assert.equal(drag.isDragging(), true, '前提: 掴んでいるはず');

  // ブラウザ側の事情で捕捉が失われる（要素の消失・別の捕捉の開始・端末の割り込み）。
  //   この経路では pointerup / pointercancel は配送されない。
  groups[1].handle.fire('lostpointercapture', { pointerId: 7 });

  assert.equal(drag.isDragging(), false, '掴みが残る＝凡例の再描画が恒久的に止まる（フェイルオープン）');
  assert.equal(doc.listeners.pointermove.length, 0, 'document の購読が残っている');
  assert.equal(groups[1].box.style.transform, '', '掴み中の見た目が残っている');
});

test('捕捉の喪失を 1 回の掴みで二重に処理しない（再入で並べ替えを 2 度指示しない）', () => {
  const { doc, groups, moves } = setup();

  groups[1].handle.fire('pointerdown', { button: 0, clientY: 450, pointerId: 7 });
  doc.fire('pointermove', { clientY: 600 });
  doc.fire('pointerup', { clientY: 600, pointerId: 7 });
  // 捕捉解除に伴って遅れて届く lostpointercapture（実 DOM では releasePointerCapture が誘発する）。
  groups[1].handle.fire('lostpointercapture', { pointerId: 7 });

  assert.deepEqual(moves, [[1, 2]], '同じ掴みで並べ替えが 2 度指示された');
});

test('ポインタ捕捉を提供しない環境でも掴める（no-op へ縮退する）', () => {
  const { doc, groups, moves } = setup();
  delete groups[1].handle.setPointerCapture;
  delete groups[1].handle.releasePointerCapture;

  groups[1].handle.fire('pointerdown', { button: 0, clientY: 450, pointerId: 7 });
  doc.fire('pointermove', { clientY: 600 });
  doc.fire('pointerup', { clientY: 600, pointerId: 7 });

  assert.deepEqual(moves, [[1, 2]], '捕捉が無い環境で並べ替えが動かなくなった');
});

// ---- ポインタの同一性（🟡-2）: 掴んだ指の事象だけを見る ---- //
//
// document 購読は「どのポインタの事象か」を選ばずに全部届く。掴んでいる最中に別の指が触れて
//   離すと、その pointerup が掴みの終了として扱われ、**意図しない位置で並べ替えが確定する**。
//   掴んだ pointerId を覚え、一致しない事象は無視する。

test('別のポインタの移動では掴んだ箱を動かさない（pointerId 照合）', () => {
  const { doc, groups } = setup();

  groups[1].handle.fire('pointerdown', { button: 0, clientY: 450, pointerId: 7 });
  doc.fire('pointermove', { clientY: 600, pointerId: 9 });   // 別の指

  assert.equal(groups[1].box.className.includes('is-dragging'), false,
    '別ポインタの移動で掴みが動いた');
  assert.equal(groups[1].box.style.transform ?? '', '', '別ポインタの移動で追従してしまった');
});

test('別のポインタを離しても掴みは終わらない（意図しない位置で確定しない）', () => {
  const { doc, drag, groups, moves } = setup();

  groups[1].handle.fire('pointerdown', { button: 0, clientY: 450, pointerId: 7 });
  doc.fire('pointermove', { clientY: 600, pointerId: 7 });
  doc.fire('pointerup', { clientY: 600, pointerId: 9 });     // 別の指を離した

  assert.equal(drag.isDragging(), true, '別ポインタの離しで掴みが終わった');
  assert.deepEqual(moves, [], '別ポインタの離しで並べ替えが確定した');
});

test('別のポインタの捕捉喪失では掴みを終えない（lostpointercapture も照合する）', () => {
  const { doc, drag, groups } = setup();

  groups[1].handle.fire('pointerdown', { button: 0, clientY: 450, pointerId: 7 });
  doc.fire('pointermove', { clientY: 600, pointerId: 7 });
  groups[1].handle.fire('lostpointercapture', { pointerId: 9 });

  assert.equal(drag.isDragging(), true, '別ポインタの捕捉喪失で掴みが終わった');
});

test('掴んだポインタを離せば従来どおり確定する（照合は正規の経路を塞がない）', () => {
  const { doc, groups, moves } = setup();

  groups[1].handle.fire('pointerdown', { button: 0, clientY: 450, pointerId: 7 });
  doc.fire('pointermove', { clientY: 600, pointerId: 7 });
  doc.fire('pointerup', { clientY: 600, pointerId: 7 });

  assert.deepEqual(moves, [[1, 2]]);
});

test('掴んでいる間は document の pointermove/up だけを購読する（離したら解除）', () => {
  const { doc, groups } = setup();

  groups[1].handle.fire('pointerdown', { button: 0, clientY: 450 });
  assert.equal(doc.listeners.pointermove.length, 1);
  doc.fire('pointerup', { clientY: 450 });
  assert.equal(doc.listeners.pointermove.length, 0, '購読が残ると次の描画分と多重に発火する');
});

// ---- PaneLegendView との協働 ---- //

const ROWS = [
  { instanceId: 'ma#1', label: 'MA', visible: true },
  { instanceId: 'osc#1', label: 'OSC', visible: true },
  { instanceId: 'osc#2', label: 'OSC2', visible: true },
];
const MODEL = {
  groups: [
    { paneIndex: 0, top: 0, height: 400, movable: false, rows: [{ instanceId: 'ma#1', values: [] }] },
    { paneIndex: 1, top: 402, height: 160, movable: true, rows: [{ instanceId: 'osc#1', values: [] }] },
    { paneIndex: 2, top: 564, height: 160, movable: true, rows: [{ instanceId: 'osc#2', values: [] }] },
  ],
};

test('凡例は描画のたびに、掴み手（チップ）と幾何を並べ替え協働子へ渡す', () => {
  const anchor = fakeElement('div', 'chart-wrap');
  const synced = [];
  const reorder = { isDragging: () => false, sync: (root, groups) => synced.push(groups), consumeClickSuppression: () => false };
  const view = new PaneLegendView({ document: fakeDoc(anchor), reorder });

  view.setInstances(ROWS);
  view.update(MODEL);

  const last = synced[synced.length - 1];
  assert.deepEqual(last.map((g) => [g.paneIndex, g.movable]), [[0, false], [1, true], [2, true]]);
  assert.deepEqual(last.map((g) => g.top), [0, 402, 564]);
  assert.ok(last[1].handle && last[1].handle.className.includes('pane-legend-chip'), '掴み手がチップでない');
  assert.ok(last[1].box && last[1].box.className === 'pane-legend');
});

test('動かせるペインのチップにだけ掴める印（is-movable）を付ける', () => {
  const anchor = fakeElement('div', 'chart-wrap');
  const view = new PaneLegendView({
    document: fakeDoc(anchor),
    reorder: { isDragging: () => false, sync: () => {}, consumeClickSuppression: () => false },
  });

  view.setInstances(ROWS);
  view.update(MODEL);

  const host = anchor.querySelector('.pane-legends');
  const chipOf = (paneIndex) => host.children
    .find((c) => c.dataset.paneIndex === String(paneIndex))
    .children.find((c) => c.className.includes('pane-legend-chip'));
  assert.equal(chipOf(0).className.includes('is-movable'), false, '価格ペインを掴めるように見せてはいけない');
  assert.equal(chipOf(1).className.includes('is-movable'), true);
});

test('ドラッグ中は凡例を作り直さない（掴んでいる要素を消さない）', () => {
  const anchor = fakeElement('div', 'chart-wrap');
  let dragging = false;
  const view = new PaneLegendView({
    document: fakeDoc(anchor),
    reorder: { isDragging: () => dragging, sync: () => {}, consumeClickSuppression: () => false },
  });
  view.setInstances(ROWS);
  view.update(MODEL);
  const host = anchor.querySelector('.pane-legends');
  const before = host.children[0];

  dragging = true;
  view.update(MODEL);   // クロスヘア移動に相当

  assert.equal(host.children[0], before, 'ドラッグ中に凡例が作り直された');
});

test('並べ替え協働子が未注入でも従来どおり描画・開閉できる（既定は no-op の Null Object）', () => {
  // 契約（isDragging / sync / consumeClickSuppression）を分岐で確かめるのをやめた代わりに、
  //   「未注入＝並べ替え無し」が完全な実装として成立していることをここで固定する。
  const anchor = fakeElement('div', 'chart-wrap');
  const view = new PaneLegendView({ document: fakeDoc(anchor) });

  view.setInstances(ROWS);
  view.update(MODEL);
  const host = anchor.querySelector('.pane-legends');
  const chip = host.children[1].children.find((c) => c.className.includes('pane-legend-chip'));
  chip.fire('click', {});

  assert.equal(host.children.length, 3, '未注入で凡例が描かれない');
  assert.equal(
    anchor.querySelector('.pane-legends').children[1].children
      .some((c) => c.className === 'pane-legend-rows'),
    false, '未注入なのに click の開閉が握りつぶされた',
  );
});

test('ドラッグ直後の click ではチップを畳まない', () => {
  const anchor = fakeElement('div', 'chart-wrap');
  let suppress = true;
  const view = new PaneLegendView({
    document: fakeDoc(anchor),
    reorder: { isDragging: () => false, sync: () => {}, consumeClickSuppression: () => { const s = suppress; suppress = false; return s; } },
  });
  view.setInstances(ROWS);
  view.update(MODEL);
  const host = anchor.querySelector('.pane-legends');
  const chip = host.children[1].children.find((c) => c.className.includes('pane-legend-chip'));

  chip.fire('click', {});

  const rowsBox = anchor.querySelector('.pane-legends').children[1].children
    .find((c) => c.className === 'pane-legend-rows');
  assert.ok(rowsBox, 'ドラッグ直後の click で畳まれてしまった');
});

// ---- ChartRenderer.movePane: upstream 隔離点 ---- //

function fakeChartWithPanes(count) {
  const panes = [];
  const moves = [];
  for (let i = 0; i < count; i += 1) {
    const pane = {
      _height: i === 0 ? 400 : 160,
      paneIndex() { return panes.indexOf(pane); },
      getHeight() { return this._height; },
      // 実バンドル（v5.2.0）の moveTo＝splice(from,1) → splice(to,0,pane)。
      moveTo(to) {
        const from = panes.indexOf(pane);
        moves.push([from, to]);
        panes.splice(from, 1);
        panes.splice(to, 0, pane);
      },
    };
    panes.push(pane);
  }
  return {
    moves,
    panes() { return panes; },
    subscribeCrosshairMove() {},
    applyOptions() {},
    timeScale() { return { applyOptions() {} }; },
  };
}

function rendererWith(count) {
  const chart = fakeChartWithPanes(count);
  const emitted = [];
  const renderer = new ChartRenderer({
    chart, mainSeries: {}, lwc: {}, onPaneLegend: (m) => emitted.push(m),
  });
  return { chart, renderer, emitted };
}

test('movePane は upstream の moveTo を呼び、凡例 DTO を再発行する', () => {
  const { chart, renderer, emitted } = rendererWith(3);

  const ok = renderer.movePane(1, 2);

  assert.equal(ok, true);
  assert.deepEqual(chart.moves, [[1, 2]]);
  assert.equal(emitted.length, 1, 'ペイン構成が変わったのに凡例が引き直されていない');
});

test('価格ペイン（メイン系列のペイン）は移動元にも移動先にもしない', () => {
  const { chart, renderer } = rendererWith(3);

  assert.equal(renderer.movePane(0, 2), false);
  assert.equal(renderer.movePane(2, 0), false);
  assert.deepEqual(chart.moves, []);
});

test('同一・範囲外・非整数の指定は何もしない（false）', () => {
  const { chart, renderer } = rendererWith(3);

  assert.equal(renderer.movePane(1, 1), false);
  assert.equal(renderer.movePane(1, 9), false);
  assert.equal(renderer.movePane(-1, 1), false);
  assert.equal(renderer.movePane(1.5, 2), false);
  assert.equal(renderer.movePane(null, undefined), false);
  assert.deepEqual(chart.moves, []);
});

test('指標ペインが 1 つだけなら並べ替えできない（movable=false・入れ替える相手が居ない）', () => {
  const { renderer } = rendererWith(2);

  const model = renderer.paneLegendModel(null);
  assert.deepEqual(model.groups, [], '系列未適用なので凡例グループは空');
  assert.equal(renderer.movePane(1, 1), false);
});

// 上の検証だけでは「ペイン 2 枚のときのガード」を確かめられない。movePane(1,1) は from===to の
//   短絡が先に効くため `total < 3` のガードへ到達せず、ガードを削っても通ってしまう（トートロジー）。
//   ペイン 2 枚に指標を 1 件置き、凡例 DTO が運ぶ movable を直接固定する。
test('ペインが 2 枚（価格＋指標 1）のとき、凡例 DTO の movable は false', () => {
  const { renderer } = rendererWith(2);
  const panes = renderer._chart.panes();
  renderer._instances.set('osc#1', { lines: new Map(), visible: true, pane: panes[1] });

  const model = renderer.paneLegendModel(null);

  assert.deepEqual(model.groups.map((g) => [g.paneIndex, g.movable]), [[1, false]],
    '入れ替える相手が居ないのに掴める合図を出している');
});

// ---- 価格ペインの特定（🟡-3 / 🟡-4-4）: 番号を決め打たず、不正な番号も受理しない ---- //
//
// バンドル実測（vendor/lightweight-charts.js v5.2.0）:
//   `paneIndex(){return this.sv.Qt().hf(this.yt)}` / `hf(t){return this.od.indexOf(t)}`
//   ＝ペインが内部配列に無いとき **-1** を返す。-1 を価格ペイン番号として受理すると
//   `_isPaneMovable` が全ペインで真になり、禁じているはずの価格ペイン移動が通る。

// 価格ペインが指定 index に居るメイン系列の Fake（getPane は upstream と同じく pane を返す）。
function mainSeriesOnPane(pane) {
  return { getPane() { return pane; } };
}

function rendererWithMainSeries(count, makeMainSeries) {
  const chart = fakeChartWithPanes(count);
  const renderer = new ChartRenderer({ chart, mainSeries: makeMainSeries(chart), lwc: {} });
  return { chart, renderer };
}

test('価格ペインが未登録（paneIndex()=-1）でも価格ペインは動かせない（-1 を番号にしない）', () => {
  const { chart, renderer } = rendererWithMainSeries(
    3, () => mainSeriesOnPane({ paneIndex: () => -1 }),
  );

  assert.equal(renderer.movePane(0, 2), false, '-1 が価格ペイン番号になり価格ペインが動いた');
  assert.equal(renderer.movePane(2, 0), false, '-1 が価格ペイン番号になり価格ペインが移動先になった');
  assert.deepEqual(chart.moves, [], '禁じている価格ペインの移動が upstream へ届いた');
});

test('価格ペインが 0 番以外に居ても、動かせるのは指標ペインだけ（番号を決め打たない）', () => {
  const { chart, renderer } = rendererWithMainSeries(
    3, (c) => mainSeriesOnPane(c.panes()[1]),   // 価格ペインは index 1
  );

  assert.equal(renderer.movePane(1, 2), false, '価格ペイン（1 番）を移動元にしてはいけない');
  assert.equal(renderer.movePane(0, 1), false, '価格ペイン（1 番）を移動先にしてはいけない');
  assert.equal(renderer.movePane(0, 2), true, '価格ペインでない指標ペイン（0 番）が動かせない');
  assert.deepEqual(chart.moves, [[0, 2]]);
});

test('凡例 DTO は movable を運ぶ（掴める見た目と実際の可否を割らない）', () => {
  const { renderer } = rendererWith(3);
  // 系列を持たない slot を直接置き、pane 割り当てだけを与える（描画経路は本検証の関心外）。
  const panes = renderer._chart.panes();
  renderer._instances.set('ma#1', { lines: new Map(), visible: true, pane: null });
  renderer._instances.set('osc#1', { lines: new Map(), visible: true, pane: panes[1] });
  renderer._instances.set('osc#2', { lines: new Map(), visible: true, pane: panes[2] });

  const model = renderer.paneLegendModel(null);

  assert.deepEqual(model.groups.map((g) => [g.paneIndex, g.movable]), [[0, false], [1, true], [2, true]]);
});

// ---- ペインの同一性（paneKey）: ISSUE-341 ---- //
//
// 並べ替えを入れた結果、paneIndex は「そのペインが今どこに居るか（位置）」だけを意味するように
//   なり、「どのペインか（同一性）」を表さなくなった。位置に紐づけて良い情報（top/height）と、
//   ペインに紐づけるべき情報（凡例の折りたたみ状態）を分けるため、DTO へ**位置に依らない安定 ID**
//   を載せる。paneIndex / top / height / movable は位置の情報として従来どおり残す。

// 3 ペイン（価格 0 / 指標 1 / 指標 2）へ指標を 1 件ずつ置いた renderer を作る。
function rendererWithPlacedInstances() {
  const { renderer } = rendererWith(3);
  const panes = renderer._chart.panes();
  renderer._instances.set('ma#1', { lines: new Map(), visible: true, pane: null });
  renderer._instances.set('osc#1', { lines: new Map(), visible: true, pane: panes[1] });
  renderer._instances.set('osc#2', { lines: new Map(), visible: true, pane: panes[2] });
  return renderer;
}

// その指標の行が乗っているグループを引く（位置ではなく中身で引く＝並べ替えの前後で使える）。
function groupOf(model, instanceId) {
  return model.groups.find((g) => (g.rows ?? []).some((r) => r.instanceId === instanceId));
}

test('凡例 DTO は全ペイン（価格ペインを含む）に paneKey を付ける', () => {
  const renderer = rendererWithPlacedInstances();

  const model = renderer.paneLegendModel(null);

  const keys = model.groups.map((g) => g.paneKey);
  assert.equal(keys.every((k) => typeof k === 'string' && k.length > 0), true,
    `全グループに paneKey が要る（価格ペインも採番する）: ${JSON.stringify(keys)}`);
  assert.equal(new Set(keys).size, keys.length, 'paneKey がペイン間で重複している');
});

test('paneKey は並べ替えても同じペインに付いて回る（位置ではなくペインの同一性）', () => {
  const renderer = rendererWithPlacedInstances();
  const before = renderer.paneLegendModel(null);
  const oscKey = groupOf(before, 'osc#1').paneKey;
  // 「同一性が存在すること」を先に要求する。これが無いと、未実装（両方 undefined）でも
  //   後段の等値比較が通ってしまい、実装の不在を検出できない弱い検定になる。
  assert.equal(typeof oscKey === 'string' && oscKey.length > 0, true, 'paneKey が付いていない');
  assert.equal(groupOf(before, 'osc#1').paneIndex, 1);

  renderer.movePane(1, 2);   // osc#1 のペインを 2 番へ

  const after = renderer.paneLegendModel(null);
  assert.equal(groupOf(after, 'osc#1').paneIndex, 2, '前提: 位置は変わっているはず');
  assert.equal(groupOf(after, 'osc#1').paneKey, oscKey,
    '同じペインなのに paneKey が変わった＝位置に紐づいており同一性を表せていない');
});

test('paneKey は入れ替わった相手のものを引き継がない（位置の取り違えを検出する）', () => {
  const renderer = rendererWithPlacedInstances();
  const before = renderer.paneLegendModel(null);
  const keyOsc1 = groupOf(before, 'osc#1').paneKey;
  const keyOsc2 = groupOf(before, 'osc#2').paneKey;
  assert.equal(typeof keyOsc1 === 'string' && typeof keyOsc2 === 'string', true, 'paneKey が付いていない');

  renderer.movePane(1, 2);

  const after = renderer.paneLegendModel(null);
  assert.equal(groupOf(after, 'osc#2').paneKey, keyOsc2, '動かしていないペインの ID が変わった');
  assert.notEqual(groupOf(after, 'osc#1').paneKey, keyOsc2, '入れ替わった相手の ID を拾っている');
  assert.equal(groupOf(after, 'osc#1').paneKey, keyOsc1);
});

// ---- 折りたたみ状態の帰属（ISSUE-341 の本体） ---- //
//
// 実測（実 UI 2026-08-09）: RSI（pane 1）を畳む → RSI を pane 2 へドラッグ → 畳まれた表示は
//   pane 1（別指標）に残り、移動した RSI 側が開いた状態になった。状態の鍵が「位置」だったため。
//   折りたたみは **そのペインの性質**であって位置の性質ではないので、鍵は paneKey にする。

// 並べ替え前。p1=価格 / p2=osc#1 / p3=osc#2。
const MODEL_KEYED = {
  groups: [
    { paneIndex: 0, paneKey: 'p1', top: 0, height: 400, movable: false, rows: [{ instanceId: 'ma#1', values: [] }] },
    { paneIndex: 1, paneKey: 'p2', top: 402, height: 160, movable: true, rows: [{ instanceId: 'osc#1', values: [] }] },
    { paneIndex: 2, paneKey: 'p3', top: 564, height: 160, movable: true, rows: [{ instanceId: 'osc#2', values: [] }] },
  ],
};
// osc#1 のペイン（p2）を 2 番へ動かした後。paneKey はペインについて回り、paneIndex だけが入れ替わる。
const MODEL_KEYED_SWAPPED = {
  groups: [
    { paneIndex: 0, paneKey: 'p1', top: 0, height: 400, movable: false, rows: [{ instanceId: 'ma#1', values: [] }] },
    { paneIndex: 1, paneKey: 'p3', top: 402, height: 160, movable: true, rows: [{ instanceId: 'osc#2', values: [] }] },
    { paneIndex: 2, paneKey: 'p2', top: 564, height: 160, movable: true, rows: [{ instanceId: 'osc#1', values: [] }] },
  ],
};

function groupAt(anchor, paneIndex) {
  return anchor.querySelector('.pane-legends').children.find((c) => c.dataset.paneIndex === String(paneIndex));
}
function chipAt(anchor, paneIndex) {
  return groupAt(anchor, paneIndex).children.find((c) => c.className.includes('pane-legend-chip'));
}
// 開いている＝行が描かれている（既定オープンなので、畳むと行が消える）。
function isOpenAt(anchor, paneIndex) {
  return groupAt(anchor, paneIndex).children.some((c) => c.className === 'pane-legend-rows');
}

test('畳んだ状態は動かしたペインについて回る（位置に残らない）', () => {
  const anchor = fakeElement('div', 'chart-wrap');
  const view = new PaneLegendView({ document: fakeDoc(anchor) });
  view.setInstances(ROWS);
  view.update(MODEL_KEYED);

  chipAt(anchor, 1).fire('click', {});          // osc#1 の居るペイン（p2）を畳む
  assert.equal(isOpenAt(anchor, 1), false, '前提: 畳めているはず');

  view.update(MODEL_KEYED_SWAPPED);             // p2 を 2 番へ並べ替え

  assert.equal(isOpenAt(anchor, 2), false, '畳んだペイン（p2）が移動先で開いてしまった');
  assert.equal(isOpenAt(anchor, 1), true, '動かしていないペイン（p3）が畳まれた＝状態が位置に残っている');
});

// ---- 握りつぶしの残留（🔴-1）: View と協働子を実物どうしで結んだ経路 ---- //
//
// 並べ替えが成立すると movePane → renderer が凡例 DTO を再発行 → View が root.innerHTML='' で
//   **掴み手ごと DOM から外す**。外れた要素へ click が配送されるかはブラウザ挙動に依存するため、
//   「click が必ず飛んでくる」ことに握りつぶしの解除を賭けてはいけない。掴み手の同一性で持てば、
//   再描画で掴み手は必ず新しい要素になる＝残留値がライブな要素と一致することが起こり得ない。

// PaneLegendView と PaneReorderDrag を実物どうしで結び、3 ペインぶんの凡例を描いた状態を作る。
function wiredLegend() {
  const anchor = fakeElement('div', 'chart-wrap');
  const doc = fakeDoc(anchor);
  const moves = [];
  const drag = new PaneReorderDrag({ document: doc, movePane: (from, to) => { moves.push([from, to]); return true; } });
  const view = new PaneLegendView({ document: doc, reorder: drag });
  view.setInstances(ROWS);
  view.update(MODEL_KEYED);
  return { anchor, doc, view, moves };
}

// pane1 のチップを掴んで pane2 の領域へ落とす（並べ替えが成立する操作）。
function dragPane1ToPane2(anchor, doc) {
  chipAt(anchor, 1).fire('pointerdown', { button: 0, clientY: 450, pointerId: 7 });
  doc.fire('pointermove', { clientY: 600, pointerId: 7 });
  doc.fire('pointerup', { clientY: 600, pointerId: 7 });
}

test('並べ替え成立で掴み手ごと作り直された後、非 movable チップの click が握りつぶされない', () => {
  const { anchor, doc, view, moves } = wiredLegend();

  dragPane1ToPane2(anchor, doc);
  assert.deepEqual(moves, [[1, 2]], '前提: 並べ替えが成立しているはず');
  view.update(MODEL_KEYED_SWAPPED);   // renderer の DTO 再発行＝全チップが作り直される

  chipAt(anchor, 0).fire('click', {});   // 次に押したのは掴めない価格ペインのチップ

  assert.equal(isOpenAt(anchor, 0), false,
    '掴んでもいないチップの開閉が握りつぶされた（残留した握りつぶし印が漏れている）');
});

test('掴んだ当のチップへ click が届いたときは開閉しない（掴み手＝開閉チップのため）', () => {
  const { anchor, doc } = wiredLegend();

  dragPane1ToPane2(anchor, doc);
  chipAt(anchor, 1).fire('click', {});   // 掴み手そのもの（再描画前＝同じ要素）へ届いた click

  assert.equal(isOpenAt(anchor, 1), true, 'ドラッグ直後の click で畳まれてしまった');
});

test('移動先で畳めば、その場でちゃんと畳める（鍵が変わっても操作は素通しのまま）', () => {
  const anchor = fakeElement('div', 'chart-wrap');
  const view = new PaneLegendView({ document: fakeDoc(anchor) });
  view.setInstances(ROWS);
  view.update(MODEL_KEYED);
  view.update(MODEL_KEYED_SWAPPED);             // 畳まずに並べ替えだけ行う

  chipAt(anchor, 2).fire('click', {});          // 移動後の位置（p2）で畳む

  assert.equal(isOpenAt(anchor, 2), false, '移動後のチップで畳めない');
  assert.equal(isOpenAt(anchor, 1), true, '関係ないペインまで畳んだ');
});

// ---- 識別子の非対称（🟡-A・再レビュー 2026-08-09） ---- //
//
// 掴んだ指以外の事象を捨てる照合（🟡-2）には、**掴み側が識別子を持たない**場合の抜けがあった。
//   `pointerdown` に pointerId が無いと drag.pointerId=null になり、識別子を**持つ**後続事象が
//   すべて「別の指」と判定されて捨てられる。pointerup も pointercancel も lostpointercapture も
//   通らないため掴みが永久に終わらず、凡例の再描画停止（isDragging 中は止める）が解けない＝
//   ISSUE-342 で塞いだフェイルオープンの再来になる。照合できない条件では照合しない。

test('掴み側が識別子を持たなくても、識別子つきの pointerup で掴みは必ず終わる', () => {
  const { doc, drag, groups, moves } = setup();

  groups[1].handle.fire('pointerdown', { button: 0, clientY: 450 });   // 識別子なしで掴む
  doc.fire('pointermove', { clientY: 600, pointerId: 1 });             // 以後は識別子つき
  doc.fire('pointerup', { clientY: 600, pointerId: 1 });

  assert.equal(drag.isDragging(), false, '掴みが終わらない＝凡例が恒久停止する');
  assert.deepEqual(moves, [[1, 2]], '並べ替えも確定していない');
});

// 捕捉は識別子が無いと取れない（_capturePointer が Number.isFinite を要求する）ので、
//   識別子なしで掴んだ場合の終了経路は document 購読しかない。その唯一の経路が識別子照合で
//   塞がれないことを、pointerup 以外の終了事象でも固定する。
test('掴み側が識別子を持たなくても、識別子つきの pointercancel で掴みは終わる', () => {
  const { doc, drag, groups } = setup();

  groups[1].handle.fire('pointerdown', { button: 0, clientY: 450 });
  doc.fire('pointercancel', { clientY: 450, pointerId: 7 });

  assert.equal(drag.isDragging(), false);
});

test('掴み中に別チップを掴もうとしても、先の掴みを乗っ取らない（再入ガード）', () => {
  const { doc, drag, groups, moves } = setup();

  groups[1].handle.fire('pointerdown', { button: 0, clientY: 450 });
  doc.fire('pointermove', { clientY: 600 });
  groups[2].handle.fire('pointerdown', { button: 0, clientY: 600 });   // 割り込み
  doc.fire('pointerup', { clientY: 600 });

  assert.equal(drag.isDragging(), false);
  assert.deepEqual(moves, [[1, 2]], '割り込みで掴みの主体がすり替わった');
  // 乗っ取られると先の箱の終了処理が走らず、追従の transform と掴み中クラスが残る。
  assert.equal(groups[1].box.style.transform, '');
  assert.equal(groups[1].box.className.includes('is-dragging'), false);
});
