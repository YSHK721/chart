// buildPeriod（期間入力＋プリセット コントロール）の検証（node:test / node:assert）。
//
// 対象: adapter/front/property_control_builders.js の buildPeriod。
// 設計入力: 基本設計_期間プリセット.md v0.1.0 §6.1 提示 / §6.2 選択 / §6.3 期間表記入力 / §7.3 入力欄。
// 構造: Arrange-Act-Assert（AAA）。jsdom 等の新規依存は使わず最小 DOM スタブで検証する（C-2）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CONTROL_BUILDERS, buildPeriod } from '../js/adapter/front/property_control_builders.js';

// ---- 最小 DOM スタブ -------------------------------------------------------

function makeEl(tag) {
  // className（文字列）と classList（集合）を実 DOM と同様に同期させる。
  const classes = new Set();
  const el = {
    tagName: String(tag).toUpperCase(),
    dataset: {},
    textContent: '',
    value: '',
    type: '',
    title: '',
    placeholder: '',
    children: [],
    _listeners: {},
    _html: '',
    classList: {
      add(c) { classes.add(c); },
      remove(c) { classes.delete(c); },
      contains(c) { return classes.has(c); },
      toggle(c, on) {
        const next = on === undefined ? !classes.has(c) : !!on;
        if (next) classes.add(c); else classes.delete(c);
      },
    },
    append(...nodes) {
      for (const n of nodes) {
        el.children.push(n);
        n.parentNode = el;
      }
    },
    addEventListener(type, fn) { (el._listeners[type] ??= []).push(fn); },
    dispatch(type, ev = {}) { for (const fn of el._listeners[type] ?? []) fn(ev); },
    contains(node) {
      if (!node) return false;
      if (node === el) return true;
      return el.children.some((c) => (typeof c.contains === 'function' ? c.contains(node) : c === node));
    },
  };
  Object.defineProperty(el, 'className', {
    get() { return [...classes].join(' '); },
    set(v) { classes.clear(); for (const c of String(v).split(/\s+/).filter(Boolean)) classes.add(c); },
    configurable: true,
  });
  // innerHTML='' でのクリアを children のクリアと同期させる（renderPop の再構築を再現）。
  Object.defineProperty(el, 'innerHTML', {
    get() { return el._html; },
    set(v) { el._html = v; if (v === '') { el.children = []; } },
    configurable: true,
  });
  return el;
}

const fakeDoc = { createElement: (tag) => makeEl(tag) };

// ctx（ControlContext）を作る。periodContext は基本設計 §8.2 の供給面。
// values の既定は「ホストが FieldDesc の初期値で seed 済み」の状態を再現する
//   （PropertiesDialog は buildFormModel の値で _values を先に埋める）。
function makeCtx({ values = { length: 50 }, periodContext = null, setPendingError = null } = {}) {
  const changes = [];
  const ctx = {
    doc: fakeDoc,
    getValue: (name) => values[name],
    setValue: (name, v) => { values[name] = v; },
    onChange: () => { changes.push({ ...values }); },
    periodContext: periodContext === null ? undefined : () => periodContext,
    _values: values,
    _changes: changes,
  };
  // 未解決入力エラーの通知面（ホストが対応している場合のみ渡される）。
  if (setPendingError) {
    ctx.setPendingError = setPendingError;
  }
  return ctx;
}

const FIELD = { name: 'length', value: 50, min: 2, max: null };
const PC_1H = { datasetRef: 'jp225_tick', timeframe: '1h', timeframeLabel: '1時間足' };

function parts(wrap) {
  return {
    input: wrap.children.find((c) => c.className.includes('prop-period-input')),
    trigger: wrap.children.find((c) => c.className.includes('prop-period-trigger')),
    pop: wrap.children.find((c) => c.className.includes('prop-period-pop')),
    err: wrap.children.find((c) => c.className.includes('prop-period-error')),
  };
}

// ---- 登録（OCP・§8.1）------------------------------------------------------

test('CONTROL_BUILDERS に period が登録されている', () => {
  assert.equal(CONTROL_BUILDERS.period, buildPeriod);
});

// ---- 初期描画（§7.1/§7.3）--------------------------------------------------

test('初期表示は本数（text 入力・A-8）で、プリセットは閉じている', () => {
  const ctx = makeCtx({ periodContext: PC_1H });
  const wrap = buildPeriod(FIELD, ctx);
  const { input, pop } = parts(wrap);
  assert.equal(input.type, 'text');
  assert.equal(input.value, '50');
  assert.ok(pop.classList.contains('is-hidden'));
});

// ---- UC-P01 提示（§6.1）----------------------------------------------------

test('トリガー押下でプリセットが実効足の集合で描かれる（表 v2・1h は 13 件）', () => {
  const ctx = makeCtx({ periodContext: PC_1H });
  const wrap = buildPeriod(FIELD, ctx);
  const { trigger, pop } = parts(wrap);

  trigger.dispatch('click');

  assert.ok(!pop.classList.contains('is-hidden'));
  const items = pop.children.filter((c) => c.className.includes('prop-period-item'));
  assert.deepEqual(
    items.map((i) => [i.children[0].textContent, i.children[1].textContent]),
    [['2時間', '2'], ['4時間', '4'], ['6時間', '6'], ['12時間', '11'], ['1日', '23'],
      ['2日', '46'], ['3日', '56'], ['1週間', '115'], ['2週間', '230'], ['3週間', '341'],
      ['1ヶ月', '495'], ['2ヶ月', '981'], ['3ヶ月', '1481']],
  );
});

test('プリセット見出しに基準時間足を出す（§7.2）', () => {
  const ctx = makeCtx({ periodContext: PC_1H });
  const wrap = buildPeriod(FIELD, ctx);
  const { trigger, pop } = parts(wrap);
  trigger.dispatch('click');
  const head = pop.children.find((c) => c.className.includes('prop-period-head'));
  assert.equal(head.textContent, '1時間足 基準');
});

test('パラメータの min 制約でプリセットが絞られる（§6.1-3）', () => {
  const ctx = makeCtx({ periodContext: { datasetRef: 'jp225_tick', timeframe: '30m' } });
  // min:3 → 30m の '1時間'=2 本は落ちる。
  const wrap = buildPeriod({ name: 'x', value: 10, min: 3, max: null }, ctx);
  const { trigger, pop } = parts(wrap);
  trigger.dispatch('click');
  const items = pop.children.filter((c) => c.className.includes('prop-period-item'));
  assert.deepEqual(items.map((i) => i.children[1].textContent),
    ['4', '8', '12', '22', '45', '90', '108', '225', '450', '668', '969']);
});

test('候補 0 件のときは空メッセージを出す（未登録 datasetRef・F-P3）', () => {
  const ctx = makeCtx({ periodContext: { datasetRef: 'unknown', timeframe: '1h' } });
  const wrap = buildPeriod(FIELD, ctx);
  const { trigger, pop } = parts(wrap);
  trigger.dispatch('click');
  assert.ok(pop.children.some((c) => c.className.includes('prop-period-empty')));
});

test('現在値と一致するプリセットは選択状態になる', () => {
  const ctx = makeCtx({ periodContext: PC_1H });
  const wrap = buildPeriod({ ...FIELD, value: 115 }, ctx);
  const { trigger, pop } = parts(wrap);
  trigger.dispatch('click');
  const active = pop.children.filter((c) => c.classList?.contains?.('is-active'));
  assert.equal(active.length, 1);
  assert.equal(active[0].dataset.periodBars, '115');
});

// ---- UC-P02 選択（§6.2）----------------------------------------------------

test('プリセット選択で本数が代入され、ポップが閉じ、onChange が走る', () => {
  const ctx = makeCtx({ periodContext: PC_1H });
  const wrap = buildPeriod(FIELD, ctx);
  const { input, trigger, pop } = parts(wrap);
  trigger.dispatch('click');
  const week = pop.children.find((c) => c.dataset?.periodBars === '115');

  week.dispatch('click');

  assert.equal(ctx._values.length, 115);
  assert.equal(input.value, '115');
  assert.ok(pop.classList.contains('is-hidden'));
  assert.equal(ctx._changes.length, 1);
});

// ---- UC-P03 期間表記入力（§6.3・§7.3）--------------------------------------

test('5d を入力して blur すると 115 へ換算され表示も置き換わる', () => {
  const ctx = makeCtx({ periodContext: PC_1H });
  const wrap = buildPeriod(FIELD, ctx);
  const { input, err } = parts(wrap);

  input.value = '5d';
  input.dispatch('input'); // 純数値ではないので即時反映しない
  assert.equal(ctx._values.length, 50, 'blur 前は代入されない');
  input.dispatch('blur');

  assert.equal(ctx._values.length, 115);
  assert.equal(input.value, '115');
  assert.equal(err.textContent, '');
});

test('Enter でも確定する', () => {
  const ctx = makeCtx({ periodContext: PC_1H });
  const wrap = buildPeriod(FIELD, ctx);
  const { input } = parts(wrap);
  input.value = '3M';
  input.dispatch('keydown', { key: 'Enter', preventDefault() {} });
  assert.equal(ctx._values.length, 1481);
});

test('純数値の入力は即時反映される（従来の number コントロールと同じ即時検証）', () => {
  const ctx = makeCtx({ periodContext: PC_1H });
  const wrap = buildPeriod(FIELD, ctx);
  const { input } = parts(wrap);
  input.value = '77';
  input.dispatch('input');
  assert.equal(ctx._values.length, 77);
});

test('解釈不能な入力は代入せずエラーを表示し、直前の値を保持する（F-P1）', () => {
  const ctx = makeCtx({ periodContext: PC_1H });
  const wrap = buildPeriod(FIELD, ctx);
  const { input, err } = parts(wrap);

  input.value = '5x';
  input.dispatch('blur');

  assert.equal(ctx._values.length, 50, '値は変わらない');
  assert.ok(err.textContent.length > 0);
  assert.ok(wrap.classList.contains('has-error'));
});

test('min 違反は代入せずエラーになる（§6.3-5）', () => {
  const ctx = makeCtx({ periodContext: { datasetRef: 'jp225_tick', timeframe: '1D' } });
  const wrap = buildPeriod({ name: 'length', value: 50, min: 10, max: null }, ctx);
  const { input, err } = parts(wrap);
  input.value = '1w'; // 1D の 1週間 = 5 本 < min 10
  input.dispatch('blur');
  assert.equal(ctx._values.length, 50);
  assert.ok(err.textContent.includes('下限'));
});

test('RECENT_BARS 超は代入せずエラーになる（F-P4）', () => {
  const ctx = makeCtx({ periodContext: { datasetRef: 'jp225_tick', timeframe: '1m' } });
  const wrap = buildPeriod({ name: 'length', value: 50, min: null, max: null }, ctx);
  const { input, err } = parts(wrap);
  input.value = '1w'; // 1m の 1週間 = 6425 本 > 1500
  input.dispatch('blur');
  assert.equal(ctx._values.length, 50);
  assert.ok(err.textContent.includes('1500'));
});

test('空入力は null を代入する（既存 number コントロールと同挙動）', () => {
  const ctx = makeCtx({ periodContext: PC_1H });
  const wrap = buildPeriod(FIELD, ctx);
  const { input } = parts(wrap);
  input.value = '';
  input.dispatch('blur');
  assert.equal(ctx._values.length, null);
});

// ---- 退化動作（periodContext 未供給）----------------------------------------

test('periodContext 未供給ならプリセットを出さず数値のみ受理する', () => {
  const ctx = makeCtx({ periodContext: null });
  const wrap = buildPeriod(FIELD, ctx);
  const { input, trigger, pop, err } = parts(wrap);

  trigger.dispatch('click');
  assert.ok(pop.children.some((c) => c.className.includes('prop-period-empty')));

  input.value = '120';
  input.dispatch('blur');
  assert.equal(ctx._values.length, 120);

  input.value = '5d';
  input.dispatch('blur');
  assert.equal(ctx._values.length, 120, '期間表記は受理しない');
  assert.ok(err.textContent.length > 0);
});

// ---- 開閉（§7.2）-----------------------------------------------------------

test('外側へフォーカスが移るとポップが閉じる（document 常駐リスナを持たない）', () => {
  const ctx = makeCtx({ periodContext: PC_1H });
  const wrap = buildPeriod(FIELD, ctx);
  const { trigger, pop } = parts(wrap);
  trigger.dispatch('click');
  assert.ok(!pop.classList.contains('is-hidden'));

  wrap.dispatch('focusout', { relatedTarget: makeEl('input') });

  assert.ok(pop.classList.contains('is-hidden'));
});

test('ポップ内へフォーカスが移っても閉じない', () => {
  const ctx = makeCtx({ periodContext: PC_1H });
  const wrap = buildPeriod(FIELD, ctx);
  const { trigger, pop } = parts(wrap);
  trigger.dispatch('click');
  const item = pop.children.find((c) => c.className.includes('prop-period-item'));

  wrap.dispatch('focusout', { relatedTarget: item });

  assert.ok(!pop.classList.contains('is-hidden'));
});

// ===========================================================================
// 2026-07-29 ユーザー報告の是正:
//   (1)「3h」と入力したら「1803h」になる＝既存値へ追記される（フォーカス時に全選択しない）。
//   (2) その結果 換算が失敗し代入されないまま OK で旧値が確定する＝「設定しても元に戻る」。
// ===========================================================================

test('フォーカスで入力欄を全選択する（既存値への追記＝1803h を構造的に防ぐ）', () => {
  const ctx = makeCtx({ periodContext: PC_1H });
  const wrap = buildPeriod({ name: 'length', value: 180, min: null, max: null }, ctx);
  const { input } = parts(wrap);
  let selected = 0;
  input.select = () => { selected += 1; };

  input.dispatch('focus');

  assert.equal(selected, 1, 'フォーカス時に select() を呼ぶ');
});

// 実 UI 実測（2026-07-29）: focus だけでは 2 回目以降のクリックで発火せず追記が起きた
//   （`180` → `1801803h`）。キャレットのみ（選択なし）のクリックでも全選択し直す。
test('フォーカス済みの欄を再クリックしても全選択する（2 回目以降の追記防止）', () => {
  const ctx = makeCtx({ periodContext: PC_1H });
  const wrap = buildPeriod({ name: 'length', value: 180, min: null, max: null }, ctx);
  const { input } = parts(wrap);
  let selected = 0;
  input.select = () => { selected += 1; };
  input.selectionStart = 3;
  input.selectionEnd = 3;   // キャレットのみ＝選択されていない

  input.dispatch('click');

  assert.equal(selected, 1, 'クリック時にも select() を呼ぶ');
});

test('範囲選択中のクリックは選択を潰さない（部分編集の余地を残す）', () => {
  const ctx = makeCtx({ periodContext: PC_1H });
  const wrap = buildPeriod({ name: 'length', value: 180, min: null, max: null }, ctx);
  const { input } = parts(wrap);
  let selected = 0;
  input.select = () => { selected += 1; };
  input.selectionStart = 0;
  input.selectionEnd = 2;   // 既にユーザーが範囲選択している

  input.dispatch('click');

  assert.equal(selected, 0, '選択済みなら select() を呼ばない');
});

test('換算に失敗した入力は OK を抑止する（旧値の暗黙確定を防ぐ）', () => {
  const pending = [];
  const ctx = makeCtx({
    periodContext: PC_1H,
    setPendingError: (name, message) => pending.push([name, message]),
  });
  const wrap = buildPeriod({ name: 'length', value: 180, min: null, max: null }, ctx);
  const { input } = parts(wrap);

  // 追記されたような不正入力（1803h → 1803 × 1時間 = 上限超）で確定を試みる。
  input.value = '1803h';
  input.dispatch('blur');

  assert.equal(pending.at(-1)[0], 'length');
  assert.ok(pending.at(-1)[1], '未解決エラーとして登録される（OK 抑止）');
  assert.equal(ctx._values.length, 50, '値は代入されない（F-P1・直前の有効値のまま）');
});

test('入力を打ち直すとエラーは解除され OK 抑止も外れる', () => {
  const pending = [];
  const ctx = makeCtx({
    periodContext: PC_1H,
    setPendingError: (name, message) => pending.push([name, message]),
  });
  const wrap = buildPeriod({ name: 'length', value: 180, min: null, max: null }, ctx);
  const { input } = parts(wrap);
  input.value = '1803h';
  input.dispatch('blur');
  assert.ok(pending.at(-1)[1]);

  // 修正中（数値以外）でもエラー表示は消え、抑止が解ける。
  input.value = '3h';
  input.dispatch('input');
  assert.equal(pending.at(-1)[1], null, 'エラー解除が通知される');

  // 確定すると換算値が入る（1時間足の 3h = 3 本＝表 '1h'=1 の 3 倍）。
  input.dispatch('blur');
  assert.equal(ctx._values.length, 3);
  assert.equal(input.value, '3');
});
