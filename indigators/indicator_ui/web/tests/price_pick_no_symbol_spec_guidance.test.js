// 「刻みが不明」のとき、2 経路とも**その理由**を案内する（工程 5 是正 C）。
//
// 設計入力（唯一の仕様源）: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md「フェイルセーフ」
//   ＝**無音で生値に落とさない。値ではなく機能を落とし、理由を出す。**
//   未知 ref → `reason='no_symbol_spec'` ／ トースト「この銘柄の価格の刻みが不明なため、
//   チャートからの価格指定を無効にしています」。
//
// 是正前の実測（node・本ファイル追加時点。同一の `reason='no_symbol_spec'` に対して）:
//   ピッカー経路   : "この位置の価格が取れません"                      ← 事実と食い違う
//   右クリック経路 : "この銘柄の価格の刻みが不明なため、…無効にしています" ← 正しい
//   前段の是正（🟡-1）で `guidanceFor` を「OTHER_PANE でなければ MSG_NO_PRICE」にしたため、
//   後から増えた 3 つ目の理由が `MSG_NO_PRICE` へ吸われた。**理由と案内の食い違いは無音より悪い**
//   （利用者は「別の場所を押せば入る」と誤解して押し続ける）。
//
// 到達性の記録（設計「工程 5 レビュー結果」のプロセス反映＝分岐の本番到達可能性を明示する）:
//   ピッカー経路のこの分岐は**現在の本番配線からは到達しない**。`PositionSizingController.requestPick`
//   が `symbolSpec === null` のときアーム自体を拒み、同じ文言を先に告知するためである
//   （`position_sizing_controller.js` requestPick）。本検定が固定するのは
//   **モジュール単体の契約**（理由コード → 案内文言の対応が 2 経路で同一）であり、
//   「アームの門番が将来外れたら無音の誤案内が出る」形を構造で塞ぐ。
//
// 構造: Arrange-Act-Assert。DOM・renderer は fake（lwc 非依存）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { PricePickController } from '../js/adapter/front/price_pick_controller.js';
import { createPositionSizingContextItems } from '../js/adapter/front/chart_app_wiring.js';
import * as resolver from '../js/adapter/front/price_pick_resolver.js';
import {
  resolvePickedPrice, NO_SYMBOL_SPEC, MSG_NO_SYMBOL_SPEC, MSG_NO_PRICE, MSG_OTHER_PANE,
} from '../js/adapter/front/price_pick_resolver.js';

class El {
  constructor() {
    this.children = [];
    this.style = {};
    this.textContent = '';
    this.className = '';
    this._cls = new Set();
    this._handlers = {};
  }

  get classList() {
    const s = this._cls;
    return {
      add: (c) => s.add(c), remove: (c) => s.delete(c), contains: (c) => s.has(c), toggle() {},
    };
  }

  appendChild(k) { this.children.push(k); return k; }

  append(...kids) { this.children.push(...kids); }

  querySelector() { return null; }

  getBoundingClientRect() { return { left: 0, top: 0 }; }

  addEventListener(type, fn) { (this._handlers[type] ||= []).push(fn); }

  fire(type, ev = {}) { (this._handlers[type] || []).forEach((fn) => fn(ev)); }
}

// 価格ペイン: y=0..299／下段ペイン: y=300..399／それ以外は null（実物と同じ振る舞い）。
const PRICE_PANE_Y = 100;
const OTHER_PANE_Y = 350;
const TIME_AXIS_Y = 450;

function fakeRenderer() {
  return {
    priceAtCoordinate: (y) => (Number.isFinite(y) ? 59000 - y : null),
    paneIndexAtCoordinate: (y) => {
      if (!Number.isFinite(y)) return null;
      if (y >= 0 && y < 300) return 0;
      if (y >= 300 && y < 400) return 1;
      return null;
    },
    snapCandidatesAt: () => [],
    suppressInteraction() { return () => {}; },
  };
}

// spec を渡した（＝銘柄仕様を扱う）ピッカー。`null` は「解決を試みて失敗した」状態。
function buildPicker(spec) {
  const wrap = new El();
  const container = new El();
  const doc = {
    createElement: () => new El(),
    querySelector: (sel) => (sel === '.chart-wrap' ? wrap : null),
    addEventListener() {},
    removeEventListener() {},
  };
  const confirmed = [];
  const picker = new PricePickController({
    container,
    renderer: fakeRenderer(),
    document: doc,
    spec,
    onConfirm: (target, price) => confirmed.push([target, price]),
  });
  picker.install();
  return {
    picker, container, wrap, confirmed,
  };
}

const ghostText = (wrap) => wrap.children
  .map((h) => [h, ...h.children].map((e) => e.textContent ?? '').join(' ')).join(' ').trim();

// 右クリック経路（共有配線のトースト差し替えを含む＝実際に画面へ出る文言）。
function rightClickToasts(spec) {
  const shown = [];
  const items = createPositionSizingContextItems({
    renderer: fakeRenderer(),
    getPositionSizing: () => ({
      symbolSpec: () => spec,
      setStopPrice() {},
      addEntryPrice() {},
      setTakePrice() {},
    }),
    getToast: () => ({ show: (t) => shown.push(t) }),
  });
  return { items, shown };
}

// ---------------------------------------------------------------------------
// 前提（この座標・この spec で本当に no_symbol_spec になるか）
// ---------------------------------------------------------------------------

test('TC-NS01 前提: 刻みが解決できない spec では価格ペインでも no_symbol_spec になる', () => {
  // Arrange / Act
  const got = resolvePickedPrice({
    renderer: fakeRenderer(), x: 50, y: PRICE_PANE_Y, spec: null,
  });
  // Assert
  assert.equal(got.reason, NO_SYMBOL_SPEC);
  assert.equal(got.price, null, '刻みが不明なのに価格が確定している');
});

// ---------------------------------------------------------------------------
// ピッカー経路（8-d）
// ---------------------------------------------------------------------------

test('TC-NS02 ピッカーのホバーは「刻みが不明」を案内する（別の理由を出さない）', () => {
  // Arrange
  const ctx = buildPicker(null);
  ctx.picker.arm('stop');
  // Act
  ctx.container.fire('pointermove', { clientX: 50, clientY: PRICE_PANE_Y });
  // Assert
  assert.equal(ghostText(ctx.wrap), MSG_NO_SYMBOL_SPEC, '理由と案内が食い違っている');
  assert.notEqual(MSG_NO_SYMBOL_SPEC, MSG_NO_PRICE, '2 つの文言が同じでは選び分けを検定できない');
});

test('TC-NS03 ピッカーのクリックも確定せず「刻みが不明」を出したままアームを続ける', () => {
  // Arrange
  const ctx = buildPicker(null);
  ctx.picker.arm('stop');
  // Act
  ctx.container.fire('click', { clientX: 50, clientY: PRICE_PANE_Y, button: 0 });
  // Assert
  assert.deepEqual(ctx.confirmed, [], '刻みが不明なのに確定している');
  assert.equal(ctx.picker.isArmed(), true);
  assert.equal(ghostText(ctx.wrap), MSG_NO_SYMBOL_SPEC);
});

// ---------------------------------------------------------------------------
// 2 経路の対称性
// ---------------------------------------------------------------------------

test('TC-NS04 ピッカーと右クリックは同じ理由に同じ文言を出す（no_symbol_spec）', () => {
  // Arrange
  const ctx = buildPicker(null);
  const rc = rightClickToasts(null);
  ctx.picker.arm('stop');
  // Act
  rc.items[0].onSelect({ x: 50, y: PRICE_PANE_Y });
  ctx.container.fire('pointermove', { clientX: 50, clientY: PRICE_PANE_Y });
  // Assert
  assert.deepEqual(rc.shown, [MSG_NO_SYMBOL_SPEC]);
  assert.equal(ghostText(ctx.wrap), rc.shown[0], '同じ理由なのに経路で案内が違う');
});

// ---------------------------------------------------------------------------
// 他の理由を巻き込まない（是正の範囲を固定する）
// ---------------------------------------------------------------------------

test('TC-NS05 刻みが解決できているときの案内は従来どおり（理由を取り違えない）', () => {
  // Arrange: 使える刻みを持つ spec。
  const spec = { tick: 1, digits: 0 };
  const ctx = buildPicker(spec);
  const rc = rightClickToasts(spec);
  ctx.picker.arm('stop');
  // Act / Assert: 下段ペイン。
  ctx.container.fire('pointermove', { clientX: 50, clientY: OTHER_PANE_Y });
  assert.equal(ghostText(ctx.wrap), MSG_OTHER_PANE);
  rc.items[0].onSelect({ x: 50, y: OTHER_PANE_Y });
  assert.deepEqual(rc.shown, [MSG_OTHER_PANE]);
  // Act / Assert: 時間軸の帯（価格に変換できない）。
  ctx.container.fire('pointermove', { clientX: 50, clientY: TIME_AXIS_Y });
  assert.equal(ghostText(ctx.wrap), MSG_NO_PRICE);
  rc.items[0].onSelect({ x: 50, y: TIME_AXIS_Y });
  assert.deepEqual(rc.shown, [MSG_OTHER_PANE, MSG_NO_PRICE]);
});

test('TC-NS06 銘柄仕様を扱わない構成（spec 未指定）は従来の契約のまま', () => {
  // Arrange: `undefined`＝そもそも銘柄仕様を扱わない呼び出し（量子化しない・従来の契約）。
  const ctx = buildPicker(undefined);
  ctx.picker.arm('stop');
  // Act
  ctx.container.fire('pointermove', { clientX: 50, clientY: PRICE_PANE_Y });
  // Assert: 価格が確定するので案内ではなく採用予定価格が出る。
  assert.match(ghostText(ctx.wrap), /58,900/, '従来どおり価格が確定していない');
});

// ---------------------------------------------------------------------------
// 構造: 文言の写しを作らない・分岐を 2 か所に持たない
// ---------------------------------------------------------------------------

const FRONT = (name) => readFileSync(
  fileURLToPath(new URL(`../js/adapter/front/${name}`, import.meta.url)), 'utf8',
);

test('TC-NS07 案内文言をピッカーが自前で持たない（単一ソースから取る）', () => {
  // Arrange / Act
  const src = FRONT('price_pick_controller.js');
  // Assert
  assert.equal(
    src.includes(MSG_NO_SYMBOL_SPEC), false,
    'ピッカーが案内文言を書き写している（裁定の文言変更で片方が取り残される）',
  );
  assert.match(src, /MSG_NO_SYMBOL_SPEC/, '単一ソースの文言を参照していない');
});

test('TC-NS08 理由コードには必ず対応する案内文言がある（理由を増やしたら文言も増える）', () => {
  // Arrange: 理由コード＝値が snake_case 文字列の大文字 export（`MSG_` 接頭辞を除く）。
  //   本是正の原因は「理由だけ増えて案内の分岐が増えなかった」ことなので、対応の欠落を
  //   宣言（コメント）ではなく機械検査で落とす。
  const reasons = Object.entries(resolver)
    .filter(([name, value]) => /^[A-Z0-9_]+$/.test(name) && !name.startsWith('MSG_')
      && typeof value === 'string' && /^[a-z_]+$/.test(value))
    .map(([name]) => name);
  // Act / Assert
  assert.deepEqual(
    reasons.slice().sort(), ['NO_PRICE', 'NO_SYMBOL_SPEC', 'OTHER_PANE'],
    `理由コードの集合が変わっている（案内の分岐も見直すこと）: ${reasons.join(', ')}`,
  );
  for (const name of reasons) {
    assert.equal(
      typeof resolver[`MSG_${name}`], 'string',
      `理由 ${name} に対応する MSG_${name} が無い（案内できない理由を作らない）`,
    );
  }
});

test('TC-NS09 すべての理由でピッカーが対応する文言を出す（既定へ黙って吸わせない）', () => {
  // Arrange: 理由 → その理由を発生させる（spec, y）と期待文言。
  const cases = [
    [MSG_NO_SYMBOL_SPEC, null, PRICE_PANE_Y],
    [MSG_OTHER_PANE, { tick: 1, digits: 0 }, OTHER_PANE_Y],
    [MSG_NO_PRICE, { tick: 1, digits: 0 }, TIME_AXIS_Y],
  ];
  // Act / Assert
  for (const [expected, spec, y] of cases) {
    const ctx = buildPicker(spec);
    ctx.picker.arm('stop');
    ctx.container.fire('pointermove', { clientX: 50, clientY: y });
    assert.equal(ghostText(ctx.wrap), expected, `y=${y} で案内が違う`);
  }
  // 3 つの文言が互いに異なることまで見る（同じなら選び分けを検定できない）。
  assert.equal(new Set(cases.map(([m]) => m)).size, 3);
});
