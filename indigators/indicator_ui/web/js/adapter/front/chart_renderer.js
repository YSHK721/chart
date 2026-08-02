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
// series_kind 台帳の消費契約（series_kind.test.js「registry is the only kind ledger」）: 本ファイルは
//   台帳消費 3 ファイルの一角であり、能力分岐の実体は SeriesDrawer（series_drawer.js・SOLID 是正 🔴-2 で
//   抽出）へ委譲した。委譲後も raw kind 文字列比較を持ち込まない契約の固定点として import を維持する。
import { seriesKind } from '../../domain/series_kind.js';
import { ScaleController, zoomedPriceRange, clampPriceRange } from './scale_controller.js';
import { CandleFeed } from './candle_feed.js';
import { SeriesDrawer, WATERMARK_COLOR, lastPointValue } from './series_drawer.js';

// zoomedPriceRange/clampPriceRange の単一ソースは scale_controller.js（SOLID 是正 🔴-2 で抽出）。
//   既存 import（テスト・他ファイル）を壊さないため本モジュールからも再 export する。
//   ※ 「export { X } from モジュール」の再 export 構文は build.mjs の stripModuleSyntax
//     （import 行剥がし）で壊れるため、import 済みシンボルの別行 export
//     （剥がし後は無害なブロック文）にする。
export { zoomedPriceRange, clampPriceRange };

// v6（§12）: ホバー中ペア外のローソク足に被せる極暗色（背景 #131722 に近い不透明暗色）。
//   per-bar color/borderColor/wickColor を本色で上書きし、ローソクのみを限りなく減光する
//   （背景ピクセルは一切変更しない）。ペア内バーは色を付けず原色（既定 up/down 着色）に委ねる。
const DIM_CANDLE_COLOR = '#16191f';

// sessions（日別プロファイル分割）: ローソク透明化用の色。透明＝価格軸は残しローソクだけ消す。
//   復元色は composition_root_front.js の mainSeries 既定（up=#26a69a / down=#ef5350）と一致させる。
const TRANSPARENT_COLOR = 'rgba(0,0,0,0)';
const CANDLE_UP_COLOR = '#26a69a';
const CANDLE_DOWN_COLOR = '#ef5350';

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
    // 背景プリミティブ（用途 key -> primitive）。attachBackgroundPrimitive が所有し、メイン系列へ 1 度だけ装着する。
    this._backgroundPrimitives = new Map();
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
    // ISSUE-114/115: チャート右端の常設余白（幅比率基準）。最新足が右端に張り付くストレスの解消。
    //   rightOffset は scrollToRealTime / FOLLOW 追従で尊重されるため、ライブ新足でも余白が維持される。
    //   ISSUE-164（ユーザー裁定）: ズームへの自動追従（可視範囲購読での再適用）は廃止済み。
    //   余白の適用は明示イベント（初期表示・時間足切替・MP 余白率変更・最新足へ戻る）のみで、
    //   ズーム中の余白 px 一定性は保証しない（ユーザー操作の意思を優先）。
    this._lastRightOffsetBars = null;
    // SOLID 是正 🔴-2: 価格軸ズーム/パン数学（handlePriceWheel/panPriceByPixels/resetPriceZoom・
    //   ISSUE-150 の pane スケール退避/復元）は ScaleController（協働子）へ委譲する。共有状態は
    //   本クラスが所有し続け、協働子には this（host）参照を注入する（公開面・挙動は不変）。
    this._scale = new ScaleController(this);
    // SOLID 是正 🔴-2: ローソクデータ所有と更新（setCandles/updateLastCandle/resync 系・
    //   rightOffset 同期 _syncRightOffset）は CandleFeed（協働子）へ委譲する（公開面・挙動は不変）。
    this._candleFeed = new CandleFeed(this);
    // SOLID 是正 🔴-2: 系列生成・スタイル（_renderSeries/applySeriesStyle/_swapSeriesType/
    //   setVisible の実体）は SeriesDrawer（協働子）へ委譲する（公開面・挙動は不変）。
    this._drawer = new SeriesDrawer(this);
    this._syncRightOffset();
    // ISSUE-164（ユーザー裁定 2026-07-23）: ズーム/ドラッグ（可視範囲変化）に反応して右余白を
    //   再適用する購読は撤去した。ユーザーの拡大縮小操作と無関係に rightOffset を適用し直すのは
    //   lwc では「最新足基準へのスクロール」副作用を持ち、『過去へ遡って拡大すると右端へ戻る』
    //   ジャンプの根本原因だった（ISSUE-148 はガードで蓋をしただけで隙間から再発）。
    //   余白の適用点は明示イベントのみ: 初期表示（直上）・時間足切替（setCandles）・
    //   MP 余白率変更（setRightMarginFraction）・最新足へ戻る操作（scrollToRealTime）。
    //   ズーム中の余白 px 一定性は保証しない（ユーザー操作の意思を優先する）。
    // 機能③: クロスヘア移動で pane ウォーターマークへ系列値を追記。
    if (typeof this._chart.subscribeCrosshairMove === 'function') {
      this._chart.subscribeCrosshairMove((param) => this._onCrosshairMove(param));
    }
  }

  // 時間足切替: メインローソク系列のデータ差し替え（実体は CandleFeed.setCandles・SOLID 是正 🔴-2）。
  setCandles(candles) {
    this._candleFeed.setCandles(candles);
  }

  // ISSUE-163: 全 pane 価格軸の手動スケールを破棄し自動スケールへ戻す（時間足切替用）。
  //   ISSUE-150 の手動スケール保持（keepPane 退避/復元）は「同一時間足での再計算」を守るための
  //   機構であり、値域が変わる時間足切替で旧レンジを持ち越すと系列がクリップして全高ブロック化する
  //   （実 UI 再現済み 2026-07-23）。切替時は退避を破棄し autoScale=true へ戻す。
  resetPaneScales() {
    for (const slot of this._instances.values()) {
      slot.savedPaneScaleRange = null;
      const host = slot.pane ? slot.scaleHost : null;
      if (!host || typeof host.priceScale !== 'function') {
        continue;
      }
      const ps = host.priceScale();
      if (ps && typeof ps.applyOptions === 'function') {
        ps.applyOptions({ autoScale: true });
      }
    }
  }

  // 基準 candles（_baseCandles）の読み取り専用アクセサ。リプレイバーが slider の min/max・
  //   index→time 変換に使う（新規追加・読取のみ＝既存描画へ非干渉）。未設定時は空配列。
  getCandles() {
    return this._baseCandles ?? [];
  }

  // メインペイン（pane 0＝価格パネル）へ背景プリミティブを 1 度だけ装着し、その実体を返す。
  //   attachPrimitive という lwc API 名を扱うのは本クラス（隔離点）に閉じる。
  //   key: 用途ごとの名前空間（用途が増えても互いの実体が混ざらない）。2 回目以降は同一実体を返す。
  //
  //   指標 pane へは装着しない: pane 内の系列は指標の再計算で作り直され、その都度プリミティブが
  //   外れる。「作り直しを検知して張り直す」同期を持ち込むと、検知の取りこぼしが**一部の pane だけ
  //   塗られない**という分かりにくい欠落として出る（実測で再現）。メイン系列は生成が 1 度きりで
  //   作り直されないため、装着も 1 度で完結し同期そのものが不要になる。
  attachBackgroundPrimitive(key, factory) {
    const existing = this._backgroundPrimitives.get(key);
    if (existing) {
      return existing;
    }
    if (!this._mainSeries || typeof this._mainSeries.attachPrimitive !== 'function') {
      return null;
    }
    const primitive = factory();
    this._mainSeries.attachPrimitive(primitive);
    this._backgroundPrimitives.set(key, primitive);
    return primitive;
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

  // 右端余白の単一権威（ISSUE-115・実体は CandleFeed._syncRightOffset・SOLID 是正 🔴-2）。
  _syncRightOffset(opts) {
    this._candleFeed._syncRightOffset(opts);
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

  // 縦パンの px→価格換算に使う pane 高の設定（実体は ScaleController・SOLID 是正 🔴-2）。
  setPaneHeight(h) {
    this._scale.setPaneHeight(h);
  }

  // 価格軸ホイールズームの本体（実体は ScaleController.handlePriceWheel・SOLID 是正 🔴-2）。
  handlePriceWheel(x, y, deltaY) {
    return this._scale.handlePriceWheel(x, y, deltaY);
  }

  // チャート本体の縦ドラッグによる価格パン（実体は ScaleController.panPriceByPixels・SOLID 是正 🔴-2）。
  panPriceByPixels(dy) {
    return this._scale.panPriceByPixels(dy);
  }

  // 価格軸のダブルクリック等で自動スケールへ復帰する（実体は ScaleController.resetPriceZoom）。
  resetPriceZoom() {
    this._scale.resetPriceZoom();
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

  // 価格軸領域判定の小ヘルパ（実体は ScaleController.isOverPriceAxis・SOLID 是正 🔴-2）。
  isOverPriceAxis(x) {
    return this._scale.isOverPriceAxis(x);
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

  // 指定の時間レンジ [from, to]（UNIX 秒）を可視範囲にする（日別プロファイルの被覆日を全 tf で表示する）。
  //   （旧 focusRecentBars＝論理バー数基準は ISSUE-164 掃除で削除済み。バー数基準だと 1m で「日数」を「分数」と解釈して日別列が
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

  // ライブ更新: 最新足の差分反映（実体は CandleFeed.updateLastCandle・SOLID 是正 🔴-2）。
  updateLastCandle(candle) {
    this._candleFeed.updateLastCandle(candle);
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
  //   （実体は CandleFeed.resyncMissedCandles・SOLID 是正 🔴-2）
  resyncMissedCandles(candles) {
    return this._candleFeed.resyncMissedCandles(candles);
  }

  // v6: candle 変更 observer を後から据える（composition root の renderer/markers 生成順序差を吸収）。
  setCandleObserver(onCandlesChanged) {
    this._onCandlesChanged = typeof onCandlesChanged === 'function' ? onCandlesChanged : () => {};
  }

  // 現在値（最新足の終値・単一源 _lastBar 由来）。現在値ビュー等の読み手向け（無ければ null）。
  //   setCandles / updateLastCandle / リビールトリムのいずれでも _lastBar が更新される。
  lastClose() {
    return this._lastBar ? (this._lastBar.close ?? null) : null;
  }

  // v6: 基準 candles の末尾足の差分マージ（実体は CandleFeed._mergeBaseCandle・SOLID 是正 🔴-2）。
  _mergeBaseCandle(candle) {
    this._candleFeed._mergeBaseCandle(candle);
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

  // instanceId の描画スロット取得/生成（実体は SeriesDrawer._slot・SOLID 是正 🔴-2）。
  _slot(instanceId) {
    return this._drawer._slot(instanceId);
  }

  // line 系列群を生成（§7.1.2: 系列キー {instanceId}::{name}）。opts.pane=true で専用 pane。
  renderLine(instanceId, payloads, opts = {}) {
    this._renderSeries(instanceId, payloads, 'line', opts);
  }

  // histogram 系列群を生成（per-point の data[].color でバー別着色・level_colors 移植）。
  renderHistogram(instanceId, payloads, opts = {}) {
    this._renderSeries(instanceId, payloads, 'histogram', opts);
  }

  // level_dash 系列群を生成（ローソク足幅の水平ダッシュ・同値 4 値の Candlestick）。
  renderLevelDash(instanceId, payloads, opts = {}) {
    this._renderSeries(instanceId, payloads, 'level_dash', opts);
  }

  // line / histogram / level_dash を共通生成する（実体は SeriesDrawer._renderSeries・SOLID 是正 🔴-2）。
  _renderSeries(instanceId, payloads, kind, opts = {}) {
    this._drawer._renderSeries(instanceId, payloads, kind, opts);
  }

  // horizontal_line 群を priceLine として生成。当該 instance に line/histogram 系列が
  // あれば その系列（pane の価格軸）へ、無ければ mainSeries（価格バンド・pane 0）へ載せる。
  renderHorizontal(instanceId, hlines) {
    const slot = this._slot(instanceId);
    slot.hlinePayloads = hlines ?? [];
    this._createPriceLines(slot, slot.hlinePayloads);
  }

  // 水準線の生成（実体は SeriesDrawer._createPriceLines・SOLID 是正 🔴-2）。
  _createPriceLines(slot, hlines) {
    this._drawer._createPriceLines(slot, hlines);
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
  //   ISSUE-151 追補2: バー確定直後は「full 再描画（新バーまで反映済み）」と「確定前に発行された
  //   latest 応答（旧バーまで）」が交錯し、旧 time の点で lwc が 'Cannot update oldest data' を
  //   投げる。従来は例外がバッチ全体を中断し残り系列の末尾更新まで失われていた（止まって見える）。
  //   stale 点は 1 点単位で黙って捨て、バッチは最後まで適用する（正しい最新値は次の応答で届く）。
  updateSeriesTail(seriesKey, points) {
    this._withSeries(seriesKey, points, (series) => {
      // ISSUE-196（不変条件の構造的保証）: 時間足切替で空にした系列（clearInstanceData 済み）へ
      //   遅延到着した末尾差分を書き込むと、旧時間足の time を 1 点だけ持つ系列が生まれ、
      //   時間軸に「ローソクに存在しない time」が復活する（= `Value is null` の発生条件）。
      //   空系列への末尾差分は捨てる（正しい全点は直後の full 再計算が setData で描く）。
      if (typeof series.data === 'function' && series.data().length === 0) {
        return;
      }
      // ISSUE-197（例外駆動の制御フローを撤去・2026-07-30 実UI実測）: latest 応答は末尾 K 点を返すため、
      //   系列末尾が既に最新バーへ進んでいる定常状態では **毎回 K-1 点が「末尾より古い」** として届く。
      //   lightweight-charts の update は last より古い time を throw で拒否するので、これを try/catch
      //   任せにすると正常動作のさなかに例外が出続ける（実測: 1分足ライブで単一パターン
      //   `n=2 ok=1 ng=1 times=T-60,T before=T after=T` が 45 秒 798 回＝約 18 回/秒。ISSUE-197 が
      //   「日→1分 切替後 45 秒で 203 件」と記録したものは切替固有ではなく定常発生だった）。
      //   捨てる点は **比較で判定して update を呼ばない**。捨てる点の集合も更新後の末尾も従来と同一
      //   （実測: after == before ＝最新値は欠落していない＝実害は無く、コストとログ汚染だけがあった）。
      const lastPoint = typeof series.data === 'function' ? series.data().slice(-1)[0] : undefined;
      const lastTime = lastPoint ? lastPoint.time : undefined;
      for (const p of points ?? []) {
        // 事前判定は time が数値（UTCTimestamp）同士のときだけ行う。business day 形式など
        //   大小比較の意味が自明でない時刻表現は従来どおり update へ渡し、下の catch で無害化する。
        if (typeof lastTime === 'number' && typeof p?.time === 'number' && p.time < lastTime) {
          continue;
        }
        try {
          series.update(p);
        } catch (_e) {
          // 事前判定で拾えない stale（非数値 time）や想定外の例外もバッチ継続を優先し、
          //   点単位で無害化する（残り系列の末尾更新まで失わせない）。
        }
      }
    });
  }

  // ISSUE-196（不変条件の構造的保証）: 当該 instance の全系列データを空にする（系列・pane・
  //   スタイル・水準線は温存＝再生成なしで data のみ空）。
  //   lightweight-charts は「時間軸に載る time は当該系列にも存在する」ことを要求し、満たさないと
  //   colorer が ensureNotNull で `Value is null` を throw する（実測: 旧時間足の指標系列が残った
  //   まま新時間足のローソクを setData した瞬間に throw・その例外が再計算バッチを中断させ、
  //   指標が旧足のまま固着して以後の再計算も同じ throw で失敗し続ける）。
  //   時間足切替のように「ローソクの time 集合が入れ替わる」局面では、旧足の指標データを
  //   同一同期ブロック内で空にすることで違反状態を発生させない（描画は後続の再計算が行う）。
  clearInstanceData(instanceId) {
    const slot = this._instances.get(instanceId);
    if (!slot) {
      return;
    }
    for (const [key, series] of slot.lines.entries()) {
      series.setData([]);
      const meta = this._overlayReadouts.get(key);
      if (meta) {
        meta.lastValue = null;
      }
    }
  }

  // UC-04 表示/非表示（実体は SeriesDrawer.setVisible・SOLID 是正 🔴-2）。
  setVisible(instanceId, visible) {
    this._drawer.setVisible(instanceId, visible);
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

  // ISSUE-109: 系列単位のスタイル上書き（実体は SeriesDrawer.applySeriesStyle・SOLID 是正 🔴-2）。
  applySeriesStyle(instanceId, seriesName, patch = {}) {
    return this._drawer.applySeriesStyle(instanceId, seriesName, patch);
  }

  // 案A（btlm_trail_marod）: 系列の描画種別の line ⇄ histogram 差し替え（実体は SeriesDrawer._swapSeriesType・SOLID 是正 🔴-2）。
  _swapSeriesType(slot, key, meta, toKind) {
    return this._drawer._swapSeriesType(slot, key, meta, toKind);
  }

  // ISSUE-150: pane 価格軸の手動スケールレンジの退避（実体は ScaleController・SOLID 是正 🔴-2）。
  _capturePaneScaleRange(slot) {
    return this._scale._capturePaneScaleRange(slot);
  }

  // ISSUE-150: 退避済み手動レンジの復元（実体は ScaleController・SOLID 是正 🔴-2）。
  _restorePaneScaleRange(slot) {
    this._scale._restorePaneScaleRange(slot);
  }

  // 水準線の除去（実体は SeriesDrawer._removePriceLines・SOLID 是正 🔴-2）。
  _removePriceLines(slot) {
    this._drawer._removePriceLines(slot);
  }

  // UC-05 削除（冪等）。系列・水準線・ウォーターマーク・専用 pane をまとめて除去する。
  remove(instanceId, { keepPane = false } = {}) {
    const slot = this._instances.get(instanceId);
    if (!slot) {
      return;
    }
    // ISSUE-150: 再計算 redraw（keepPane=true）は pane の全系列を除去→再追加するため、pane 価格軸の
    //   手動スケール（軸ドラッグで autoScale=false になった状態）が失われる（メイン軸は mainSeries が
    //   残るため保持される＝非対称）。除去前に手動レンジを退避し、_renderSeries の再追加後に復元する。
    //   自動スケール中（autoScale !== false）は退避しない＝挙動不変。
    if (keepPane) {
      slot.savedPaneScaleRange = this._capturePaneScaleRange(slot);
    }
    // 価格線は系列除去より先に外す（pane 配置では水準線の host が当の系列のため）。
    this._removePriceLines(slot);
    for (const key of slot.lines.keys()) {
      // 読み取り欄の overlay メタも掃除する（残ると削除済み指標が読み取り欄に残る）。
      this._overlayReadouts.delete(key);
    }
    // 案A（btlm_trail_marod）: スワップ用に退避した保持データ（seriesData）も掃除する（slot 破棄で
    //   GC 対象になるが、保持配列を明示解放して即時開放する）。
    slot.seriesData.clear();
    for (const series of slot.lines.values()) {
      this._chart.removeSeries(series);
    }
    // ISSUE-149: 再計算の redraw（keepPane=true）は pane・watermark・slot を温存し系列だけを
    //   除去する。従来の全除去→末尾 addPane では pane の並び順が更新のたびに最下段へ移動していた
    //   （オシレーター更新で位置が変わる）。_ensurePane は slot.pane 既存時に再利用するため、
    //   直後の redraw が同じ pane（同じ位置）へ再生成される。
    if (keepPane) {
      slot.lines.clear();
      slot.styleMeta.clear();
      slot.scaleHost = null;       // 除去済み系列を指すため必ず初期化（水準線 host の再解決に必要）
      slot.priceLineHost = null;
      slot.hlinePayloads = null;
      slot.visible = true;         // 全除去→新規 slot と同じ初期状態（非表示は呼び出し側が再適用）
      return;
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
