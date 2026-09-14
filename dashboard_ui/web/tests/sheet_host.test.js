// sheet_host — 表示系統のホスト要素の**所有規約**。
//
// 設計入力: indigators/indicator_ui/web/js/adapter/front/overlay_host.js:11-22 の規約
//   （SRP: ホスト要素はそこへ描く View が所有する／OCP: 要素名を列挙する中央 factory を置かない／
//    DIP: View はページが宣言した id ではなく注入されたアンカーに依存する／
//    フェイルクローズ: DOM はあるがアンカーが無ければ**例外**。無言 no-op にしない）。
//   ISSUE-276 の全滅は「要素不在なら no-op」が契約違反を無症状にしたため実 UI で気付けなかった。
//
// dashboard 固有の要件（arch-spec §7 / unified_root.js:387-396）: host は sim と共有する
//   bottomPane の器である。したがって `disable()` で**必ず unmount** し、統合ページへ 1 要素も
//   残さない（残すと sim モードの版面に dashboard の残骸が混ざる）。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { fakeDoc, fakeEl, flatten } from './_fake_dom.js';
import { createSheetHost, SHEET_HOST_CLASS } from '../js/adapter/front/sheet_host.js';

describe('sheet_host — ホスト要素の所有', () => {
  test('mount_creates_the_host_under_the_injected_anchor', () => {
    // Arrange
    const doc = fakeDoc();
    const anchor = fakeEl('div');
    const host = createSheetHost({ doc });
    // Act
    const el = host.mount(anchor);
    // Assert: 器は View が作る（ページの index.html は 1 要素も持たない）。
    assert.equal(anchor.children.length, 1);
    assert.equal(anchor.children[0], el);
    assert.equal(el.classList.contains(SHEET_HOST_CLASS), true);
  });

  test('mount_twice_does_not_duplicate_the_host', () => {
    // 再入（モードの出入り）でホストを増やさない（overlay_host.js と同じ規約）。
    const doc = fakeDoc();
    const anchor = fakeEl('div');
    const host = createSheetHost({ doc });
    // Act
    const first = host.mount(anchor);
    const second = host.mount(anchor);
    // Assert
    assert.equal(first, second);
    assert.equal(anchor.children.length, 1);
  });

  test('unmount_leaves_nothing_behind_in_the_shared_anchor', () => {
    // Arrange: host は sim と共有する bottomPane の器。
    const doc = fakeDoc();
    const anchor = fakeEl('div');
    const host = createSheetHost({ doc });
    host.mount(anchor);
    host.element().appendChild(doc.createElement('table'));
    // Act
    host.unmount();
    // Assert
    assert.equal(anchor.children.length, 0);
    assert.equal(host.element(), null);
  });

  test('unmount_also_removes_the_stylesheet_the_view_installed', () => {
    // CSS も View の持ち物。残すと dashboard を出た後も sim の版面へ規則が効き続ける。
    const doc = fakeDoc();
    const anchor = fakeEl('div');
    const host = createSheetHost({ doc, styleHref: '/dashboard/css/dashboard.css' });
    host.mount(anchor);
    assert.equal(doc.head.children.length, 1);
    // Act
    host.unmount();
    // Assert
    assert.equal(doc.head.children.length, 0);
  });

  test('the_stylesheet_is_installed_once_even_across_remounts', () => {
    const doc = fakeDoc();
    const anchor = fakeEl('div');
    const host = createSheetHost({ doc, styleHref: '/dashboard/css/dashboard.css' });
    // Act
    host.mount(anchor);
    host.mount(anchor);
    // Assert
    assert.equal(doc.head.children.length, 1);
  });

  test('unmount_before_mount_is_harmless', () => {
    const host = createSheetHost({ doc: fakeDoc() });
    assert.doesNotThrow(() => host.unmount());
  });

  test('a_missing_anchor_fails_closed_instead_of_becoming_a_silent_no_op', () => {
    // ISSUE-276: 「要素不在なら no-op」が契約違反を無症状にした。
    const host = createSheetHost({ doc: fakeDoc() });
    assert.throws(() => host.mount(null), /アンカー/);
  });

  test('an_environment_without_dom_declines_to_draw_rather_than_throwing', () => {
    // 純ロジック環境（DOM 無し）は描画対象そのものが無い＝契約違反ではない
    //   （overlay_host.js:44-48 と同じ区別）。
    const host = createSheetHost({ doc: null });
    assert.equal(host.mount(fakeEl('div')), null);
  });

  test('the_host_never_reaches_outside_itself_into_the_page_id_space', () => {
    // ISP: View が触れるのは自分のホスト 1 要素だけ（document 全体の id 空間に依存しない）。
    const doc = fakeDoc();
    const anchor = fakeEl('div');
    const host = createSheetHost({ doc });
    host.mount(anchor);
    // Assert: 生成物に id を付けない（ページの id 空間を奪わない）。
    for (const el of flatten(host.element())) {
      assert.equal(el.id, '', `id を持つ要素があります: ${el.tagName}`);
    }
  });
});
