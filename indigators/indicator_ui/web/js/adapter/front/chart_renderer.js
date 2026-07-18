// ChartRenderer（adapter/front/chart_renderer.js）— ChartRendererPort 実装・upstream 隔離点（唯一）。
//
// 設計入力: 内部設計書 §3.3.4 / §7.1.2。
// ★ lightweight-charts v5.2.0 の JS API 名（addSeries / addPane / removePane / panes /
//   createPriceLine / setData / applyOptions / removeSeries / removePriceLine /
//   subscribeCrosshairMove / createTextWatermark）を呼ぶのは本ファイルだけ。
//   他ファイルでこれらの API 名を参照しない（§2.2 grep 0 件強制）。
//
// 隠蔽する責務:
//   - line       → chart/pane.addSeries(LineSeries, ...) + series.setData(points)
//   - histogram  → chart/pane.addSeries(HistogramSeries, ...)（data[].color でバー別着色）
//   - horizontal_line → host series.createPriceLine(...)
//   - pane 指標（オシレータ）は専用 pane を生成（機能①: pane ごとに独立した価格軸／
//     機能④: pane 境界 separator のドラッグで高さ調整＝v5 既定 ON）。
//   - 機能②: pane 左上に指標名のテキストウォーターマーク。
//   - 機能③: クロスヘア移動で当該 pane の系列値をウォーターマークへ追記。
//   - lineStyle 文字列 → v5 LineStyle 整数（solid=0 / dotted=1 / dashed=2）
//   - 系列キー {instanceId}::{series_name}（§5.7 衝突回避）
//
// DOM 非依存: chart / mainSeries / lwc は composition root から注入（テストは Fake を渡す）。

import { fmtValue } from './format.js';
import { seriesKind } from '../../domain/series_kind.js';

// lineStyle 文字列 → lightweight-charts LineStyle 整数（v4/v5 共通: Solid=0 / Dotted=1 / Dashed=2）。
const LINE_STYLE_INT = Object.freeze({ solid: 0, dotted: 1, dashed: 2 });

function toLineStyleInt(style) {
  return LINE_STYLE_INT[style] ?? LINE_STYLE_INT.solid;
}

// メイン（ローソク）pane と オシレータ pane の高さ相対比。ローソクを大きく見せる初期値。
//   ユーザーは pane separator のドラッグ（機能④）で後から自由に調整できる。
const MAIN_PANE_STRETCH = 3;
const INDICATOR_PANE_STRETCH = 1;

// ISSUE-114/115: チャート右端の常設余白（チャート幅比率）。最新足の右側に常時 幅×この比率 の
//   空きを px 基準で確保する。lwc rightOffset は論理バー単位のため、バー数固定では全体表示
//   （微小 barSpacing）で余白が消える（ISSUE-115）＝_syncRightOffset が width×frac÷barSpacing を
//   都度再計算して適用する。MP プロファイル右マージン（setRightMarginFraction）とは比率の max 合成。
const BASE_RIGHT_MARGIN_FRACTION = 0.05;

const WATERMARK_COLOR = 'rgba(209, 212, 220, 0.9)';

// v6（§12）: ホバー中ペア外のローソク足に被せる極暗色（背景 #131722 に近い不透明暗色）。
//   per-bar color/borderColor/wickColor を本色で上書きし、ローソクのみを限りなく減光する
//   （背景ピクセルは一切変更しない）。ペア内バーは色を付けず原色（既定 up/down 着色）に委ねる。
const DIM_CANDLE_COLOR = '#16191f';

// sessions（日別プロファイル分割）: ローソク透明化用の色。透明＝価格軸は残しローソクだけ消す。
//   復元色は composition_root_front.js の mainSeries 既定（up=#26a69a / down=#ef5350）と一致させる。
const TRANSPARENT_COLOR = 'rgba(0,0,0,0)';
const CANDLE_UP_COLOR = '#26a69a';
const CANDLE_DOWN_COLOR = '#ef5350';

// σ 水準線のカラースキーム（histogram の level_colors と同義: 中心からの距離で 緑→赤）。
// 端点は common/level_colors.py の _CALM/_HOT（#2e7d32 / #d32f2f）に一致させる。
const SCHEME_CALM = [46, 125, 50]; // 緑（中心＝穏やか）
const SCHEME_HOT = [211, 47, 47]; // 赤（両極端＝過熱）
// 明度係数（背景 #131722 に馴染ませる。小さいほど暗い。0..1）。灰一色より色で識別でき、かつ控えめ。
const LEVEL_LINE_DIM = 0.55;

function lerp(a, b, t) {
  return a + (b - a) * t;
}

// 価格軸ホイールズームの中核式（純関数・単体テスト用に export）。
//   range: { min, max } 現在の価格レンジ、price: カーソル位置の価格、deltaY: wheel の deltaY。
//   ズーム係数 f = 0.9^(-deltaY/100)（deltaY<0=上=ズームイン=レンジ×0.90／deltaY>0=下=×1/0.90）。
//   トラックパッド（微小 deltaY 連続）は指数で滑らかに比例。1 イベントの暴れ防止で f∈[0.5, 2] にクランプ。
//   カーソル価格 p を中心に newMin = p-(p-min)*f / newMax = p+(max-p)*f。
//   span を現 span×1e-4 未満に縮めない（最小 span クランプ）。price が range 外なら中央基準。
const PRICE_ZOOM_F_MIN = 0.5;
const PRICE_ZOOM_F_MAX = 2;
const PRICE_ZOOM_SPAN_MIN_RATIO = 1e-4;

// データ全幅に対する絶対クランプ（暴走防止・実機バグ修正）: ホイール連打/慣性スクロールで
//   「読む→係数→書く」のフィードバックが複利増幅し 1e24 等へ発散したため、ズーム結果を
//   データ（baseCandles の low/high）由来の絶対範囲に制限する。span ∈ [dataSpan×1e-4, dataSpan×5]、
//   さらに表示中心（c）をデータ範囲内 [dataRange.min, dataRange.max] にクランプする（最大ズームアウトでも
//   ローソクが視界から消えない）。純関数。
const PRICE_ZOOM_MAX_SPAN_RATIO = 5;
const PRICE_ZOOM_ABS_MIN_SPAN_RATIO = 1e-4;

export function clampPriceRange(range, dataRange) {
  if (!dataRange || !(dataRange.max > dataRange.min)) {
    return range; // データレンジ不明時はそのまま（従来挙動）。
  }
  const dataSpan = dataRange.max - dataRange.min;
  const maxSpan = dataSpan * PRICE_ZOOM_MAX_SPAN_RATIO;
  const minSpan = dataSpan * PRICE_ZOOM_ABS_MIN_SPAN_RATIO;
  let span = range.max - range.min;
  let c = (range.min + range.max) / 2;
  if (span > maxSpan) span = maxSpan;
  if (span < minSpan) span = minSpan;
  // 中心はデータ範囲内に保つ（最大ズームアウトでもローソクが視界から消えない）。
  const cMin = dataRange.min;
  const cMax = dataRange.max;
  if (c < cMin) c = cMin;
  if (c > cMax) c = cMax;
  return { min: c - span / 2, max: c + span / 2 };
}

export function zoomedPriceRange(range, price, deltaY) {
  const min = range.min;
  const max = range.max;
  const span = max - min;
  if (!Number.isFinite(deltaY) || deltaY === 0) {
    return { min, max }; // deltaY 不正/0 は無変化（NaN 伝播防止）。
  }
  let f = Math.pow(0.9, -deltaY / 100);
  if (f < PRICE_ZOOM_F_MIN) {
    f = PRICE_ZOOM_F_MIN;
  } else if (f > PRICE_ZOOM_F_MAX) {
    f = PRICE_ZOOM_F_MAX;
  }
  // price が range 外なら中央（(min+max)/2）を基準にする。
  const p = (price >= min && price <= max) ? price : (min + max) / 2;
  let newMin = p - (p - min) * f;
  let newMax = p + (max - p) * f;
  // 最小 span クランプ: 現 span×1e-4 未満へは縮めない（p 中心を保ったまま拡げる）。
  const minSpan = span * PRICE_ZOOM_SPAN_MIN_RATIO;
  if ((newMax - newMin) < minSpan) {
    const c = (newMin + newMax) / 2;
    newMin = c - minSpan / 2;
    newMax = c + minSpan / 2;
  }
  return { min: newMin, max: newMax };
}

// 中心からの距離比 t∈[0,1] を 緑→赤 へ補間し dim で減光した rgb 文字列にする。
function schemeColor(t, dim) {
  const r = Math.round(lerp(SCHEME_CALM[0], SCHEME_HOT[0], t) * dim);
  const g = Math.round(lerp(SCHEME_CALM[1], SCHEME_HOT[1], t) * dim);
  const b = Math.round(lerp(SCHEME_CALM[2], SCHEME_HOT[2], t) * dim);
  return `rgb(${r}, ${g}, ${b})`;
}

// 系列データ末尾点の value を取り出す（読み取り欄の hover 解除時 fallback 用）。空なら null。
function lastPointValue(data) {
  const arr = data ?? [];
  if (arr.length === 0) {
    return null;
  }
  const last = arr[arr.length - 1];
  return (last && last.value !== undefined) ? last.value : null;
}

export class ChartRenderer {
  // chart: LightweightCharts.createChart(...) の戻り（addSeries/addPane/panes/removePane を持つ）。
  // mainSeries: addSeries(CandlestickSeries, ...) の戻り（pane 0・createPriceLine を持つ）。
  // lwc: グローバル LightweightCharts 名前空間（LineSeries/HistogramSeries/createTextWatermark）。
  // onCrosshairReadout: クロスヘア価格読み取り欄へ読み取り DTO を渡すコールバック
  //   （省略時 no-op＝後方互換）。DTO はプレーンなデータ構造（series 実体・lwc 型を含めない）。
  // onCandlesChanged: 基準 candles 変更（setCandles 全置換 / updateLastCandle 差分）時に呼ぶ
  //   observer（省略時 no-op＝後方互換）。trade markers renderer が hover 中なら highlight 解除へ使う
  //   （ChartRenderer 起点の単一同期点＝v6・§12 / フェーズ2 確定機構）。
  constructor({ chart, mainSeries, lwc, onCrosshairReadout, onCandlesChanged }) {
    this._chart = chart;
    this._mainSeries = mainSeries;
    this._lwc = lwc ?? {};
    this._onCrosshairReadout = typeof onCrosshairReadout === 'function' ? onCrosshairReadout : () => {};
    // v6: 基準 candles の単一所有者（setCandles 全置換・updateLastCandle 差分で更新）。
    //   per-bar 減光（dimCandlesOutsidePair）・基準復元（restoreCandles）はこの基準から導出する。
    this._baseCandles = null;
    // v6: candle 変更 observer（後方互換 no-op）。setCandleObserver で後から差し替え可能（生成順序吸収）。
    this._onCandlesChanged = typeof onCandlesChanged === 'function' ? onCandlesChanged : () => {};
    // 読み取り欄の最新足の単一源（lightweight-charts から逆引きしない＝upstream API 名を増やさない）。
    //   setCandles で配列末尾、updateLastCandle で当該足を保持する。
    this._lastBar = null;
    // overlay（pane 0 重ね描き）line 系列の読み取り用メタ。key {instanceId}::{name} ->
    //   { series, color, name, lastValue }。読み取り欄の overlay 行と fallback 値に使う。
    this._overlayReadouts = new Map();
    // instanceId -> { lines, priceLines, hlinePayloads, visible, scaleHost, priceLineHost,
    //                 pane, watermark, paneName }
    this._instances = new Map();
    this._mainStretchSet = false;
    // 増分2: setCandleTrim の直近トリム末尾 index（位置不変時の再 setData 回避＝プロト lastTrimIdx）。
    //   null=未トリム。setCandles で候補が変わるためリセットする。
    this._lastTrimIdx = null;
    // スクラブ時の表示フィット（_fitTrimView）に使う右プロファイル領域の割合（setRightMarginFraction で更新）。
    this._profileMarginFraction = 0;
    // スクラブ追従で保持するズーム倍率（可視論理幅）のキャッシュ。getVisibleLogicalRange 一時失敗時に使う。
    this._replayViewSpan = null;
    // sessions（日別プロファイル）の time→{poc,vah,val} Map（読み取り欄で当日 MP を出す）。null=非表示。
    this._sessionMP = null;
    // 価格軸ホイールズームは lwc v5.2 の priceScale ネイティブ API
    //   （getVisibleRange/setVisibleRange/autoScale）で実現する＝軸ドラッグと同一の内部状態。
    //   自前の override 状態は持たない（手動スケールの保持・解除は lwc が所有する）。
    // 価格パンの px→価格換算に使う pane 高（container 高 - timeScale().height() 相当）。
    //   composition root が setPaneHeight で供給する。
    this._paneHeight = null;
    // ISSUE-114/115: チャート右端の常設余白（幅比率基準・px 一定）。最新足が右端に張り付く
    //   ストレスの解消。rightOffset は scrollToRealTime / FOLLOW 追従で尊重されるため、ライブ新足
    //   でも余白が維持される。ズーム（barSpacing 変化）時は可視範囲購読で bars を再計算し px 幅を
    //   一定に保つ（同値スキップでループ防止）。timeScale 非対応 Fake は no-op。
    this._lastRightOffsetBars = null;
    this._syncRightOffset();
    {
      const ts = typeof this._chart.timeScale === 'function' ? this._chart.timeScale() : null;
      if (ts && typeof ts.subscribeVisibleLogicalRangeChange === 'function') {
        ts.subscribeVisibleLogicalRangeChange(() => this._syncRightOffset());
      }
    }
    // 機能③: クロスヘア移動で pane ウォーターマークへ系列値を追記。
    if (typeof this._chart.subscribeCrosshairMove === 'function') {
      this._chart.subscribeCrosshairMove((param) => this._onCrosshairMove(param));
    }
  }

  // 時間足切替: メインローソク系列のデータを差し替え、可視範囲を全体へ合わせる。
  setCandles(candles) {
    const arr = candles ?? [];
    this._mainSeries.setData(arr);
    // v6: 基準 candles を全置換で更新（per-bar 減光/復元の元集合）。
    this._baseCandles = arr;
    // 増分2: 基準 candles が入れ替わったのでトリム位置キャッシュをリセット（次の setCandleTrim で再 set）。
    this._lastTrimIdx = null;
    // 価格スケールの手動状態（軸ドラッグ/ホイールズーム）には本メソッドでは触れない。setCandles は
    //   replay_ui の足リビールで毎バー呼ばれるため、ここで解除するとバー境界のたびにズームが消える
    //   （旧実装の不具合）。解除点は「価格軸 dblclick（resetPriceZoom）」と「時間足切替
    //   （TimeframeController.setTimeframe が resetPriceZoom を呼ぶ・ISSUE-113 ユーザー裁定）」の 2 点で、
    //   いずれも呼び出し側の責務。手動スケールは lwc 内部状態なので setData 全置換でも lwc 自身が保持する。
    this._replayViewSpan = null; // スクラブ span キャッシュのみリセット（価格ズームとは別物）。
    // ISSUE-114: fitContent は全データを幅いっぱいへフィットし右余白を潰すため、直後に
    //   scrollToRealTime で常設 rightOffset を反映する（barSpacing は fitContent の結果を維持）。
    //   scrollToRealTime 非提供（後方互換 Fake）は no-op（下の呼出し済みガードに委ねる）。
    // 読み取り欄の最新足の単一源を更新（配列末尾の足）。空配列なら null。
    this._lastBar = arr.length > 0 ? arr[arr.length - 1] : null;
    const ts = this._chart.timeScale();
    ts.fitContent();
    // fitContent で barSpacing が確定した直後に余白バー数を再計算してから右端へスクロール
    //   （ISSUE-115: px 基準余白の反映順）。
    this._syncRightOffset({ force: true });
    if (typeof ts.scrollToRealTime === 'function') {
      ts.scrollToRealTime();
    }
    // v6: candle 変更を observer へ通知（ChartRenderer 起点同期＝hover 中なら highlight 解除へ）。
    this._onCandlesChanged();
  }

  // 基準 candles（_baseCandles）の読み取り専用アクセサ。リプレイバーが slider の min/max・
  //   index→time 変換に使う（新規追加・読取のみ＝既存描画へ非干渉）。未設定時は空配列。
  getCandles() {
    return this._baseCandles ?? [];
  }

  // 増分2: チャートの通常操作（スクロール/ズーム）を停止/復元する（リプレイスワイプ捕捉用）。
  //   移植元 prototype_260630-01 updateCaptureMode（capture 中 handleScroll/handleScale=false）。
  //   lightweight-charts の applyOptions 直叩きは本所（ChartRenderer）に閉じる（primitive/actor は呼ばない）。
  //   enabled=false でスクロール/ズーム停止、true で復元。applyOptions 非提供時は no-op（後方互換）。
  setUserInteraction(enabled) {
    if (typeof this._chart.applyOptions !== 'function') {
      return;
    }
    const on = !!enabled;
    this._chart.applyOptions({ handleScroll: on, handleScale: on });
  }

  // MP プロファイル専用の右マージン: ローソクを左へ寄せ、チャート右側 frac（例 0.30）を空けて
  //   プロファイルのバーがローソク足と重ならないようにする（試作 prototype_260630-01 の
  //   PROFILE_FRAC=0.30「ヒートの重なり回避」の移植・実機FB「バーと足が重なって視認性が悪い」）。
  //   実装は timeScale の rightOffset（単位=バー数）＝ width*frac / barSpacing を設定する。
  //   frac=null で 0（既定）へ復元。ズームで barSpacing が変わると px マージンはドリフトする
  //   （v1 の許容・再トグルで再計算）。timeScale/applyOptions 非提供時は no-op（後方互換）。
  setRightMarginFraction(frac) {
    // スクラブ時の表示フィット（setCandleTrim → setVisibleLogicalRange の blank）にも使う margin 率を保持。
    //   プロト applyAsofView の PROFILE_FRAC と同義（右プロファイル領域の割合）。0=マージンなし。
    this._profileMarginFraction = (frac != null && frac > 0 && frac < 1) ? frac : 0;
    // 実適用は単一権威 _syncRightOffset（常設余白 5% と max 合成・ISSUE-115）。解除（frac=null）でも
    //   0 へ戻さず常設余白へ復元される（ISSUE-114: 右端張り付き防止）。
    this._syncRightOffset({ force: true });
  }

  // 右端余白の単一権威（ISSUE-115）: 実効比率 = max(常設 5%, MP プロファイル余白率) を px 換算し、
  //   rightOffset バー数 = width×frac ÷ barSpacing（小数のまま＝px 幅一定）で適用する。
  //   可視範囲購読（ズームで barSpacing 変化）からも呼ばれるため、同値（±0.01 バー）はスキップして
  //   applyOptions→範囲変化→再購読のループを防ぐ。width 未確定（レイアウト前）は何もしない。
  _syncRightOffset({ force = false } = {}) {
    const ts = typeof this._chart.timeScale === 'function' ? this._chart.timeScale() : null;
    if (!ts || typeof ts.applyOptions !== 'function') {
      return;
    }
    const w = typeof ts.width === 'function' ? ts.width() : 0;
    const bs = (typeof ts.options === 'function' && ts.options() && ts.options().barSpacing) || 6;
    if (!(w > 0) || !(bs > 0)) {
      return;
    }
    const frac = Math.max(BASE_RIGHT_MARGIN_FRACTION, this._profileMarginFraction || 0);
    const bars = (w * frac) / bs;
    if (!force && this._lastRightOffsetBars !== null
        && Math.abs(bars - this._lastRightOffsetBars) < 0.01) {
      return;
    }
    this._lastRightOffsetBars = bars;
    ts.applyOptions({ rightOffset: bars });
  }

  // sessions（日別プロファイル分割）: ローソクを透明化して価格軸のみ残す/復元する（移植元 prototype_260630-01）。
  //   lightweight-charts の mainSeries.applyOptions 直叩きは本所（ChartRenderer）に閉じる（primitive/actor は
  //   呼ばない）。on=true で up/down/border/wick の各色を透明へ上書きし、on=false で元の既定色へ復元する。
  //   applyOptions 非提供時は no-op（後方互換）。冪等（同じ状態の再設定は無害）。
  setCandleTransparency(on) {
    if (typeof this._mainSeries.applyOptions !== 'function') {
      return;
    }
    if (on) {
      this._mainSeries.applyOptions({
        upColor: TRANSPARENT_COLOR, downColor: TRANSPARENT_COLOR,
        borderUpColor: TRANSPARENT_COLOR, borderDownColor: TRANSPARENT_COLOR,
        wickUpColor: TRANSPARENT_COLOR, wickDownColor: TRANSPARENT_COLOR,
      });
    } else {
      // 復元＝既定のローソク配色へ戻す（sessions OFF / MP 削除 / sessions 解除で必ず呼ぶ）。
      this._mainSeries.applyOptions({
        upColor: CANDLE_UP_COLOR, downColor: CANDLE_DOWN_COLOR,
        borderUpColor: CANDLE_UP_COLOR, borderDownColor: CANDLE_DOWN_COLOR,
        wickUpColor: CANDLE_UP_COLOR, wickDownColor: CANDLE_DOWN_COLOR,
      });
    }
  }

  // 縦パンの px→価格換算に使う pane 高（container 高 - timeScale().height() 相当）を設定。
  //   composition root が resize 時などに供給する。消費者は panPriceByPixels のみ（未設定時は
  //   false＝安全側）。handlePriceWheel は getVisibleRange を使うため pane 高に依存しない。
  setPaneHeight(h) {
    this._paneHeight = (typeof h === 'number' && h > 0) ? h : null;
  }

  // 右価格軸ハンドル（mainSeries.priceScale('right') 優先、無ければ chart.priceScale('right')）。
  //   lwc 直叩きは本所に隔離。いずれも非提供なら null（後方互換）。
  _rightPriceScale() {
    if (typeof this._mainSeries.priceScale === 'function') {
      return this._mainSeries.priceScale('right');
    }
    if (typeof this._chart.priceScale === 'function') {
      return this._chart.priceScale('right');
    }
    return null;
  }

  // baseCandles の価格全幅 {min,max}（絶対クランプの基準）。未設定/空は null。
  _candlesPriceRange() {
    const arr = this._baseCandles;
    if (!arr || arr.length === 0) {
      return null;
    }
    let min = Infinity;
    let max = -Infinity;
    for (const c of arr) {
      if (c.low < min) min = c.low;
      if (c.high > max) max = c.high;
    }
    return (Number.isFinite(min) && Number.isFinite(max) && max > min) ? { min, max } : null;
  }

  // 価格軸ホイールズームの本体。x が価格軸領域（x >= timeScale().width()）のときだけ処理する。
  //   lwc v5.2 の priceScale ネイティブ API（getVisibleRange/setVisibleRange）で実装する。
  //   setVisibleRange は lwc 内部で autoScale=false を設定する＝軸ドラッグの手動スケールと
  //   同一の内部状態。よってドラッグ/ホイールの区別なく「手動スケールは dblclick まで維持」で
  //   統一され、setData 全置換（時間足切替・足リビール）でも lwc 自身が状態を保持する。
  //   カーソル価格 p=coordinateToPrice(y) を不動点に zoomedPriceRange で新レンジを算出し、
  //   clampPriceRange（baseCandles 全幅基準）で発散を封じる。処理したら true（呼び出し側が
  //   preventDefault する）。軸領域外・API 非提供・レンジ不明は false（時間軸ズームへ委ねる）。
  handlePriceWheel(x, y, deltaY) {
    const ts = typeof this._chart.timeScale === 'function' ? this._chart.timeScale() : null;
    if (!ts || typeof ts.width !== 'function') {
      return false;
    }
    if (x < ts.width()) {
      return false; // チャート本体領域＝時間軸ズームに委ねる。
    }
    const ps = this._rightPriceScale();
    if (!ps || typeof ps.getVisibleRange !== 'function' || typeof ps.setVisibleRange !== 'function') {
      return false; // ネイティブ API 非提供（後方互換 Fake 等）は安全側で何もしない。
    }
    const vr = ps.getVisibleRange();
    if (!vr || !Number.isFinite(vr.from) || !Number.isFinite(vr.to)) {
      return false; // データ無し等で表示レンジ不明。
    }
    const base = { min: Math.min(vr.from, vr.to), max: Math.max(vr.from, vr.to) };
    if (!(base.max > base.min)) {
      return false; // 縮退レンジ。
    }
    // ズームの不動点はカーソル価格（取得不能時は zoomedPriceRange がレンジ中央へフォールバック）。
    const p = typeof this._mainSeries.coordinateToPrice === 'function'
      ? this._mainSeries.coordinateToPrice(y) : null;
    const next = clampPriceRange(
      zoomedPriceRange(base, (p == null ? NaN : p), deltaY), this._candlesPriceRange(),
    );
    if (!(next.max > next.min)) {
      return false;
    }
    ps.setVisibleRange({ from: next.min, to: next.max });
    return true;
  }

  // チャート本体の縦ドラッグによる価格パン（上下移動）。dy=ポインタ縦移動量[px]（下+）。
  //   横方向は lwc の時間軸パンに委ね（本メソッドは価格のみ操作）、両者が合成されて 2D パンになる。
  //   ドラッグ下げ（dy>0）＝内容を下へ引く＝表示レンジを上へ（min/max とも +）。span は不変。
  //   ネイティブ getVisibleRange で現レンジを読み、dy ぶん平行移動して setVisibleRange で書く。
  //   px→価格の換算に pane 高（setPaneHeight 供給）を使う。結果は clampPriceRange
  //   （span 保存・中心はデータ範囲内）で制限。処理したら true。
  panPriceByPixels(dy) {
    const paneHeight = this._paneHeight;
    if (!(paneHeight > 0) || !(Math.abs(dy) > 0)) {
      return false;
    }
    const ps = this._rightPriceScale();
    if (!ps || typeof ps.getVisibleRange !== 'function' || typeof ps.setVisibleRange !== 'function') {
      return false;
    }
    const vr = ps.getVisibleRange();
    if (!vr || !Number.isFinite(vr.from) || !Number.isFinite(vr.to)) {
      return false;
    }
    const min = Math.min(vr.from, vr.to);
    const max = Math.max(vr.from, vr.to);
    if (!(max > min)) {
      return false;
    }
    const span = max - min;
    const shift = (dy / paneHeight) * span; // dy>0（下げ）→表示レンジを上へ。
    const next = clampPriceRange(
      { min: min + shift, max: max + shift }, this._candlesPriceRange(),
    );
    ps.setVisibleRange({ from: next.min, to: next.max });
    return true;
  }

  // 価格軸のダブルクリック等で自動スケールへ復帰する。手動スケール（ドラッグ/ホイール）の
  //   解除点はユーザーのこの操作のみ（システムは勝手に解除しない）。
  resetPriceZoom() {
    const ps = this._rightPriceScale();
    if (ps && typeof ps.applyOptions === 'function') {
      ps.applyOptions({ autoScale: true });
    }
  }

  // ISSUE-116: 最新足が可視範囲内か（「最新のバーまでスクロール」ボタンの表示判定用）。
  //   getVisibleLogicalRange().to が末尾 index 以上なら可視（右余白ぶん to は末尾より大きくなる）。
  //   API 非提供・データ無し・レンジ不明は true（＝最新扱い・ボタンを出さない安全側）。
  isLatestBarVisible() {
    const ts = typeof this._chart.timeScale === 'function' ? this._chart.timeScale() : null;
    const arr = this._baseCandles;
    if (!ts || typeof ts.getVisibleLogicalRange !== 'function' || !arr || arr.length === 0) {
      return true;
    }
    const range = ts.getVisibleLogicalRange();
    if (!range || !Number.isFinite(range.to)) {
      return true;
    }
    return range.to >= arr.length - 1;
  }

  // 価格軸領域判定の小ヘルパ（x >= timeScale().width()）。composition root の dblclick 判定用（任意）。
  isOverPriceAxis(x) {
    const ts = typeof this._chart.timeScale === 'function' ? this._chart.timeScale() : null;
    if (!ts || typeof ts.width !== 'function') {
      return false;
    }
    return x >= ts.width();
  }

  // 増分2: x 座標 → 論理 index（timeScale().coordinateToLogical）。リプレイスワイプの x→足 index 変換。
  //   lwc 座標 API を本所に隔離する（actor/primitive は renderer 経由でのみ座標を得る）。
  //   timeScale/coordinateToLogical 非提供時は null（後方互換・呼び出し側でガード）。
  coordinateToLogical(x) {
    const ts = typeof this._chart.timeScale === 'function' ? this._chart.timeScale() : null;
    if (!ts || typeof ts.coordinateToLogical !== 'function') {
      return null;
    }
    return ts.coordinateToLogical(x);
  }

  // 直近 n バーを可視範囲にフィットさせる（sessions=日別プロファイルの時間軸連動タイルを見せる初期ズーム）。
  //   全期間表示だと 1 日=barSpacing が極小でタイルが潰れるため、sessions 有効化時に直近 n 日へ寄せる。
  //   lwc の timeScale().setVisibleLogicalRange 直叩きは本所（ChartRenderer）に閉じる。
  focusRecentBars(n) {
    const total = this._baseCandles ? this._baseCandles.length : 0;
    if (!(total > 0) || !(n > 0)) {
      return;
    }
    const ts = typeof this._chart.timeScale === 'function' ? this._chart.timeScale() : null;
    if (!ts || typeof ts.setVisibleLogicalRange !== 'function') {
      return;
    }
    const from = Math.max(-0.5, total - n - 0.5);
    const to = total - 0.5 + Math.max(1, n * 0.04); // 右端に僅かな余白（最新タイルが切れないように）。
    ts.setVisibleLogicalRange({ from, to });
  }

  // 指定の時間レンジ [from, to]（UNIX 秒）を可視範囲にする（日別プロファイルの被覆日を全 tf で表示する）。
  //   focusRecentBars は論理バー数基準のため、1m では「日数」を「分数」と解釈して日別列（日境界時刻）が
  //   画面外に落ちる。本メソッドは時間ベース（setVisibleRange）でズームし、全 tf で列を可視化する。
  focusTimeRange(from, to) {
    const ts = (this._chart && typeof this._chart.timeScale === 'function') ? this._chart.timeScale() : null;
    if (!ts || typeof ts.setVisibleRange !== 'function') {
      return;
    }
    if (!(Number.isFinite(from) && Number.isFinite(to) && from < to)) {
      return; // 不正レンジは触らない（現状維持）。
    }
    ts.setVisibleRange({ from, to });
  }

  // 増分2: スナップショット用のローソク局所トリム（基準 candles を time<=T へスライスして setData）。
  //   移植元 prototype_260630-01 applyAsofView（当時の見え方＝ローソクを T までに切る・位置変化時のみ再 set）。
  //   time=null で全ローソク復元（スナップショット OFF・replay OFF）。基準未供給なら no-op。
  //   mainSeries.setData を呼ぶのは本所のみ（upstream 隔離・grep0件規約維持）。位置不変時は再 set しない
  //   （重い再描画を回避＝プロト lastTrimIdx 相当）。
  setCandleTrim(time) {
    if (!this._baseCandles) {
      return;
    }
    // トリム状態の同一性で再 set 要否を決める（null=未トリム／数値=末尾 index）。位置不変なら再 set しない。
    //   null は「全ローソク（未トリム）」を表す単一の状態＝復元は「既にトリム済みのとき」だけ実行する。
    //   これにより replay/snapshot OFF 時（未トリム状態）に setCandleTrim(null) を呼んでも series へ触れない
    //   （挙動不変＝冗長 setData を出さない）。
    if (time == null) {
      if (this._lastTrimIdx === null) {
        return; // 既に未トリム＝何もしない（OFF 時の挙動不変）。
      }
      this._lastTrimIdx = null;
      this._mainSeries.setData(this._baseCandles); // トリム解除＝全ローソク復元。
      this._fitTrimView(this._baseCandles.length); // プロト applyAsofView: 先頭〜末尾へフィット（＋blank）。
      // 読み取り欄の単一源（_lastBar）も全ローソクの最終足へ復元し、即時再発火する
      //   （スナップショット解除後に古い T 時点の値が残らないように）。
      this._lastBar = this._baseCandles.length > 0
        ? this._baseCandles[this._baseCandles.length - 1] : null;
      this._emitReadout(null);
      return;
    }
    let idx = -1;
    for (let i = 0; i < this._baseCandles.length; i += 1) {
      if (this._baseCandles[i].time <= time) {
        idx = i;
      } else {
        break; // time 昇順前提（越えたら打ち切り）。
      }
    }
    if (idx === -1) {
      return; // time がデータ先頭より前（縮退）＝トリム無効。全ローソクを維持し series へ触れない。
    }
    if (idx === this._lastTrimIdx) {
      return; // 位置変化なし＝重い setData を回避（プロト applyAsofView 相当）。
    }
    this._lastTrimIdx = idx;
    this._mainSeries.setData(this._baseCandles.slice(0, idx + 1));
    this._fitTrimView(idx + 1); // プロト applyAsofView: 表示を先頭〜T にフィット（＋右 blank）。
    //   これにより、ズームイン中でもスクラブで視界が T に追従する（過去→現在へも戻れる・実機で確認した
    //   「現在へ戻れない」バグの修正）。移植元 prototype_260630-01/js/app.js L318-322。
    // 読み取り欄の単一源（_lastBar）をトリム後の最終足（=T 時点の足）へ更新し、即時再発火する。
    //   これが無いとスナップショット中も左上の読み取り欄がトリム前の最新足（例 2026-07-02）を
    //   表示し続け、当時（T）の表示と矛盾する（実機で確認したバグ）。
    this._lastBar = this._baseCandles[idx];
    this._emitReadout(null);
  }

  // スクラブ時の表示追従: **現在のズーム倍率（可視論理幅 span）を保ったまま** T（末尾トリム足）を
  //   右端へスクロールし、右 f をプロファイル余白にする。これにより「拡大したまま過去↔現在を行き来」
  //   できる（ユーザー選択・プロトの毎回全historyフィットからは意図的に外れる）。
  //   span が全体を覆う（未ズーム）ときは from<=-0.5 となり自然に先頭〜T の全history表示になる
  //   （プロト applyAsofView と実質同等）。getVisibleLogicalRange 非提供時は {from:-0.5, to:L-0.5+blank}
  //   の全historyフィットにフォールバック。lwc の timeScale 直叩きは本所（ChartRenderer）に閉じる。
  _fitTrimView(L) {
    if (!(L > 0)) {
      return;
    }
    const ts = typeof this._chart.timeScale === 'function' ? this._chart.timeScale() : null;
    if (!ts || typeof ts.setVisibleLogicalRange !== 'function') {
      return; // 非提供（テスト/SSR）は no-op（後方互換）。
    }
    const f = this._profileMarginFraction || 0;
    const lastIdx = L - 0.5; // 末尾バー（=T）の論理右端（プロトの -0.5 系に合わせる）。
    // 現在の可視幅（ズーム倍率）を読む。読めた値はキャッシュし、**一時的に取得不能でも直前の span を
    //   使う**（全history へ戻さない＝ズーム保持を確実にする）。span を一度も得ていない初回だけ全history。
    let span = null;
    if (typeof ts.getVisibleLogicalRange === 'function') {
      const r = ts.getVisibleLogicalRange();
      if (r && r.to > r.from) {
        span = r.to - r.from;
      }
    }
    if (span != null) {
      this._replayViewSpan = span; // ユーザーのズーム（スクラブ間の変更含む）を捕捉。
    } else if (this._replayViewSpan != null) {
      span = this._replayViewSpan; // 取得失敗は直前の span で補う（フォールバックで全historyへ戻さない）。
    }
    if (span == null) {
      // span を一度も得ていない（初回・API 非対応）＝全history フィット（プロト相当）。
      const blank = (f > 0 && f < 1) ? (L * f) / (1 - f) : 0;
      ts.setVisibleLogicalRange({ from: -0.5, to: lastIdx + blank });
      return;
    }
    // ズーム倍率(span)を保持したまま T を右端へ（右 f を余白に）スクロール。
    const to = lastIdx + span * f;
    const from = to - span;
    ts.setVisibleLogicalRange({ from, to });
  }

  // ライブ更新: 最新足を差分反映する（series.update を呼ぶのは本所のみ・upstream 隔離維持）。
  //   既存 time なら上書き、新しい time なら追加（lightweight-charts の update 仕様）。
  updateLastCandle(candle) {
    // ★スナップショット（トリム）中は series へ現在足を入れない。トリム系列（過去 T 時点まで）へ
    //   ライブの現在足（time=now・現在価格）を append すると、トリム範囲外の不可解な位置にバーが
    //   プロットされる（放置でライブ更新が発火し発生・実機バグの修正）。基準 _baseCandles は更新し、
    //   トリム解除後に最新足へ正しく復帰できるようにする（読み取り欄は T 時点のまま維持）。
    if (this._lastTrimIdx !== null) {
      this._mergeBaseCandle(candle);
      return;
    }
    // 後退ガード（ISSUE-096）: 時間足切替（setCandles で系列＋_lastBar が新周期へ差替）直後に、
    //   インフライトの旧周期ライブ tick が実系列末尾より古い time で来ると、lightweight-charts の
    //   series.update が "Cannot update oldest data" を投げる（player 内 _bar 基準の後退ガードでは
    //   系列側が差し替わったケースを捕捉できない）。実系列末尾（_lastBar）より古い time のライブ足は
    //   skip する（旧周期の stale tick は新周期 base へ混ぜない）。同/新 time は従来どおり反映する。
    if (this._lastBar != null && typeof candle.time === 'number'
        && typeof this._lastBar.time === 'number' && candle.time < this._lastBar.time) {
      return;
    }
    this._mainSeries.update(candle);
    // 最新足の単一源を更新し、hover していない読み取り表示が古くならないよう DTO を再発火する。
    this._lastBar = candle;
    // v6: 基準 candles の末尾を差分反映（同 time は置換・新 time は追加）。減光の元集合を同期。
    this._mergeBaseCandle(candle);
    this._emitReadout(null);
    // v6: candle 変更を observer へ通知（live tick でも hover 中なら highlight 解除させる）。
    this._onCandlesChanged();
  }

  // ライブ欠落補完（ISSUE-106）: タブ休止（PC スリープ・バックグラウンドタイマー抑制）や更新停止で
  //   足境界を 2 本以上またぐと、差分経路（updateLastCandle＝末尾 1 本前提）では途中の確定足を挿入
  //   できず（lwc の series.update は末尾より古い time を受け付けない）恒久的な歯抜けになる。
  //   fetched（サーバー正の /candles 全件・time 昇順）に「実系列末尾より新しい足が 2 本以上」または
  //   「既知範囲内の未保持 time（穴）」を検出したときのみ setData 全置換で再同期する。
  //   通常運転（差分 0〜1 本・全 time 既知）は何もせず false（従来差分経路のまま挙動不変）。
  //   fitContent は呼ばない（ユーザーのズーム・スクロール位置を保持）。
  //   現在足の後退防止（ISSUE-049 系）: 置換前末尾（LiveTickPlayer が書いた最新値）の time が
  //   新データ末尾以上なら置換後に復元する（最大 60 秒古いサーバー値で価格を巻き戻さない）。
  //   スナップショット（トリム）中は不介入（updateLastCandle と同方針・解除後の tick で再同期される）。
  resyncMissedCandles(candles) {
    const arr = Array.isArray(candles) ? candles : [];
    if (arr.length === 0 || this._lastTrimIdx !== null) {
      return false;
    }
    const base = this._baseCandles;
    if (!base || base.length === 0 || this._lastBar == null) {
      return false; // 初期ロード前は setCandles（全置換）の責務。
    }
    const lastTime = this._lastBar.time;
    const known = new Set(base.map((b) => b.time));
    let newer = 0;
    let hole = false;
    for (const c of arr) {
      if (c.time > lastTime) {
        newer += 1;
      } else if (!known.has(c.time)) {
        hole = true;
      }
    }
    if (newer < 2 && !hole) {
      return false;
    }
    const prevLast = this._lastBar;
    const newest = arr[arr.length - 1];
    this._mainSeries.setData(arr);
    this._baseCandles = arr;
    this._lastBar = newest;
    if (typeof prevLast.time === 'number' && typeof newest.time === 'number'
        && prevLast.time >= newest.time) {
      this._mainSeries.update(prevLast);
      this._lastBar = prevLast;
      this._mergeBaseCandle(prevLast);
    }
    this._emitReadout(null);
    this._onCandlesChanged();
    return true;
  }

  // v6: candle 変更 observer を後から据える（composition root の renderer/markers 生成順序差を吸収）。
  setCandleObserver(onCandlesChanged) {
    this._onCandlesChanged = typeof onCandlesChanged === 'function' ? onCandlesChanged : () => {};
  }

  // v6: 基準 candles の末尾足を差分マージする（updateLastCandle 用）。基準未保持なら単一要素配列。
  _mergeBaseCandle(candle) {
    if (!candle) {
      return;
    }
    const base = this._baseCandles ? this._baseCandles.slice() : [];
    if (base.length > 0 && base[base.length - 1].time === candle.time) {
      base[base.length - 1] = candle;
    } else {
      base.push(candle);
    }
    this._baseCandles = base;
  }

  // v6（§12）: ホバー中ペア [from,to] 外のローソク足を per-bar 極暗色へ上書きして mainSeries へ反映する。
  //   ペア内バーは色を付けず原色（既定 up/down 着色）に委ねる。time/open/high/low/close は基準と完全一致
  //   （データ非改変・背景ピクセルも不変）。基準 candles 未供給時は no-op（候補据え置き・後方互換）。
  //   mainSeries.setData を呼ぶのは本所のみ（upstream 隔離・grep0件規約維持）。
  dimCandlesOutsidePair({ from, to }) {
    if (!this._baseCandles) {
      return;
    }
    const dimmed = this._baseCandles.map((bar) => {
      if (bar.time >= from && bar.time <= to) {
        return bar; // ペア内は原色維持（色上書きしない）。
      }
      return {
        ...bar,
        color: DIM_CANDLE_COLOR,
        borderColor: DIM_CANDLE_COLOR,
        wickColor: DIM_CANDLE_COLOR,
      };
    });
    this._mainSeries.setData(dimmed);
  }

  // v6（§12）: per-bar 減光を解除し基準 candles（色上書きなし）を復元する。基準未供給なら no-op。
  restoreCandles() {
    if (!this._baseCandles) {
      return;
    }
    this._mainSeries.setData(this._baseCandles);
  }

  _slot(instanceId) {
    let slot = this._instances.get(instanceId);
    if (!slot) {
      slot = {
        lines: new Map(), priceLines: [], hlinePayloads: null, visible: true,
        // scaleHost: 当該 instance の line/histogram 系列の先頭（水準線の載せ先・pane の価格軸基準）。
        // priceLineHost: 水準線（createPriceLine）を載せた系列（pane=scaleHost / overlay=mainSeries）。
        // pane/watermark/paneName: pane 指標のみ（機能①②）。overlay 指標は pane 0 のため null。
        scaleHost: null, priceLineHost: null, pane: null, watermark: null, paneName: null,
        // styleMeta（ISSUE-109）: 系列キー -> { name, kind, color, width, style, visible }。
        //   生成時スタイルの記録＋applySeriesStyle の上書き結果を保持し、スタイルタブの
        //   初期表示（実描画値）と instance 単位 setVisible との可視性合成に使う。
        styleMeta: new Map(),
      };
      this._instances.set(instanceId, slot);
    }
    return slot;
  }

  // pane 指標なら専用 pane を生成し指標名ウォーターマーク（機能①②）を立てる。overlay は null（pane 0）。
  _ensurePane(slot, opts) {
    if (!opts.pane) {
      return null;
    }
    if (slot.pane) {
      return slot.pane;
    }
    // 初回 pane 追加時にメイン（ローソク）pane を大きめへ（以後ユーザーのドラッグを尊重し再設定しない）。
    if (!this._mainStretchSet) {
      const panes = this._chart.panes ? this._chart.panes() : [];
      if (panes[0] && typeof panes[0].setStretchFactor === 'function') {
        panes[0].setStretchFactor(MAIN_PANE_STRETCH);
      }
      this._mainStretchSet = true;
    }
    // v5 は空 pane を既定で自動削除する。系列の再計算（remove→redraw）で一時的に空になった
    // 瞬間に pane が消えて index がずれ、直後の removePane が誤 pane を対象化／例外となり、
    // 再描画前に処理が中断して指標が消える。preserveEmptyPane=true で pane の寿命を removePane
    // のみの単一権威にする（ISSUE: period 変更で Volatility 等が消える不具合の根治）。
    const pane = this._chart.addPane(true);
    if (pane && typeof pane.setPreserveEmptyPane === 'function') {
      pane.setPreserveEmptyPane(true);
    }
    if (pane && typeof pane.setStretchFactor === 'function') {
      pane.setStretchFactor(INDICATOR_PANE_STRETCH);
    }
    slot.pane = pane;
    slot.paneName = opts.name ?? '';
    if (typeof this._lwc.createTextWatermark === 'function') {
      slot.watermark = this._lwc.createTextWatermark(pane, {
        horzAlign: 'left',
        vertAlign: 'top',
        lines: [{ text: slot.paneName, color: WATERMARK_COLOR, fontSize: 12 }],
      });
    }
    return pane;
  }

  // line 系列群を生成（§7.1.2: 系列キー {instanceId}::{name}）。opts.pane=true で専用 pane。
  renderLine(instanceId, payloads, opts = {}) {
    this._renderSeries(instanceId, payloads, 'line', opts);
  }

  // histogram 系列群を生成（per-point の data[].color でバー別着色・level_colors 移植）。
  renderHistogram(instanceId, payloads, opts = {}) {
    this._renderSeries(instanceId, payloads, 'histogram', opts);
  }

  // line / histogram を共通生成する（upstream API 名 addSeries は本所のみ）。
  _renderSeries(instanceId, payloads, kind, opts = {}) {
    const slot = this._slot(instanceId);
    const pane = this._ensurePane(slot, opts);
    const definition = seriesKind(kind).seriesType === 'histogram'
      ? this._lwc.HistogramSeries
      : this._lwc.LineSeries;
    for (const p of payloads ?? []) {
      const options = {
        color: p.color,
        priceLineVisible: false,
        lastValueVisible: false,
        title: p.name,
      };
      if (seriesKind(kind).appliesLineStyle) {
        options.lineWidth = p.width;
        options.lineStyle = toLineStyleInt(p.style);
      }
      // pane 指標は専用 pane（IPaneApi.addSeries）、overlay 指標は pane 0（IChartApi.addSeries）。
      const series = pane
        ? pane.addSeries(definition, options)
        : this._chart.addSeries(definition, options);
      series.setData(p.data ?? []);
      const key = `${instanceId}::${p.name}`;
      slot.lines.set(key, series);
      slot.styleMeta.set(key, {
        name: p.name, kind, color: p.color ?? null,
        width: p.width ?? null, style: p.style ?? null, visible: true,
        // heat（ISSUE-112）: histogram でバー別着色（data[].color＝値に応じたヒート配色）を持つか。
        //   heat=true の系列はユーザー色上書きの対象外（ヒート絶対優先・ユーザー裁定）。
        heat: seriesKind(kind).supportsHeat && (p.data ?? []).some((pt) => pt && pt.color != null),
      });
      if (!slot.scaleHost) {
        slot.scaleHost = series;
      }
      // overlay（pane 0 重ね描き）の line 系列のみ読み取り欄の overlay 行に載せる。
      //   color/name と末尾点 value（hover 解除時の fallback）を保持する。
      if (!pane && seriesKind(kind).overlayReadout) {
        this._overlayReadouts.set(key, {
          series, color: p.color, name: p.name, lastValue: lastPointValue(p.data),
          visible: true,
        });
      }
    }
  }

  // horizontal_line 群を priceLine として生成。当該 instance に line/histogram 系列が
  // あれば その系列（pane の価格軸）へ、無ければ mainSeries（価格バンド・pane 0）へ載せる。
  renderHorizontal(instanceId, hlines) {
    const slot = this._slot(instanceId);
    slot.hlinePayloads = hlines ?? [];
    this._createPriceLines(slot, slot.hlinePayloads);
  }

  _createPriceLines(slot, hlines) {
    const host = slot.scaleHost ?? this._mainSeries;
    slot.priceLineHost = host;
    // pane 指標（オシレータ）の σ 水準線には histogram と同じカラースキーム（中心からの距離で
    // 緑→赤）を減光して適用し、灰一色で背景に埋もれる問題を改善する。overlay バンド
    // （price_range_power / hl_band 等）は bull/bear 等の意味付き色を持つため backend 色を維持。
    const lines = hlines ?? [];
    const useScheme = !!slot.pane && lines.length > 0;
    let center = 0;
    let maxDist = 0;
    if (useScheme) {
      const prices = lines.map((h) => h.price);
      center = (Math.max(...prices) + Math.min(...prices)) / 2;
      maxDist = Math.max(...prices.map((p) => Math.abs(p - center)));
    }
    for (const h of lines) {
      const color = useScheme
        ? schemeColor(maxDist > 0 ? Math.abs(h.price - center) / maxDist : 0, LEVEL_LINE_DIM)
        : h.color;
      const pl = host.createPriceLine({
        price: h.price,
        color,
        lineWidth: h.width,
        lineStyle: toLineStyleInt(h.style),
        title: h.text,
        axisLabelVisible: h.axis_label_visible ?? false,
      });
      slot.priceLines.push(pl);
    }
  }

  // 機能③: クロスヘア移動で各 pane のウォーターマークを「指標名  値1  値2 …」へ更新。
  //   併せてクロスヘア価格読み取り欄（左上オーバーレイ）の読み取り DTO を構築・発火する。
  _onCrosshairMove(param) {
    const seriesData = param && param.seriesData;
    // 機能③（sub-pane ウォーターマーク・後方互換）— 既存ロジックは削らず維持。
    for (const slot of this._instances.values()) {
      if (!slot.watermark) {
        continue;
      }
      const parts = [];
      if (seriesData) {
        for (const series of slot.lines.values()) {
          const d = seriesData.get(series);
          if (d !== undefined && d !== null) {
            const v = (typeof d === 'object') ? (d.value ?? d.close) : d;
            const text = fmtValue(v);
            if (text) {
              parts.push(text);
            }
          }
        }
      }
      const label = parts.length ? `${slot.paneName}  ${parts.join('  ')}` : slot.paneName;
      slot.watermark.applyOptions({ lines: [{ text: label, color: WATERMARK_COLOR, fontSize: 12 }] });
    }
    // クロスヘア価格読み取り欄（左上オーバーレイ）への DTO 発火。
    this._emitReadout(param);
    // tf-period ホバー読取（依頼者指示 2026-07-13・a案ツールチップ）: カーソル位置の座標 DTO
    //   { x, y, time, price } を配線先（composition root）へ渡す。lwc 型は渡さない（隔離維持）。
    //   カーソルがチャート外（point 無し）は null＝ツールチップ hide。ハンドラ未設定は no-op。
    if (typeof this._onTfPeriodHover === 'function') {
      const pt = param && param.point;
      if (pt && param.time != null && typeof this._mainSeries.coordinateToPrice === 'function') {
        const price = this._mainSeries.coordinateToPrice(pt.y);
        this._onTfPeriodHover(price != null
          ? { x: pt.x, y: pt.y, time: Number(param.time), price: Number(price) }
          : null);
      } else {
        this._onTfPeriodHover(null);
      }
    }
  }

  // tf-period ホバー座標ハンドラを設定する（composition root が配線・null で解除）。
  setTfPeriodHoverHandler(fn) {
    this._onTfPeriodHover = typeof fn === 'function' ? fn : null;
  }

  // 読み取り DTO を構築してコールバックへ渡す。param=null（ライブ更新由来）は hover 解除扱い。
  _emitReadout(param) {
    this._onCrosshairReadout(this._buildReadoutDto(param));
  }

  // 読み取り DTO を構築する（プレーンなデータ構造・series 実体や lwc 型は含めない＝隔離維持）。
  //   { time, ohlc:{open,high,low,close}|null, overlays:[{name,value,color}] }。
  _buildReadoutDto(param) {
    const seriesData = (param && param.seriesData) || null;
    // main OHLC: seriesData に main があればそれ、無ければ（hover 解除）最新足 _lastBar へフォールバック。
    const mainData = seriesData ? seriesData.get(this._mainSeries) : undefined;
    const src = (mainData !== undefined && mainData !== null) ? mainData : this._lastBar;
    const ohlc = (src && src.open !== undefined)
      ? { open: src.open, high: src.high, low: src.low, close: src.close }
      : null;
    // overlays: pane0 overlay 系列の seriesData 値、無ければ保持した lastValue。色は保持した color。
    const overlays = [];
    for (const meta of this._overlayReadouts.values()) {
      if (meta.visible === false) {
        continue;  // 非表示（eye トグル OFF）の overlay は読み取り欄に出さない。
      }
      const d = seriesData ? seriesData.get(meta.series) : undefined;
      const value = (d !== undefined && d !== null && d.value !== undefined) ? d.value : meta.lastValue;
      overlays.push({ name: meta.name, value, color: meta.color });
    }
    const time = (param && param.time !== undefined) ? param.time
      : (this._lastBar ? this._lastBar.time : undefined);
    // sessions: 当日 MP（POC/VAH/VAL）を time で引いて DTO に載せる（供給時のみ・sessions 表示中）。
    const sessionMP = (this._sessionMP && time != null) ? (this._sessionMP.get(time) || null) : null;
    return { time, ohlc, overlays, sessionMP };
  }

  // sessions の time→{poc,vah,val} Map を供給する（読み取り欄で当日 MP を出す）。null で非表示。
  //   lwc へは触れない純データ受け渡し（actor が sessions 応答から構築して渡す）。
  setSessionMP(map) {
    this._sessionMP = (map && typeof map.get === 'function') ? map : null;
  }

  // seriesKey の系列を全 instance から引き当て apply(series) を実行し、overlay 読み取りの
  //   fallback 値（末尾点 value）を points 末尾で更新する。未知 seriesKey は no-op。
  //   series への upstream 呼び出し（setData / update）は apply 内＝本所に閉じる（隔離維持）。
  //   ISSUE-112: 流入 points は写像せず素通しする（バー別ヒート配色はユーザー色より絶対優先＝
  //   ユーザー裁定。ISSUE-111 の userColor 写像機構は撤去）。
  _withSeries(seriesKey, points, apply) {
    for (const slot of this._instances.values()) {
      const series = slot.lines.get(seriesKey);
      if (series) {
        apply(series);
        const meta = this._overlayReadouts.get(seriesKey);
        if (meta) {
          meta.lastValue = lastPointValue(points);
        }
        return;
      }
    }
  }

  // UC-03 再計算: 既存系列を再生成せず data のみ差し替え。
  setData(seriesKey, points) {
    this._withSeries(seriesKey, points, (series) => series.setData(points ?? []));
  }

  // Latest 末尾K差分反映: 末尾K点を series.update で 1 点ずつ反映する（過去確定足は不変）。
  //   series.update を呼ぶのは ChartRenderer のみ（upstream 隔離維持）。既存 time は上書き、
  //   新しい time は追加（lightweight-charts の update 仕様）。未知 seriesKey は no-op。
  updateSeriesTail(seriesKey, points) {
    this._withSeries(seriesKey, points, (series) => {
      for (const p of points ?? []) {
        series.update(p);
      }
    });
  }

  // UC-04 表示/非表示。line/histogram は applyOptions({visible})、priceLine は除去/再生成。
  //   ISSUE-109: 系列単位の可視性（styleMeta.visible）と AND 合成する（インスタンス eye ON へ
  //   戻しても、スタイル設定で個別非表示にした系列は非表示のまま）。
  setVisible(instanceId, visible) {
    const slot = this._instances.get(instanceId);
    if (!slot) {
      return;
    }
    slot.visible = visible;
    for (const [key, series] of slot.lines) {
      const seriesVisible = visible && (slot.styleMeta.get(key)?.visible ?? true);
      series.applyOptions({ visible: seriesVisible });
      // 読み取り欄の overlay 行も表示状態へ追従させる（非表示は欄から除外）。
      const meta = this._overlayReadouts.get(key);
      if (meta) {
        meta.visible = seriesVisible;
      }
    }
    if (slot.hlinePayloads !== null) {
      if (visible && slot.priceLines.length === 0) {
        this._createPriceLines(slot, slot.hlinePayloads);
      } else if (!visible && slot.priceLines.length > 0) {
        this._removePriceLines(slot);
      }
    }
  }

  // ISSUE-109: 現在の系列スタイル（実描画値）を返す。スタイルタブの初期表示用。
  //   [{ name, kind, color, width, style, visible }]（生成順）。未知 instance は空配列。
  getSeriesStyles(instanceId) {
    const slot = this._instances.get(instanceId);
    if (!slot) {
      return [];
    }
    return [...slot.styleMeta.values()].map((m) => ({ ...m }));
  }

  // ISSUE-109: 系列単位のスタイル上書き（色/線幅/線種/可視性）。patch は差分のみ指定可。
  //   lwc series.applyOptions で即時反映（再計算不要・仕様 §6.1）。styleMeta と overlay 読み取り欄の
  //   色/可視性も同期する。未知系列は no-op（false）。可視性はインスタンス可視と AND 合成。
  applySeriesStyle(instanceId, seriesName, patch = {}) {
    const slot = this._instances.get(instanceId);
    if (!slot) {
      return false;
    }
    const key = `${instanceId}::${seriesName}`;
    const series = slot.lines.get(key);
    const meta = slot.styleMeta.get(key);
    if (!series || !meta) {
      return false;
    }
    // ISSUE-112（ユーザー裁定）: バー別ヒート配色（heat）の histogram は色 patch を無視する
    //   （ヒート表示が絶対優先・データ全塗り替えでヒートを潰す ISSUE-111 の機構は撤去）。
    //   heat 以外の histogram は series options.color が素で効く（バー別色が無いため上書き不要）。
    if (patch.color != null && !(seriesKind(meta.kind).supportsHeat && meta.heat)) {
      meta.color = patch.color;
    }
    if (patch.width != null) {
      meta.width = patch.width;
    }
    if (patch.style != null) {
      meta.style = patch.style;
    }
    if (patch.visible !== undefined && patch.visible !== null) {
      meta.visible = !!patch.visible;
    }
    const options = { color: meta.color, visible: slot.visible && meta.visible };
    if (seriesKind(meta.kind).appliesLineStyle) {
      if (meta.width != null) {
        options.lineWidth = meta.width;
      }
      if (meta.style != null) {
        options.lineStyle = toLineStyleInt(meta.style);
      }
    }
    series.applyOptions(options);
    const ro = this._overlayReadouts.get(key);
    if (ro) {
      ro.color = meta.color;
      ro.visible = slot.visible && meta.visible;
    }
    return true;
  }

  _removePriceLines(slot) {
    const host = slot.priceLineHost ?? this._mainSeries;
    for (const pl of slot.priceLines) {
      host.removePriceLine(pl);
    }
    slot.priceLines = [];
  }

  // UC-05 削除（冪等）。系列・水準線・ウォーターマーク・専用 pane をまとめて除去する。
  remove(instanceId) {
    const slot = this._instances.get(instanceId);
    if (!slot) {
      return;
    }
    // 価格線は系列除去より先に外す（pane 配置では水準線の host が当の系列のため）。
    this._removePriceLines(slot);
    for (const key of slot.lines.keys()) {
      // 読み取り欄の overlay メタも掃除する（残ると削除済み指標が読み取り欄に残る）。
      this._overlayReadouts.delete(key);
    }
    for (const series of slot.lines.values()) {
      this._chart.removeSeries(series);
    }
    if (slot.watermark && typeof slot.watermark.detach === 'function') {
      slot.watermark.detach();
    }
    // 専用 pane を除去（index はリオーダーされるため除去時に解決する）。preserveEmptyPane=true の
    // ため上の removeSeries では自動削除されず、ここで一度だけ確実に除去できる。idx<0 は防御。
    if (slot.pane && typeof this._chart.removePane === 'function') {
      const idx = slot.pane.paneIndex();
      if (idx >= 0) {
        this._chart.removePane(idx);
      }
    }
    this._instances.delete(instanceId);
  }

  // ─── ライブ追従（LiveFollowController 用）向け additive メソッド群 ───
  //   本 3 メソッドは構築子・既存メソッドを 1byte も変えずに末尾追加したもの（present 固有の
  //   ライブ追従トグルが呼ぶ）。replay は本メソッドを一切呼ばないため symlink 共有下でも inert
  //   （replay 側の描画差分ゼロ）。lwc の timeScale/applyOptions 直叩きは本所（ChartRenderer）に閉じる。

  // 可視論理範囲の変化を購読し、右端に居るか（atRightEdge）を bool で cb へ渡す。
  //   atRightEdge = getVisibleLogicalRange().to >= (total-1) - EPS（EPS≈1バー）。total は基準 candles 本数。
  //   timeScale/subscribe/getVisibleLogicalRange 非提供時・range 取得不能時は no-op（後方互換）。
  subscribeVisibleRange(cb) {
    if (typeof cb !== 'function') {
      return;
    }
    const ts = typeof this._chart.timeScale === 'function' ? this._chart.timeScale() : null;
    if (!ts || typeof ts.subscribeVisibleLogicalRangeChange !== 'function') {
      return;
    }
    const EPS = 1; // 右端判定の許容（約 1 バー）。programmatic scroll 由来の微小ずれを吸収する。
    ts.subscribeVisibleLogicalRangeChange(() => {
      if (typeof ts.getVisibleLogicalRange !== 'function') {
        return;
      }
      const r = ts.getVisibleLogicalRange();
      if (!r || typeof r.to !== 'number') {
        return; // 範囲未確定（データ空・初期化前）は通知しない。
      }
      const total = this._baseCandles ? this._baseCandles.length : 0;
      const atRightEdge = r.to >= (total - 1) - EPS;
      cb(atRightEdge);
    });
  }

  // 最新足（リアルタイム端）へスナップする（再FOLLOW の catch-up・» ボタン）。
  //   timeScale/scrollToRealTime 非提供時は no-op（後方互換）。
  //   speed（ISSUE-116 追記3）: >1 で lwc 既定アニメ（約 1000ms・速度指定不可）の代わりに自前
  //   イージング（ease-out cubic・1000/speed ms）で scrollToPosition(pos, false) を毎フレーム刻む。
  //   必要 API（scrollPosition/scrollToPosition/requestAnimationFrame）が欠ける環境は lwc 既定へ
  //   フォールバック（SSR/テスト・後方互換）。既存呼出し（speed 省略=1）は挙動不変。
  scrollToRealTime({ speed = 1 } = {}) {
    const ts = typeof this._chart.timeScale === 'function' ? this._chart.timeScale() : null;
    if (!ts || typeof ts.scrollToRealTime !== 'function') {
      return;
    }
    const raf = typeof requestAnimationFrame === 'function' ? requestAnimationFrame : null;
    if (!(speed > 1) || !raf
        || typeof ts.scrollPosition !== 'function' || typeof ts.scrollToPosition !== 'function') {
      ts.scrollToRealTime();
      return;
    }
    const from = ts.scrollPosition();
    const to = (typeof ts.options === 'function' && ts.options() && ts.options().rightOffset) || 0;
    const duration = 1000 / speed;
    let t0 = null;
    const step = (now) => {
      if (t0 === null) {
        t0 = now;
      }
      const k = Math.min(1, (now - t0) / duration);
      const eased = 1 - (1 - k) ** 3; // ease-out cubic
      ts.scrollToPosition(from + (to - from) * eased, false);
      if (k < 1) {
        raf(step);
      }
    };
    raf(step);
  }

  // 分析モードの背景 tint を適用/解除する（on=true で薄い tint、off で既定背景へ復元）。
  //   既定背景は初回呼び出し時に chart.options().layout.background から遅延取得しキャッシュする
  //   （構築子を変更しないため）。取得不能時は既定色フォールバック。applyOptions 非提供時は no-op。
  setAnalysisTint(on) {
    if (typeof this._chart.applyOptions !== 'function') {
      return;
    }
    // 分析モード（ANALYSIS）背景 tint 色。既定背景 #131722 より僅かに紫寄りに振り状態を明示する
    //   （薄い tint＝ユーザー要求「背景色で状態明示」。目視微調整はユーザーが後で実施）。
    const ANALYSIS_TINT_COLOR = '#1b1a24';
    // options 取得不能時の既定背景フォールバック色（composition root の layout.background と同値）。
    const DEFAULT_BACKGROUND_COLOR = '#131722';
    // 既定背景を一度だけ捕捉（構築子外・遅延初期化）。以降の tint on/off はこの基準へ復元する。
    //   ISSUE-119: options() が返す background は lwc 内部 options への参照でありうる。applyOptions は
    //   内部オブジェクトへの in-place マージのため、参照のまま保持すると tint ON で基準色まで tint 色に
    //   書き換わり復元が無変化になる。浅いコピーで snapshot 化して内部と切り離す。
    if (this._analysisTintBase === undefined) {
      let base = null;
      if (typeof this._chart.options === 'function') {
        const o = this._chart.options();
        base = (o && o.layout && o.layout.background) ? { ...o.layout.background } : null;
      }
      this._analysisTintBase = base;
    }
    if (on) {
      // 既定背景の type を保ったまま色だけ分析 tint へ差し替える（type 不明時は色のみ）。
      const type = this._analysisTintBase ? this._analysisTintBase.type : undefined;
      const bg = (type !== undefined)
        ? { type, color: ANALYSIS_TINT_COLOR }
        : { color: ANALYSIS_TINT_COLOR };
      this._chart.applyOptions({ layout: { background: bg } });
      return;
    }
    // 復元: 捕捉した既定背景（無ければ既定色フォールバック）。
    const restore = this._analysisTintBase || { color: DEFAULT_BACKGROUND_COLOR };
    this._chart.applyOptions({ layout: { background: restore } });
  }
}
