// series_def_color_role.test.js — SeriesDef の色の意味（colorRole）宣言席（§4.1・§7.2）。
//
// 検証の眼目は 2 つ。
//   (1) 「色の値」の席（旧 colorRule）を撤去し、「色の意味」の席（colorRole）を新設したこと。
//       同一概念に 2 つの呼び名を残さない（§7.2 根拠 3・目的 1）。
//   (2) §4.1.3 規則 1「kind==='horizontal_line' の SeriesDef は必ず level。例外を作らない」が
//       **構造的に**（手書きの反復ではなく型の不変条件として）成立すること。

import test from 'node:test';
import assert from 'node:assert/strict';

import { SeriesDef, SeriesKind } from '../js/domain/domain_models.js';
import { ColorRole, isColorRole } from '../js/domain/color_roles.js';

const base = { kind: SeriesKind.LINE, sourceColumn: 'x', seriesName: 'x', dynamic: false };

test('§7.2 colorRule の席は撤去されている（色の値と色の意味の二重の呼び名を残さない）', () => {
  const s = new SeriesDef(base);
  assert.equal('colorRule' in s, false);
  // 旧席へ値を渡しても復活しない（後方互換の抜け道を作らない）。
  const legacy = new SeriesDef({ ...base, colorRule: '#123456' });
  assert.equal('colorRule' in legacy, false);
});

test('§4.1 colorRole の席が新設され、既定は null（未宣言）', () => {
  const s = new SeriesDef(base);
  assert.equal('colorRole' in s, true);
  assert.equal(s.colorRole, null);
});

test('§4.1 colorRole は宣言した語彙をそのまま保持する', () => {
  for (const token of Object.values(ColorRole)) {
    const s = new SeriesDef({ ...base, colorRole: token });
    assert.equal(s.colorRole, token, token);
  }
});

test('§5.7 F-C3 未知トークンの宣言は null（未宣言）へ縮退する', () => {
  for (const bad of ['signal', 'center', 'band', 'extreme', 'readout', '', 0, {}, []]) {
    const s = new SeriesDef({ ...base, colorRole: bad });
    assert.equal(s.colorRole, null, String(bad));
  }
});

test('§4.1.3 規則 1: horizontal_line は宣言に依らず必ず level（例外を作らない）', () => {
  const h = { kind: SeriesKind.HORIZONTAL_LINE, sourceColumn: null, seriesName: 'h', dynamic: false };
  // 未宣言でも level。
  assert.equal(new SeriesDef(h).colorRole, ColorRole.LEVEL);
  // 別トークンを宣言しても level（供給経路が priceLine で構造的に別＝E-10 のため）。
  for (const token of Object.values(ColorRole)) {
    assert.equal(new SeriesDef({ ...h, colorRole: token }).colorRole, ColorRole.LEVEL, token);
  }
  // 未知トークンでも level。
  assert.equal(new SeriesDef({ ...h, colorRole: 'nonsense' }).colorRole, ColorRole.LEVEL);
});

test('horizontal_line 以外の kind には level 強制が波及しない', () => {
  for (const kind of [SeriesKind.LINE, SeriesKind.HISTOGRAM, SeriesKind.LEVEL_DASH]) {
    assert.equal(new SeriesDef({ ...base, kind }).colorRole, null, kind);
    assert.equal(new SeriesDef({ ...base, kind, colorRole: ColorRole.PRIMARY }).colorRole,
      ColorRole.PRIMARY, kind);
  }
});

test('保持された colorRole は常に語彙内か null（全域性）', () => {
  const samples = [undefined, null, 'primary', 'nonsense', 3, SeriesKind.HORIZONTAL_LINE];
  for (const v of samples) {
    for (const kind of Object.values(SeriesKind)) {
      const s = new SeriesDef({ ...base, kind, colorRole: v });
      assert.ok(s.colorRole === null || isColorRole(s.colorRole), `${kind}/${String(v)}`);
    }
  }
});

test('SeriesDef は従来どおり凍結され、他フィールドは不変', () => {
  const s = new SeriesDef({ ...base, style: 'dotted', width: 2, priceScaleId: 'p', axisLabelVisible: true, pointStyleEditable: true, barStyleEditable: true });
  assert.ok(Object.isFrozen(s));
  assert.equal(s.style, 'dotted');
  assert.equal(s.width, 2);
  assert.equal(s.priceScaleId, 'p');
  assert.equal(s.axisLabelVisible, true);
  assert.equal(s.pointStyleEditable, true);
  assert.equal(s.barStyleEditable, true);
});
