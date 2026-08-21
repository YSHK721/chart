// bottom_pane_view（下部ペイン＋分割線の器）の契約テスト。
//
// 固定する不変条件:
//   1. 器（#um-bottom-splitter / #um-bottom-pane）は View が生成し所有する（HTML を触らない）。
//   2. 表示層の挿し先は `host()`＝ペインそのもの（統合ページの id を表示層に知らせない）。
//   3. 分割線のドラッグでペイン高が変わり、**可動域**（ペイン下限・上側の下限）を出ない
//      ＝引き切ってもチャートは消えない（旧 `.chart-wrap { display:none }` の排他へ戻さない）。
//   4. 可動域は「上の要素＋ペイン」の実測高から決める。版面（#app）高から引くと、分割に
//      与らない兄弟（ツールバー等）の分だけ上側の下限が守られない（実測 2026-08-21）。
//   5. ドラッグ中はポインタを分割線へ捕捉する。捕捉しないとカーソルがペイン内の iframe へ
//      入った瞬間に pointermove が子文書へ配られ、下方向のドラッグが無反応になる（実測 同日）。
//   6. mount / unmount は冪等で、購読は器と一緒に消える（モード往復で積み上がらない）。
//   7. 既定高は CSS が持つ（起動時に View は flex-basis を書かない＝版面への自動介入なし）。
//
// 環境は node（DOM なし）。必要な面だけの fake を置く（jsdom は導入しない＝既存流儀）。
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { describe, test, expect } from 'vitest';
import {
  BOTTOM_PANE_ID,
  BOTTOM_SPLITTER_ID,
  MIN_ABOVE_PX,
  MIN_PANE_PX,
  clampPaneHeight,
  createBottomPaneView,
} from '../js/bottom_pane_view.js';

/** 最小の要素ダブル（子・属性・購読・ポインタ捕捉を観測できるだけ）。 */
function fakeEl(tag = 'div') {
  return {
    tagName: String(tag).toUpperCase(),
    id: '',
    title: '',
    style: {},
    attrs: {},
    children: [],
    listeners: {},
    captured: [],
    released: [],
    setAttribute(k, v) { this.attrs[k] = v; },
    appendChild(child) { this.children.push(child); return child; },
    removeChild(child) {
      const i = this.children.indexOf(child);
      if (i >= 0) this.children.splice(i, 1);
      return child;
    },
    addEventListener(ev, fn) { (this.listeners[ev] ||= []).push(fn); },
    removeEventListener(ev, fn) {
      const list = this.listeners[ev] || [];
      const i = list.indexOf(fn);
      if (i >= 0) list.splice(i, 1);
    },
    setPointerCapture(id) { this.captured.push(id); },
    releasePointerCapture(id) { this.released.push(id); },
    emit(ev, payload) { (this.listeners[ev] || []).forEach((fn) => fn(payload)); },
  };
}

function fakeDoc() {
  const doc = fakeEl('#document');
  doc.createElement = (tag) => fakeEl(tag);
  return doc;
}

/** 上（チャート）502px・ペイン 450px で mount 済みの View を返す（実 UI の実測値）。 */
function mounted({ aboveHeight = 502, paneHeight = 450, withAbove = true } = {}) {
  const doc = fakeDoc();
  const app = fakeEl('div');
  const above = fakeEl('div');
  const heights = new Map([[above, aboveHeight]]);
  const view = createBottomPaneView({
    doc,
    measureHeight: (el) => (heights.has(el) ? heights.get(el) : 0),
  });
  view.mount(app, { above: withAbove ? above : null });
  heights.set(view.host(), paneHeight);
  return { doc, app, above, view, heights };
}

/** 分割線を掴んで dy だけ動かす（上へ引くと dy は負）。離すかは呼び手が決める。 */
function drag(view, dy, { release = false } = {}) {
  const sp = view.splitterElement();
  sp.emit('pointerdown', { clientY: 500, pointerId: 7, preventDefault() {} });
  sp.emit('pointermove', { clientY: 500 + dy, pointerId: 7 });
  if (release) sp.emit('pointerup', { pointerId: 7 });
}

describe('clampPaneHeight — 可動域', () => {
  test('可動域の内側_そのまま返す', () => {
    // Assert
    expect(clampPaneHeight(400, 952)).toBe(400);
  });

  test('下限を割る要求_ペイン下限まで戻す', () => {
    // Assert
    expect(clampPaneHeight(10, 952)).toBe(MIN_PANE_PX);
  });

  test('上限を超える要求_上側の下限を残す', () => {
    // Assert: 上（チャート）側は必ず MIN_ABOVE_PX 残る（＝チャートが消える版面にしない）。
    expect(clampPaneHeight(9999, 952)).toBe(952 - MIN_ABOVE_PX);
  });

  test('予算が測れない_下限だけを掛ける', () => {
    // Arrange: 未 mount・描画前・上の要素なしでは 0 が返る。ここで上限 0 と解釈すると潰れる。
    // Assert
    expect(clampPaneHeight(400, 0)).toBe(400);
    expect(clampPaneHeight(10, Number.NaN)).toBe(MIN_PANE_PX);
  });

  test('予算が極端に少ない_ペイン下限を優先する', () => {
    // Assert: 上限が下限を割る場合でも 0 にはしない（無音の失敗を作らない）。
    expect(clampPaneHeight(400, 150)).toBe(MIN_PANE_PX);
  });

  test('予算は版面高ではない_分割に与らない兄弟を含めない', () => {
    // Arrange: 版面 1000・ツールバー 42・分割線 6 → 予算は 952（＝上 502 ＋ ペイン 450）。
    //   版面高 1000 で計算すると上限が 880 になり、上側は 1000-42-6-880 = 72px＝下限割れ。
    // Assert: 予算基準なら上限は 832 で、上側にはちょうど下限 120 が残る。
    expect(clampPaneHeight(9999, 952)).toBe(832);
    expect(952 - 832).toBe(MIN_ABOVE_PX);
  });
});

describe('createBottomPaneView — 器の生成と所有', () => {
  test('mount_分割線とペインを版面の末尾へこの順で挿す', () => {
    // Arrange & Act
    const { app } = mounted();
    // Assert
    expect(app.children.map((c) => c.id)).toEqual([BOTTOM_SPLITTER_ID, BOTTOM_PANE_ID]);
  });

  test('mount_分割線は操作できる境界として役割を持つ', () => {
    // Assert: 見た目だけの線にしない（読み上げ・将来のキーボード操作の入口）。
    const { view } = mounted();
    expect(view.splitterElement().attrs.role).toBe('separator');
    expect(view.splitterElement().attrs['aria-orientation']).toBe('horizontal');
  });

  test('mount_既定高はCSSに委ねる_flexBasisを書かない', () => {
    // Assert: 起動時に View が版面へ手を入れない（既定 45% は index.html が持つ）。
    const { view } = mounted();
    expect(view.host().style.flexBasis).toBeUndefined();
  });

  test('二重mount_器は増えない', () => {
    // Arrange
    const { app, view } = mounted();
    // Act
    view.mount(app);
    // Assert
    expect(app.children).toHaveLength(2);
  });

  test('host_表示層の挿し先はペインそのもの', () => {
    // Assert
    const { view } = mounted();
    expect(view.host().id).toBe(BOTTOM_PANE_ID);
  });

  test('mount_両側の下限を版面へ書く', () => {
    // Assert: 下限は View の定数だけが持ち、版面へは View が書く（CSS と二重に持たない）。
    //   これが無いと、ドラッグで広げた後にウィンドウを縮めたとき flex がチャート側だけを
    //   削ってチャートが消える（実測 2026-08-21: 高さ 600 へ縮めてチャート 0px）。
    const { view, above } = mounted();
    expect(view.host().style.minHeight).toBe(`${MIN_PANE_PX}px`);
    expect(above.style.minHeight).toBe(`${MIN_ABOVE_PX}px`);
  });

  test('unmount_上の要素へ書いた下限を戻す', () => {
    // Arrange: 上の要素は他所（live core の版面）の持ち物。
    const { view, above } = mounted();
    // Act
    view.unmount();
    // Assert
    expect(above.style.minHeight).toBe('');
  });

  test('mount_購読は文書ではなく分割線が持つ', () => {
    // Assert: 文書へ purchase を残さないので、器を外せば購読も消える（積み上がらない）。
    const { doc, view } = mounted();
    expect(doc.listeners.pointermove).toBeUndefined();
    expect(doc.listeners.pointerup).toBeUndefined();
    expect(view.splitterElement().listeners.pointerdown).toHaveLength(1);
    expect(view.splitterElement().listeners.pointermove).toHaveLength(1);
  });

  test('unmount_器を残さない', () => {
    // Arrange
    const { app, view } = mounted();
    // Act
    view.unmount();
    // Assert
    expect(app.children).toHaveLength(0);
    expect(view.isMounted()).toBe(false);
  });

  test('二重unmount_例外にならない', () => {
    // Arrange
    const { view } = mounted();
    view.unmount();
    // Assert
    expect(() => view.unmount()).not.toThrow();
  });
});

describe('createBottomPaneView — 分割線のドラッグ', () => {
  test('上へ引く_ペインが高くなる', () => {
    // Arrange
    const { view } = mounted({ paneHeight: 450 });
    // Act
    drag(view, -100);
    // Assert
    expect(view.host().style.flexBasis).toBe('550px');
  });

  test('下へ引く_ペインが低くなる', () => {
    // Arrange
    const { view } = mounted({ paneHeight: 450 });
    // Act
    drag(view, 200);
    // Assert
    expect(view.host().style.flexBasis).toBe('250px');
  });

  test('引き切る_上側は下限だけ残る', () => {
    // Act
    const { view } = mounted({ aboveHeight: 502, paneHeight: 450 });
    drag(view, -5000);
    // Assert: 上限で止まる＝チャートが消える版面（旧 H-C の排他）へ戻らない。
    expect(view.host().style.flexBasis).toBe(`${952 - MIN_ABOVE_PX}px`);
  });

  test('押し下げ切る_ペインは下限で止まる', () => {
    // Act
    const { view } = mounted({ paneHeight: 450 });
    drag(view, 5000);
    // Assert
    expect(view.host().style.flexBasis).toBe(`${MIN_PANE_PX}px`);
  });

  test('掴んだ時点でポインタを分割線へ捕捉する', () => {
    // Assert: 捕捉しないと、ペイン内の iframe へカーソルが入った時点で移動が届かなくなる。
    const { view } = mounted();
    drag(view, -10);
    expect(view.splitterElement().captured).toEqual([7]);
  });

  test('離したとき_捕捉を解く', () => {
    // Arrange & Act
    const { view } = mounted();
    drag(view, -10, { release: true });
    // Assert
    expect(view.splitterElement().released).toEqual([7]);
  });

  test('予算はドラッグ開始時に1回だけ測る', () => {
    // Arrange: 移動のたびに測ると、自分が変えた高さを読み直して予算が揺れる。
    const { view, heights } = mounted({ aboveHeight: 502, paneHeight: 450 });
    const sp = view.splitterElement();
    // Act: 途中でペイン高の実測値が変わっても（実 DOM では毎フレーム変わる）上限は動かない。
    sp.emit('pointerdown', { clientY: 500, pointerId: 1, preventDefault() {} });
    heights.set(view.host(), 900);
    heights.set(sp, 0);
    sp.emit('pointermove', { clientY: -5000, pointerId: 1 });
    // Assert
    expect(view.host().style.flexBasis).toBe(`${952 - MIN_ABOVE_PX}px`);
  });

  test('押していない間の移動_高さは変わらない', () => {
    // Arrange
    const { view } = mounted();
    // Act
    view.splitterElement().emit('pointermove', { clientY: 100, pointerId: 1 });
    // Assert
    expect(view.host().style.flexBasis).toBeUndefined();
  });

  test('離した後の移動_高さは変わらない', () => {
    // Arrange
    const { view } = mounted({ paneHeight: 450 });
    drag(view, -100, { release: true });
    // Act
    view.splitterElement().emit('pointermove', { clientY: 0, pointerId: 7 });
    // Assert
    expect(view.host().style.flexBasis).toBe('550px');
  });

  test('捕捉が切れた_ドラッグ状態を残さない', () => {
    // Arrange
    const { view } = mounted({ paneHeight: 450 });
    const sp = view.splitterElement();
    drag(view, -100);
    // Act: pointercancel（別ウィンドウへ移る等）でも掴みっぱなしにしない。
    sp.emit('pointercancel', { pointerId: 7 });
    sp.emit('pointermove', { clientY: 0, pointerId: 7 });
    // Assert
    expect(view.host().style.flexBasis).toBe('550px');
  });

  test('上の要素が無い_下限だけを掛ける', () => {
    // Arrange: 予算が測れない構成（E2E fixture 等）。上限 0 と誤解して潰さない。
    const { view } = mounted({ withAbove: false, paneHeight: 450 });
    // Act
    drag(view, -300);
    // Assert
    expect(view.host().style.flexBasis).toBe('750px');
  });

  test('setHeightPx_可動域へ丸めて適用する', () => {
    // Arrange
    const { view } = mounted();
    // Act
    view.setHeightPx(10);
    // Assert
    expect(view.host().style.flexBasis).toBe(`${MIN_PANE_PX}px`);
  });
});
