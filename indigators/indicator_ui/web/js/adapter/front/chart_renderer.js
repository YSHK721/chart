// ChartRenderer（adapter/front/chart_renderer.js）— ChartRendererPort 実装・upstream 隔離点（唯一）。
// @upstream-isolation: chart_renderer.js
//
// 設計入力: 内部設計書 §3.3.4 / §7.1.2。
// ★ lightweight-charts v5.2.0 の JS API 名（addSeries / addPane / removePane / panes /
//   createPriceLine / setData / applyOptions / removeSeries / removePriceLine /
//   subscribeCrosshairMove / createTextWatermark / timeScale / attachPrimitive / priceScale /
//   getPane / getHeight / paneIndex / moveTo）を
//   呼んでよいのは **宣言された隔離単位** に限る:
//     (a) ChartRenderer 本体とその内部協働子（series_drawer / candle_feed / scale_controller）
//     (b) チャート生成の bootstrap（chart_bootstrap）
//     (c) lwc プラグイン契約（ISeriesPrimitive）の実装＝chart を受け取るのが upstream 仕様
//     (d) 合成根（可視範囲の購読のみ。ChartRenderer へ寄せるのが望ましい残件）
//   これは ``tests/upstream_isolation_declaration.test.js`` が **実際に走査して強制**する。
//   同検定は上の API 名リストと自身の施行リストの一致も検査する（宣言だけ増える／施行だけ増える、
//   という食い違いを構造的に不可能にする）。moveTo だけは canvas 2D の同名 API と衝突するため、
//   受け手が canvas コンテキストでない場合に upstream と判定する（理由は同検定に記載）。
//
//   かつてここは「呼ぶのは本ファイルだけ（§2.2 grep 0 件強制）」と書いていたが、その grep を
//   実行する仕組みは存在せず、実際は 11 ファイルが呼んでいた（ISSUE-262）。宣言を実態へ正し、
//   施行をテストへ移した。隔離単位を変えるときは当該テストの許可リストと本コメントを同時に更新する。
//
// 隠蔽する責務:
//   - line       → chart/pane.addSeries(LineSeries, ...) + series.setData(points)
//   - histogram  → chart/pane.addSeries(HistogramSeries, ...)（data[].color でバー別着色）
//   - horizontal_line → host series.createPriceLine(...)
//   - pane 指標（オシレータ）は専用 pane を生成（機能①: pane ごとに独立した価格軸／
//     機能④: pane 境界 separator のドラッグで高さ調整＝v5 既定 ON）。
//   - 機能②③（ISSUE-276 で置換）: pane 左上のテキストウォーターマーク（指標名＋系列値）は撤去し、
//     ペイン別凡例（pane_legend_view）へ統合した。本 class は幾何と値の DTO を供給するだけで、
//     文字の配置は DOM 側が持つ（canvas と DOM の二重表示・重なりを構造的に無くす）。
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
import { SeriesDrawer, lastPointValue } from './series_drawer.js';
import { PaneGeometryController } from './pane_geometry_controller.js';
import { enforceAscendingTimes } from './series_time_guard.js';
// クロム配線点の単一情報源（基本設計_指標カラーテーマ.md §4.2・A-9）。本ファイルが持っていた
//   5 つの色定数（減光ローソク・ローソク復元色 up/down・分析 tint・背景フォールバック）は、
//   chart_bootstrap.js / replay_boundary_dim.js の同値リテラルと事実上の二重定義だった。
import { CHROME_CURRENT } from '../../usecase/chrome_tokens.js';

// zoomedPriceRange/clampPriceRange の単一ソースは scale_controller.js（SOLID 是正 🔴-2 で抽出）。
//   既存 import（テスト・他ファイル）を壊さないため本モジュールからも再 export する。
//   ※ 「export { X } from モジュール」の再 export 構文は build.mjs の stripModuleSyntax
//     （import 行剥がし）で壊れるため、import 済みシンボルの別行 export
//     （剥がし後は無害なブロック文）にする。
export { zoomedPriceRange, clampPriceRange };

// 減光ローソクの色定数（旧 DIM_CANDLE_COLOR = '#16191f'）は本ファイルに置かない。
//   クロム配線点の単一情報源（CHROME_CURRENT.dimCandle）へ移し、実際に使う値は配信された
//   `this._chromeSlots.dimCandle` から読む（テーマで変えられる／書き手が 1 箇所に保たれる）。
//   ここに定数を戻すと、テーマが配った色と描画に使う色が再び食い違う（ISSUE-357 の再発）。

// time 昇順の点列から time が一致する点を返す（無ければ undefined）。ローソク・指標系列とも
//   lightweight-charts のデータ規約で time 昇順のため二分探索で引く（右クリック 1 回あたり
//   系列数ぶんの探索になるので線形走査にしない）。time は UTCTimestamp（数値）を前提とし、
//   business day 形式など数値でない時刻表現は「引けない」（undefined）＝黙って別の足を返さない。
function pointAtTime(points, time) {
  if (!Array.isArray(points) || typeof time !== 'number') {
    return undefined;
  }
  let lo = 0;
  let hi = points.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const t = points[mid] ? points[mid].time : undefined;
    if (typeof t !== 'number') {
      return undefined;
    }
    if (t === time) {
      return points[mid];
    }
    if (t < time) {
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return undefined;
}

// 系列の指定 time の値（線・ヒストグラム＝value / ローソク＝close）。無ければ undefined。
//   series.data() は upstream API のため呼び出しは本モジュールに閉じる（隔離維持）。
function pointValueAt(series, time) {
  if (!series || typeof series.data !== 'function') {
    return undefined;
  }
  const p = pointAtTime(series.data(), time);
  if (p === undefined || p === null) {
    return undefined;
  }
  return (typeof p === 'object') ? (p.value ?? p.close) : p;
}

// sessions（日別プロファイル分割）: ローソク透明化用の色。透明＝価格軸は残しローソクだけ消す。
//   透明でないときの色は「配信済みのクロム色」から導出する（_deriveCandleOptions）。
const TRANSPARENT_COLOR = 'rgba(0,0,0,0)';

// クロム色の保持（FR-C13・§7.8「クロムの色の書き手は 1 箇所」）。
//   派生クロム（減光ローソク #18 / 分析 tint #19 / リプレイ減光境界 #20）とローソク復元色
//   （#12/#13）・背景フォールバック（#2）は、いずれもモジュール定数として読むと**配信された
//   テーマ色に追随しない**（旧実装の欠陥: 20 点を受け取って 11 点しか読んでいなかった）。
//   よって値は 1 つの保持状態に集約し、各利用点はそこから読む。CHROME_CURRENT は
//   「配信前の初期値」＝現行リテラルとしてのみ使う（未配信時の挙動は不変・D-11）。
const INITIAL_CHROME_SLOTS = Object.freeze({ ...CHROME_CURRENT });

// ペイン配分の下限（MIN_*）と合計保存の丸め（roundKeepingSum）は PaneGeometryController が
//   所有する（ISSUE-479 Wave2 J-2: 幾何ロールと一緒に移した）。

// 受け取った配線点だけを上書きした新しい保持値を返す（未指定＝undefined の配線点は現状維持）。
//   lightweight-charts の applyOptions が部分マージであることと同じ規約にする。
function mergeChromeSlots(held, patch) {
  const next = { ...held };
  for (const [id, color] of Object.entries(patch ?? {})) {
    if (color !== undefined) {
      next[id] = color;
    }
  }
  return next;
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
  // onPaneLegend: ペイン別凡例（ISSUE-276）へ「ペイン幾何＋各インスタンスの系列値」の DTO を渡す
  //   コールバック（省略時 no-op＝後方互換）。upstream（pane 幾何・seriesData）に触れるのは本 class
  //   だけで、View へは数値と文字列だけを渡す（隔離維持）。
  constructor({ chart, mainSeries, lwc, onCrosshairReadout, onCandlesChanged, onPaneLegend }) {
    this._chart = chart;
    this._mainSeries = mainSeries;
    this._lwc = lwc ?? {};
    this._onCrosshairReadout = typeof onCrosshairReadout === 'function' ? onCrosshairReadout : () => {};
    this._onPaneLegend = typeof onPaneLegend === 'function' ? onPaneLegend : () => {};
    // v6: 基準 candles の単一所有者（setCandles 全置換・updateLastCandle 差分で更新）。
    //   per-bar 減光（dimCandlesOutsidePair）・基準復元（restoreCandles）はこの基準から導出する。
    this._baseCandles = null;
    // 背景プリミティブ（用途 key -> primitive）。attachBackgroundPrimitive が所有し、メイン系列へ 1 度だけ装着する。
    this._backgroundPrimitives = new Map();
    // 配信済みクロム色の保持（FR-C13）。初期値＝現行リテラル（配信前＝テーマなしと同じ見た目）。
    //   applyChromeColors が更新し、派生・復元の各利用点はここだけを読む（色の出所を 1 つにする）。
    this._chromeSlots = { ...INITIAL_CHROME_SLOTS };
    // 表示モード（クロム出力のもう 1 つの入力）。出力は _derive* が保持色と併せて導出する。
    //   ここに状態として置くことで「テーマを適用したらモードが無かったことにされる」
    //   （透明ローソクが不透明へ戻る・分析 tint が消える・減光色だけ旧色で残る）が起き得なくなる。
    this._candlesTransparent = false;  // sessions / tf-period 列によるローソク透明化。
    this._analysisTintOn = false;      // 分析モードの背景 tint。
    this._dimRange = null;             // ペア hover 中の減光レンジ {from,to}（null=減光なし）。
    // 保持値の購読者（自分では色を決めず、配られた色を自分の描画へ適用する側）。
    //   本 class の外にある描画（リプレイ減光境界の lwc プリミティブ）へ同じ保持値を届ける。
    this._chromeObservers = new Set();
    // v6: candle 変更 observer（後方互換 no-op）。setCandleObserver で後から差し替え可能（生成順序吸収）。
    this._onCandlesChanged = typeof onCandlesChanged === 'function' ? onCandlesChanged : () => {};
    // 読み取り欄の最新足の単一源（lightweight-charts から逆引きしない＝upstream API 名を増やさない）。
    //   setCandles で配列末尾、updateLastCandle で当該足を保持する。
    this._lastBar = null;
    // overlay（pane 0 重ね描き）line 系列の読み取り用メタ。key {instanceId}::{name} ->
    //   { series, color, name, lastValue }。読み取り欄の overlay 行と fallback 値に使う。
    // ISSUE-278 #15: overlay 読取欄用の Map は撤去した（値はペイン別凡例が slot.lastValues で持つ）。
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
    // ペイン幾何ロールの状態（安定採番・並び順購読者・総高の測定関数・幾何指紋・再確認予約・
    //   目標配分）は PaneGeometryController が所有する（ISSUE-181「状態も一緒に移す」）。
    // lwc 操作可否の合成（suppressInteraction 参照）。明示フラグ AND 抑止者ゼロ で有効。
    this._interactionEnabled = true;
    this._interactionSuppressors = new Set();
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
    // ISSUE-479 Wave2 J-2: ペイン幾何（何面あるか・どこからどこまでか・どれが動かせるか）と、
    //   その従属変数である凡例 DTO の発行は PaneGeometryController（協働子）へ委譲する。
    //   幾何ロールの状態は協働子が所有し、本クラスの公開面・挙動は不変（薄い委譲だけが残る）。
    this._paneGeom = new PaneGeometryController(this);
    this._syncRightOffset();
    // ISSUE-164（ユーザー裁定 2026-07-23）: ズーム/ドラッグ（可視範囲変化）に反応して右余白を
    //   再適用する購読は撤去した。ユーザーの拡大縮小操作と無関係に rightOffset を適用し直すのは
    //   lwc では「最新足基準へのスクロール」副作用を持ち、『過去へ遡って拡大すると右端へ戻る』
    //   ジャンプの根本原因だった（ISSUE-148 はガードで蓋をしただけで隙間から再発）。
    //   余白の適用点は明示イベントのみ: 初期表示（直上）・時間足切替（setCandles）・
    //   MP 余白率変更（setRightMarginFraction）・最新足へ戻る操作（scrollToRealTime）。
    //   ズーム中の余白 px 一定性は保証しない（ユーザー操作の意思を優先する）。
    // クロスヘア移動でペイン別凡例の値と読み取り欄を更新する（ISSUE-276）。
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
    // 段階 5-E: 装着＝配信登録。背景プリミティブは canvas 描画で CSS 変数を解決できないため、
    //   色は注入で受ける（FR-C13）。装着と配信登録を別の呼び出しに分けると「装着したのに
    //   配られない」経路が必ず生まれるので、1 つの操作にまとめる。
    //   ここで現在の保持値を 1 回配ることで、テーマ適用が先・装着が後でも古い色が残らない
    //   （addChromeObserver が登録直後に 1 回配るのと同じ規約）。
    this._pushChromeToBackgroundPrimitive(primitive);
    return primitive;
  }

  // 背景プリミティブ 1 つへ保持色を渡す。受け口を持たないプリミティブ（既存・後方互換）は
  //   素通りする（全域的・例外を投げない）。
  _pushChromeToBackgroundPrimitive(primitive) {
    if (primitive && typeof primitive.setChromeColors === 'function') {
      primitive.setChromeColors(this._chromeSlots);
    }
  }

  // 増分2: チャートの通常操作（スクロール/ズーム）を停止/復元する（リプレイスワイプ捕捉用）。
  //   移植元 prototype_260630-01 updateCaptureMode（capture 中 handleScroll/handleScale=false）。
  //   lightweight-charts の applyOptions 直叩きは本所（ChartRenderer）に閉じる（primitive/actor は呼ばない）。
  //   enabled=false でスクロール/ズーム停止、true で復元。applyOptions 非提供時は no-op（後方互換）。
  setUserInteraction(enabled) {
    this._interactionEnabled = !!enabled;
    this._applyUserInteraction();
  }

  /**
   * lwc 操作（handleScroll / handleScale）の抑止を**登録**する（解除関数を返す）。
   *
   * なぜ合成にするか（実測 2026-08-20）: 抑止の口は `setUserInteraction` の**単数スロット**しか
   * 無く、現に 3 者が奪い合っている——MP のスワイプ捕捉（`mp_replay_scrub.js`）・水準線 drag・
   * アーム式ピッカー。単数のままだと「drag を離した瞬間に、アーム継続中のピッカーの抑止まで
   * 復帰する」（工程 5 🔴-2 で再現）。単数スロット競合は `setCandleObserver` /
   * `setTfPeriodHoverHandler` で既に踏んだ破綻型なので、`addVerticalPanBlocker` と同型の
   * 登録方式にして構造的に潰す。
   *
   * 抑止を持つ者が 1 人でも居る間は復帰しない。解除関数は冪等（二重呼び出しで他者の抑止を
   * 巻き添えにしない＝トークンで持ち主を区別する）。
   *
   * @returns {Function} 解除関数。
   */
  suppressInteraction() {
    const token = {};
    this._interactionSuppressors.add(token);
    this._applyUserInteraction();
    return () => {
      if (this._interactionSuppressors.delete(token)) {
        this._applyUserInteraction();
      }
    };
  }

  // 実効値を lwc へ配る（明示フラグ AND 抑止者ゼロ）。
  _applyUserInteraction() {
    if (typeof this._chart.applyOptions !== 'function') {
      return;
    }
    const on = this._interactionEnabled && this._interactionSuppressors.size === 0;
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
  //   on=true で up/down/border/wick の各色が透明になり、on=false で配信済みのローソク色へ戻る。
  //   本メソッドは**入力（モード）を更新して導出結果を押し出すだけ**で、色は 1 つも決めない
  //   （決めるのは _deriveCandleOptions）。applyOptions 非提供時は no-op（後方互換）。冪等。
  setCandleTransparency(on) {
    this._candlesTransparent = !!on;
    this._pushCandleOptions();
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

  // ISSUE-368 スライス 3: コンテナ上の y 座標 → 価格の**公開**変換。
  //   由来: y→価格の公開変換はどこにも無く（内部利用は本ファイル :613 の _onCrosshairMove と
  //   scale_controller の 2 箇所だけ）、水準線 drag が価格を得る手段を持たなかった。
  //   upstream の API 名（coordinateToPrice）は隔離点である本ファイル内に留め、呼び出し側は
  //   priceAtCoordinate しか知らない（`upstream_isolation_declaration.test.js` の隔離規約）。
  //   可視範囲外は upstream が null を返す＝そのまま null を返す（0 へ倒すと画面外の掴みが
  //   価格 0 として下流へ流れる）。非有限 y は変換を呼ばない（NaN 価格を作らない）。
  priceAtCoordinate(y) {
    if (!Number.isFinite(y)) {
      return null;
    }
    const series = this._mainSeries;
    if (!series || typeof series.coordinateToPrice !== 'function') {
      return null;
    }
    const price = series.coordinateToPrice(y);
    return price == null ? null : Number(price);
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
      return; // 塗る対象が無い＝要求は保持しない（基準供給後に勝手に減光しない・後方互換）。
    }
    this._dimRange = { from, to };
    this._pushDimmedCandles();
  }

  // v6（§12）: per-bar 減光を解除し基準 candles（色上書きなし）を復元する。基準未供給なら no-op。
  //   減光オーバーレイの解除＝データの所有権を基準（CandleFeed）へ返す操作なので、ここだけは
  //   導出（_deriveDimmedCandles）ではなく基準そのものを書き戻す。
  restoreCandles() {
    this._dimRange = null;
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
  //   ISSUE-276: 生成後にペイン別凡例を更新する。クロスヘアを乗せる前でも「最新値つきの行」が
  //   出るようにするため（ウォーターマークは値なしの指標名だけを出しており、値を見るには必ず
  //   クロスヘアが要った）。ペインの増減もここで反映される。
  _renderSeries(instanceId, payloads, kind, opts = {}) {
    this._drawer._renderSeries(instanceId, payloads, kind, opts);
    this._emitPaneLegend(null);
  }

  // horizontal_line 群を priceLine として生成。当該 instance に line/histogram 系列が
  // あれば その系列（pane の価格軸）へ、無ければ mainSeries（価格バンド・pane 0）へ載せる。
  renderHorizontal(instanceId, hlines) {
    const slot = this._slot(instanceId);
    slot.hlinePayloads = hlines ?? [];
    this._createPriceLines(slot, slot.hlinePayloads);
    // _slot() はスロットを新設し得る＝凡例の入力（在席集合）が変わる。水準線だけの指標
    //   （_renderSeries を一度も通らない）でも、適用直後に行が出るようにする。
    this._emitPaneLegend(null);
  }

  // 水準線の生成（実体は SeriesDrawer._createPriceLines・SOLID 是正 🔴-2）。
  _createPriceLines(slot, hlines) {
    this._drawer._createPriceLines(slot, hlines);
  }

  // クロスヘア移動でペイン別凡例（値）とクロスヘア価格読み取り欄（OHLC）を更新する。
  //   ISSUE-276: 旧「ペイン左上ウォーターマークへ指標名＋値を焼く」経路は撤去した。同じ情報を
  //   凡例行が持つため 2 系統になっており、凡例 DOM がウォーターマークの上に載って判読不能だった。
  _onCrosshairMove(param) {
    this._emitPaneLegend(param);
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

  // ペイン別凡例 DTO の発行（実体は PaneGeometryController._emitPaneLegend・ISSUE-479 Wave2 J-2）。
  _emitPaneLegend(param = null) {
    this._paneGeom._emitPaneLegend(param);
  }

  // 次フレームでの幾何突き合わせ予約（実体は PaneGeometryController._scheduleGeometryRecheck）。
  _scheduleGeometryRecheck() {
    this._paneGeom._scheduleGeometryRecheck();
  }

  // 指標ペインの並べ替え（実体は PaneGeometryController.movePane・ISSUE-479 Wave2 J-2）。
  movePane(fromIndex, toIndex) {
    return this._paneGeom.movePane(fromIndex, toIndex);
  }

  // ペイン並び順の変化の購読口（実体は PaneGeometryController.setPaneOrderObserver）。
  setPaneOrderObserver(fn) {
    this._paneGeom.setPaneOrderObserver(fn);
  }

  // ペイン別凡例の DTO（幾何＋値・実体は PaneGeometryController.paneLegendModel）。
  paneLegendModel(param = null, valuePickerFor = null) {
    return this._paneGeom.paneLegendModel(param, valuePickerFor);
  }

  // ペインの安定 ID（実体は PaneGeometryController._paneKeysOrdered）。
  _paneKeysOrdered() {
    return this._paneGeom._paneKeysOrdered();
  }

  // 各ペインの高さ（実体は PaneGeometryController._paneHeights）。
  _paneHeights() {
    return this._paneGeom._paneHeights();
  }

  // ペイン領域の総高（実体は PaneGeometryController._paneAreaHeight）。
  _paneAreaHeight() {
    return this._paneGeom._paneAreaHeight();
  }

  // ペイン領域の総高を測る関数の供給口（実体は PaneGeometryController.setPaneAreaHeightProvider）。
  setPaneAreaHeightProvider(fn) {
    this._paneGeom.setPaneAreaHeightProvider(fn);
  }

  // ペイン間の区切り高（実体は PaneGeometryController._paneSeparatorPx）。
  _paneSeparatorPx(heights) {
    return this._paneGeom._paneSeparatorPx(heights);
  }

  // ペイン幾何の指紋（実体は PaneGeometryController._paneGeometrySignature）。
  _paneGeometrySignature() {
    return this._paneGeom._paneGeometrySignature();
  }

  // 目標配分の比で全ペインを伸縮（実体は PaneGeometryController._applyGoalRatios）。
  _applyGoalRatios(area, heights, goal) {
    return this._paneGeom._applyGoalRatios(area, heights, goal);
  }

  // 幾何を実測へ揃える（実体は PaneGeometryController.syncPaneGeometry）。
  syncPaneGeometry() {
    return this._paneGeom.syncPaneGeometry();
  }

  // 利用者が決めた配分の控え（実体は PaneGeometryController._notePaneGeometry）。
  _notePaneGeometry() {
    this._paneGeom._notePaneGeometry();
  }

  // 幾何が動いていたら凡例を引き直す（実体は PaneGeometryController.refreshPaneLegendIfGeometryChanged）。
  refreshPaneLegendIfGeometryChanged() {
    return this._paneGeom.refreshPaneLegendIfGeometryChanged();
  }

  // 各ペインの上端 y（実体は PaneGeometryController._paneTops）。
  _paneTops(heights, separator) {
    return this._paneGeom._paneTops(heights, separator);
  }

  // 現在のペイン順に並んだ pane 指標の instanceId（実体は PaneGeometryController.paneOrderInstanceIds）。
  paneOrderInstanceIds() {
    return this._paneGeom.paneOrderInstanceIds();
  }

  // slot が属するペイン番号（実体は PaneGeometryController._slotPaneIndex）。
  _slotPaneIndex(slot) {
    return this._paneGeom._slotPaneIndex(slot);
  }

  // メイン系列が居るペインの番号（実体は PaneGeometryController._pricePaneIndex）。
  _pricePaneIndex() {
    return this._paneGeom._pricePaneIndex();
  }

  // 当該ペインを並べ替えられるか（実体は PaneGeometryController._isPaneMovable）。
  _isPaneMovable(paneIndex, paneCount = null) {
    return this._paneGeom._isPaneMovable(paneIndex, paneCount);
  }

  // slot の各系列の表示値。**どの値を取るか**は picker（Strategy）が決め、本メソッドは
  //   「どの系列を出すか（可視の扱い）」と「名前・色をどう付けるか」だけを担う。
  //   系列単位で非表示（styleMeta.visible=false）のものは出さない（凡例と描画を一致させる）。
  //   picker: (series, key) => value|undefined。
  _slotValues(slot, pick) {
    const out = [];
    if (slot.visible === false) {
      return out;   // インスタンスごと非表示（eye OFF）＝値は出さない（行は残す＝再表示できる）。
    }
    for (const [key, series] of slot.lines) {
      const meta = slot.styleMeta ? slot.styleMeta.get(key) : null;
      if (meta && meta.visible === false) {
        continue;
      }
      out.push({ name: meta ? meta.name : key, value: pick(series, key), color: meta ? meta.color : undefined });
    }
    return out;
  }

  // 既定の値取り出し（凡例の規約）: クロスヘア位置に値があればそれ、無ければ保持した最新値。
  _crosshairValue(slot, series, key, seriesData) {
    const d = seriesData ? seriesData.get(series) : undefined;
    let value;
    if (d !== undefined && d !== null) {
      value = (typeof d === 'object') ? (d.value ?? d.close) : d;
    }
    if (value === undefined || value === null) {
      value = slot.lastValues ? slot.lastValues.get(key) : undefined;
    }
    return value;
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
    // ISSUE-276: overlay 各系列の値は**ペイン別凡例の行**が持つ（読み取り欄からは外す）。
    //   同じ値を 2 系統に出していたため、指標が増えるほど読み取り欄が伸びて凡例と重なっていた
    //   （実測: 指標 11 件で読み取り欄 229px＋凡例 295px）。読み取り欄は OHLC と時刻だけを担う。
    //   overlays は空配列で残す（View・既存呼出の形を壊さない）。
    const overlays = [];
    const time = (param && param.time !== undefined) ? param.time
      : (this._lastBar ? this._lastBar.time : undefined);
    // sessions: 当日 MP（POC/VAH/VAL）を time で引いて DTO に載せる（供給時のみ・sessions 表示中）。
    const sessionMP = (this._sessionMP && time != null) ? (this._sessionMP.get(time) || null) : null;
    return { time, ohlc, overlays, sessionMP };
  }

  /**
   * チャート要素の左上を原点とする x 座標が指す足の情報を返す（ユーザー指示 2026-08-09・右クリックコピー）。
   *
   * 返すのは情報ウィンド（クロスヘア読み取り欄＋ペイン別凡例）と**同じ材料**で、
   *   { time, ohlc:{open,high,low,close}|null, sessionMP:{poc,vah,val}|null,
   *     indicators: [{ instanceId, values: [{ name, value, color }] }] }
   * 座標→足の解決は upstream（timeScale().coordinateToTime）に触れる本 class に閉じる。
   *
   * クロスヘア経路との違いは **値が無い足で最新値へ落ちない**ことだけ（凡例は「クロスヘアが
   * 無ければ最新値」という表示規約を持つが、足を名指しでコピーする場面でその足に無い値を
   * 最新値で埋めると、別の足の値を「その足の値」として配ってしまう）。
   *
   * @param {number} x  チャート要素の左上基準の x（px）。
   * @returns {object|null} 足が無い座標（データ範囲外・時間軸未確定）は null。
   */
  barInfoAt(x) {
    const time = this._timeAtCoordinate(x);
    if (time == null) {
      return null;
    }
    const candle = pointAtTime(this.getCandles(), time);
    const ohlc = (candle && candle.open !== undefined)
      ? { open: candle.open, high: candle.high, low: candle.low, close: candle.close }
      : null;
    const model = this.paneLegendModel(null, () => (series) => pointValueAt(series, time));
    const indicators = [];
    for (const g of model.groups) {
      for (const r of g.rows ?? []) {
        indicators.push({ instanceId: r.instanceId, values: r.values ?? [] });
      }
    }
    const sessionMP = this._sessionMP ? (this._sessionMP.get(time) || null) : null;
    return { time, ohlc, sessionMP, indicators };
  }

  /**
   * ISSUE-368 スライス 8-b: x 座標が指す足の**スナップ候補**をプレーンデータで列挙する。
   *
   * @param {number} x チャート要素の左上基準の x（px）。
   * @returns {Array<{kind:string,label:string,price:number}>|null}
   *   足が無い座標（データ範囲外・時間軸未確定）は null。
   */
  snapCandidatesAt(x) {
    const time = this._timeAtCoordinate(x);
    if (time == null) {
      return null;
    }
    const series = [];
    const levels = [];
    const pricePane = this._pricePaneIndex();
    for (const slot of this._instances.values()) {
      if (this._slotPaneIndex(slot) !== pricePane) {
        continue;   // オシレーターペインの値は価格ではない（55 を価格として吸うと桁が変わる）。
      }
      for (const v of this._slotValues(slot, (s) => pointValueAt(s, time))) {
        if (Number.isFinite(v.value)) {
          series.push({ kind: 'series', label: v.name, price: v.value });
        }
      }
      if (slot.visible === false) {
        continue;   // 水準線は _slotValues を通らない＝可視の判定をここでも行う（描画と一致させる）。
      }
      for (const h of slot.hlinePayloads ?? []) {
        if (h && Number.isFinite(h.price)) {
          levels.push({ kind: 'level', label: h.text ?? '', price: h.price });
        }
      }
    }
    const ohlc = [];
    const candle = pointAtTime(this.getCandles(), time);
    if (candle && candle.open !== undefined) {
      for (const label of ['open', 'high', 'low', 'close']) {
        ohlc.push({ kind: 'ohlc', label, price: candle[label] });
      }
    }
    // 並びが解決の優先順（スナップ解決器は同距離で先頭を採る）。指標系列＝クリックの狙い、
    //   水準線＝明示的に置かれた参照、OHLC＝常に在る背景、の順に置く。
    return [...series, ...levels, ...ohlc];
  }

  // y 座標が属するペイン番号（実体は PaneGeometryController.paneIndexAtCoordinate）。
  paneIndexAtCoordinate(y) {
    return this._paneGeom.paneIndexAtCoordinate(y);
  }

  // x 座標（チャート要素基準）が指す足の time。範囲外・非対応環境（Fake/SSR）は null。
  //   バンドル実測（v5.2.0）: `coordinateToTime` は座標→バー index（Math.ceil）→ 元の time
  //   （originalTime）へ写す。データ範囲外の index は null を返す＝足の無い所では開かない。
  _timeAtCoordinate(x) {
    if (!Number.isFinite(x) || typeof this._chart.timeScale !== 'function') {
      return null;
    }
    const ts = this._chart.timeScale();
    if (!ts || typeof ts.coordinateToTime !== 'function') {
      return null;
    }
    const t = ts.coordinateToTime(x);
    return t == null ? null : t;
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
        // ISSUE-276: ペイン別凡例の「クロスヘア無しの表示値」をここで更新する
        //   （overlay/pane を問わず 1 経路で保つ＝系列ごとに鮮度が割れない）。
        if (slot.lastValues) {
          slot.lastValues.set(seriesKey, lastPointValue(points));
        }
        return;
      }
    }
  }

  // UC-03 再計算: 既存系列を再生成せず data のみ差し替え。
  setData(seriesKey, points) {
    this._withSeries(seriesKey, points, (series) => {
      // ISSUE-383: lwc へ渡す直前の時系列契約防壁（清浄なら同一参照＝挙動不変）。
      series.setData(enforceAscendingTimes(points ?? [], seriesKey));
    });
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
    // ISSUE-278 追補（実 UI 実測 2026-08-07）: 末尾値が変わったら凡例も描き直す。これが無いと、
    //   ライブの足内更新（/live_ticks の末尾値・5 秒ポーリング）で価格と OHLC は動くのに
    //   **凡例の値だけが次のクロスヘア移動まで凍る**（実測: 20 秒間 65,088.632 のまま）。
    //   凡例の入力（slot.lastValues）が変わる経路はすべて再発火させる、が唯一の規約。
    this._emitPaneLegend(null);
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
    }
  }

  // UC-04 表示/非表示（実体は SeriesDrawer.setVisible・SOLID 是正 🔴-2）。
  setVisible(instanceId, visible) {
    this._drawer.setVisible(instanceId, visible);
    // 凡例の値は可視状態でふるい落とす契約（_slotValues）。可視を変えたらモデルを作り直す。
    //   これが無いと、目 OFF にしても次のクロスヘア移動まで値チップが出たままになる。
    this._emitPaneLegend(null);
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
    const applied = this._drawer.applySeriesStyle(instanceId, seriesName, patch);
    // 系列単位の可視・色は凡例の表示内容そのもの（_slotValues が meta を読む）＝同期して作り直す。
    this._emitPaneLegend(null);
    return applied;
  }

  // チャートクロム（面・目盛線・軸・文字・ローソク・現在値ライン）へ解決済みの色を書き込む
  //   （基本設計_指標カラーテーマ.md §4.2・§5.2 UC-C02 手順 2・A-11）。
  //
  //   配線点 id → lightweight-charts のオプション経路という写像は upstream API の知識であり、
  //   本クラス（宣言された唯一の upstream 隔離点）が持つ。ChromeThemeApplier は解決済みの値を
  //   本メソッドへ渡すだけで lwc を知らない（§7.3 ISP の ChromeSinkPort をここで満たす）。
  //
  //   §3.4: 書くのは色だけ。時間足・表示レンジ・価格スケール・スクロール位置には触れない。
  //     ローソクデータへ触れるのは**減光オーバーレイが有効なときだけ**（自分が所有する
  //     per-bar 色の塗り直し）で、トリム・ライブ末尾には介入しない。
  //   F-C10: applyOptions 非提供（SSR・後方互換 Fake）は no-op（setAnalysisTint と同一方針）。
  //
  //   受け取った 20 点は**すべて保持する**。lwc のオプションへ即書けるのは 11 点だけで、
  //   6 点（派生 3＝#18/#19/#20・ローソク復元 2＝#12/#13・背景フォールバック 1＝#2）は
  //   「あとで別の経路（減光・透明化復元・分析 tint・リプレイ減光境界）が読む値」である。
  //   保持しないとこの 6 点は受け取った瞬間に捨てられる（FR-C13 が死ぬ＝旧実装の欠陥）。
  //   残る 3 点（現在値表示・CSS 機構）は本 class の管轄外だが、保持値の形を配線点表と同一に
  //   保つため素通しで持つ（表と保持値がずれると「どの id が届くか」が 2 通りになる）。
  applyChromeColors(slots = {}) {
    this._chromeSlots = mergeChromeSlots(this._chromeSlots, slots);
    // 色はクロム 3 出力すべての入力なので、3 出力すべてを導出し直す（モードは保たれる）。
    this._pushChartOptions();
    this._pushCandleOptions();
    this._pushDimmedCandles();
    // 装着済みの背景プリミティブ（取引密度帯など）へも同じ保持値を配る。ここを落とすと
    //   帯だけ旧色で残る（減光ローソクで実際に起きた欠陥と同型）。
    for (const primitive of this._backgroundPrimitives.values()) {
      this._pushChromeToBackgroundPrimitive(primitive);
    }
    this._notifyChromeObservers();
  }

  // ─── クロム出力の導出（§7.8「クロムの色の書き手は 1 箇所」）────────────────
  //
  // 「今の見え方」を決める入力は 2 系統ある: 配信済みのクロム色（_chromeSlots・20 点）と、
  // 表示モード（ローソク透明化 / ペア外減光 / 分析 tint）。かつては出力を書く場所が
  // applyChromeColors / setCandleTransparency / dimCandlesOutsidePair / setAnalysisTint の
  // 4 つに分かれ、互いの状態を知らないまま同じ出力へ書いていた。そのため片方の入力を変えると
  // もう片方の入力が無かったことにされた（実測: 透明ローソクがテーマ適用で不透明へ戻る /
  // 分析 tint がテーマ適用で消える / ペア外の減光色だけ旧色で残る）。
  //
  // よって出力ごとに導出関数を 1 本だけ置き、入力から毎回作り直す。上記 4 メソッドは
  // 「自分が持つ入力を更新し、その入力が効く出力を押し出す」だけで、色を 1 つも決めない。
  // どの入力が変わっても同じ規則で出力が決まるため、入力の適用順序は結果に影響しない。

  // 出力 1: メイン系列のローソクオプション（入力: 保持色 + 透明化モード）。
  //   #10/#11 は 1 配線点＝3 オプションずつ。同一トークンから配るため 3 経路が食い違わない。
  //   透明化からの復元色（#12/#13）は #10/#11 と同一トークン（bullish / bearish）に束ねられて
  //   おり（chrome_tokens.js）、書き手が 1 つになった今は同じ導出結果そのものである。
  _deriveCandleOptions() {
    const held = this._chromeSlots;
    const up = this._candlesTransparent ? TRANSPARENT_COLOR : held.candleUp;
    const down = this._candlesTransparent ? TRANSPARENT_COLOR : held.candleDown;
    return {
      upColor: up, borderUpColor: up, wickUpColor: up,
      downColor: down, borderDownColor: down, wickDownColor: down,
      // 現在値ライン（#14）は値の上下と無関係な固定色（ISSUE-084）＝透明化に従属しない。
      priceLineColor: held.priceLine,
    };
  }

  // 出力 2: チャート全体のオプション（入力: 保持色 + 分析 tint モード）。
  _deriveChartOptions() {
    const held = this._chromeSlots;
    return {
      layout: {
        background: this._deriveBackground(),
        textColor: held.layoutTextColor,
        panes: {
          separatorColor: held.paneSeparator,
          separatorHoverColor: held.paneSeparatorHover,
        },
      },
      grid: {
        vertLines: { color: held.gridVertLines },
        horzLines: { color: held.gridHorzLines },
      },
      rightPriceScale: { borderColor: held.rightPriceScaleBorder },
      timeScale: { borderColor: held.timeScaleBorder },
    };
  }

  // 背景（#1 layoutBackground / #2 backgroundFallback / #19 analysisTint）は 1 つの出力。
  //   分析モード中は tint 色、それ以外は面の色（#1 と #2 は同一トークン surface＝同値）。
  //   type は生成時の値を保つ: lwc は background を部分マージするため、色だけ渡せば type は
  //   温存されるが、捕捉できているときは明示して「地の型」を書き換えないことを構造で示す。
  _deriveBackground() {
    const held = this._chromeSlots;
    const color = this._analysisTintOn ? held.analysisTint : held.layoutBackground;
    // ISSUE-119: 背景オプションの **type** だけを一度捕捉する（構築子外・遅延初期化）。
    //   options() が返す background は lwc 内部 options への参照でありうる。applyOptions は
    //   内部オブジェクトへの in-place マージのため、参照のまま保持すると tint ON で基準色まで
    //   tint 色に書き換わり復元が無変化になる。浅いコピーで snapshot 化して内部と切り離す。
    if (this._analysisTintBase === undefined) {
      let base = null;
      if (this._chart && typeof this._chart.options === 'function') {
        const o = this._chart.options();
        base = (o && o.layout && o.layout.background) ? { ...o.layout.background } : null;
      }
      this._analysisTintBase = base;
    }
    const type = this._analysisTintBase ? this._analysisTintBase.type : undefined;
    return (type !== undefined) ? { type, color } : { color };
  }

  // 出力 3: メイン系列の per-bar 減光色（入力: 保持色 + 減光レンジ + 基準 candles）。
  //   減光が無効なら null を返す＝**データの書き手は名乗り出ない**。ローソクデータの所有者は
  //   CandleFeed（setCandles / updateLastCandle）と setCandleTrim であり、色の都合でトリムや
  //   ライブ末尾を巻き戻さない（§3.4: 触れてよいのは自分が所有する per-bar 色だけ）。
  _deriveDimmedCandles() {
    const range = this._dimRange;
    if (!range || !this._baseCandles) {
      return null;
    }
    // 入力は「**現在所有されている**ローソク集合」であって基準の全件ではない。
    //   トリム中（MP スナップショット・リプレイの as-of）に全件を書き戻すと、色を塗り直したつもりで
    //   「どのバーが存在するか」まで変えてしまい、T より後のバーが再表示される（§3.4 が許すのは
    //   自分が所有する per-bar 色の塗り直しだけで、バー集合の変更は含まれない）。
    //   トリム状態の単一情報源は _lastTrimIdx（null＝未トリム）。
    const owned = this._lastTrimIdx === null
      ? this._baseCandles
      : this._baseCandles.slice(0, this._lastTrimIdx + 1);
    // 減光色は配信済みの保持値から引く（#18 は surface 派生＝テーマの背景に追随する・FR-C13）。
    const dim = this._chromeSlots.dimCandle;
    return owned.map((bar) => {
      if (bar.time >= range.from && bar.time <= range.to) {
        return bar; // ペア内は原色維持（色上書きしない）。
      }
      return {
        ...bar, color: dim, borderColor: dim, wickColor: dim,
      };
    });
  }

  // 導出結果の押し出し（upstream への書き込みはこの 3 つだけ）。
  //   F-C10: applyOptions 非提供（SSR・後方互換 Fake）は no-op。
  _pushChartOptions() {
    if (!this._chart || typeof this._chart.applyOptions !== 'function') {
      return;
    }
    this._chart.applyOptions(this._deriveChartOptions());
  }

  _pushCandleOptions() {
    if (!this._mainSeries || typeof this._mainSeries.applyOptions !== 'function') {
      return;
    }
    this._mainSeries.applyOptions(this._deriveCandleOptions());
  }

  _pushDimmedCandles() {
    const dimmed = this._deriveDimmedCandles();
    if (dimmed) {
      this._mainSeries.setData(dimmed);
    }
  }

  // クロム保持値の購読口。本 class の外にある描画（リプレイ減光境界の lwc プリミティブ）へ、
  //   同じ保持値を届けるための唯一の経路（購読者は色を決めず、受け取った色を塗るだけ）。
  //   登録直後に現在の保持値を 1 回配るため、購読の開始順序で結果が変わらない
  //   （起動時配信 → 後からリプレイ層を組み立てる、という実際の順序で色が古いまま残らない）。
  //   戻り値は購読解除関数。非関数の要求は無視する（全域的・例外を投げない）。
  addChromeObserver(observer) {
    if (typeof observer !== 'function') {
      return () => {};
    }
    this._chromeObservers.add(observer);
    observer(this._chromeSlots);
    return () => this._chromeObservers.delete(observer);
  }

  _notifyChromeObservers() {
    for (const observer of this._chromeObservers) {
      observer(this._chromeSlots);
    }
  }

  // 水準線（horizontal_line）へ色を届ける入口（実体は SeriesDrawer.applyLevelLineColor・
  //   基本設計_指標カラーテーマ.md §7.2 S2(b)・A-5）。applySeriesStyle は priceLine 経路に
  //   到達しない（E-10）ため、テーマの level トークン専用の入口を 1 個だけ公開する。
  //   凡例は水準線を持たないため再描画しない（applySeriesStyle との非対称は意図的）。
  applyLevelLineColor(instanceId, color) {
    return this._drawer.applyLevelLineColor(instanceId, color);
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
    // ISSUE-276: ウォーターマークは生成しなくなったが、旧 slot（再描画途中の残骸）が
    //   持っている可能性に備えて detach は残す（存在しなければ no-op）。
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
    // ISSUE-276: 除去でペイン構成が変わる（後続ペインの index と top がずれる）ため再発火する。
    this._emitPaneLegend(null);
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
  //   分析モード（ANALYSIS）の tint 色は既定背景より僅かに紫寄り＝状態の明示（ユーザー要求
  //   「背景色で状態明示」）。色は**配信済みの保持値**（#19 は surface 派生・E-29）から引く。
  //   本メソッドは入力（モード）を更新して導出結果を押し出すだけで、色は 1 つも決めない
  //   （決めるのは _deriveBackground）。applyOptions 非提供時は no-op。
  setAnalysisTint(on) {
    this._analysisTintOn = !!on;
    this._pushChartOptions();
  }
}
