// market_profile_primitive.js（MarketProfileHistogramPrimitive）の描画仕様検証。
//
// 設計入力: pair_lines_primitive.test.js を手本（fake target/series で座標・矩形・線分を観測）。
//   v5 primitive 事実: attached({chart,series,requestUpdate})・paneViews()→renderer().draw(target)→
//   target.useBitmapCoordinateSpace(scope=>scope.context 描画)・series.priceToCoordinate（範囲外 null）。
//   実 canvas / 実 lwc には依存しない。構造: Arrange-Act-Assert。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { MarketProfileHistogramPrimitive, heatColor, sessionValueArea } from '../js/adapter/front/market_profile_primitive.js';

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
// date → UTC 00:00 time（秒）。2024-01-01=1704067200・以降 +86400。
const DATE_TIME = {
  '2024-01-01': 1704067200,
  '2024-01-02': 1704067200 + 86400,
  '2024-01-03': 1704067200 + 2 * 86400,
};
// timeToCoordinate が DATE_TIME を x へ写像する fake chart（date time→x 恒等・範囲外 null）。
function fakeChartSessions(nullTimes = new Set()) {
  return {
    timeScale: () => ({ timeToCoordinate: (t) => (nullTimes.has(t) ? null : t) }),
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
  // Arrange / Act: 800px 幅 → nFit = floor(800/102) = 7 >= 3 日 → 全 3 日描画。
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

test('sessions mode: nFit limits to the most recent days when width is narrow', () => {
  // Arrange: 幅 250px → nFit = floor(250/102) = 2 → 直近 2 日（01-02, 01-03）のみ。
  const prim = new MarketProfileHistogramPrimitive();
  const target = drawSessions(prim, { width: 250 });
  // 列背景 2 + 直近 2 日の非ゼロ数 (01-02: 2)+(01-03: 3)=5 → 7 rect。01-01 は含まれない。
  assert.equal(target.rects.length, 7, `直近 2 日ぶんのみ: ${target.rects.length}`);
  const labels = target.texts.map((t) => t.s);
  assert.ok(!labels.includes('01-01') && labels.includes('01-02') && labels.includes('01-03'));
  // 切捨て時は「直近N/全M日」注記（試作準拠・total 未提供時は all.length フォールバック）。
  assert.ok(labels.some((s) => s.includes('直近2/全3日')), `注記: ${labels}`);
});

test('sessions mode: tiles columns across full width (時刻座標に依存しない)', () => {
  // 回帰: 列は cx=i*colW の全幅タイル（ズームアウトで右端に潰れない）。
  //   timeToCoordinate が全日 null でも列は描かれる（時刻座標を使わない）。
  const prim = new MarketProfileHistogramPrimitive();
  const target = drawSessions(prim, {
    width: 306, // colW = 306/3 = 102
    nullTimes: new Set(Object.values(DATE_TIME)), // 全日の座標を null に
  });
  // 3 列の背景が x=0,102,204 にタイルされる（時刻座標非依存）。
  const bgs = target.rects.filter((r) => r.h === 600);
  assert.deepEqual(bgs.map((r) => Math.round(r.x)), [0, 102, 204]);
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

// sessions_total（キャップ前の実日数・修正1）: setSessions(sessions, total) の total が注記の M に載る。
//   controller はキャップ後の直近 60 日ぶんだけ sessions を返すため、受信 sessions.length では実日数を
//   表せない。total を渡すと注記「直近N/全M日」の M がキャップ前実日数になり誤読を防ぐ。
test('sessions mode: annotation uses total (pre-cap day count) for the M in 直近N/全M日', () => {
  // Arrange: 3 日ぶん受信・total=4146（キャップ前の実日数）・幅 250 → nFit=2（直近 2 日描画）。
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget(250);
  prim.attached({ chart: fakeChartSessions(), series: fakeSeries(), requestUpdate: () => {} });
  prim.setProfile(PROFILE);
  prim.setVisible(true);
  // Act: total を第 2 引数で渡す。
  prim.setSessions(SESSIONS3, 4146);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  // Assert: 注記の M は受信 3 でも直近 2 でもなく、total=4146。
  const labels = target.texts.map((t) => t.s);
  assert.ok(labels.some((s) => s.includes('直近2/全4146日')), `注記: ${labels}`);
});

test('sessions mode: annotation falls back to all.length when total is omitted (後方互換)', () => {
  // Arrange: total 未提供（従来の単一引数呼び出し）→ M は受信 sessions 長（all.length=3）。
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget(250); // nFit=2 → 直近 2 日・切捨てで注記が出る。
  prim.attached({ chart: fakeChartSessions(), series: fakeSeries(), requestUpdate: () => {} });
  prim.setProfile(PROFILE);
  prim.setVisible(true);
  // Act: total を渡さない（従来 API）。
  prim.setSessions(SESSIONS3);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  // Assert: M は all.length=3（フォールバック）。
  const labels = target.texts.map((t) => t.s);
  assert.ok(labels.some((s) => s.includes('直近2/全3日')), `注記: ${labels}`);
});

// ===========================================================================
// 単日フォーカス（列クリックで拡大・本タスク）
//   sessionDateAt(xRatio): 直近描画の一覧レイアウトで xRatio(0..1)→列 date（範囲外/非表示 null）。
//   setSessionFocus(date): その 1 日を全幅で描画（列背景タイル無し・「クリックで一覧へ」注記）。
//   focus date 不在時は通常一覧へフォールバック。setSessionFocus(null) で一覧復帰。
// ===========================================================================

test('sessionDateAt: maps xRatio 0..1 to the drawn column date after a list draw', () => {
  // Arrange: 幅 800 → nFit=7 >= 3 → 3 列（01-01/01-02/01-03）を全幅タイル（各 1/3）で描画。
  const prim = new MarketProfileHistogramPrimitive();
  drawSessions(prim, { width: 800 });
  // Assert: 0.0→列0(01-01)・0.5→列1(01-02)・0.9→列2(01-03)。
  assert.equal(prim.sessionDateAt(0.0), '2024-01-01');
  assert.equal(prim.sessionDateAt(0.5), '2024-01-02');
  assert.equal(prim.sessionDateAt(0.9), '2024-01-03');
});

test('sessionDateAt: uses the recent nFit subset (narrow width) as the hit-test source', () => {
  // Arrange: 幅 250 → nFit=2 → 直近 2 日（01-02/01-03）のみ描画＝ヒットテストも 2 列。
  const prim = new MarketProfileHistogramPrimitive();
  drawSessions(prim, { width: 250 });
  assert.equal(prim.sessionDateAt(0.25), '2024-01-02'); // 左半分＝直近 2 日の 1 日目。
  assert.equal(prim.sessionDateAt(0.75), '2024-01-03'); // 右半分＝2 日目。
});

test('sessionDateAt: returns null for out-of-range xRatio (境界)', () => {
  const prim = new MarketProfileHistogramPrimitive();
  drawSessions(prim, { width: 800 });
  assert.equal(prim.sessionDateAt(-0.01), null); // 下限外
  assert.equal(prim.sessionDateAt(1.0), null);   // 右端 1.0 は範囲外
  assert.equal(prim.sessionDateAt(1.5), null);   // 上限外
  assert.equal(prim.sessionDateAt(NaN), null);   // NaN
});

test('sessionDateAt: returns null when not in sessions list mode (通常モード/非描画)', () => {
  // Arrange: sessions を描画していない（通常モード）→ 一覧レイアウト未確定＝ヒットテスト対象なし。
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget(800);
  prim.attached({ chart: fakeChart(), series: fakeSeries(), requestUpdate: () => {} });
  prim.setProfile(PROFILE);
  prim.setVisible(true);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  assert.equal(prim.sessionDateAt(0.5), null);
});

test('setSessionFocus(date): draws only that day full-width with a 「クリックで一覧へ」note', () => {
  // Arrange: 3 日ぶん・幅 800。focus に 01-02 を指定 → その 1 日だけ全幅描画。
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget(800);
  prim.attached({ chart: fakeChartSessions(), series: fakeSeries(), requestUpdate: () => {} });
  prim.setProfile(PROFILE);
  prim.setVisible(true);
  prim.setSessions(SESSIONS3);
  // Act
  prim.setSessionFocus('2024-01-02');
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  // Assert: 列背景タイル（h=600）は無い＝一覧の複数列を描いていない。
  const bgs = target.rects.filter((r) => r.h === 600);
  assert.equal(bgs.length, 0, 'フォーカス中は一覧の列背景を描かない');
  // 01-02 の非ゼロ tpo は [0,3,1] → 2 本のバー。
  assert.equal(target.rects.length, 2, 'focus した 1 日の非ゼロ bin ぶんのバー');
  // バーは全幅寄り（左端 x=4 起点）。一覧の列内バー（x=left+2, left=i*colW）ではない。
  assert.ok(target.rects.every((r) => r.x === 4), 'フォーカスバーは全幅左端(4)起点');
  // ヘッダの日付（大きめ）と「クリックで一覧へ」注記が出る。
  const labels = target.texts.map((t) => t.s);
  assert.ok(labels.includes('2024-01-02'), 'ヘッダに日付');
  assert.ok(labels.includes('クリックで一覧へ'), '一覧へ戻す注記');
});

test('setSessionFocus: falls back to the list when the focus date is not in sessions (防御)', () => {
  // Arrange: 受信 sessions に無い date を focus → 通常一覧（3 列）へフォールバック。
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget(800);
  prim.attached({ chart: fakeChartSessions(), series: fakeSeries(), requestUpdate: () => {} });
  prim.setProfile(PROFILE);
  prim.setVisible(true);
  prim.setSessions(SESSIONS3);
  // Act: 存在しない date。
  prim.setSessionFocus('1999-12-31');
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  // Assert: 一覧の列背景 3 が描かれる（フォールバック）。
  const bgs = target.rects.filter((r) => r.h === 600);
  assert.equal(bgs.length, 3, '不在 date は一覧へフォールバック');
});

test('setSessionFocus(null): restores the list view (直近タイル)', () => {
  // Arrange: focus → null で一覧へ復帰。
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget(800);
  prim.attached({ chart: fakeChartSessions(), series: fakeSeries(), requestUpdate: () => {} });
  prim.setProfile(PROFILE);
  prim.setVisible(true);
  prim.setSessions(SESSIONS3);
  prim.setSessionFocus('2024-01-02');
  prim.setSessionFocus(null); // 一覧へ
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  // Assert: 一覧の 3 列背景が戻る＋ヒットテストも一覧の 3 列を解ける。
  const bgs = target.rects.filter((r) => r.h === 600);
  assert.equal(bgs.length, 3, 'null で一覧の 3 列へ復帰');
  assert.equal(prim.sessionDateAt(0.5), '2024-01-02');
});

// ---------------------------------------------------------------------------
// sessionPriceRange（単日フォーカスの価格軸ズーム源）: tpo>0 の bin 価格 min/max ± 半ビン。
// ---------------------------------------------------------------------------
test('sessionPriceRange: returns min/max of non-zero bins ± half bin', () => {
  const prim = new MarketProfileHistogramPrimitive();
  prim.setProfile(PROFILE); // bins price 100/101/102（step=1・half=0.5）
  prim.setSessions(SESSIONS3);
  // 01-01 の tpo=[1,2,0] → 非ゼロ bin は 100,101 → {min:99.5, max:101.5}
  assert.deepEqual(prim.sessionPriceRange('2024-01-01'), { min: 99.5, max: 101.5 });
});

test('sessionPriceRange: null for unknown date / all-zero day / no sessions', () => {
  const prim = new MarketProfileHistogramPrimitive();
  prim.setProfile(PROFILE);
  assert.equal(prim.sessionPriceRange('2024-01-01'), null); // sessions 未設定
  prim.setSessions([{ date: '2024-01-01', tpo: [0, 0, 0] }]);
  assert.equal(prim.sessionPriceRange('2024-01-01'), null); // 全ゼロ日
  assert.equal(prim.sessionPriceRange('9999-01-01'), null); // 不在 date
});

// ===========================================================================
// sessionValueArea（純関数・backend _value_area の JS 版）: 日別 tpo を降順（同値は index 昇順）に
//   累積し 総量×va に達する bin 集合の中心価格 min/max を {vah,val} で返す。
// ===========================================================================
const VA_BINS = [
  { price: 100 }, { price: 101 }, { price: 102 }, { price: 103 },
];

test('sessionValueArea: hand-calc — tpo=[1,2,3,4] va=0.70 → VAH=103 VAL=102', () => {
  // total=10・threshold=7。降順 order=[103(4),102(3),...] → cum 4,7 で停止。VA={103,102}。
  assert.deepEqual(sessionValueArea([1, 2, 3, 4], VA_BINS, 0.70), { vah: 103, val: 102 });
});

test('sessionValueArea: ties broken by index ascending (決定論・backend 一致)', () => {
  // tpo=[2,2,2,2] total=8 threshold=5.6。order は index 昇順 → 100,101,102 で cum 2,4,6>=5.6 停止。
  //   VA={100,101,102} → VAH=102 VAL=100。同値でも index 昇順で決定論。
  assert.deepEqual(sessionValueArea([2, 2, 2, 2], VA_BINS, 0.70), { vah: 102, val: 100 });
});

test('sessionValueArea: full VA when a single bin already exceeds threshold', () => {
  // tpo=[0,0,0,10] total=10 threshold=7。最頻 bin(103) 単独で 10>=7 → VA={103}。
  assert.deepEqual(sessionValueArea([0, 0, 0, 10], VA_BINS, 0.70), { vah: 103, val: 103 });
});

test('sessionValueArea: null for total 0 / empty / length mismatch guarded', () => {
  assert.equal(sessionValueArea([0, 0, 0, 0], VA_BINS, 0.70), null); // 総量 0
  assert.equal(sessionValueArea([], VA_BINS, 0.70), null);           // 空
  assert.equal(sessionValueArea(null, VA_BINS, 0.70), null);         // 非配列
  // 長さ不一致は短い方まで（tpo=[5] だけ → bin 100 のみ）。
  assert.deepEqual(sessionValueArea([5], VA_BINS, 0.70), { vah: 100, val: 100 });
});

// ===========================================================================
// 単日フォーカスの分析メトリクス表示（本タスク・追加2）: 拡大中の左上に
//   POC / VAH VAL / 合計[atom] / レンジ のテキストと、POC 赤細線 + VA 灰破線を描く。
//   一覧モードでは出さない。
// ===========================================================================
// atom 付き profile（合計の単位に atom を添える）。bins は PROFILE と同じ price 100/101/102。
const PROFILE_ATOM = { ...PROFILE, atom: 'tick滞在秒(セッション認識)' };
// フォーカス対象日: tpo=[1,2,0]（price100=1, price101=2, price102=0）。
//   total=3・POC=price101（最頻）・レンジ=100〜101（tpo>0）。VA(0.70): threshold=2.1、
//   order=[101(2),100(1),102(0)] → cum 2,3>=2.1（101,100）→ VAH=101 VAL=100。
const FOCUS_SESSIONS = [{ date: '2024-01-02', tpo: [1, 2, 0] }];

function drawFocus(prim, { profile = PROFILE_ATOM, width = 800 } = {}) {
  const target = fakeTarget(width);
  prim.attached({ chart: fakeChartSessions(), series: fakeSeries(), requestUpdate: () => {} });
  prim.setProfile(profile);
  prim.setVisible(true);
  prim.setSessions(FOCUS_SESSIONS);
  prim.setSessionFocus('2024-01-02');
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  return target;
}

test('focus view: shows POC/VAH/VAL/合計/レンジ metric texts with atom unit', () => {
  const prim = new MarketProfileHistogramPrimitive();
  const target = drawFocus(prim);
  const labels = target.texts.map((t) => t.s);
  assert.ok(labels.includes('POC 101'), `POC 行: ${labels}`);
  assert.ok(labels.includes('VAH 101  VAL 100'), `VA 行: ${labels}`);
  assert.ok(labels.includes('合計 3 tick滞在秒(セッション認識)'), `合計行(atom 単位): ${labels}`);
  assert.ok(labels.includes('レンジ 100〜101'), `レンジ行: ${labels}`);
});

test('focus view: 合計 has no unit when profile.atom is absent', () => {
  const prim = new MarketProfileHistogramPrimitive();
  const target = drawFocus(prim, { profile: PROFILE }); // atom なし
  const labels = target.texts.map((t) => t.s);
  assert.ok(labels.includes('合計 3'), `単位なしの合計行: ${labels}`);
  assert.ok(!labels.some((s) => s.startsWith('合計 3 ')), 'atom 無しでは単位を付けない');
});

test('focus view: draws a POC red line and two VA dashed lines', () => {
  const prim = new MarketProfileHistogramPrimitive();
  const target = drawFocus(prim);
  // POC 線: 実線（dash 空）・y=101（POC 価格・恒等写像）。
  const solid = target.lines.filter((l) => l.dash.length === 0);
  assert.equal(solid.length, 1, 'POC は 1 本の実線');
  assert.equal(solid[0].y1, 101, 'POC 線は POC 価格(101)');
  // VA 帯線: 破線 2 本（VAH=101 / VAL=100）。
  const dashed = target.lines.filter((l) => l.dash.length > 0);
  assert.equal(dashed.length, 2, 'VA は破線 2 本');
  const vaYs = dashed.map((l) => l.y1).sort((a, b) => a - b);
  assert.deepEqual(vaYs, [100, 101], 'VA 破線は VAL(100)/VAH(101)');
});

test('focus view: metric lines are horizontal (full width)', () => {
  const prim = new MarketProfileHistogramPrimitive();
  const target = drawFocus(prim, { width: 800 });
  for (const l of target.lines) {
    assert.equal(l.y1, l.y2, '水平線（y 一定）');
    assert.equal(l.x1, 0);
    assert.equal(l.x2, 800);
  }
});

test('list mode: does NOT show focus metric texts or VA lines', () => {
  // 一覧モード（focus なし）では POC/VAH/VAL/合計/レンジ のテキストも VA 線も出ない。
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget(800);
  prim.attached({ chart: fakeChartSessions(), series: fakeSeries(), requestUpdate: () => {} });
  prim.setProfile(PROFILE_ATOM);
  prim.setVisible(true);
  prim.setSessions(FOCUS_SESSIONS); // focus は設定しない＝一覧
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  const labels = target.texts.map((t) => t.s);
  assert.ok(!labels.some((s) => s.startsWith('POC ')), '一覧では POC テキストなし');
  assert.ok(!labels.some((s) => s.startsWith('VAH ')), '一覧では VA テキストなし');
  assert.ok(!labels.some((s) => s.startsWith('合計')), '一覧では合計テキストなし');
  assert.ok(!labels.some((s) => s.startsWith('レンジ')), '一覧ではレンジテキストなし');
  assert.equal(target.lines.length, 0, '一覧では POC/VA 線を描かない');
});

// ===========================================================================
// 単日拡大の分割レイアウト（本タスク）: dayPath があれば左 70% にティック推移の価格ライン
//   （polyline・x=(t-min)/(max-min)*width*0.70）、ヒストグラムは右 30%（x0 >= width*0.70）。
//   dayPath 無しは従来どおり全幅ヒストグラム（回帰）。FOCUS_PATH_FRACTION=0.70。
// ===========================================================================
// bins price 100/101/102（既存 PROFILE）。focus 日 tpo=[1,2,0]。
const DP_SESSIONS = [{ date: '2024-01-02', tpo: [1, 2, 0] }];
// その日のティック推移（t 昇順・p は既存 fakeSeries が恒等写像で y にする値域）。
const DP_PATH = [
  { t: 1000, p: 100 },
  { t: 1500, p: 101 },
  { t: 2000, p: 102 },
];

function drawFocusWithPath(prim, { width = 800, path = DP_PATH } = {}) {
  const target = fakeTarget(width);
  prim.attached({ chart: fakeChartSessions(), series: fakeSeries(), requestUpdate: () => {} });
  prim.setProfile(PROFILE);
  prim.setVisible(true);
  prim.setSessions(DP_SESSIONS);
  prim.setSessionFocus('2024-01-02', path); // 第 2 引数で dayPath を渡す。
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  return target;
}

test('focus split: with dayPath, draws a price polyline within the left 70% of width', () => {
  const prim = new MarketProfileHistogramPrimitive();
  const target = drawFocusWithPath(prim, { width: 800 });
  // ポリライン（>2 頂点）が 1 本描かれる。
  assert.equal(target.polylines.length, 1, '左70%に価格ポリライン 1 本');
  const poly = target.polylines[0];
  assert.equal(poly.pts.length, DP_PATH.length, '全ティック点が頂点になる');
  // すべての頂点 x が余白込みの左70%領域（PAD..0.7*width-PAD・PAD=32）に収まる（実機FB: 両側余白）。
  const PAD = 32;
  for (const pt of poly.pts) {
    assert.ok(pt.x >= PAD - 1e-6 && pt.x <= 800 * 0.70 - PAD + 1e-6, `polyline x=${pt.x} は余白内側`);
  }
  // 先頭 t=min → x=PAD、末尾 t=max → x=0.7*width-PAD（余白ぶん内側に整列）。
  assert.ok(Math.abs(poly.pts[0].x - PAD) < 1e-6, '先頭 t=min → x=PAD(32)');
  assert.ok(Math.abs(poly.pts.at(-1).x - (800 * 0.70 - PAD)) < 1e-6, '末尾 t=max → x=0.7*width-PAD');
});

test('focus split: with dayPath, histogram bars start within the right 30% (x0 >= width*0.7)', () => {
  const prim = new MarketProfileHistogramPrimitive();
  const target = drawFocusWithPath(prim, { width: 800 });
  // ヒストグラムのバー（focus 日の非ゼロ bin）が右 30% 内から始まる。
  const bars = target.rects.filter((r) => r.h !== 600); // 列背景（h=600）は除外
  assert.ok(bars.length >= 1, 'ヒストグラムのバーが描かれる');
  for (const b of bars) {
    assert.ok(b.x >= 800 * 0.70 - 1e-6, `バー x0=${b.x} は右30%内（>=560）`);
  }
});

test('focus split: without dayPath falls back to full-width histogram (回帰・従来)', () => {
  const prim = new MarketProfileHistogramPrimitive();
  const target = drawFocusWithPath(prim, { width: 800, path: null }); // path 無し
  // ポリラインは描かれない。
  assert.equal(target.polylines.length, 0, 'path 無しはポリライン無し');
  // バーは従来どおり全幅左端（x=4）起点。
  const bars = target.rects.filter((r) => r.h !== 600);
  assert.ok(bars.length >= 1);
  assert.ok(bars.every((b) => b.x === 4), 'path 無しは全幅左端(4)起点（従来）');
});

test('focus split: setSessionFocus(null) clears the day path (no polyline after unfocus→refocus without path)', () => {
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget(800);
  prim.attached({ chart: fakeChartSessions(), series: fakeSeries(), requestUpdate: () => {} });
  prim.setProfile(PROFILE);
  prim.setVisible(true);
  prim.setSessions(DP_SESSIONS);
  prim.setSessionFocus('2024-01-02', DP_PATH); // path あり
  prim.setSessionFocus(null);                  // 解除（path クリア）
  prim.setSessionFocus('2024-01-02');          // 第 2 引数なし＝path 無し
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  assert.equal(target.polylines.length, 0, '解除後の path 無し再フォーカスはポリライン無し');
});

// ---------------------------------------------------------------------------
// ライン上ラベル（実機FB）: POC/VAH/VAL の各水平線の直上・左端(x=6)に「項目名 価格」を表示。
//   右上メトリクスブロック（右寄せ・x=width-6）とは位置で区別できる。
// ---------------------------------------------------------------------------
test('focus view: each metric line has an on-line label (項目名+価格・左端 x=6)', () => {
  const prim = new MarketProfileHistogramPrimitive();
  const target = drawFocus(prim, { width: 800 });
  // 左端(x=6)のライン上ラベルとして POC/VAH/VAL の3つが描かれる（右上ブロックは x=width-6=794）。
  const onLine = target.texts.filter((t) => t.x === 6);
  const names = onLine.map((t) => t.s.split(' ')[0]);
  assert.ok(names.includes('POC'), `POC ラベル: ${JSON.stringify(names)}`);
  assert.ok(names.includes('VAH'), `VAH ラベル: ${JSON.stringify(names)}`);
  assert.ok(names.includes('VAL'), `VAL ラベル: ${JSON.stringify(names)}`);
  // 各ラベルに数値（価格）が含まれる。
  for (const t of onLine) {
    if (['POC', 'VAH', 'VAL'].includes(t.s.split(' ')[0])) {
      assert.ok(/\d/.test(t.s), `価格を含む: ${t.s}`);
    }
  }
});

// ---------------------------------------------------------------------------
// _drawDayPath 縮退経路（レビュー🟡-2）: 全点同時刻・1点・toY null 分断の防御分岐の回帰網。
// ---------------------------------------------------------------------------
test('focus split: day path with all-same timestamps draws no polyline (時間レンジ縮退)', () => {
  const prim = new MarketProfileHistogramPrimitive();
  const target = drawFocusWithPath(prim, {
    width: 800,
    path: [{ t: 100, p: 10 }, { t: 100, p: 11 }, { t: 100, p: 12 }],
  });
  assert.equal(target.polylines.length, 0, '同時刻のみの path はラインを描かない');
});

test('focus split: a single-point day path draws no polyline (頂点不足)', () => {
  const prim = new MarketProfileHistogramPrimitive();
  const target = drawFocusWithPath(prim, { width: 800, path: [{ t: 100, p: 10 }] });
  assert.equal(target.polylines.length, 0, '1 点では描かない');
});

test('focus split: toY=null price splits the polyline into runs (範囲外価格で分断)', () => {
  // Arrange: p=15 が範囲外（toY null）→ 前後 2 run に分断される（drawFocusWithPath と同構成）。
  const prim = new MarketProfileHistogramPrimitive();
  const series = fakeSeries(new Set([15]));
  const target = fakeTarget(800);
  prim.attached({ chart: fakeChartSessions(), series, requestUpdate: () => {} });
  prim.setProfile(PROFILE);
  prim.setVisible(true);
  prim.setSessions(DP_SESSIONS);
  prim.setSessionFocus('2024-01-02', [
    { t: 100, p: 10 }, { t: 200, p: 11 }, { t: 300, p: 15 }, // 15 は範囲外
    { t: 400, p: 12 }, { t: 500, p: 13 },
  ]);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  // Assert: 2 本の run（100-200 / 400-500）に分断される。各 run は 2 頂点＝polylines（>2頂点）でなく
  //   lines に記録されるため、day-path 色（0.95 アルファ）の stroke 数で数える。
  const runs = target.lines.filter((l) => String(l.color).includes('0.95'));
  assert.equal(runs.length, 2, `分断で 2 run: ${runs.length}`);
});
