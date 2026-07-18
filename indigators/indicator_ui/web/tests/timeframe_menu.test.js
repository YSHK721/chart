// ISSUE-117/122/123: 時間足ドロップダウン（TimeframeMenu）と syncButtons ラベル同期の回帰検証。
//
// 仕様: メニュー DOM（トリガー＋カテゴリ＋項目）はコンポーネントが生成する（ISSUE-123: index.html
//   直書きの値渡し複製を廃止・空マウント #tf-menu へ生成）。項目集合は groups 注入（既定＝present 9 足）。
//   トリガークリックで開閉・項目（data-timeframe）クリックで閉じる・外側クリックで閉じる。
//   選択実行と is-active 同期は既存の bind()/syncButtons 機構（[data-timeframe] 一括配線）に委譲。
//   トリガーラベルは syncButtons が現在足要素の表記へ更新（_el.timeframeMenuLabel）。
// 構造: Arrange-Act-Assert（AAA）。jsdom を避けた最小 DOM スタブ（C-2）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { TimeframeMenu } from '../js/adapter/front/timeframe_menu.js';
import { TimeframeController } from '../js/adapter/front/timeframe_controller.js';

function fakeEl() {
  const el = {
    id: '', className: '', textContent: '', title: '', type: '',
    dataset: {}, children: [],
    _handlers: {},
    _cls: new Set(),
    classList: {
      toggle(c, on) {
        const has = el._cls.has(c);
        const next = on === undefined ? !has : on;
        if (next) el._cls.add(c); else el._cls.delete(c);
      },
      contains(c) { return el._cls.has(c); },
    },
    append(...kids) { el.children.push(...kids); },
    appendChild(kid) { el.children.push(kid); },
    addEventListener(ev, fn) { el._handlers[ev] = fn; },
    fire(ev, arg) { if (el._handlers[ev]) el._handlers[ev](arg); },
  };
  // className への代入で is-hidden 等の初期クラスを _cls にも反映する（実 DOM の挙動を最小模倣）。
  return new Proxy(el, {
    set(target, prop, value) {
      if (prop === 'className' && typeof value === 'string') {
        target._cls = new Set(value.split(/\s+/).filter(Boolean));
        target.classList.toggle = (c, on) => {
          const has = target._cls.has(c);
          const next = on === undefined ? !has : on;
          if (next) target._cls.add(c); else target._cls.delete(c);
        };
        target.classList.contains = (c) => target._cls.has(c);
      }
      target[prop] = value;
      return true;
    },
  });
}

function buildMenu({ groups } = {}) {
  const mount = fakeEl();
  const docHandlers = {};
  const doc = {
    createElement: () => fakeEl(),
    getElementById: (id) => (id === 'tf-menu' ? mount : null),
    addEventListener: (ev, fn) => { docHandlers[ev] = fn; },
  };
  new TimeframeMenu({ document: doc, groups }).install();
  const trigger = mount.children[0];
  const pop = mount.children[1];
  return { mount, trigger, pop, docHandlers };
}

test('ISSUE-123 DOM生成: マウントへトリガー（#tf-menu-trigger/#tf-menu-label）とポップを生成する', () => {
  const { mount, trigger, pop } = buildMenu();
  assert.equal(mount.children.length, 2);
  assert.equal(trigger.id, 'tf-menu-trigger');
  assert.equal(trigger.children[0].id, 'tf-menu-label', 'ラベル span（syncButtons が更新）');
  assert.equal(pop.id, 'tf-menu-pop');
  assert.equal(pop.classList.contains('is-hidden'), true, '既定は閉');
});

test('ISSUE-123 DOM生成: 既定 groups は present 9 足（30m 含む）・項目は data-timeframe を持つ', () => {
  const { pop } = buildMenu();
  const items = pop.children.filter((c) => c.className === 'tf-menu-item');
  assert.deepEqual(items.map((i) => i.dataset.timeframe),
    ['1m', '5m', '15m', '30m', '1h', '4h', '1D', '1W', '1M']);
  const cats = pop.children.filter((c) => c.className === 'tf-menu-cat').map((c) => c.textContent);
  assert.deepEqual(cats, ['分', '時間', '日']);
});

test('ISSUE-123 groups 注入: replay の 8 足（30m なし）を生成できる（値渡しでなく設定注入）', () => {
  const { pop } = buildMenu({
    groups: [
      { cat: '分', items: [['1m', '1分'], ['5m', '5分'], ['15m', '15分']] },
      { cat: '時間', items: [['1h', '1時間'], ['4h', '4時間']] },
      { cat: '日', items: [['1D', '日'], ['1W', '週'], ['1M', '月']] },
    ],
  });
  const items = pop.children.filter((c) => c.className === 'tf-menu-item');
  assert.deepEqual(items.map((i) => i.dataset.timeframe),
    ['1m', '5m', '15m', '1h', '4h', '1D', '1W', '1M']);
});

test('ISSUE-117 開閉: トリガークリックでトグル（既定は閉）', () => {
  const { trigger, pop } = buildMenu();
  trigger.fire('click', { stopPropagation() {} });
  assert.equal(pop.classList.contains('is-hidden'), false, '1 回目で開く');
  trigger.fire('click', { stopPropagation() {} });
  assert.equal(pop.classList.contains('is-hidden'), true, '2 回目で閉じる');
});

test('ISSUE-117 項目選択: data-timeframe を持つ要素のクリックで閉じる（選択実行は bind() 側）', () => {
  const { trigger, pop } = buildMenu();
  trigger.fire('click', { stopPropagation() {} });
  pop.fire('click', { target: { dataset: { timeframe: '1h' } } });
  assert.equal(pop.classList.contains('is-hidden'), true);
});

test('ISSUE-117 項目以外のクリック（カテゴリ見出し等）では閉じない', () => {
  const { trigger, pop } = buildMenu();
  trigger.fire('click', { stopPropagation() {} });
  pop.fire('click', { target: { dataset: {} } });
  assert.equal(pop.classList.contains('is-hidden'), false);
});

test('ISSUE-117 外側クリック: document クリックで閉じる', () => {
  const { trigger, pop, docHandlers } = buildMenu();
  trigger.fire('click', { stopPropagation() {} });
  docHandlers.click();
  assert.equal(pop.classList.contains('is-hidden'), true);
});

test('ISSUE-117 防御: DOM 不在・マウント欠落でも install は例外を投げない', () => {
  assert.doesNotThrow(() => new TimeframeMenu({ document: null }).install());
  assert.doesNotThrow(() => new TimeframeMenu({
    document: { createElement: () => ({}), getElementById: () => null },
  }).install());
});

// ---- syncButtons のトリガーラベル同期 ---------------------------------------

function labelHost(timeframe) {
  const mk = (tf, text) => ({
    dataset: { timeframe: tf }, textContent: text,
    classList: { toggle() {} },
  });
  return {
    _timeframe: timeframe,
    _el: {
      timeframeBtns: [mk('1m', '1分'), mk('1h', '1時間'), mk('1D', '日')],
      timeframeMenuLabel: { textContent: '' },
    },
  };
}

test('ISSUE-117 syncButtons: トリガーラベルを現在足の表記へ更新する', () => {
  const host = labelHost('1h');
  new TimeframeController(host).syncButtons();
  assert.equal(host._el.timeframeMenuLabel.textContent, '1時間');
  host._timeframe = '1D';
  new TimeframeController(host).syncButtons();
  assert.equal(host._el.timeframeMenuLabel.textContent, '日');
});

test('ISSUE-117 syncButtons: ラベル要素不在（旧 DOM）は従来どおり例外なく同期のみ', () => {
  const host = labelHost('1m');
  delete host._el.timeframeMenuLabel;
  assert.doesNotThrow(() => new TimeframeController(host).syncButtons());
});
