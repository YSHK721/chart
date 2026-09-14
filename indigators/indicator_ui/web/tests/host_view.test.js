// host_view.test.js — ロール射影 createHostView の振る舞い（ISP・ISSUE-255）。
//
// 何を固定するか: 契約が「宣言」ではなく「実体」として効くこと。
//   - 契約面は素通し（メソッドは host に bind＝subclass override が効く／フィールドは毎回 host から読む）
//   - 契約外は **例外**（フェイルクローズ。undefined を返して静かに壊れない）
//   - 書き込みは禁止（状態確定は host のコミット用メソッド経由）
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { createHostView } from '../js/adapter/front/host_view.js';

const CONTRACT = Object.freeze({
  role: 'TestHost',
  methods: Object.freeze(['doThing']),
  fields: Object.freeze(['_state']),
  optionalFields: Object.freeze(['_maybe']),
});

function makeHost() {
  return {
    _state: { n: 1 },
    _secret: 'ヒミツ',
    doThing(x) { return `${this._state.n}:${x}`; },
    otherThing() { return 'これは契約外'; },
  };
}

test('契約面のメソッドは host に bind されて呼べる（this が host のまま）', () => {
  const host = makeHost();
  const view = createHostView(host, CONTRACT);

  assert.equal(view.doThing('a'), '1:a');
});

test('契約面のフィールドは参照のたびに host から読む（可変状態に追随する）', () => {
  const host = makeHost();
  const view = createHostView(host, CONTRACT);

  assert.equal(view._state.n, 1);
  host._state = { n: 2 };                    // host 側で state が差し替わる（実運用と同じ）
  assert.equal(view._state.n, 2);
});

test('subclass の override が射影越しに効く（LSP）', () => {
  const base = makeHost();
  const sub = Object.create(base);
  sub.doThing = function doThing(x) { return `sub:${x}`; };
  const view = createHostView(sub, CONTRACT);

  assert.equal(view.doThing('a'), 'sub:a');
});

test('契約外メンバーの読み取りは例外（フェイルクローズ・静かな縮退にしない）', () => {
  const view = createHostView(makeHost(), CONTRACT);

  assert.throws(() => view._secret, /契約外の host メンバー/);
  assert.throws(() => view.otherThing, /契約外の host メンバー/);
});

test('未在席の optional 面は例外ではなく undefined（宣言済みだから）', () => {
  const view = createHostView(makeHost(), CONTRACT);

  assert.equal(view._maybe, undefined);
});

test('射影への書き込みは例外（状態確定は host のコミット用メソッド経由）', () => {
  const view = createHostView(makeHost(), CONTRACT);

  assert.throws(() => { view._state = { n: 9 }; }, /書き換えられません/);
});

test('in 演算子は契約面だけを真とする（面の一覧は契約と一致）', () => {
  const view = createHostView(makeHost(), CONTRACT);

  assert.equal('doThing' in view, true);
  assert.equal('_secret' in view, false);
  assert.deepEqual(Object.keys(view).sort(), ['_maybe', '_state', 'doThing'].sort());
});

test('await 判定（then 探索）で例外にならない（ランタイム由来の探索は素通し）', async () => {
  const view = createHostView(makeHost(), CONTRACT);

  const got = await Promise.resolve(view);   // then を触られても throw しないこと
  assert.equal(got.doThing('a'), '1:a');
});

test('host / 契約が不正なら構築時に落ちる（無言で空射影を作らない）', () => {
  assert.throws(() => createHostView(null, CONTRACT), /host が null/);
  assert.throws(() => createHostView({}, null), /role を持つ契約/);
  assert.throws(
    () => createHostView({}, { role: 'X', methods: [], fields: [], optionalFields: [] }),
    /契約が空/,
  );
});
