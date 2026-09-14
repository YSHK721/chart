// chrome_literals_single_source.test.js — クロム現行リテラルの単一情報源化（A-9）の 2 方向検証
//   （基本設計_指標カラーテーマ.md §7.4 段階 1 通過条件 6）。
//
// 方向 1（値の恒等性）: fake lwc で createChart / addSeries に渡るオプションを捕捉し、全色値が
//   単一情報源化の**前後で文字列として完全一致**することを固定する。この表が回帰ガードになる。
// 方向 2（重複の除去）: 対象 3 ファイルのコードからクロム色リテラルが消えていること。値の一致
//   だけを見ると「両方に同じ値が書いてある」状態を通してしまい、二重定義が残る（§7.2 S1 の欠点
//   そのもの）。よって「リテラルが在るか」を構造として落とす。

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { createChartWithMainSeries } from '../js/adapter/front/chart_bootstrap.js';
import { CHROME_CURRENT } from '../js/usecase/chrome_tokens.js';

const abs = (rel) => fileURLToPath(new URL(rel, import.meta.url));

// --- 方向 1: createChart / addSeries のオプション捕捉 --------------------

function makeFakeLwc() {
  const captured = { chartOptions: null, seriesOptions: null, seriesDefinition: null };
  const chart = {
    addSeries(definition, options) {
      captured.seriesDefinition = definition;
      captured.seriesOptions = options;
      return { __series: true };
    },
  };
  const lwc = {
    ColorType: { Solid: 'solid' },
    CrosshairMode: { Normal: 0 },
    CandlestickSeries: { __candles: true },
    createChart(container, options) {
      captured.chartOptions = options;
      return chart;
    },
  };
  return { lwc, captured };
}

// 導入前の実測値（chart_bootstrap.js の現行リテラル）。ここを書き換えたら見た目が変わる。
const EXPECTED_CHART_COLORS = {
  'layout.background.color': '#131722',
  'layout.textColor': '#d1d4dc',
  'layout.panes.separatorColor': '#2a2e39',
  'layout.panes.separatorHoverColor': 'rgba(178,181,189,0.2)',
  'grid.vertLines.color': '#1f2530',
  'grid.horzLines.color': '#1f2530',
  'rightPriceScale.borderColor': '#2a2e39',
  'timeScale.borderColor': '#2a2e39',
};
const EXPECTED_SERIES_COLORS = {
  upColor: '#26a69a',
  downColor: '#ef5350',
  borderUpColor: '#26a69a',
  borderDownColor: '#ef5350',
  wickUpColor: '#26a69a',
  wickDownColor: '#ef5350',
  priceLineColor: '#ff9800',
};

function pick(obj, path) {
  return path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj);
}

test('通過条件 6: createChart に渡る色値が現行と文字列完全一致（hex 8 経路）', () => {
  const { lwc, captured } = makeFakeLwc();
  createChartWithMainSeries({ lwc, container: {} });
  for (const [path, expected] of Object.entries(EXPECTED_CHART_COLORS)) {
    assert.equal(pick(captured.chartOptions, path), expected, path);
  }
});

test('通過条件 6: addSeries(Candlestick) に渡る色値が現行と文字列完全一致（7 経路）', () => {
  const { lwc, captured } = makeFakeLwc();
  createChartWithMainSeries({ lwc, container: {} });
  for (const [key, expected] of Object.entries(EXPECTED_SERIES_COLORS)) {
    assert.equal(captured.seriesOptions[key], expected, key);
  }
});

test('通過条件 6: 色以外の生成オプションは不変（単一情報源化が色以外へ波及しない）', () => {
  const { lwc, captured } = makeFakeLwc();
  const { chart, mainSeries } = createChartWithMainSeries({ lwc, container: {} });
  assert.ok(chart);
  assert.ok(mainSeries);
  const o = captured.chartOptions;
  assert.equal(o.layout.background.type, 'solid');
  assert.equal(o.layout.panes.enableResize, true);
  assert.equal(o.crosshair.mode, 0);
  assert.equal(o.timeScale.timeVisible, true);
  assert.equal(o.timeScale.secondsVisible, false);
  assert.equal(o.autoSize, true);
  assert.equal(captured.seriesDefinition, lwc.CandlestickSeries);
  assert.equal(captured.seriesOptions.priceLineVisible, true);
  assert.equal(captured.seriesOptions.priceLineWidth, 1);
  assert.equal(captured.seriesOptions.lastValueVisible, true);
});

test('通過条件 6: 捕捉値は CHROME_CURRENT（単一情報源）と一致する', () => {
  const { lwc, captured } = makeFakeLwc();
  createChartWithMainSeries({ lwc, container: {} });
  const o = captured.chartOptions;
  const s = captured.seriesOptions;
  assert.equal(o.layout.background.color, CHROME_CURRENT.layoutBackground);
  assert.equal(o.layout.textColor, CHROME_CURRENT.layoutTextColor);
  assert.equal(o.layout.panes.separatorColor, CHROME_CURRENT.paneSeparator);
  assert.equal(o.layout.panes.separatorHoverColor, CHROME_CURRENT.paneSeparatorHover);
  assert.equal(o.grid.vertLines.color, CHROME_CURRENT.gridVertLines);
  assert.equal(o.grid.horzLines.color, CHROME_CURRENT.gridHorzLines);
  assert.equal(o.rightPriceScale.borderColor, CHROME_CURRENT.rightPriceScaleBorder);
  assert.equal(o.timeScale.borderColor, CHROME_CURRENT.timeScaleBorder);
  assert.equal(s.upColor, CHROME_CURRENT.candleUp);
  assert.equal(s.downColor, CHROME_CURRENT.candleDown);
  assert.equal(s.priceLineColor, CHROME_CURRENT.priceLine);
});

// --- 方向 2: 対象ファイルからクロム色リテラルが消えていること -------------

// コメント（// … と /* … */）を除去する。値ではなく**コードに書かれているか**を見るため。
function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1');
}

// A-9 の対象 3 ファイル（§7.1・前提条件 6）。
const TARGETS = [
  ['chart_bootstrap.js', '../js/adapter/front/chart_bootstrap.js'],
  ['chart_renderer.js', '../js/adapter/front/chart_renderer.js'],
  ['replay_boundary_dim.js', '../../../../simulator/replay_ui/web/js/adapter/front/replay_boundary_dim.js'],
];

// 20 配線点が持つ現行リテラルの相異集合。これがコードに残っていれば二重定義。
const CHROME_LITERALS = [...new Set(Object.values(CHROME_CURRENT))];

test('A-9: 対象 3 ファイルのコードにクロム色リテラルが残っていない（二重定義の除去）', () => {
  for (const [name, rel] of TARGETS) {
    const code = stripComments(readFileSync(abs(rel), 'utf8'));
    for (const literal of CHROME_LITERALS) {
      assert.equal(code.includes(literal), false,
        `${name}: クロム現行リテラル ${literal} がコードに残っている（chrome_tokens.js が単一情報源）`);
    }
  }
});

test('A-9: 対象 3 ファイルは chrome_tokens.js を参照している（値の出所が 1 箇所）', () => {
  for (const [name, rel] of TARGETS) {
    const code = readFileSync(abs(rel), 'utf8');
    assert.match(code, /chrome_tokens\.js/, `${name}: chrome_tokens.js を import していない`);
  }
});
