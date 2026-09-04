// ScaleController（adapter/front/scale_controller.js）— 価格軸ズーム/パン数学の協働クラス
// @upstream-isolation: scale_controller.js
//   （SOLID 是正 🔴-2: chart_renderer.js から 1:1 抽出）。
//
// ChartRenderer（ファサード）の内部協働子。共有状態（_chart / _mainSeries / _paneHeight /
//   _baseCandles）は ChartRenderer が所有し続け、本クラスはコンストラクタで注入された host 参照
//   経由で読み書きする（協働子間の直接依存は作らない）。公開面（ChartRenderer の public
//   メソッド・export）は不変で、実体だけが本ファイルへ移動した。

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

export class ScaleController {
  // host: ChartRenderer インスタンス（共有状態 _chart/_mainSeries/_paneHeight/_baseCandles の所有者）。
  constructor(host) {
    this._h = host;
  }

  // 縦パンの px→価格換算に使う pane 高（container 高 - timeScale().height() 相当）を設定。
  //   composition root が resize 時などに供給する。消費者は panPriceByPixels のみ（未設定時は
  //   false＝安全側）。handlePriceWheel は getVisibleRange を使うため pane 高に依存しない。
  setPaneHeight(h) {
    this._h._paneHeight = (typeof h === 'number' && h > 0) ? h : null;
  }

  // 右価格軸ハンドル（mainSeries.priceScale('right') 優先、無ければ chart.priceScale('right')）。
  //   lwc 直叩きは本所に隔離。いずれも非提供なら null（後方互換）。
  _rightPriceScale() {
    if (typeof this._h._mainSeries.priceScale === 'function') {
      return this._h._mainSeries.priceScale('right');
    }
    if (typeof this._h._chart.priceScale === 'function') {
      return this._h._chart.priceScale('right');
    }
    return null;
  }

  // baseCandles の価格全幅 {min,max}（絶対クランプの基準）。未設定/空は null。
  _candlesPriceRange() {
    const arr = this._h._baseCandles;
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
    const ts = typeof this._h._chart.timeScale === 'function' ? this._h._chart.timeScale() : null;
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
    const p = typeof this._h._mainSeries.coordinateToPrice === 'function'
      ? this._h._mainSeries.coordinateToPrice(y) : null;
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
    const paneHeight = this._h._paneHeight;
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

  // 価格軸領域判定の小ヘルパ（x >= timeScale().width()）。composition root の dblclick 判定用（任意）。
  isOverPriceAxis(x) {
    const ts = typeof this._h._chart.timeScale === 'function' ? this._h._chart.timeScale() : null;
    if (!ts || typeof ts.width !== 'function') {
      return false;
    }
    return x >= ts.width();
  }

  // ISSUE-150: pane 価格軸の手動スケールレンジを読む（scaleHost 系列の priceScale 経由）。
  //   手動スケール中（options().autoScale === false）のみ {from, to} を返し、自動スケール中・
  //   pane 無し・API 非提供・レンジ不正は null（＝退避しない）。読み取りのみで状態を変えない。
  _capturePaneScaleRange(slot) {
    const host = slot.pane ? slot.scaleHost : null;
    if (!host || typeof host.priceScale !== 'function') {
      return null;
    }
    const ps = host.priceScale();
    if (!ps || typeof ps.options !== 'function' || typeof ps.getVisibleRange !== 'function') {
      return null;
    }
    if (ps.options().autoScale !== false) {
      return null;
    }
    const vr = ps.getVisibleRange();
    if (!vr || !Number.isFinite(vr.from) || !Number.isFinite(vr.to) || vr.from === vr.to) {
      return null;
    }
    return { from: vr.from, to: vr.to };
  }

  // ISSUE-150: 退避済みの手動レンジを再生成後の pane 価格軸へ復元する（1 redraw で 1 回だけ）。
  //   setVisibleRange は lwc 内部で autoScale=false を設定する＝軸ドラッグの手動スケールと同一状態。
  //   復元失敗（レンジ不正等）は自動スケールのまま進める（redraw を壊さない）。
  _restorePaneScaleRange(slot) {
    const saved = slot.savedPaneScaleRange;
    if (!saved) {
      return;
    }
    const host = slot.pane ? slot.scaleHost : null;
    if (!host || typeof host.priceScale !== 'function') {
      // 系列が未追加（このグループの payload が空等）＝退避を保持し、後続グループで復元する。
      return;
    }
    // ここで復元を確定消費する（成功・失敗いずれでも二重適用しない）。
    slot.savedPaneScaleRange = null;
    const ps = host.priceScale();
    if (!ps || typeof ps.setVisibleRange !== 'function') {
      return;
    }
    try {
      ps.setVisibleRange({ from: saved.from, to: saved.to });
    } catch (e) {
      // 復元不能（lwc がレンジを拒否等）は自動スケールへフォールバック（描画継続を優先）。
    }
  }
}
