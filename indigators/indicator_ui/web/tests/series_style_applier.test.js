// series_style_applier.test.js — 系列スタイル適用ロール（ISSUE-479 Wave2 J-1 SRP）の抽出を固定する。
//
// 何を固定するか:
//   R1 構造: 「保存済みスタイルと選択中テーマから実描画色を決めて renderer へ配る」本体は
//       indicator_controller.js に無い（協働子への 1 行委譲だけが残る）。テーマ供給ポートの
//       保持も協働子が所有する（ISSUE-181「状態も一緒に移す」）。
//   C3 計算量: _applyStoredStyles 1 回あたり renderer.getSeriesStyles の発行は 1 回で、
//       系列本数を変えても増えない（系列ごとに実系列メタを取り直さない）。
//       回数は焼き込まず、系列 3 本 / 12 本の 2 点で「増えないこと」を固定する。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { IndicatorController } from '../js/adapter/front/indicator_controller.js';

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const FRONT = join(WEB, 'js', 'adapter', 'front');
const CONTROLLER_SRC = readFileSync(join(FRONT, 'indicator_controller.js'), 'utf8');

const STYLE_METHODS = ['_applyStoredStyles', '_activeColorTheme', '_applyLevelLineColor'];

function methodBody(name) {
  const lines = CONTROLLER_SRC.split('\n');
  const start = lines.findIndex((l) => new RegExp(`^  ${name}\\(`).test(l));
  assert.notEqual(start, -1, `${name} が indicator_controller.js に見つからない（公開面が消えている）`);
  const body = [];
  for (let i = start + 1; i < lines.length; i += 1) {
    if (lines[i] === '  }') break;
    const code = lines[i].trim();
    if (code === '' || code.startsWith('//') || code.startsWith('*') || code.startsWith('/*')) continue;
    body.push(code);
  }
  return body;
}

test('R1: スタイル適用の本体は indicator_controller.js に無い（協働子への委譲だけが残る）', () => {
  const offenders = [];
  for (const name of STYLE_METHODS) {
    const body = methodBody(name);
    if (body.length !== 1 || !body[0].includes('this._style.')) {
      offenders.push(`${name}: ${body.length} 行 / ${body.join(' ')}`.slice(0, 160));
    }
  }
  assert.deepEqual(offenders, [],
    `スタイル適用の本体が indicator_controller.js に残っています:\n  ${offenders.join('\n  ')}`);
});

test('R1: テーマ供給ポートの保持は協働子が所有する（host のフィールドではない）', () => {
  assert.equal(
    /this\._colorThemeProvider\s*=/.test(CONTROLLER_SRC), false,
    'IndicatorController がテーマ供給ポートを自身のフィールドとして持っている（状態が host のまま）',
  );
});

// ---------------------------------------------------------------------------
// 計算量ゲート（Test Spy＝実系列メタの取得発行回数を数える）
// ---------------------------------------------------------------------------

const noop = () => {};

// 系列 seriesCount 本を持つ 1 インスタンスを applied に載せ、スタイル適用だけを走らせる。
function build(seriesCount) {
  const spy = { getSeriesStyles: 0, applySeriesStyle: 0, applyLevelLineColor: 0 };
  const metas = Array.from({ length: seriesCount }, (_, i) => ({
    name: `s${i}`, baseColor: '#0f0f0f',
  }));
  const renderer = {
    renderLine: noop, renderHorizontal: noop, setData: noop, setVisible: noop,
    remove: noop, setCandles: noop,
    getSeriesStyles() { spy.getSeriesStyles += 1; return metas; },
    applySeriesStyle() { spy.applySeriesStyle += 1; },
    applyLevelLineColor() { spy.applyLevelLineColor += 1; },
  };
  const controller = new IndicatorController({
    catalog: { listIndicators: () => [], get: () => null },
    compute: { compute: async () => ({ ok: true, generation: 0, series: [] }) },
    persistence: {
      loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop,
      loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer,
    document: null,
  });
  // 適用済み 1 件（styles は保存済み上書きあり＝スタイル適用の経路へ確実に入る）。
  controller._commitState({
    ...controller._state,
    applied: [{
      instanceId: 'i#1', indicatorId: 'ind', variant: null, params: {}, visible: true,
      styles: Object.fromEntries(metas.map((m) => [m.name, { width: 2 }])),
    }],
  });
  return { controller, spy, seriesCount };
}

function measure(rig) {
  const before = { ...rig.spy };
  rig.controller._applyStoredStyles('i#1');
  return {
    getSeriesStyles: rig.spy.getSeriesStyles - before.getSeriesStyles,
    applySeriesStyle: rig.spy.applySeriesStyle - before.applySeriesStyle,
  };
}

test('C3: スタイル適用 1 回あたりの実系列メタ取得は、系列を増やしても増えない', () => {
  // Arrange: 系列本数だけが違う 2 点。
  const few = build(3);
  const many = build(12);
  // Act
  const a = measure(few);
  const b = measure(many);
  // Assert: 取得は「そのインスタンスの実系列集合」を 1 度引く仕事＝系列数に依らない。
  assert.equal(b.getSeriesStyles, a.getSeriesStyles,
    `系列を増やすとメタ取得が増えている（${a.getSeriesStyles} → ${b.getSeriesStyles}）`);
  // 書き込みは系列ごとに 1 回＝出力（実際に色/幅を配った系列）と一致する（発行 − 使用 = 0）。
  assert.equal(a.applySeriesStyle, few.seriesCount);
  assert.equal(b.applySeriesStyle, many.seriesCount);
});

test('C5: 系列ごとにメタを取り直す変異を入れると C3 が赤になる（検出力の実測）', () => {
  // Arrange: 「1 系列につきもう 1 回メタを引く」浪費を注入する（負の対照）。
  const inject = (rig) => {
    const style = rig.controller._style;
    const inner = style._applyLevelLineColor.bind(style);
    style._applyLevelLineColor = (instanceId, theme, timeframe) => {
      for (let i = 0; i < rig.seriesCount; i += 1) {
        style._host._renderer.getSeriesStyles(instanceId);
      }
      return inner(instanceId, theme, timeframe);
    };
    return rig;
  };
  const few = inject(build(3));
  const many = inject(build(12));
  // Act
  const a = measure(few);
  const b = measure(many);
  // Assert
  assert.notEqual(b.getSeriesStyles, a.getSeriesStyles,
    '浪費を注入しても計算量ゲートが同じ値を返している（ゲートが空振りしている）');
});
