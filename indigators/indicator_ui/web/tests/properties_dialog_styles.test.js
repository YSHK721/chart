// ISSUE-109: スタイル/可視性タブの実機能化の回帰検証（node:test / node:assert）。
//
// 対象:
//   - PropertiesDialog._seriesRows: 実描画系列（seriesStyles）からの行構築・bucket 粒度畳み込み
//   - PropertiesDialog._buildStylePane/_buildVisibilityPane: 初期値が実描画値
//   - PropertiesDialog._collectStyleChanges: 変更差分のみの patch 化（bucket 行は全系列へ展開）
//   - PropertiesDialog(seriesTabs:false): スタイル/可視性タブ自体を出さない（MP）
//   - facade.setSeriesStyles: styles マージ・serialize/deserialize 往復
// 構造: Arrange-Act-Assert（AAA）。jsdom を避けた最小 DOM スタブ（C-2）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { PropertiesDialog } from '../js/adapter/front/properties_dialog.js';
import {
  emptyState, apply, setSeriesStyles, serialize, deserialize,
} from '../js/usecase/facade.js';

// 最小 DOM スタブ（createElement した要素が value/checked/append/addEventListener を持つ）。
function styleFakeDoc() {
  const make = () => ({
    className: '', dataset: {}, textContent: '', type: '', value: '', min: '', step: '',
    checked: false, selected: false, disabled: false, style: {},
    children: [],
    append(...kids) { this.children.push(...kids); },
    addEventListener() {},
  });
  return { createElement: make };
}

const STATIC_DEF = {
  id: 'moving_averages',
  displayNameKey: 'ind.ma',
  params: [],
  series: [
    { seriesName: 'MA', dynamic: false },
    { seriesName: 'Smoothing', dynamic: false },
  ],
  compute: { variants: ['default'] },
};

const BAND_DEF = {
  id: 'profit_band',
  displayNameKey: 'ind.profit_band',
  params: [],
  series: [
    {
      seriesName: null,
      dynamic: true,
      seriesNamePattern: { template: '{bucket} {pct}%', buckets: ['nOH', 'pOL'], pcts: ['80', '95'] },
    },
  ],
  compute: { variants: ['default'] },
};

// 実描画系列スタイル（renderer.getSeriesStyles の戻り相当）。
const MA_STYLES = [
  { name: 'MA', kind: 'line', color: '#2962ff', width: 1, style: 'solid', visible: true },
  { name: 'Smoothing', kind: 'line', color: '#00aa00', width: 2, style: 'dotted', visible: true },
];

test('ISSUE-109 _seriesRows: 実描画系列から 1 系列=1 行を構築し初期値は実描画値', () => {
  const dialog = new PropertiesDialog({ document: styleFakeDoc(), def: STATIC_DEF, seriesStyles: MA_STYLES });
  const rows = dialog._seriesRows();
  assert.equal(rows.length, 2);
  assert.deepEqual(rows[0], { label: 'MA', names: ['MA'], kind: 'line', heat: false, color: '#2962ff', width: 1, style: 'solid', visible: true, display: null, pointStyleEditable: false, barStyleEditable: false });
  assert.deepEqual(rows[1].names, ['Smoothing']);
  assert.equal(rows[1].color, '#00aa00');
  assert.equal(rows[1].style, 'dotted');
});

test('ISSUE-109 _seriesRows: bucket 規則系列（profit_band）は系統粒度へ畳む（仕様 §6.1）', () => {
  const bandStyles = [
    { name: 'nOH 80%', kind: 'line', color: '#111111', width: 1, style: 'solid', visible: true },
    { name: 'nOH 95%', kind: 'line', color: '#222222', width: 1, style: 'dotted', visible: true },
    { name: 'pOL 80%', kind: 'line', color: '#333333', width: 1, style: 'solid', visible: true },
  ];
  const dialog = new PropertiesDialog({ document: styleFakeDoc(), def: BAND_DEF, seriesStyles: bandStyles });
  const rows = dialog._seriesRows();
  assert.equal(rows.length, 2, 'bucket ごとに 1 行（nOH / pOL）');
  assert.deepEqual(rows[0].names, ['nOH 80%', 'nOH 95%']);
  assert.deepEqual(rows[1].names, ['pOL 80%']);
});

test('ISSUE-109 スタイルタブ初期値: 入力要素へ実描画値が展開される（プレースホルダ #2962ff 固定の是正）', () => {
  const dialog = new PropertiesDialog({ document: styleFakeDoc(), def: STATIC_DEF, seriesStyles: MA_STYLES });
  dialog._buildStylePane();
  assert.equal(dialog._styleState.length, 2);
  assert.equal(dialog._styleState[1].color.value, '#00aa00');
  assert.equal(dialog._styleState[1].width.value, '2');
  assert.equal(dialog._styleState[1].style.value, 'dotted');
});

test('ISSUE-109 _collectStyleChanges: 変更された行×フィールドのみ patch 化（無変更は空）', () => {
  const dialog = new PropertiesDialog({ document: styleFakeDoc(), def: STATIC_DEF, seriesStyles: MA_STYLES });
  dialog._buildStylePane();
  dialog._buildVisibilityPane();
  // 無変更 → 空 patch
  assert.deepEqual(dialog._collectStyleChanges(), {});
  // MA の色と線種のみ変更
  dialog._styleState[0].color.value = '#ff0000';
  dialog._styleState[0].style.value = 'dashed';
  const patch = dialog._collectStyleChanges();
  assert.deepEqual(patch, { MA: { color: '#ff0000', style: 'dashed' } });
});

test('ISSUE-109 _collectStyleChanges: bucket 行の変更は全構成系列へ展開される', () => {
  const bandStyles = [
    { name: 'nOH 80%', kind: 'line', color: '#111111', width: 1, style: 'solid', visible: true },
    { name: 'nOH 95%', kind: 'line', color: '#222222', width: 1, style: 'dotted', visible: true },
  ];
  const dialog = new PropertiesDialog({ document: styleFakeDoc(), def: BAND_DEF, seriesStyles: bandStyles });
  dialog._buildStylePane();
  dialog._styleState[0].width.value = '3';
  const patch = dialog._collectStyleChanges();
  assert.deepEqual(patch, {
    'nOH 80%': { width: 3 },
    'nOH 95%': { width: 3 },
  });
});

test('ISSUE-109 可視性タブ: チェック変更が visible として patch に載る（初期は実可視状態）', () => {
  const styles = [
    { name: 'MA', kind: 'line', color: '#2962ff', width: 1, style: 'solid', visible: true },
    { name: 'Smoothing', kind: 'line', color: '#00aa00', width: 2, style: 'dotted', visible: false },
  ];
  const dialog = new PropertiesDialog({ document: styleFakeDoc(), def: STATIC_DEF, seriesStyles: styles });
  dialog._buildStylePane();
  dialog._buildVisibilityPane();
  assert.equal(dialog._visibilityState[0].checkbox.checked, true);
  assert.equal(dialog._visibilityState[1].checkbox.checked, false, '非表示系列は初期 unchecked');
  dialog._visibilityState[0].checkbox.checked = false;
  assert.deepEqual(dialog._collectStyleChanges(), { MA: { visible: false } });
});

test('ISSUE-109 _onOkClick: onApply 第3引数に styles 差分を渡す（後方互換: 第1/第2引数は従来どおり）', () => {
  let got = null;
  const dialog = new PropertiesDialog({
    document: styleFakeDoc(),
    def: STATIC_DEF,
    seriesStyles: MA_STYLES,
    onApply: (values, variant, extra) => { got = { values, variant, extra }; },
  });
  dialog._buildStylePane();
  dialog._buildVisibilityPane();
  dialog._styleState[0].color.value = '#ff0000';
  dialog._revalidate = () => true; // OK ガードを通す（フォーム検証は対象外）
  dialog.close = () => {};
  dialog._onOkClick();
  assert.ok(got);
  assert.equal(got.variant, 'default');
  assert.deepEqual(got.extra, { styles: { MA: { color: '#ff0000' } } });
});

test('ISSUE-109 seriesTabs:false（MP）: タブバーはパラメーターのみ・スタイル/可視性ペインを生成しない', () => {
  const dialog = new PropertiesDialog({ document: styleFakeDoc(), def: STATIC_DEF, seriesTabs: false });
  const bar = dialog._buildTabBar();
  assert.equal(bar.children.length, 1, 'タブはパラメーターのみ');
  assert.equal(bar.children[0].dataset.propTab, 'inputs');
  dialog._buildBody();
  assert.equal(dialog._panes.has('style'), false);
  assert.equal(dialog._panes.has('visibility'), false);
});

test('ISSUE-109 seriesStyles 空（hline のみ指標）: スタイルタブは空注記を出し行を作らない', () => {
  const dialog = new PropertiesDialog({ document: styleFakeDoc(), def: STATIC_DEF, seriesStyles: [] });
  const pane = dialog._buildStylePane();
  assert.equal(dialog._styleState.length, 0);
  assert.equal(pane.children.length, 1);
  assert.match(pane.children[0].textContent, /スタイル編集可能な系列はありません/);
});

// ---- facade.setSeriesStyles / 永続化 ---------------------------------------

const FAKE_GATEWAY = { compute: async ({ generation }) => ({ generation }) };

async function stateWithOneInstance() {
  const { state, instance } = await apply(
    emptyState(),
    { indicatorId: 'moving_averages', variant: 'default', params: {}, datasetRef: 'jp225' },
    FAKE_GATEWAY,
  );
  return { state, instance };
}

test('ISSUE-109 facade.setSeriesStyles: 差分をフィールド単位でマージした新 state を返す', async () => {
  const { state, instance } = await stateWithOneInstance();
  const s1 = setSeriesStyles(state, instance.instanceId, { MA: { color: '#ff0000' } });
  const s2 = setSeriesStyles(s1, instance.instanceId, { MA: { width: 3 }, Upper: { visible: false } });
  const inst = s2.applied.find((i) => i.instanceId === instance.instanceId);
  assert.deepEqual(inst.styles, {
    MA: { color: '#ff0000', width: 3 },
    Upper: { visible: false },
  });
  // 元 state は不変（不変データ規律）
  assert.equal(state.applied[0].styles, null);
});

test('ISSUE-109 facade.setSeriesStyles: 空 patch は state を変更しない', async () => {
  const { state, instance } = await stateWithOneInstance();
  assert.equal(setSeriesStyles(state, instance.instanceId, {}), state);
  assert.equal(setSeriesStyles(state, instance.instanceId, null), state);
});

test('ISSUE-109 永続化: styles が serialize/deserialize を往復して保存・復元される', async () => {
  const { state, instance } = await stateWithOneInstance();
  const styled = setSeriesStyles(state, instance.instanceId, { MA: { color: '#ff0000', style: 'dashed' } });
  const restored = deserialize(serialize(styled));
  const inst = restored.applied.find((i) => i.instanceId === instance.instanceId);
  assert.deepEqual(inst.styles, { MA: { color: '#ff0000', style: 'dashed' } });
});

test('案A 永続化: display（ドット/ライン）patch が serialize/deserialize を往復する', async () => {
  const { state, instance } = await stateWithOneInstance();
  const styled = setSeriesStyles(state, instance.instanceId, { btlm_trail_mean: { display: 'line' } });
  const restored = deserialize(serialize(styled));
  const inst = restored.applied.find((i) => i.instanceId === instance.instanceId);
  assert.deepEqual(inst.styles, { btlm_trail_mean: { display: 'line' } });
  // 世代前進（recompute→redraw→_applyStoredStyles）でも display は引き継がれる。
  assert.deepEqual(inst.nextGeneration().styles, { btlm_trail_mean: { display: 'line' } });
});

test('ISSUE-109 永続化: styles 無しの旧データも styles:null で復元される（後方互換）', async () => {
  const { state } = await stateWithOneInstance();
  const json = serialize(state);
  // 旧スキーマ相当（styles キー無し）を合成
  const parsed = JSON.parse(json);
  for (const i of parsed.applied) {
    delete i.styles;
  }
  const restored = deserialize(JSON.stringify(parsed));
  assert.equal(restored.applied[0].styles, null);
});

test('ISSUE-109 世代前進: nextGeneration（recompute 経路）でも styles が引き継がれる', async () => {
  const { state, instance } = await stateWithOneInstance();
  const styled = setSeriesStyles(state, instance.instanceId, { MA: { color: '#ff0000' } });
  const inst = styled.applied[0];
  const advanced = inst.nextGeneration();
  assert.deepEqual(advanced.styles, { MA: { color: '#ff0000' } });
  assert.equal(advanced.generation, inst.generation + 1);
});

// ---- ISSUE-110: buildSeriesStyleRows（usecase 純関数）/ reconcileSeriesStyles -----

import { buildSeriesStyleRows } from '../js/usecase/form_model.js';
import { reconcileSeriesStyles } from '../js/usecase/facade.js';

test('ISSUE-110 buildSeriesStyleRows: bucket 接頭辞は pattern.template から導出される（書式ハードコード排除）', () => {
  // template を '{bucket}_{pct}' に変えても（空白区切りでなくても）正しく畳まれること。
  const def = {
    series: [{
      dynamic: true,
      seriesNamePattern: { template: '{bucket}_{pct}', buckets: ['up', 'dn'] },
    }],
  };
  const styles = [
    { name: 'up_80', color: '#111111', width: 1, style: 'solid', visible: true },
    { name: 'up_95', color: '#222222', width: 1, style: 'solid', visible: true },
    { name: 'dn_80', color: '#333333', width: 1, style: 'solid', visible: true },
  ];
  const rows = buildSeriesStyleRows(def, styles);
  assert.equal(rows.length, 2);
  assert.deepEqual(rows[0].names, ['up_80', 'up_95']);
  assert.deepEqual(rows[1].names, ['dn_80']);
});

test('ISSUE-110 buildSeriesStyleRows: 空 bucket（tgp 型 template）は畳まず 1 系列=1 行', () => {
  const def = {
    series: [{
      dynamic: true,
      seriesNamePattern: { template: 'btlm_q{pct}', buckets: [''] },
    }],
  };
  const styles = [
    { name: 'btlm_q5', color: '#111111', width: 1, style: 'solid', visible: true },
    { name: 'btlm_q95', color: '#222222', width: 1, style: 'solid', visible: true },
  ];
  const rows = buildSeriesStyleRows(def, styles);
  assert.equal(rows.length, 2);
  assert.deepEqual(rows.map((r) => r.label), ['btlm_q5', 'btlm_q95']);
});

test('ISSUE-110 reconcileSeriesStyles: 実系列に無い stale キーを剪定し、全滅なら styles=null', async () => {
  const { state, instance } = await stateWithOneInstance();
  const styled = setSeriesStyles(state, instance.instanceId, {
    'btlm_q5': { color: '#ff0000' }, 'btlm_q95': { color: '#00ff00' },
  });
  // q_high 変更で btlm_q95 → btlm_q90 に改名された想定
  const pruned = reconcileSeriesStyles(styled, instance.instanceId, ['btlm_q5', 'btlm_q90', 'btlm_mean']);
  const inst = pruned.applied[0];
  assert.deepEqual(inst.styles, { 'btlm_q5': { color: '#ff0000' } }, 'stale キー btlm_q95 のみ剪定');
  // 全キー stale → styles=null へ戻る
  const all = reconcileSeriesStyles(styled, instance.instanceId, ['other']);
  assert.equal(all.applied[0].styles, null);
});

test('ISSUE-110 reconcileSeriesStyles: 実系列集合が空/未知 instance/styles 無しは無変更（同一 state）', async () => {
  const { state, instance } = await stateWithOneInstance();
  const styled = setSeriesStyles(state, instance.instanceId, { MA: { color: '#ff0000' } });
  assert.equal(reconcileSeriesStyles(styled, instance.instanceId, []), styled, '空集合は判定不能＝剪定しない');
  assert.equal(reconcileSeriesStyles(styled, 'unknown#9', ['MA']), styled);
  assert.equal(reconcileSeriesStyles(state, instance.instanceId, ['MA']), state, 'styles 無しは無変更');
  // stale 無し（全キー実在）も同一 state
  assert.equal(reconcileSeriesStyles(styled, instance.instanceId, ['MA']), styled);
});

// ---- ISSUE-111: histogram 系列（ADXNeedle 等）は色のみ編集可（線幅/線種を出さない） ----

test('ISSUE-111 スタイルタブ: histogram 行は色のみ・線幅/線種入力を生成しない', () => {
  const def = { id: 'profit_adx_needle', displayNameKey: 'ADXNeedle', params: [], series: [], compute: { variants: ['default'] } };
  const styles = [
    { name: 'adx_needle', kind: 'histogram', color: '#888888', width: null, style: null, visible: true },
  ];
  const dialog = new PropertiesDialog({ document: styleFakeDoc(), def, seriesStyles: styles });
  dialog._buildStylePane();
  assert.equal(dialog._styleState.length, 1);
  const s = dialog._styleState[0];
  assert.ok(s.color, '色入力はある');
  assert.equal(s.width, null, '線幅入力は生成しない');
  assert.equal(s.style, null, '線種入力は生成しない');
});

test('ISSUE-111 _collectStyleChanges: histogram 行は色のみ patch 化（width/style 無しで例外なし）', () => {
  const def = { id: 'profit_adx_needle', displayNameKey: 'ADXNeedle', params: [], series: [], compute: { variants: ['default'] } };
  const styles = [
    { name: 'adx_needle', kind: 'histogram', color: '#888888', width: null, style: null, visible: true },
  ];
  const dialog = new PropertiesDialog({ document: styleFakeDoc(), def, seriesStyles: styles });
  dialog._buildStylePane();
  dialog._buildVisibilityPane();
  assert.deepEqual(dialog._collectStyleChanges(), {}, '無変更は空');
  dialog._styleState[0].color.value = '#ff0000';
  assert.deepEqual(dialog._collectStyleChanges(), { adx_needle: { color: '#ff0000' } });
});

test('ISSUE-111 混在指標: line 行には線幅/線種・histogram 行には色のみ（行単位で出し分け）', () => {
  const def = { id: 'x', displayNameKey: 'x', params: [], series: [], compute: { variants: ['default'] } };
  const styles = [
    { name: 'osc', kind: 'histogram', color: '#888888', width: null, style: null, visible: true },
    { name: 'signal', kind: 'line', color: '#2962ff', width: 1, style: 'solid', visible: true },
  ];
  const dialog = new PropertiesDialog({ document: styleFakeDoc(), def, seriesStyles: styles });
  dialog._buildStylePane();
  assert.equal(dialog._styleState[0].width, null);
  assert.equal(dialog._styleState[0].style, null);
  assert.ok(dialog._styleState[1].width, 'line 行は線幅あり');
  assert.ok(dialog._styleState[1].style, 'line 行は線種あり');
});

test('ISSUE-111 buildSeriesStyleRows: 行に kind を保持する（bucket 行は先頭系列の kind）', () => {
  const rows = buildSeriesStyleRows(
    { series: [] },
    [{ name: 'adx_needle', kind: 'histogram', color: '#888888', visible: true }],
  );
  assert.equal(rows[0].kind, 'histogram');
});

// ---- ISSUE-112: ヒート配色 histogram は色ピッカーも出さない（ヒート絶対優先） -------

test('ISSUE-112 スタイルタブ: heat histogram 行は色ピッカーなし・「ヒート配色（自動）」を明示', () => {
  const def = { id: 'profit_adx_needle', displayNameKey: 'ADXNeedle', params: [], series: [], compute: { variants: ['default'] } };
  const styles = [
    { name: 'adx_needle', kind: 'histogram', heat: true, color: '#006400', width: null, style: null, visible: true },
  ];
  const dialog = new PropertiesDialog({ document: styleFakeDoc(), def, seriesStyles: styles });
  const pane = dialog._buildStylePane();
  const s = dialog._styleState[0];
  assert.equal(s.color, null, '色入力を生成しない');
  assert.equal(s.width, null);
  assert.equal(s.style, null);
  const row = pane.children[0];
  const note = row.children.find((c) => c.className === 'prop-style-heat');
  assert.ok(note, 'ヒート注記あり');
  assert.equal(note.textContent, 'ヒート配色（自動）');
  // 色変更の収集も発生しない（全入力 null で例外なく空 patch）
  assert.deepEqual(dialog._collectStyleChanges(), {});
});

test('ISSUE-112 非 heat histogram 行は従来どおり色のみ編集可', () => {
  const def = { id: 'x', displayNameKey: 'x', params: [], series: [], compute: { variants: ['default'] } };
  const styles = [
    { name: 'flat', kind: 'histogram', heat: false, color: '#888888', width: null, style: null, visible: true },
  ];
  const dialog = new PropertiesDialog({ document: styleFakeDoc(), def, seriesStyles: styles });
  dialog._buildStylePane();
  const s = dialog._styleState[0];
  assert.ok(s.color, '色入力あり');
  assert.equal(s.width, null);
  assert.equal(s.style, null);
  s.color.value = '#ff0000';
  assert.deepEqual(dialog._collectStyleChanges(), { flat: { color: '#ff0000' } });
});

test('ISSUE-112 buildSeriesStyleRows: heat フラグを行へ伝搬する', () => {
  const rows = buildSeriesStyleRows(
    { series: [] },
    [{ name: 'adx_needle', kind: 'histogram', heat: true, color: '#006400', visible: true }],
  );
  assert.equal(rows[0].heat, true);
});

test('ISSUE-112 heat histogram でも可視性タブのチェックは有効（visible patch は出る）', () => {
  const def = { id: 'profit_adx_needle', displayNameKey: 'ADXNeedle', params: [], series: [], compute: { variants: ['default'] } };
  const styles = [
    { name: 'adx_needle', kind: 'histogram', heat: true, color: '#006400', width: null, style: null, visible: true },
  ];
  const dialog = new PropertiesDialog({ document: styleFakeDoc(), def, seriesStyles: styles });
  dialog._buildStylePane();
  dialog._buildVisibilityPane();
  dialog._visibilityState[0].checkbox.checked = false;
  assert.deepEqual(dialog._collectStyleChanges(), { adx_needle: { visible: false } });
});

// ---- 案A（btlm_trail）: buildSeriesStyleRows の display 伝搬 + pointStyleEditable ゲート ----
test('案A buildSeriesStyleRows: display を styleMeta から行へ伝搬する', () => {
  const rows = buildSeriesStyleRows(
    { series: [] },
    [{ name: 'btlm_trail_mean', kind: 'line', color: '#7b68ee', width: 2, style: 'solid', visible: true, display: 'dots' }],
  );
  assert.equal(rows[0].display, 'dots');
});

test('案A buildSeriesStyleRows: pointStyleEditable は SeriesDef 一致（静的/動的）で解決・未付与は false', () => {
  const def = { series: [
    { seriesName: 'btlm_trail_mean', dynamic: false, pointStyleEditable: true },
    { dynamic: true, seriesNamePattern: { template: 'btlm_trail_q{pct}', buckets: [''], pcts: ['5', '95'] }, pointStyleEditable: true },
    { seriesName: 'btlm_trail_beta', dynamic: false, pointStyleEditable: false },
  ] };
  const styles = [
    { name: 'btlm_trail_mean', kind: 'line', color: '#7b68ee', visible: true, display: 'dots' },
    { name: 'btlm_trail_q5', kind: 'line', color: '#7b68ee', visible: true, display: 'dots' },
    { name: 'btlm_trail_beta', kind: 'line', color: '#aaa', visible: true, display: null },
  ];
  const rows = buildSeriesStyleRows(def, styles);
  const byName = Object.fromEntries(rows.map((r) => [r.names[0], r]));
  assert.equal(byName.btlm_trail_mean.pointStyleEditable, true);   // 静的一致
  assert.equal(byName.btlm_trail_q5.pointStyleEditable, true);     // 動的パターン一致
  assert.equal(byName.btlm_trail_beta.pointStyleEditable, false);  // フラグ false
});

test('案A buildSeriesStyleRows: フラグ未付与指標（他指標）は pointStyleEditable=false（非波及）', () => {
  const def = { series: [{ seriesName: 'MA', dynamic: false }] }; // pointStyleEditable 未指定
  const rows = buildSeriesStyleRows(def, [{ name: 'MA', kind: 'line', color: '#2962ff', visible: true }]);
  assert.equal(rows[0].pointStyleEditable, false);
});

// ---- 案A（btlm_trail）: スタイルタブの「系列表示（ドット/ライン）」ゲート付きコントロール ----
const TRAIL_DEF = {
  id: 'btlm_trail', displayNameKey: 'ind.btlm_trail', params: [],
  series: [
    { seriesName: 'btlm_trail_mean', dynamic: false, kind: 'line', pointStyleEditable: true },
    { seriesName: 'btlm_trail_beta', dynamic: false, kind: 'line' },
  ],
  compute: { variants: ['default'] },
};
const TRAIL_STYLES = [
  { name: 'btlm_trail_mean', kind: 'line', color: '#7b68ee', width: 2, style: 'solid', visible: true, display: 'dots' },
  { name: 'btlm_trail_beta', kind: 'line', color: '#a0a0a0', width: 1, style: 'solid', visible: true, display: null },
];

test('案A統合: pointStyleEditable 系列は 4 択 unified（dot/solid/dotted/dashed）で初期値=dot・別体 style/display は無し', () => {
  const dialog = new PropertiesDialog({ document: styleFakeDoc(), def: TRAIL_DEF, seriesStyles: TRAIL_STYLES });
  dialog._buildStylePane();
  const mean = dialog._styleState.find((s) => s.names[0] === 'btlm_trail_mean');
  const beta = dialog._styleState.find((s) => s.names[0] === 'btlm_trail_beta');
  // 対象系列: 統合 4 択のみ（別体 style/display コントロールは持たない＝1 行構成）。
  assert.ok(mean.unified, 'mean は統合 4 択コントロールを持つ');
  assert.equal(mean.unified.value, 'dot', '既定は dot（display=dots 由来）');
  assert.equal(mean.initial.unified, 'dot');
  assert.equal(mean.style, null, '別体の線種 select は無い');
  assert.ok(!mean.display, '別体の display select は無い');
  // 未付与系列（beta）: 従来の 3 択 style（unified なし）。
  assert.equal(beta.unified, null);
  assert.ok(beta.style, 'beta は従来の 3 択 style');
});

test('案A統合 _collectStyleChanges: dot→dotted は {display:line, style:dotted}、→dot は {display:dots}', () => {
  const dialog = new PropertiesDialog({ document: styleFakeDoc(), def: TRAIL_DEF, seriesStyles: TRAIL_STYLES });
  dialog._buildStylePane();
  dialog._buildVisibilityPane();
  assert.deepEqual(dialog._collectStyleChanges(), {});
  const mean = dialog._styleState.find((s) => s.names[0] === 'btlm_trail_mean');
  mean.unified.value = 'dotted';
  assert.deepEqual(dialog._collectStyleChanges(), { btlm_trail_mean: { display: 'line', style: 'dotted' } });
});

test('案A統合 _collectStyleChanges: 線種→dot は {display:dots}（線種は保持・display のみ）', () => {
  const styles = [{ name: 'btlm_trail_mean', kind: 'line', color: '#7b68ee', width: 2, style: 'dashed', visible: true, display: 'line' }];
  const def = { id: 'btlm_trail', params: [], series: [{ seriesName: 'btlm_trail_mean', dynamic: false, kind: 'line', pointStyleEditable: true }], compute: { variants: ['default'] } };
  const dialog = new PropertiesDialog({ document: styleFakeDoc(), def, seriesStyles: styles });
  dialog._buildStylePane();
  dialog._buildVisibilityPane();
  const mean = dialog._styleState[0];
  assert.equal(mean.unified.value, 'dashed', '初期は dashed（display=line, style=dashed 由来）');
  mean.unified.value = 'dot';
  assert.deepEqual(dialog._collectStyleChanges(), { btlm_trail_mean: { display: 'dots' } });
});

// ==========================================================================
// 案A（MAROD 棒グラフ）: barStyleEditable ゲート + 統合 select の 'bar' 追加
// ==========================================================================

// MAROD line 相当（barStyleEditable=true・pointStyleEditable=false）の def/styles。
const MAROD_DEF = {
  id: 'btlm_trail_marod', displayNameKey: 'ind.btlm_trail_marod', params: [],
  series: [
    { seriesName: 'btlm_trail_marod', dynamic: false, kind: 'line', barStyleEditable: true },
  ],
  compute: { variants: ['default'] },
};
const MAROD_STYLES = [
  { name: 'btlm_trail_marod', kind: 'line', color: '#7b68ee', width: 2, style: 'solid', visible: true, display: null },
];

test('案A(MAROD) buildSeriesStyleRows: barStyleEditable を SeriesDef 一致（静的）で解決・未付与は false', () => {
  const rows = buildSeriesStyleRows(MAROD_DEF, MAROD_STYLES);
  assert.equal(rows[0].barStyleEditable, true, '静的 seriesName 一致で棒編集可');
  // 未付与指標（他指標）は barStyleEditable=false（非波及）。
  const other = buildSeriesStyleRows(
    { series: [{ seriesName: 'MA', dynamic: false }] },
    [{ name: 'MA', kind: 'line', color: '#2962ff', visible: true }],
  );
  assert.equal(other[0].barStyleEditable, false);
  // pointStyleEditable のみの btlm_trail は barStyleEditable=false（棒対象外）。
  const trail = buildSeriesStyleRows(
    { series: [{ seriesName: 'btlm_trail_mean', dynamic: false, pointStyleEditable: true }] },
    [{ name: 'btlm_trail_mean', kind: 'line', color: '#7b68ee', visible: true, display: 'dots' }],
  );
  assert.equal(trail[0].barStyleEditable, false);
  assert.equal(trail[0].pointStyleEditable, true);
});

test('案A(MAROD) スタイルタブ: barStyleEditable 系列は統合 4 択 [solid,dotted,dashed,bar]（dot なし）', () => {
  const dialog = new PropertiesDialog({ document: styleFakeDoc(), def: MAROD_DEF, seriesStyles: MAROD_STYLES });
  dialog._buildStylePane();
  const marod = dialog._styleState.find((s) => s.names[0] === 'btlm_trail_marod');
  assert.ok(marod.unified, 'MAROD は統合 select を持つ');
  const opts = marod.unified.children.map((o) => o.value);
  assert.deepEqual(opts, ['solid', 'dotted', 'dashed', 'bar'], 'dot は出さず末尾に bar');
  assert.equal(marod.unified.value, 'solid', '既定は style=solid');
  assert.equal(marod.initial.unified, 'solid');
  assert.equal(marod.style, null, '別体の線種 select は無い');
});

test('案A(MAROD) スタイルタブ: display=bar の系列は初期値 bar', () => {
  const styles = [{ name: 'btlm_trail_marod', kind: 'histogram', color: '#7b68ee', width: 2, style: 'solid', visible: true, display: 'bar' }];
  const dialog = new PropertiesDialog({ document: styleFakeDoc(), def: MAROD_DEF, seriesStyles: styles });
  dialog._buildStylePane();
  const marod = dialog._styleState[0];
  assert.equal(marod.unified.value, 'bar', 'display=bar 由来で初期値 bar');
  assert.equal(marod.initial.unified, 'bar');
});

test('案A(MAROD) _collectStyleChanges: bar 選択は {display:"bar"}（style を載せない）', () => {
  const dialog = new PropertiesDialog({ document: styleFakeDoc(), def: MAROD_DEF, seriesStyles: MAROD_STYLES });
  dialog._buildStylePane();
  dialog._buildVisibilityPane();
  assert.deepEqual(dialog._collectStyleChanges(), {}, '無変更は空');
  const marod = dialog._styleState[0];
  marod.unified.value = 'bar';
  assert.deepEqual(dialog._collectStyleChanges(), { btlm_trail_marod: { display: 'bar' } });
});

test('案A(MAROD) _collectStyleChanges: bar→dotted は {display:line, style:dotted}（棒解除で線種へ）', () => {
  const styles = [{ name: 'btlm_trail_marod', kind: 'histogram', color: '#7b68ee', width: 2, style: 'solid', visible: true, display: 'bar' }];
  const dialog = new PropertiesDialog({ document: styleFakeDoc(), def: MAROD_DEF, seriesStyles: styles });
  dialog._buildStylePane();
  dialog._buildVisibilityPane();
  const marod = dialog._styleState[0];
  assert.equal(marod.unified.value, 'bar');
  marod.unified.value = 'dotted';
  assert.deepEqual(dialog._collectStyleChanges(), { btlm_trail_marod: { display: 'line', style: 'dotted' } });
});

test('案A(MAROD) 回帰: btlm_trail（pointStyleEditable のみ）の統合 4 択は従来どおり [dot,solid,dotted,dashed]（bar なし）', () => {
  const dialog = new PropertiesDialog({ document: styleFakeDoc(), def: TRAIL_DEF, seriesStyles: TRAIL_STYLES });
  dialog._buildStylePane();
  const mean = dialog._styleState.find((s) => s.names[0] === 'btlm_trail_mean');
  const opts = mean.unified.children.map((o) => o.value);
  assert.deepEqual(opts, ['dot', 'solid', 'dotted', 'dashed'], 'btlm_trail は bar を出さない（挙動不変）');
});
