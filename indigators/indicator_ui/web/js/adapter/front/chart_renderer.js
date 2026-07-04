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

// lineStyle 文字列 → lightweight-charts LineStyle 整数（v4/v5 共通: Solid=0 / Dotted=1 / Dashed=2）。
const LINE_STYLE_INT = Object.freeze({ solid: 0, dotted: 1, dashed: 2 });

function toLineStyleInt(style) {
  return LINE_STYLE_INT[style] ?? LINE_STYLE_INT.solid;
}

// メイン（ローソク）pane と オシレータ pane の高さ相対比。ローソクを大きく見せる初期値。
//   ユーザーは pane separator のドラッグ（機能④）で後から自由に調整できる。
const MAIN_PANE_STRETCH = 3;
const INDICATOR_PANE_STRETCH = 1;

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
    // 価格軸ホイールズーム: override 価格レンジ（null=自動スケール）。handlePriceWheel で更新し、
    //   resetPriceZoom / ダブルクリックで null（自動スケール復帰）へ戻す。
    this._priceZoomRange = null;
    // 価格軸ズームの座標→価格変換に使う pane 高（container 高 - timeScale().height() 相当）。
    //   composition root が setPaneHeight で供給（未設定時は timeScale から導出フォールバック）。
    this._paneHeight = null;
    // 価格軸ズームの autoscaleInfoProvider は _applyPriceProvider で mainSeries へ設定する。
    //   ★呼び出しの度に新クロージャを applyOptions で再設定して再オートスケールを誘発する
    //   （中身だけ変えても lwc は再計算しないため）。override 有→{priceRange} を返し、null→
    //   original() 素通し（挙動不変）。切替は _priceZoomRange の内部状態で行う。
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
    // 価格ズーム override とスクラブ span キャッシュもリセットする（時間足/データセット切替で
    //   新しい価格帯へ自動スケール復帰させる。これが無いと provider が旧レンジを返し続け、新足が
    //   枠外にはみ出したまま dblclick まで戻らない＝レビュー指摘 🟡#1 の修正）。
    this._priceZoomRange = null;
    this._replayViewSpan = null;
    this._applyPriceProvider(); // provider を素通し（自動スケール）へ即時復帰。
    // 読み取り欄の最新足の単一源を更新（配列末尾の足）。空配列なら null。
    this._lastBar = arr.length > 0 ? arr[arr.length - 1] : null;
    this._chart.timeScale().fitContent();
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
    const ts = typeof this._chart.timeScale === 'function' ? this._chart.timeScale() : null;
    if (!ts || typeof ts.applyOptions !== 'function') {
      return;
    }
    if (frac == null || !(frac > 0)) {
      ts.applyOptions({ rightOffset: 0 }); // 復元（lwc 既定）。
      return;
    }
    const w = typeof ts.width === 'function' ? ts.width() : 0;
    const bs = (typeof ts.options === 'function' && ts.options() && ts.options().barSpacing) || 6;
    const bars = w > 0 ? Math.max(0, Math.round((w * frac) / bs)) : 0;
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

  // 価格軸ホイールズーム: 座標→価格変換に使う pane 高（container 高 - timeScale().height() 相当）を設定。
  //   composition root が resize 時などに供給する。未設定時は handlePriceWheel が timeScale().height()
  //   から導出フォールバックするが、container 高が必要なため設定を推奨（新規追加・既存描画へ非干渉）。
  setPaneHeight(h) {
    this._paneHeight = (typeof h === 'number' && h > 0) ? h : null;
  }

  // 価格軸ホイールズームの autoscaleInfoProvider を **mainSeries** へ適用する。
  //   注意: autoscaleInfoProvider は series オプション（priceScale に渡しても lwc は無視＝実機で確認した
  //   バグの修正）。また provider の中身（_priceZoomRange）だけ変えても lwc は再オートスケールしないため、
  //   **呼び出しの度に新しいクロージャを applyOptions で再設定**してオプション変更→再計算を誘発する。
  //   override（_priceZoomRange）有→{priceRange:{minValue,maxValue}}／null→original() 素通し（挙動不変）。
  _applyPriceProvider() {
    if (typeof this._mainSeries.applyOptions !== 'function') {
      return;
    }
    this._mainSeries.applyOptions({
      autoscaleInfoProvider: (original) => {
        if (this._priceZoomRange) {
          // _priceZoomRange は「目標の可視レンジ」。lwc は provider のレンジへ scaleMargins
          //   （既定 上0.2/下0.1）を上乗せして表示するため、逆算で内側に縮めた値を返す
          //   （そのまま返すと毎回 margins ぶん広がり複利増幅＝1e24 発散の真因・実機バグ修正）。
          const m = this._scaleMargins();
          const vis = this._priceZoomRange;
          const span = vis.max - vis.min;
          return {
            priceRange: {
              minValue: vis.min + span * m.bottom,
              maxValue: vis.max - span * m.top,
            },
          };
        }
        return typeof original === 'function' ? original() : original;
      },
    });
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
  //   カーソル価格 p=coordinateToPrice(y)、現レンジは coordinateToPrice(0)（上端＝高値）と
  //   coordinateToPrice(paneHeight)（下端＝安値）から読む。zoomedPriceRange で新レンジを算出し
  //   _priceZoomRange を更新、priceScale('right').applyOptions({autoScale:true}) で provider を効かせる。
  //   処理したら true（呼び出し側が preventDefault する）。軸領域外・座標 null・データ無しは false。
  //   チャート本体上のホイール（時間軸ズーム）は奪わない（x<width で false）。
  handlePriceWheel(x, y, deltaY) {
    const ts = typeof this._chart.timeScale === 'function' ? this._chart.timeScale() : null;
    if (!ts || typeof ts.width !== 'function') {
      return false;
    }
    if (x < ts.width()) {
      return false; // チャート本体領域＝時間軸ズームに委ねる。
    }
    if (typeof this._mainSeries.coordinateToPrice !== 'function') {
      return false;
    }
    // pane 高（container 高 - timeScale 高）。setPaneHeight 未設定時は座標の下端が不明なため
    //   timeScale().height() を差し引けないが、既存レンジは 0..paneHeight で読む必要がある。
    const paneHeight = this._paneHeight;
    if (!(paneHeight > 0)) {
      return false; // pane 高未供給＝座標→価格の下端が定まらない（安全側で何もしない）。
    }
    const p = this._mainSeries.coordinateToPrice(y);
    if (p == null) {
      return false; // データ無し・座標変換不能。
    }
    // 基準レンジ: ズーム中は内部状態（目標可視レンジ）を使い、表示を読み直さない
    //   （lwc が scaleMargins を上乗せした表示を再度読むと複利増幅する＝発散の真因・実機バグ修正）。
    let base = this._priceZoomRange;
    if (!base) {
      const topPrice = this._mainSeries.coordinateToPrice(0);
      const bottomPrice = this._mainSeries.coordinateToPrice(paneHeight);
      if (topPrice == null || bottomPrice == null) {
        return false;
      }
      const min = Math.min(topPrice, bottomPrice);
      const max = Math.max(topPrice, bottomPrice);
      if (!(max > min)) {
        return false; // 縮退レンジ。
      }
      base = { min, max };
    }
    // データ全幅（baseCandles の low/high）由来の絶対クランプで発散を封じる。
    this._priceZoomRange = clampPriceRange(
      zoomedPriceRange(base, p, deltaY), this._candlesPriceRange(),
    );
    this._applyPriceProvider(); // series へ新クロージャを再設定＝オプション変更で再オートスケール誘発。
    const ps = this._rightPriceScale();
    if (ps && typeof ps.applyOptions === 'function') {
      ps.applyOptions({ autoScale: true }); // 軸ドラッグ等で autoScale=false でも provider を効かせる。
    }
    return true;
  }

  // チャート本体の縦ドラッグによる価格パン（上下移動）。dy=ポインタ縦移動量[px]（下+）。
  //   横方向は lwc の時間軸パンに委ね（本メソッドは価格のみ操作）、両者が合成されて 2D パンになる。
  //   ドラッグ下げ（dy>0）＝内容を下へ引く＝表示レンジを上へ（min/max とも +）。span は不変。
  //   基準レンジはズーム/パン中の内部状態（_priceZoomRange）を優先し、無ければ現表示から読む
  //   （表示を毎回読み直すと scaleMargins 上乗せで発散するため・handlePriceWheel と同じ方針）。
  //   結果は clampPriceRange（span 保存・中心はデータ範囲内）で制限。処理したら true。
  panPriceByPixels(dy) {
    if (typeof this._mainSeries.coordinateToPrice !== 'function') {
      return false;
    }
    const paneHeight = this._paneHeight;
    if (!(paneHeight > 0) || !(Math.abs(dy) > 0)) {
      return false;
    }
    let base = this._priceZoomRange;
    if (!base) {
      const topPrice = this._mainSeries.coordinateToPrice(0);
      const bottomPrice = this._mainSeries.coordinateToPrice(paneHeight);
      if (topPrice == null || bottomPrice == null) {
        return false;
      }
      const min = Math.min(topPrice, bottomPrice);
      const max = Math.max(topPrice, bottomPrice);
      if (!(max > min)) {
        return false;
      }
      base = { min, max };
    }
    const span = base.max - base.min;
    const shift = (dy / paneHeight) * span; // dy>0（下げ）→表示レンジを上へ。
    this._priceZoomRange = clampPriceRange(
      { min: base.min + shift, max: base.max + shift }, this._candlesPriceRange(),
    );
    this._applyPriceProvider();
    const ps = this._rightPriceScale();
    if (ps && typeof ps.applyOptions === 'function') {
      ps.applyOptions({ autoScale: true });
    }
    return true;
  }

  // 価格ズーム（override）が有効か。true のとき本体ドラッグの上下パンを許可する（全体表示では不許可）。
  isPriceZoomed() {
    return this._priceZoomRange != null;
  }

  // 価格軸のダブルクリック等で自動スケールへ復帰する（override を解除）。provider は素通しに戻る。
  resetPriceZoom() {
    this._priceZoomRange = null;
    this._applyPriceProvider(); // 新クロージャ再設定で即時に自動スケールへ復帰させる。
    const ps = this._rightPriceScale();
    if (ps && typeof ps.applyOptions === 'function') {
      ps.applyOptions({ autoScale: true });
    }
  }

  // テスト用フック: インストール済み autoscaleInfoProvider を明示的に呼び出す（本番未使用）。
  //   lightweight-charts が内部で呼ぶ provider の挙動（override→{priceRange}／null→original 素通し）を
  //   Fake priceScale 越しに検証するために使う。provider 未インストール時は original 素通し相当。
  __callAutoscaleProvider(original) {
    if (this._priceZoomRange) {
      return { priceRange: { minValue: this._priceZoomRange.min, maxValue: this._priceZoomRange.max } };
    }
    return typeof original === 'function' ? original() : original;
  }

  // 右価格軸の scaleMargins（{top,bottom}・0..1）。options() 非提供時は 0（テスト互換・逆算なし）。
  _scaleMargins() {
    const ps = this._rightPriceScale();
    const m = (ps && typeof ps.options === 'function' && ps.options() && ps.options().scaleMargins) || null;
    const top = m && Number.isFinite(m.top) ? m.top : 0;
    const bottom = m && Number.isFinite(m.bottom) ? m.bottom : 0;
    // 合計が 1 に近い異常値は無効化（ゼロ割/負 span 防止）。
    return (top + bottom < 0.9) ? { top, bottom } : { top: 0, bottom: 0 };
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

  // リプレイスワイプの感度基準＝1 バーあたりのピクセル幅（barSpacing）。
  //   移植元 prototype_260630-01/js/app.js L452-453: |logicalToCoordinate(1) - logicalToCoordinate(0)|。
  //   ★ズームアウトで barSpacing が極小（<0.5px）のときは 8px を下限に使う（プロト準拠）。これにより
  //     少しのマウス移動でスライダが暴走するのを防ぐ（絶対マッピングの過敏さの修正）。非提供時も 8。
  pixelsPerBar() {
    const ts = typeof this._chart.timeScale === 'function' ? this._chart.timeScale() : null;
    if (ts && typeof ts.logicalToCoordinate === 'function') {
      const c0 = ts.logicalToCoordinate(0);
      const c1 = ts.logicalToCoordinate(1);
      if (c0 != null && c1 != null && Math.abs(c1 - c0) > 0.5) {
        return Math.abs(c1 - c0);
      }
    }
    return 8; // barSpacing 極小/非提供時の下限（プロト準拠）。
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
    this._mainSeries.update(candle);
    // 最新足の単一源を更新し、hover していない読み取り表示が古くならないよう DTO を再発火する。
    this._lastBar = candle;
    // v6: 基準 candles の末尾を差分反映（同 time は置換・新 time は追加）。減光の元集合を同期。
    this._mergeBaseCandle(candle);
    this._emitReadout(null);
    // v6: candle 変更を observer へ通知（live tick でも hover 中なら highlight 解除させる）。
    this._onCandlesChanged();
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
    const definition = kind === 'histogram' ? this._lwc.HistogramSeries : this._lwc.LineSeries;
    for (const p of payloads ?? []) {
      const options = {
        color: p.color,
        priceLineVisible: false,
        lastValueVisible: false,
        title: p.name,
      };
      if (kind === 'line') {
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
      if (!slot.scaleHost) {
        slot.scaleHost = series;
      }
      // overlay（pane 0 重ね描き）の line 系列のみ読み取り欄の overlay 行に載せる。
      //   color/name と末尾点 value（hover 解除時の fallback）を保持する。
      if (!pane && kind === 'line') {
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
  setVisible(instanceId, visible) {
    const slot = this._instances.get(instanceId);
    if (!slot) {
      return;
    }
    slot.visible = visible;
    // 読み取り欄の overlay 行も表示状態へ追従させる（非表示は欄から除外）。
    for (const key of slot.lines.keys()) {
      const meta = this._overlayReadouts.get(key);
      if (meta) {
        meta.visible = visible;
      }
    }
    for (const series of slot.lines.values()) {
      series.applyOptions({ visible });
    }
    if (slot.hlinePayloads !== null) {
      if (visible && slot.priceLines.length === 0) {
        this._createPriceLines(slot, slot.hlinePayloads);
      } else if (!visible && slot.priceLines.length > 0) {
        this._removePriceLines(slot);
      }
    }
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
}
