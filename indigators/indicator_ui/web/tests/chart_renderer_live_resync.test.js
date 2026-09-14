// chart_renderer.js resyncMissedCandles（ISSUE-106 ライブ欠落補完）の仕様検証。
//
// 設計入力: ISSUE-106。タブ休止（PC スリープ・バックグラウンドタイマー抑制）で足境界を
//   2 本以上またぐと、差分経路（updateLastCandle＝末尾 1 本前提）は途中の確定足を挿入できず
//   （lwc series.update は末尾より古い time を受け付けない）恒久的な歯抜けになる。
//   resyncMissedCandles はサーバー正の取得配列に「末尾より新しい足 2 本以上」または
//   「既知範囲内の未保持 time（穴）」を検出したときのみ setData 全置換で再同期する。
//   通常運転（差分 0〜1 本）は false を返し series へ触れない（挙動不変）。
// 構造: Arrange-Act-Assert（AAA）。DOM 非依存（Fake chart/series 注入）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ChartRenderer } from '../js/adapter/front/chart_renderer.js';

// Fake series。setData / update を記録（chart_renderer.test.js と同型の最小構成）。
function fakeSeries() {
  return {
    _data: null, _updates: [], _options: {}, _priceLines: [],
    setData(points) { this._data = points; },
    update(point) { this._updates.push(point); },
    applyOptions(opts) { Object.assign(this._options, opts); },
    createPriceLine(opt) { const pl = { opt }; this._priceLines.push(pl); return pl; },
    removePriceLine(pl) { this._priceLines = this._priceLines.filter((x) => x !== pl); },
  };
}

// Fake chart（最小）。timeScale().fitContent 呼び出し数を記録（resync がズームへ触れない検証用）。
function fakeChart() {
  let fitCount = 0;
  return {
    get fitCount() { return fitCount; },
    timeScale() { return { fitContent() { fitCount += 1; } }; },
    subscribeCrosshairMove() {},
    applyOptions() {},
    panes() { return []; },
  };
}

function bar(time, close = time * 10) {
  return { time, open: close - 1, high: close + 1, low: close - 2, close };
}

function newRenderer() {
  const chart = fakeChart();
  const main = fakeSeries();
  const renderer = new ChartRenderer({ chart, mainSeries: main, lwc: {} });
  return { renderer, chart, main };
}

// ===========================================================================
// 再同期する（バグの回帰固定）
// ===========================================================================

test('resync: 休止で確定足を2本以上取りこぼしたら setData 全置換で補完する（ISSUE-106 回帰）', () => {
  const { renderer, chart, main } = newRenderer();
  renderer.setCandles([bar(1), bar(2), bar(3)]); // 7/14 相当まで読込済み
  const fitAfterLoad = chart.fitCount;
  // 休止明け: サーバーは 4,5,6（7/15,16,17）まで進んでいる＝末尾より新しい足 3 本。
  const fetched = [bar(1), bar(2), bar(3), bar(4), bar(5), bar(6)];
  const resynced = renderer.resyncMissedCandles(fetched);
  assert.equal(resynced, true);
  assert.deepEqual(main._data, fetched); // 歯抜けなく全足が series へ入る
  assert.equal(chart.fitCount, fitAfterLoad); // fitContent は呼ばない（ズーム保持）
});

test('resync: player が現在足を先に書き既知範囲に穴があるケースも補完する（実機の歯抜け形）', () => {
  const { renderer, main } = newRenderer();
  renderer.setCandles([bar(1), bar(2), bar(3)]);
  // LiveTickPlayer が今日（6）の現在足だけを書いた＝実系列は 1,2,3,6 で 4,5 が穴。
  const playerBar = bar(6, 63708);
  renderer.updateLastCandle(playerBar);
  const fetched = [bar(1), bar(2), bar(3), bar(4), bar(5), bar(6)];
  const resynced = renderer.resyncMissedCandles(fetched);
  assert.equal(resynced, true);
  assert.deepEqual(main._data, fetched); // 穴 4,5 が補完される
  // 現在足の巻き戻し防止: 置換後、player の最新値（time 同値）を update で復元する。
  assert.deepEqual(main._updates.at(-1), playerBar);
});

test('resync: 置換前末尾（player 値）の time が新データ末尾以上なら復元し価格を巻き戻さない', () => {
  const { renderer, main } = newRenderer();
  renderer.setCandles([bar(1), bar(2)]);
  const playerBar = bar(7, 99999); // サーバー未知の新しい足を player が既に書いている
  renderer.updateLastCandle(playerBar);
  const fetched = [bar(1), bar(2), bar(3), bar(4), bar(5), bar(6)];
  assert.equal(renderer.resyncMissedCandles(fetched), true);
  assert.deepEqual(main._updates.at(-1), playerBar); // setData 後に復元 update
  assert.deepEqual(renderer.getCandles().at(-1), playerBar); // 基準 candles 末尾にも合流
});

test('resync: 休止明けで player より新しい足がサーバーにあれば新データ末尾を採用する', () => {
  const { renderer, main } = newRenderer();
  renderer.setCandles([bar(1), bar(2), bar(3)]);
  const fetched = [bar(1), bar(2), bar(3), bar(4), bar(5), bar(6)];
  assert.equal(renderer.resyncMissedCandles(fetched), true);
  // 置換前末尾（3）は新データ末尾（6）より古い＝復元しない（update は発行されない）。
  assert.equal(main._updates.length, 0);
  assert.deepEqual(renderer.getCandles().at(-1), bar(6));
});

// ===========================================================================
// 再同期しない（通常運転の挙動不変）
// ===========================================================================

test('resync: 取得データが実系列と同一なら何もしない（false・series 非接触）', () => {
  const { renderer, main } = newRenderer();
  const loaded = [bar(1), bar(2), bar(3)];
  renderer.setCandles(loaded);
  main._data = 'sentinel'; // 以後 setData が呼ばれない検証用
  assert.equal(renderer.resyncMissedCandles([bar(1), bar(2), bar(3)]), false);
  assert.equal(main._data, 'sentinel');
});

test('resync: 新しい足が1本だけ（通常のバー境界進行）は従来差分経路に委ねる（false）', () => {
  const { renderer, main } = newRenderer();
  renderer.setCandles([bar(1), bar(2), bar(3)]);
  main._data = 'sentinel';
  assert.equal(renderer.resyncMissedCandles([bar(1), bar(2), bar(3), bar(4)]), false);
  assert.equal(main._data, 'sentinel');
});

test('resync: 初期ロード前（基準 candles 未保持）は不介入（setCandles の責務）', () => {
  const { renderer, main } = newRenderer();
  assert.equal(renderer.resyncMissedCandles([bar(1), bar(2), bar(3)]), false);
  assert.equal(main._data, null);
});

test('resync: 空・非配列は不介入（false）', () => {
  const { renderer } = newRenderer();
  renderer.setCandles([bar(1)]);
  assert.equal(renderer.resyncMissedCandles([]), false);
  assert.equal(renderer.resyncMissedCandles(undefined), false);
});

test('resync: スナップショット（トリム）中は不介入（解除後の tick で再同期される）', () => {
  const { renderer, main } = newRenderer();
  renderer.setCandles([bar(1), bar(2), bar(3)]);
  renderer.setCandleTrim(2); // T=2 でトリム（_lastTrimIdx 設定）
  main._data = 'sentinel';
  assert.equal(renderer.resyncMissedCandles([bar(1), bar(2), bar(3), bar(4), bar(5)]), false);
  assert.equal(main._data, 'sentinel'); // トリム系列を破壊しない
});
