// property_form_state.test.js — フォーム値所有と検証判定の単体検定（ISSUE-502 段階 4D）。
//
// 是正前は同じ規則が PropertiesDialog（829 行・DOM 構築と同居）の中にあり、実 DOM を組み立てないと
//   判定を確かめられなかった。DOM 非依存へ分けた結果、値と判定だけを直接固定できる。
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { PropertyFormState } from '../js/usecase/property_form_state.js';

// n（既定 10・最小 1）と mode（enum）を持つ最小 def。
const DEF = {
  id: 'x',
  params: [
    {
      name: 'n', type: 'int', default: 10,
      constraints: [{ kind: 'min_value', operands: ['n', 1] }],
    },
    {
      name: 'mode', type: 'enum', default: 'a', enumValues: ['a', 'b'],
    },
  ],
  compute: { variants: ['robust', 'global'] },
};

const make = (over = {}) => new PropertyFormState({ def: DEF, ...over });

test('初期値は params 優先 → 既定値フォールバック', () => {
  assert.deepEqual(make().values, { n: 10, mode: 'a' });
  assert.deepEqual(make({ params: { n: 42 } }).values, { n: 42, mode: 'a' });
});

test('値の所有: setValue / getValue / 入れ物の差し替え（遅延アクセサが最新を見る）', () => {
  const form = make();
  form.setValue('n', 3);
  assert.equal(form.getValue('n'), 3);
  // デフォルト復元は入れ物ごと差し替える。差し替え後も getValue は最新を返す。
  form.resetToDefaults();
  assert.equal(form.getValue('n'), 10);
  form.replaceValues({ n: 7, mode: 'b' });
  assert.equal(form.getValue('n'), 7);
  assert.equal(form.values.mode, 'b');
});

test('バリアント: 既定は variants[0]・指定があればそれ・評価コンテキストへ重なる', () => {
  assert.equal(make().variant, 'robust');
  assert.equal(make({ variant: 'global' }).variant, 'global');
  const form = make({ context: { timeframe: '1m' } });
  assert.deepEqual(form.evalContext(), { timeframe: '1m', variant: 'robust' });
  form.variant = 'global';
  assert.deepEqual(form.evalContext(), { timeframe: '1m', variant: 'global' });
});

test('検証: 違反があれば OK 不可・違反一覧に載る', () => {
  const form = make();
  assert.deepEqual(form.validation(), { violations: [], ok: true });
  form.setValue('n', 0); // min_value(1) 違反
  const { violations, ok } = form.validation();
  assert.equal(ok, false);
  assert.deepEqual(violations.map((v) => v.param), ['n']);
});

test('検証: 条件付き非表示のフィールドの違反は OK を阻害しない（トグル安全化）', () => {
  const def = {
    ...DEF,
    params: [
      { ...DEF.params[0], conditionalVisible: (values) => values.mode === 'a' },
      DEF.params[1],
    ],
  };
  const form = new PropertyFormState({ def });
  form.setValue('n', 0);
  assert.equal(form.validation().ok, false, 'mode=a のときは表示されているので阻害する');
  form.setValue('mode', 'b');
  assert.deepEqual(form.validation(), { violations: [], ok: true }, '隠れた欄の違反は阻害しない');
});

test('検証: 未解決の入力エラーが在席する間は OK 不可（解消で戻る）', () => {
  const form = make();
  form.setPendingError('n', '期間を解釈できません');
  assert.equal(form.pendingErrorCount, 1);
  assert.equal(form.validation().ok, false);
  form.setPendingError('n', null);
  assert.equal(form.pendingErrorCount, 0);
  assert.equal(form.validation().ok, true);
});

test('有効化・表示: 述語へ現在値と評価コンテキストが渡る', () => {
  const seen = [];
  const def = {
    ...DEF,
    params: [
      {
        ...DEF.params[0],
        conditionalEnable: (values, ctx) => { seen.push(ctx.timeframe); return values.mode === 'a'; },
        conditionalVisible: (values) => values.mode === 'a',
      },
      DEF.params[1],
    ],
  };
  const form = new PropertyFormState({ def, context: { timeframe: '5m' } });
  assert.deepEqual(form.enablement(), { n: true, mode: true });
  assert.deepEqual(form.visibility(), { n: true, mode: true });
  form.setValue('mode', 'b');
  assert.equal(form.enablement().n, false);
  assert.equal(form.visibility().n, false);
  assert.deepEqual([...new Set(seen)], ['5m'], '外部状態が述語へ届いていない');
});

// ISSUE-080: option 単位の有効化。判定だけを返し DOM へは触らない。
const OPTION_DEF = {
  id: 'y',
  params: [{
    name: 'src', type: 'enum', default: 'dwell', enumValues: ['dwell', 'zp'],
    optionEnable: (raw, values) => !(raw === 'zp' && values.mode === 'sessions'),
  }, { name: 'mode', type: 'enum', default: 'normal', enumValues: ['normal', 'sessions'] }],
  compute: { variants: ['default'] },
};

test('option 有効化: 無効な option を返し、選択中が無効なら先頭の有効値を指す', () => {
  const form = new PropertyFormState({ def: OPTION_DEF });
  const pdef = OPTION_DEF.params[0];
  // 通常モード: すべて有効・自動切替なし。
  assert.deepEqual(form.optionEnablement(pdef, ['dwell', 'zp']), {
    enabled: [true, true], firstEnabled: 'dwell', currentDisabled: false,
  });
  // 日別モード＋選択中が zp: zp が無効になり、切替先として dwell を指す。
  form.setValue('mode', 'sessions');
  form.setValue('src', 'zp');
  assert.deepEqual(form.optionEnablement(pdef, ['dwell', 'zp']), {
    enabled: [true, false], firstEnabled: 'dwell', currentDisabled: true,
  });
  // 選択中が有効なら自動切替は起きない（ユーザー操作を尊重）。
  form.setValue('src', 'dwell');
  assert.equal(form.optionEnablement(pdef, ['dwell', 'zp']).currentDisabled, false);
});

test('option 有効化: 表示値から enum の元型（数値）へ復元して述語へ渡す', () => {
  const seen = [];
  const def = {
    id: 'z',
    params: [{
      name: 'k', type: 'enum', default: 1, enumValues: [1, 2],
      optionEnable: (raw) => { seen.push(raw); return raw !== 2; },
    }],
    compute: { variants: ['default'] },
  };
  const form = new PropertyFormState({ def });
  const out = form.optionEnablement(def.params[0], ['1', '2']);
  assert.deepEqual(seen, [1, 2], '文字列のまま述語へ渡している（元型が失われる）');
  assert.deepEqual(out, { enabled: [true, false], firstEnabled: 1, currentDisabled: false });
});
