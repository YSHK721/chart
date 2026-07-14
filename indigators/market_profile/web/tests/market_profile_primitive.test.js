// market_profile_primitive.js（MarketProfileHistogramPrimitive）の描画仕様検証。
//
// 設計入力: pair_lines_primitive.test.js を手本（fake target/series で座標・矩形・線分を観測）。
//   v5 primitive 事実: attached({chart,series,requestUpdate})・paneViews()→renderer().draw(target)→
//   target.useBitmapCoordinateSpace(scope=>scope.context 描画)・series.priceToCoordinate（範囲外 null）。
//   実 canvas / 実 lwc には依存しない。構造: Arrange-Act-Assert。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { MarketProfileHistogramPrimitive, heatColor } from '../js/adapter/front/market_profile_primitive.js';

// heatColor（ヒート配色ヘルパ）: norm 0..1 を 青(hue240)→赤(hue0)。境界・防御分岐の回帰網。
test('heatColor: norm=0 は青(hue240)・norm=1 は赤(hue0)', () => {
  assert.equal(heatColor(0), 'hsla(240, 95%, 46%, 0.9)');
  assert.equal(heatColor(1), 'hsla(0, 95%, 58%, 0.9)');
});
test('heatColor: 範囲外/NaN/null は [0,1] にクランプ（0=青 / >1=赤）', () => {
  assert.equal(heatColor(-1), heatColor(0)); // 下限クランプ
  assert.equal(heatColor(2), heatColor(1));  // 上限クランプ
  assert.equal(heatColor(NaN), heatColor(0)); // NaN→0=青
  assert.equal(heatColor(null), heatColor(0)); // null→0=青
});
test('heatColor: alpha 指定が反映される', () => {
  assert.ok(heatColor(0.5, 0.3).endsWith(', 0.3)'));
});

// fake series: price→y 恒等写像（priceNulls の価格は null＝範囲外）。
function fakeSeries(priceNulls = new Set()) {
  return { priceToCoordinate(price) { return priceNulls.has(price) ? null : price; } };
}
const fakeChart = () => ({});

// fake target: fillRect（横バー/列背景）・stroke（POC/VA 水平線）・fillText（日付ラベル/注記）を記録する。
function fakeTarget(width = 800) {
  const rects = [];
  const lines = [];
  const texts = [];
  let cur = null;
  const polylines = [];
  const context = {
    fillStyle: null, strokeStyle: null, globalAlpha: 1, lineWidth: 1, font: '', textBaseline: '',
    _dash: [],
    fillRect(x, y, w, h) { rects.push({ x, y, w, h, fill: this.fillStyle, alpha: this.globalAlpha }); },
    fillText(s, x, y) { texts.push({ s, x, y }); },
    // beginPath は「線分（1 moveTo + 1 lineTo）」と「ポリライン（1 moveTo + 多 lineTo）」の双方を
    //   同一 API で捕捉する。cur.pts に全頂点を積み、stroke で lines（先頭/末尾を x1/y1・x2/y2 に写像）
    //   と polylines（全頂点・幅の検証用）へ確定する。
    beginPath() { cur = { pts: [] }; },
    moveTo(x, y) { cur.x1 = x; cur.y1 = y; cur.pts.push({ x, y }); },
    lineTo(x, y) { cur.x2 = x; cur.y2 = y; cur.pts.push({ x, y }); },
    setLineDash(d) { this._dash = d || []; },
    stroke() {
      const rec = { ...cur, color: this.strokeStyle, dash: this._dash.slice(), lineWidth: this.lineWidth };
      lines.push(rec);
      if (cur.pts && cur.pts.length > 2) {
        polylines.push({ pts: cur.pts.slice(), color: this.strokeStyle, lineWidth: this.lineWidth });
      }
    },
    save() {}, restore() {},
  };
  return {
    rects, lines, texts, polylines,
    useBitmapCoordinateSpace(fn) {
      fn({ context, bitmapSize: { width, height: 600 }, horizontalPixelRatio: 1, verticalPixelRatio: 1 });
    },
  };
}

const PROFILE = {
  bins: [
    { price: 100, tpo: 2, norm: 0.5 },
    { price: 101, tpo: 4, norm: 1.0 },
    { price: 102, tpo: 1, norm: 0.25 },
  ],
  poc: 101, va_low: 100, va_high: 101, price_min: 100, price_max: 102,
};

// attach → setProfile → setVisible(true) → draw を実行して target を返す共通手順。
function drawProfile(prim, { chart, series, target, visible = true }) {
  prim.attached({ chart, series, requestUpdate: () => {} });
  prim.setProfile(PROFILE);
  prim.setVisible(visible);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
}

test('draws one horizontal bar per bin when visible', () => {
  // Arrange
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget();
  // Act
  drawProfile(prim, { chart: fakeChart(), series: fakeSeries(), target });
  // Assert: 3 bins → 3 バー
  assert.equal(target.rects.length, 3);
});

test('bar widths are proportional to norm and right-aligned to the chart edge', () => {
  // Arrange
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget(800);
  // Act
  drawProfile(prim, { chart: fakeChart(), series: fakeSeries(), target });
  // Assert: バーは bins の配列順に描かれる（PROFILE 順 = norm 0.5, 1.0, 0.25）。
  //   バーは価格に対し上下中心化（y = price - barH/2）されるため、y キーではなく描画順で識別する。
  const [mid, wide, narrow] = target.rects; // norm 0.5 / 1.0 / 0.25
  assert.ok(wide.w > mid.w && mid.w > narrow.w, 'バー長は norm に比例すべき');
  for (const r of target.rects) {
    assert.ok(Math.abs((r.x + r.w) - 800) < 1e-6, 'バーは右端に整列すべき');
  }
});

test('draws POC / VAH / VAL horizontal reference lines', () => {
  // Arrange
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget();
  // Act
  drawProfile(prim, { chart: fakeChart(), series: fakeSeries(), target });
  // Assert: POC(101) / VAH(101) / VAL(100) の 3 本
  assert.equal(target.lines.length, 3);
  const ys = target.lines.map((l) => l.y1).sort((a, b) => a - b);
  assert.deepEqual(ys, [100, 101, 101]);
});

test('draws nothing when not visible (toggle OFF)', () => {
  // Arrange
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget();
  // Act
  drawProfile(prim, { chart: fakeChart(), series: fakeSeries(), target, visible: false });
  // Assert
  assert.equal(target.rects.length, 0);
  assert.equal(target.lines.length, 0);
});

// ---------------------------------------------------------------------------
// スナップショット（増分2 C）: setSnapshot(true) で累積バーを減光（DIM_ALPHA=0.30）し、
//   today[] を today_max スケールで通常ヒート色で重畳。OFF（既定）は従来の明るい累積バー（不変）。
//   移植元 prototype_260630-01 drawComposite（showToday 時の DIM＋当日強調）。
// ---------------------------------------------------------------------------
const PROFILE_WITH_TODAY = {
  bins: [
    { price: 100, tpo: 2, norm: 0.5 },
    { price: 101, tpo: 4, norm: 1.0 },
    { price: 102, tpo: 1, norm: 0.25 },
  ],
  poc: 101, va_low: 100, va_high: 101, price_min: 100, price_max: 102,
  // 当日ぶん: price 101 のみ滞在（today_max=3）。
  today: [0, 3, 0], today_max: 3,
};

function drawProfileP(prim, profile, { series, target, snapshot = false }) {
  prim.attached({ chart: fakeChart(), series, requestUpdate: () => {} });
  prim.setProfile(profile);
  prim.setVisible(true);
  if (snapshot) prim.setSnapshot(true);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
}

test('snapshot OFF (default): cumulative bars use the normal alpha (0.9) — unchanged behavior', () => {
  // Arrange
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget();
  // Act: setSnapshot は呼ばない（既定 OFF）
  drawProfileP(prim, PROFILE_WITH_TODAY, { series: fakeSeries(), target });
  // Assert: 全バーが通常アルファ（減光しない）
  assert.equal(target.rects.length, 3);
  for (const r of target.rects) {
    assert.ok(String(r.fill).endsWith(', 0.9)'), '通常時は 0.9 アルファ');
  }
});

test('snapshot ON: cumulative bars are dimmed (DIM_ALPHA=0.30) and today[] bars overlaid at bright alpha', () => {
  // Arrange
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget();
  // Act
  drawProfileP(prim, PROFILE_WITH_TODAY, { series: fakeSeries(), target, snapshot: true });
  // Assert: 3 累積バー（減光 0.3）＋ 1 当日バー（price101・明るい）
  const dim = target.rects.filter((r) => String(r.fill).includes(', 0.3)'));
  const bright = target.rects.filter((r) => !String(r.fill).includes(', 0.3)'));
  assert.equal(dim.length, 3, '累積バーは全て減光(0.3)');
  assert.equal(bright.length, 1, '当日ぶん(today>0)の 1 本のみ明るく重畳');
});

test('snapshot ON without today[] data: only dims, no overlay bars (no throw)', () => {
  // Arrange: today なしの profile（snapshot ON でも今日バーは描かない）
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget();
  const noToday = { ...PROFILE_WITH_TODAY };
  delete noToday.today; delete noToday.today_max;
  // Act
  assert.doesNotThrow(() => drawProfileP(prim, noToday, { series: fakeSeries(), target, snapshot: true }));
  // Assert: 3 累積バーは減光、当日オーバーレイは無し
  const bright = target.rects.filter((r) => !String(r.fill).includes(', 0.3)'));
  assert.equal(bright.length, 0);
});

test('setSnapshot(false) after ON restores bright cumulative bars', () => {
  // Arrange
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget();
  prim.attached({ chart: fakeChart(), series: fakeSeries(), requestUpdate: () => {} });
  prim.setProfile(PROFILE_WITH_TODAY);
  prim.setVisible(true);
  prim.setSnapshot(true);
  prim.setSnapshot(false);
  // Act
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  // Assert: 減光バーが無い（全て通常アルファ）
  const dim = target.rects.filter((r) => String(r.fill).includes(', 0.3)'));
  assert.equal(dim.length, 0);
});

// ---------------------------------------------------------------------------
// リプレイ（増分1）: T 縦線（setCursorTime）。移植元 prototype_260630-01 の遡り時点 T 縦線。
//   chart.timeScale().timeToCoordinate(T) → x を得て、y=0..height の垂直線を描く。null で消える。
// ---------------------------------------------------------------------------
// timeToCoordinate を提供する fake chart（T→x 恒等写像・範囲外は null）。
function fakeChartWithTime(nullTimes = new Set()) {
  return {
    timeScale: () => ({ timeToCoordinate: (t) => (nullTimes.has(t) ? null : t) }),
  };
}

test('setCursorTime(T) draws a vertical line at timeToCoordinate(T) (リプレイ T 縦線)', () => {
  // Arrange
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget(800);
  prim.attached({ chart: fakeChartWithTime(), series: fakeSeries(), requestUpdate: () => {} });
  prim.setProfile(PROFILE);
  prim.setVisible(true);
  // Act: T=250 を設定（POC/VA 3 本 + T 縦線 1 本 = 4 本）。
  prim.setCursorTime(250);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  // Assert: 垂直線（x1===x2===250・y は上下に伸びる）が 1 本増える。
  const vlines = target.lines.filter((l) => l.x1 === l.x2 && l.x1 === 250);
  assert.equal(vlines.length, 1, 'T=250 の縦線が 1 本描かれる');
  assert.ok(vlines[0].y1 !== vlines[0].y2, '縦線は垂直（y が上下に伸びる）');
});

test('setCursorTime(null) removes the T vertical line (replay OFF で消える)', () => {
  // Arrange
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget(800);
  prim.attached({ chart: fakeChartWithTime(), series: fakeSeries(), requestUpdate: () => {} });
  prim.setProfile(PROFILE);
  prim.setVisible(true);
  prim.setCursorTime(250);
  // Act: null で消す。
  prim.setCursorTime(null);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  // Assert: 縦線は無い（POC/VA の 3 本のみ）。
  const vlines = target.lines.filter((l) => l.x1 === l.x2);
  assert.equal(vlines.length, 0, 'T 縦線は消える');
  assert.equal(target.lines.length, 3, 'POC/VA の 3 本のみ');
});

test('snapshot ON: T 縦線は描かない（プロト準拠・トリムで T＝右端のため不要）', () => {
  // プロト js/app.js L152-158: 縦線は「asof ON && asoftrim(snapshot) OFF」のときのみ。
  //   snapshot ON はローソクが T までトリムされ T が右端に来るので縦線は不要（描かない）。
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget(800);
  prim.attached({ chart: fakeChartWithTime(), series: fakeSeries(), requestUpdate: () => {} });
  prim.setProfile(PROFILE);
  prim.setVisible(true);
  prim.setSnapshot(true);
  // Act: snapshot ON で T を設定しても縦線は増えない（POC/VA の 3 本のみ）。
  prim.setCursorTime(250);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  const vlines = target.lines.filter((l) => l.x1 === l.x2);
  assert.equal(vlines.length, 0, 'snapshot 中は T 縦線を描かない');
  assert.equal(target.lines.length, 3, 'POC/VA の 3 本のみ');
});

test('snapshot OFF（リプレイのみ）は timeToCoordinate(T) で実足に縦線を立てる（プロト準拠）', () => {
  // Arrange: snapshot OFF（既定）。T=250 → x=250（恒等写像）で実足に追従。
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget(800);
  prim.attached({ chart: fakeChartWithTime(), series: fakeSeries(), requestUpdate: () => {} });
  prim.setProfile(PROFILE);
  prim.setVisible(true);
  // Act
  prim.setCursorTime(250);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  // Assert: x=250（timeToCoordinate）で実足に追従。
  const vlines = target.lines.filter((l) => l.x1 === l.x2);
  assert.equal(vlines.length, 1, 'snapshot OFF は縦線 1 本');
  assert.equal(vlines[0].x1, 250, 'timeToCoordinate に追従');
});

test('setCursorTime skips the line when timeToCoordinate returns null (範囲外 T)', () => {
  // Arrange
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget(800);
  prim.attached({ chart: fakeChartWithTime(new Set([999])), series: fakeSeries(), requestUpdate: () => {} });
  prim.setProfile(PROFILE);
  prim.setVisible(true);
  // Act: 範囲外 T（timeToCoordinate→null）。
  prim.setCursorTime(999);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  // Assert: 縦線は描かれない（POC/VA の 3 本のみ）。
  assert.equal(target.lines.length, 3);
});

test('skips a bin whose price is out of range (priceToCoordinate null)', () => {
  // Arrange: price 101 を範囲外にする → その bin と POC/VAH 線は描かれない
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget();
  // Act
  drawProfile(prim, { chart: fakeChart(), series: fakeSeries(new Set([101])), target });
  // Assert: 描画される bin は 100 と 102 の 2 本
  assert.equal(target.rects.length, 2);
});

test('draw is a safe no-op before attach', () => {
  // Arrange
  const prim = new MarketProfileHistogramPrimitive();
  prim.setProfile(PROFILE);
  prim.setVisible(true);
  const target = fakeTarget();
  // Act / Assert
  assert.doesNotThrow(() => prim.paneViews().forEach((v) => v.renderer().draw(target)));
  assert.equal(target.rects.length, 0);
});

test('setProfile and setVisible request an update so the chart re-draws', () => {
  // Arrange
  const prim = new MarketProfileHistogramPrimitive();
  let updates = 0;
  prim.attached({ chart: fakeChart(), series: fakeSeries(), requestUpdate: () => { updates += 1; } });
  // Act
  prim.setProfile(PROFILE);
  prim.setVisible(true);
  // Assert
  assert.equal(updates, 2);
});


// ===========================================================================
// sessions（日別プロファイル分割・移植元 prototype_260630-01 drawSessions）
//   setSessions(sessions|null): non-null で sessions モード＝各営業日の列を描き、
//   通常の累積バー・POC/VA 線は描かない。null で通常モードへ復帰。
//   列は**全幅に等間隔タイル**（cx=i*colW・時刻座標に置かない＝ズーム非依存・試作準拠）。
//   直近から列幅>=SESS_MIN_COL を確保できる nFit 日だけ描く。列ごとに交互背景＋
//   日付ラベル(MM-DD)、日内 max で正規化・日別 POC 行を白で強調。切捨て時は左下に注記。
// ===========================================================================

// 3 日ぶんの sessions（各 tpo は PROFILE.bins と同じ 3 bin 長・price 100/101/102 に対応）。
const SESSIONS3 = [
  { date: '2024-01-01', tpo: [1, 2, 0] },
  { date: '2024-01-02', tpo: [0, 3, 1] },
  { date: '2024-01-03', tpo: [2, 1, 1] },
];
// date → dateToUnix（UTC 00:00 秒）。primitive の dateToUnix と一致（2024-01-01=1704067200・以降 +86400）。
const DATE_TIME = {
  '2024-01-01': 1704067200,
  '2024-01-02': 1704067200 + 86400,
  '2024-01-03': 1704067200 + 2 * 86400,
};
// dateToUnix(time) → 画面 x への写像（連続 3 日を 100/118/136＝18px 間隔＝barSpacing に）。
const X_OF = {
  [1704067200]: 100,
  [1704067200 + 86400]: 118,
  [1704067200 + 2 * 86400]: 136,
};
// timeToCoordinate が各セッション日を x へ写像する fake chart（範囲外/nullTimes は null＝カリング）。
function fakeChartSessions(nullTimes = new Set()) {
  return {
    timeScale: () => ({ timeToCoordinate: (t) => (nullTimes.has(t) ? null : (X_OF[t] ?? null)) }),
  };
}

function drawSessions(prim, { width = 800, sessions = SESSIONS3, nullTimes = new Set() } = {}) {
  const target = fakeTarget(width);
  prim.attached({ chart: fakeChartSessions(nullTimes), series: fakeSeries(), requestUpdate: () => {} });
  prim.setProfile(PROFILE);
  prim.setVisible(true);
  prim.setSessions(sessions);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  return target;
}

test('sessions mode: draws per-day bars and NOT the cumulative POC/VA lines', () => {
  // Arrange / Act: 3 日とも timeToCoordinate 有効 → 全 3 日描画。
  const prim = new MarketProfileHistogramPrimitive();
  const target = drawSessions(prim, { width: 800 });
  // Assert: 通常の累積 POC/VA 水平線は引かれない（試作準拠）。
  assert.equal(target.lines.length, 0, 'sessions 中は累積 POC/VA 線を描かない');
  // 列背景 3 + 各日の非ゼロ tpo バー（2+2+3=7）= 10 rect。
  assert.equal(target.rects.length, 10, `列背景+per-day バー: ${target.rects.length}`);
  // 列上部に日付ラベル(MM-DD)が全日ぶん描かれる。
  const labels = target.texts.map((t) => t.s);
  assert.ok(labels.includes('01-01') && labels.includes('01-02') && labels.includes('01-03'));
});

test('sessions mode: タイルは各日の時間座標(timeToCoordinate)に配置される（横スクロール/ズーム連動）', () => {
  // 各日を x=100/118/136 に写像 → colW=18(中央値ギャップ)・tileW=15.3。背景左=cx-tileW/2。
  const prim = new MarketProfileHistogramPrimitive();
  const target = drawSessions(prim, { width: 800 });
  const bgs = target.rects.filter((r) => r.h === 600); // 列背景（全高）。
  const tileW = 18 * 0.85;
  assert.deepEqual(
    bgs.map((r) => Math.round(r.x)),
    [100, 118, 136].map((cx) => Math.round(cx - tileW / 2)),
    'タイル中心が timeToCoordinate に一致',
  );
  assert.ok(bgs.every((r) => Math.abs(r.w - tileW) < 1e-6), '列幅=隣接間隔(18)*0.85');
});

test('sessions mode: OHLC 付きセッションは列を方向ティントし終値線のみ描く（上げ=緑・下げ=赤）', () => {
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget(800);
  prim.attached({ chart: fakeChartSessions(), series: fakeSeries(), requestUpdate: () => {} });
  prim.setProfile(PROFILE);
  prim.setVisible(true);
  // 2024-01-01=上げ日(close>open)、2024-01-02=下げ日(close<open)。
  const withOhlc = SESSIONS3.map((s, i) => {
    if (i === 0) return { ...s, open: 100.2, high: 102, low: 100, close: 101.5 };
    if (i === 1) return { ...s, open: 101.5, high: 102, low: 100, close: 100.3 };
    return s;
  });
  prim.setSessions(withOhlc);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  // 水平線（y1==y2）は各 OHLC 日で **終値のみ 1 本**（H/L/始値の線は描かない）。
  const hlines = target.lines.filter((l) => l.y1 === l.y2);
  const ys = hlines.map((l) => l.y1).sort((a, b) => a - b);
  assert.deepEqual(ys, [100.3, 101.5], '終値線のみ（2 日ぶん・H/L/始値は描かない）');
  assert.ok(hlines.every((l) => l.lineWidth === 1), '終値線は控えめ(1px)');
  // 上げ日=軟緑・下げ日=軟赤。
  assert.equal(hlines.find((l) => l.y1 === 101.5).color, 'rgba(38, 166, 154, 0.8)', '上げ=軟緑');
  assert.equal(hlines.find((l) => l.y1 === 100.3).color, 'rgba(239, 83, 80, 0.8)', '下げ=軟赤');
  // ティントは**高安レンジ（low..high）のみ**。fake は toY=price 恒等なので rect は y=low, h=high-low。
  //   上げ日(01-01): low=100/high=102 → 薄緑・y=100・h=2。下げ日(01-02): low=100/high=102 → 薄赤。
  const tintUp = target.rects.find((r) => r.fill === 'rgba(38, 166, 154, 0.12)');
  const tintDown = target.rects.find((r) => r.fill === 'rgba(239, 83, 80, 0.12)');
  assert.ok(tintUp && Math.abs(tintUp.y - 100) < 1e-9 && Math.abs(tintUp.h - 2) < 1e-9,
    '上げ日は高安レンジ(100..102)を薄緑ティント');
  assert.ok(tintDown, '下げ日は薄赤ティント');
  // 全列背景(h=600)ではない（レンジのみ）。
  assert.equal(target.rects.filter((r) => r.h === 600 && r.fill && r.fill.includes('166')).length, 0,
    '全列ティントはしない（高安レンジのみ）');
});

test('sessions mode: OHLC 未付与のセッションは水平線を描かない（後方互換）', () => {
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget(800);
  prim.attached({ chart: fakeChartSessions(), series: fakeSeries(), requestUpdate: () => {} });
  prim.setProfile(PROFILE);
  prim.setVisible(true);
  prim.setSessions(SESSIONS3); // OHLC 無し。
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  assert.equal(target.lines.filter((l) => l.y1 === l.y2).length, 0, 'OHLC 無しは水平線を描かない');
});

test('sessions mode: 視野外の日（timeToCoordinate=null）はカリングして描かない', () => {
  // 01-01 の座標を null に → 01-02/01-03 のみ描く（横スクロールで視野外を省く）。
  const prim = new MarketProfileHistogramPrimitive();
  const target = drawSessions(prim, { nullTimes: new Set([DATE_TIME['2024-01-01']]) });
  const labels = target.texts.map((t) => t.s);
  assert.ok(!labels.includes('01-01'), '視野外の 01-01 は描かない');
  assert.ok(labels.includes('01-02') && labels.includes('01-03'));
  // 列背景 2 + 非ゼロバー(01-02:2)+(01-03:3)=5 → 7 rect。
  assert.equal(target.rects.length, 7, `可視 2 日ぶんのみ: ${target.rects.length}`);
});

test('setSessions(null) restores normal mode: cumulative bars + POC/VA lines return', () => {
  // Arrange: sessions ON → null で通常モードへ復帰。
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget(800);
  prim.attached({ chart: fakeChartSessions(), series: fakeSeries(), requestUpdate: () => {} });
  prim.setProfile(PROFILE);
  prim.setVisible(true);
  prim.setSessions(SESSIONS3);
  prim.setSessions(null); // 復帰
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  // Assert: 通常モード＝3 bins のバー + POC/VAH/VAL の 3 本線。
  assert.equal(target.rects.length, 3, '通常の累積バー 3 本');
  assert.equal(target.lines.length, 3, 'POC/VA の 3 本線が戻る');
});

test('sessions mode: safe no-op when sessions is empty array', () => {
  const prim = new MarketProfileHistogramPrimitive();
  const target = drawSessions(prim, { width: 800, sessions: [] });
  assert.equal(target.rects.length, 0);
  assert.equal(target.lines.length, 0);
});

// 「直近N/全M日」注記は時間軸連動化で廃止。setSessions は単一引数（sessions のみ・total 撤去）。
test('sessions mode: 「直近N/全M日」注記は描かない（注記UI・total 配線を撤去）', () => {
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget(800);
  prim.attached({ chart: fakeChartSessions(), series: fakeSeries(), requestUpdate: () => {} });
  prim.setProfile(PROFILE);
  prim.setVisible(true);
  prim.setSessions(SESSIONS3);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  const labels = target.texts.map((t) => t.s);
  assert.ok(!labels.some((s) => s.includes('全') && s.includes('日')), '注記は描かない');
});

// 時間足毎profile列（tf-period・最小価格単位）の描画: 各列を time→x、各レベルを price→y に横バーで描く。
test('setTfPeriods: 各周期列の占有レベルを time→x / price→y に横バーで描く（POC は際立たせる）', () => {
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget(800);
  prim.attached({ chart: fakeChartWithTime(), series: fakeSeries(), requestUpdate: () => {} });
  prim.setVisible(true);
  // 2 周期: time=100(levels 10:2,11:1 poc10) / time=200(levels 20:1 poc20)。unit=1。
  prim.setTfPeriods([
    { time: 100, levels: [[10, 2], [11, 1]], poc: 10 },
    { time: 200, levels: [[20, 1]], poc: 20 },
  ], 1);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  // price(=y) に横バーが描かれる（fakeSeries は price 恒等・fakeChartWithTime は time 恒等）。
  const ysDrawn = new Set(target.rects.map((r) => Math.round(r.y)));
  assert.ok(ysDrawn.has(10) && ysDrawn.has(11) && ysDrawn.has(20), '価格 10/11/20 に描画');
  // POC(price=10) は C_SESS_POC（白）で描かれる。
  const pocRects = target.rects.filter((r) => Math.round(r.y) === 10 && r.fill === 'rgba(255,255,255,0.95)');
  assert.ok(pocRects.length >= 1, 'POC は白で際立つ');
});

test('setTfPeriods(null): 非適用へ復帰（tf-period 描画を止める）', () => {
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget(800);
  prim.attached({ chart: fakeChartWithTime(), series: fakeSeries(), requestUpdate: () => {} });
  prim.setVisible(true);
  prim.setTfPeriods(null, 1);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  assert.equal(target.rects.length, 0, 'tf-period 非適用時は列を描かない');
});

// ===========================================================================
// sessions 日中足整列（ISSUE-072）: tFirst/tLast（当日実在バー範囲）付与時は
//   timeToIndex(exact)→logicalToCoordinate の日スパンへタイルを整列する。
//   深夜 00:00 バーが無い日（セッション開始 01:00 等）でもタイルが消えない。
// ===========================================================================

// 1m 相当の fake timeScale: バー time 配列から exact index を引く。深夜バーは存在しない
//   （day1: 01:00 と 12:00 の 2 本 / day2: 01:00 と 18:00 の 2 本）。x = 100 + index*10。
function fakeChartIntraday(barTimes) {
  return {
    timeScale: () => ({
      timeToCoordinate: () => null,          // 深夜 time は非バー＝旧経路では全カリングされる状況。
      timeToIndex: (t, nearest) => {
        const i = barTimes.indexOf(t);
        return i >= 0 ? i : (nearest ? 0 : null);
      },
      logicalToCoordinate: (i) => 100 + i * 10,
    }),
  };
}

const DAY1 = 1704067200;            // 2024-01-01 00:00 UTC
const DAY2 = DAY1 + 86400;          // 2024-01-02
const INTRA_BARS = [DAY1 + 3600, DAY1 + 43200, DAY2 + 3600, DAY2 + 64800];

test('sessions 日中足: tFirst/tLast 付与時は日スパン（実在バー範囲）へタイル整列（深夜バー不在でも描く）', () => {
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget(800);
  prim.attached({ chart: fakeChartIntraday(INTRA_BARS), series: fakeSeries(), requestUpdate: () => {} });
  prim.setProfile(PROFILE);
  prim.setVisible(true);
  prim.setSessions([
    { date: '2024-01-01', tpo: [1, 2, 0], tFirst: DAY1 + 3600, tLast: DAY1 + 43200 },
    { date: '2024-01-02', tpo: [0, 3, 1], tFirst: DAY2 + 3600, tLast: DAY2 + 64800 },
  ]);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  // day1: idx 0..1 → x 100..110（span 10・中心 105・tw=8.5）／day2: idx 2..3 → x 120..130。
  const bgs = target.rects.filter((r) => r.h === 600);
  assert.equal(bgs.length, 2, '深夜バー不在（timeToCoordinate=null）でも両日ともタイル描画');
  assert.deepEqual(
    bgs.map((r) => Math.round(r.x * 100) / 100),
    [105 - 4.25, 125 - 4.25],
    'タイル中心=日スパン中央・幅=スパン*0.85',
  );
  assert.ok(bgs.every((r) => Math.abs(r.w - 8.5) < 1e-9), '列幅=当日実在バー範囲のピクセル幅*0.85');
});

test('sessions 日中足: tFirst/tLast 未付与は従来経路（timeToCoordinate=null なら従来どおりカリング・後方互換）', () => {
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget(800);
  prim.attached({ chart: fakeChartIntraday(INTRA_BARS), series: fakeSeries(), requestUpdate: () => {} });
  prim.setProfile(PROFILE);
  prim.setVisible(true);
  prim.setSessions([{ date: '2024-01-01', tpo: [1, 2, 0] }]); // tFirst/tLast 無し（旧呼び出し）。
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  assert.equal(target.rects.filter((r) => r.h === 600).length, 0, '旧経路の挙動は不変（カリング）');
});

test('sessions 1D 相当: 単一バー日（tFirst===tLast）は従来の深夜アンカー＋中央値幅（byte 不変）', () => {
  // 1D: date と深夜バーが 1:1（X_OF 写像・従来テストと同じ座標系）。tFirst/tLast を同値で付与しても
  //   span を作らず（iR<=iL）、従来の timeToCoordinate(深夜)＋中央値幅の描画になる。
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget(800);
  const chart = {
    timeScale: () => ({
      timeToCoordinate: (t) => (X_OF[t] ?? null),
      timeToIndex: (t) => ({ [DATE_TIME['2024-01-01']]: 0, [DATE_TIME['2024-01-02']]: 1, [DATE_TIME['2024-01-03']]: 2 }[t] ?? null),
      logicalToCoordinate: (i) => 100 + i * 18,
    }),
  };
  prim.attached({ chart, series: fakeSeries(), requestUpdate: () => {} });
  prim.setProfile(PROFILE);
  prim.setVisible(true);
  prim.setSessions(SESSIONS3.map((s) => ({ ...s, tFirst: DATE_TIME[s.date], tLast: DATE_TIME[s.date] })));
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  const bgs = target.rects.filter((r) => r.h === 600);
  const tileW = 18 * 0.85;
  assert.deepEqual(
    bgs.map((r) => Math.round(r.x)),
    [100, 118, 136].map((cx) => Math.round(cx - tileW / 2)),
    '1D は従来どおり深夜アンカー中心＋中央値幅',
  );
});

// ===========================================================================
