// ペイン幾何の追随（ISSUE-440）— 凡例の位置がペイン幾何の変化に付いていくことの検証。
//
// 由来（実測 2026-08-21・統合 UI 8000・1600×1000）:
//   1. 起動直後の凡例が正位置より 42px 下に出た（740px / 正 698px）。価格軸でホイールを回すと
//      698px へ直った＝直る条件が「setPaneHeight が走る操作をしたとき」だった。
//      原因: 区切り高を「総高 − 各ペイン高の合計」で逆算するのに、総高が push（setPaneHeight）
//      でしか更新されず、押されなかった経路では**古い総高**が使われていた。
//   2. ペイン区切りを 100px 上へ引いてもラベルが動かない（ペイン上端 458px に対しラベル 558px）。
//      原因: 凡例の再発行の契機が「データ・構成・クロスヘア」しか無く、幾何の変化が契機に無かった。
//
// 固定する不変条件:
//   A. 総高は**使う時点で測る**（供給された測定関数を毎回呼ぶ）。押し込まれた古い値より優先する。
//   B. 幾何が変わったときだけ凡例を引き直す（変わらなければ発行しない＝無駄な再描画を作らない）。
//   C. どの経路で発行しても「最後に配った幾何」は 1 つ（通常発行の直後に幾何を確かめても
//      二重発行にならない）。
//
// 構造は AAA。chart / mainSeries は Fake（DOM・実描画非依存）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ChartRenderer } from '../js/adapter/front/chart_renderer.js';
import { makeMeasurePaneAreaHeight, installPaneGeometryFollow } from '../js/adapter/front/chart_bootstrap.js';

const LineSeries = { kind: 'Line' };

function fakeSeries() {
  return {
    _data: [], _options: {}, _priceLines: [],
    setData(points) { this._data = points ?? []; },
    data() { return this._data ?? []; },
    update() {},
    applyOptions(o) { Object.assign(this._options, o); },
    createPriceLine(opt) { const pl = { opt }; this._priceLines.push(pl); return pl; },
    removePriceLine(pl) { this._priceLines = this._priceLines.filter((x) => x !== pl); },
  };
}

// ペイン高を後から書き換えられる最小の Fake チャート（区切りドラッグ・版面リサイズの再現）。
function fakeChart(heights) {
  const state = { heights: [...heights] };
  const panesArr = [];
  const makePane = () => {
    const pane = {
      _series: [],
      paneIndex() { return panesArr.indexOf(pane); },
      getHeight() { return state.heights[panesArr.indexOf(pane)] ?? 0; },
      setStretchFactor() {}, setPreserveEmptyPane() {},
      addSeries(def, opts) {
        const s = fakeSeries(); s._pane = pane; pane._series.push(s); return s;
      },
    };
    return pane;
  };
  panesArr.push(makePane());
  return {
    state,
    panes() { return panesArr; },
    addPane() { const p = makePane(); panesArr.push(p); return p; },
    removePane(i) { panesArr.splice(i, 1); },
    addSeries(def, opts) { return panesArr[0].addSeries(def, opts); },
    removeSeries() {},
    applyOptions() {},
    timeScale() { return { width: () => 800, height: () => 28, fitContent() {}, coordinateToTime: () => null }; },
    subscribeCrosshairMove() {},
  };
}

// 価格ペイン＋指標ペイン 1 枚の構成を作る（指標ペインに凡例の行が 1 つ載る）。
function build(heights = [697, 232]) {
  const chart = fakeChart(heights);
  const main = fakeSeries();
  main.getPane = () => chart.panes()[0];
  const emitted = [];
  const renderer = new ChartRenderer({
    chart, mainSeries: main, lwc: { LineSeries },
    onPaneLegend: (model) => emitted.push(model),
  });
  renderer.renderLine('osc#1', [{
    name: 'osc', kind: 'line', style: 'solid', width: 1, color: '#0f0',
    data: [{ time: 20, value: 55 }],
  }], { pane: true });
  return { chart, renderer, emitted };
}

const topOfPane1 = (model) => model.groups.find((g) => g.paneIndex === 1)?.top;

test('総高は毎回測り直す（押し込まれた古い値より実測を優先する）', () => {
  // Arrange: 価格 697 / 指標 232・区切り 1px ＝ 総高 930。押し込み値は古い 1204 のまま。
  const { chart, renderer } = build([697, 232]);
  renderer.setPaneHeight(1204);
  let measured = 930;
  renderer.setPaneAreaHeightProvider(() => measured);
  // Act / Assert: 区切りは 930-929=1px ＝ 指標ペインの上端は 698（古い総高なら 972）。
  assert.equal(topOfPane1(renderer.paneLegendModel()), 698);

  // Arrange: 版面が縮む（下部ペインの分割線・ウィンドウのリサイズ相当）。
  chart.state.heights = [355, 118];
  measured = 474;
  // Act / Assert: 測り直した総高から区切りを出すので上端も追随する（355 + 1）。
  assert.equal(topOfPane1(renderer.paneLegendModel()), 356);
});

test('測定関数が未供給なら従来どおり押し込み値を使う（既存の呼び出しは不変）', () => {
  // Arrange
  const { renderer } = build([697, 232]);
  renderer.setPaneHeight(930);
  // Act / Assert
  assert.equal(topOfPane1(renderer.paneLegendModel()), 698);
});

test('測定関数が 0 を返す環境では押し込み値へ縮退する（測れない＝上限 0 と誤解しない）', () => {
  // Arrange: 版面が未レイアウト（clientHeight 0）の瞬間。
  const { renderer } = build([697, 232]);
  renderer.setPaneHeight(930);
  renderer.setPaneAreaHeightProvider(() => 0);
  // Act / Assert
  assert.equal(topOfPane1(renderer.paneLegendModel()), 698);
});

test('幾何が変わったときだけ凡例を引き直す', () => {
  // Arrange
  const { chart, renderer, emitted } = build([697, 232]);
  renderer.setPaneAreaHeightProvider(() => 930);
  renderer.refreshPaneLegendIfGeometryChanged();
  const first = emitted.length;
  // Act: 幾何が動いていない
  const again = renderer.refreshPaneLegendIfGeometryChanged();
  // Assert
  assert.equal(again, false, '変わっていなければ発行しない');
  assert.equal(emitted.length, first, '無駄な再描画を作らない');

  // Act: 区切りのドラッグでペイン高が動いた（総高は変わらないので合計は不変＝697+232=457+472）
  chart.state.heights = [457, 472];
  const changed = renderer.refreshPaneLegendIfGeometryChanged();
  // Assert
  assert.equal(changed, true);
  assert.equal(emitted.length, first + 1);
  assert.equal(topOfPane1(emitted.at(-1)), 458);
});

test('版面の総高だけが変わった場合も引き直す（ペイン高が同じでも区切りが変わる）', () => {
  // Arrange
  const { renderer, emitted } = build([697, 232]);
  let measured = 930;
  renderer.setPaneAreaHeightProvider(() => measured);
  renderer.refreshPaneLegendIfGeometryChanged();
  const n = emitted.length;
  // Act
  measured = 940;
  const changed = renderer.refreshPaneLegendIfGeometryChanged();
  // Assert
  assert.equal(changed, true);
  assert.equal(emitted.length, n + 1);
});

test('通常発行の直後に幾何を確かめても二重発行にならない', () => {
  // Arrange: 可視切替など既存経路で発行された直後の状態。
  const { renderer, emitted } = build([697, 232]);
  renderer.setPaneAreaHeightProvider(() => 930);
  renderer.setVisible('osc#1', true);
  const n = emitted.length;
  assert.ok(n > 0, '前提: 既存経路で発行されている');
  // Act
  const changed = renderer.refreshPaneLegendIfGeometryChanged();
  // Assert
  assert.equal(changed, false);
  assert.equal(emitted.length, n);
});

test('版面の寸法変化を自分で観測して引き直す（lwc の通知に依存しない）', () => {
  // Arrange: lwc の subscribeSizeChange は autoSize 由来のリサイズで発火しないことを実測した
  //   （2026-08-21: ウィンドウ 1000→800 で凡例 DOM の変化 0 件）。よって寸法は自分で観測する。
  const { chart, renderer, emitted } = build([697, 232]);
  let measured = 930;
  renderer.setPaneAreaHeightProvider(() => measured);
  renderer.refreshPaneLegendIfGeometryChanged();
  const n = emitted.length;
  const observers = [];
  const frames = [];
  const win = {
    ResizeObserver: class { constructor(fn) { observers.push(fn); } observe() {} disconnect() { this.off = true; } },
    requestAnimationFrame: (fn) => frames.push(fn),
  };
  const stop = installPaneGeometryFollow({ container: {}, renderer, win });
  // Act: 版面が縮み、lwc のペイン高更新は**こちらのコールバックより後**に来る（次フレーム）。
  measured = 474;
  observers.forEach((fn) => fn());
  chart.state.heights = [355, 118];
  frames.forEach((fn) => fn());
  // Assert: 次フレームの突き合わせで最終的に正しい上端が配られる。
  assert.equal(topOfPane1(emitted.at(-1)), 356);
  assert.ok(emitted.length > n);
  // Assert: 解除できる（購読を残さない）。
  assert.equal(typeof stop, 'function');
});

test('ResizeObserver の無い環境では何もしない（例外にしない）', () => {
  // Arrange / Act
  const { renderer } = build([697, 232]);
  const stop = installPaneGeometryFollow({ container: {}, renderer, win: {} });
  // Assert
  assert.equal(typeof stop, 'function');
  assert.doesNotThrow(() => stop());
});

test('配った直後に幾何が確定する場合も追随する（次フレームで突き合わせる）', () => {
  // Arrange: ペインの増減は lwc が次の描画で高さを配り直すため、適用した瞬間の DTO は
  //   古い高さで組まれている。実測 2026-08-21: 起動直後の凡例がペイン上端 558/745px に対し
  //   698/930px のまま動かなかった（マウスを動かすと直る＝発行の契機が無いだけ）。
  const frames = [];
  const original = globalThis.requestAnimationFrame;
  globalThis.requestAnimationFrame = (fn) => { frames.push(fn); return frames.length; };
  try {
    const { chart, renderer, emitted } = build([957, 0]);   // 追加直後（新ペインの高さ未確定）
    renderer.setPaneAreaHeightProvider(() => 930);
    const n = emitted.length;
    assert.ok(frames.length > 0, '発行のたびに次フレームの突き合わせを予約する');
    // Act: 次フレームまでに lwc が高さを配り直した。
    chart.state.heights = [697, 232];
    frames.splice(0).forEach((fn) => fn());
    // Assert: 予約が発火して正しい上端が配り直される。
    assert.ok(emitted.length > n);
    assert.equal(topOfPane1(emitted.at(-1)), 698);
  } finally {
    globalThis.requestAnimationFrame = original;
  }
});

test('予約は多重に積まない（クロスヘア移動のたびに rAF を溜めない）', () => {
  // Arrange
  const frames = [];
  const original = globalThis.requestAnimationFrame;
  globalThis.requestAnimationFrame = (fn) => { frames.push(fn); return frames.length; };
  try {
    const { renderer } = build([697, 232]);
    renderer.setPaneAreaHeightProvider(() => 930);
    const before = frames.length;
    // Act: 幾何が変わらないまま何度も発行される（既存経路）。
    renderer.setVisible('osc#1', true);
    renderer.setVisible('osc#1', true);
    renderer.setVisible('osc#1', true);
    // Assert: 予約は 1 本だけ（未発火の予約があるあいだは足さない）。
    assert.equal(frames.length, before);
  } finally {
    globalThis.requestAnimationFrame = original;
  }
});

test('makeMeasurePaneAreaHeight は container 高 − 時間軸高を返す（測れなければ 0）', () => {
  // Arrange
  const chart = { timeScale: () => ({ height: () => 28 }) };
  // Act / Assert
  assert.equal(makeMeasurePaneAreaHeight({ container: { clientHeight: 958 }, chart })(), 930);
  assert.equal(makeMeasurePaneAreaHeight({ container: { clientHeight: 0 }, chart })(), 0);
  assert.equal(makeMeasurePaneAreaHeight({ container: null, chart })(), 0);
  assert.equal(makeMeasurePaneAreaHeight({ container: { clientHeight: 958 }, chart: {} })(), 958);
});
