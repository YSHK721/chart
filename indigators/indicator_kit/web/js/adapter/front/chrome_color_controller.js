// ChromeColorController（adapter/front/chrome_color_controller.js）— クロム色ロールの協働クラス
// @upstream-isolation: chrome_color_controller.js
//   （ISSUE-479 Wave2 J-2b: chart_renderer.js から 1:1 抽出。ScaleController / CandleFeed /
//    SeriesDrawer / PaneGeometryController と同形＝生 host 参照を受け取る）。
//
// 担う関心は 1 つ:「クロムの色（§7.8『クロムの色の書き手は 1 箇所』）を保持し、表示モードと
//   合わせて出力を導出して upstream へ押し出す」。
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
//
// 状態の所有（ISSUE-181「状態も一緒に移す」）: 保持色（_chromeSlots）・表示モード 3 種
//   （_candlesTransparent / _analysisTintOn / _dimRange）・保持値の購読者（_chromeObservers）・
//   背景オプションの型の捕捉（_analysisTintBase）は**本クラスが所有する**。
//
// host に残る共有状態（本クラスは読むだけ）: _chart / _mainSeries / _baseCandles /
//   _lastTrimIdx（トリム状態の単一情報源）/ _backgroundPrimitives。

// クロム配線点の単一情報源（基本設計_指標カラーテーマ.md §4.2・A-9）。
import { CHROME_CURRENT } from '../../usecase/chrome_tokens.js';

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

// 受け取った配線点だけを上書きした新しい保持値を返す（未指定＝undefined の配線点は現状維持）。
//   lightweight-charts の applyOptions が部分マージであることと同じ規約にする。
export function mergeChromeSlots(held, patch) {
  const next = { ...held };
  for (const [id, color] of Object.entries(patch ?? {})) {
    if (color !== undefined) {
      next[id] = color;
    }
  }
  return next;
}

export class ChromeColorController {
  // host: ChartRenderer インスタンス（_chart / _mainSeries / _baseCandles / _lastTrimIdx /
  //   _backgroundPrimitives の所有者）。
  constructor(host) {
    this._h = host;
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
    //   本ロールの外にある描画（リプレイ減光境界の lwc プリミティブ）へ同じ保持値を届ける。
    this._chromeObservers = new Set();
    // 背景オプションの type の捕捉（遅延初期化・_deriveBackground 参照）。undefined=未捕捉。
    this._analysisTintBase = undefined;
  }

  // 背景プリミティブ 1 つへ保持色を渡す。受け口を持たないプリミティブ（既存・後方互換）は
  //   素通りする（全域的・例外を投げない）。
  _pushChromeToBackgroundPrimitive(primitive) {
    if (primitive && typeof primitive.setChromeColors === 'function') {
      primitive.setChromeColors(this._chromeSlots);
    }
  }

  // sessions（日別プロファイル分割）: ローソクを透明化して価格軸のみ残す/復元する（移植元 prototype_260630-01）。
  //   on=true で up/down/border/wick の各色が透明になり、on=false で配信済みのローソク色へ戻る。
  //   本メソッドは**入力（モード）を更新して導出結果を押し出すだけ**で、色は 1 つも決めない
  //   （決めるのは _deriveCandleOptions）。applyOptions 非提供時は no-op（後方互換）。冪等。
  setCandleTransparency(on) {
    this._candlesTransparent = !!on;
    this._pushCandleOptions();
  }

  // v6（§12）: ホバー中ペア [from,to] 外のローソク足を per-bar 極暗色へ上書きして mainSeries へ反映する。
  //   ペア内バーは色を付けず原色（既定 up/down 着色）に委ねる。time/open/high/low/close は基準と完全一致
  //   （データ非改変・背景ピクセルも不変）。基準 candles 未供給時は no-op（候補据え置き・後方互換）。
  //   mainSeries.setData を呼ぶのは upstream 隔離単位の中だけ（grep0件規約維持）。
  dimCandlesOutsidePair({ from, to }) {
    if (!this._h._baseCandles) {
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
    if (!this._h._baseCandles) {
      return;
    }
    this._h._mainSeries.setData(this._h._baseCandles);
  }

  //   受け取った 20 点は**すべて保持する**。lwc のオプションへ即書けるのは 11 点だけで、
  //   6 点（派生 3＝#18/#19/#20・ローソク復元 2＝#12/#13・背景フォールバック 1＝#2）は
  //   「あとで別の経路（減光・透明化復元・分析 tint・リプレイ減光境界）が読む値」である。
  //   保持しないとこの 6 点は受け取った瞬間に捨てられる（FR-C13 が死ぬ＝旧実装の欠陥）。
  //   残る 3 点（現在値表示・CSS 機構）は本ロールの管轄外だが、保持値の形を配線点表と同一に
  //   保つため素通しで持つ（表と保持値がずれると「どの id が届くか」が 2 通りになる）。
  applyChromeColors(slots = {}) {
    this._chromeSlots = mergeChromeSlots(this._chromeSlots, slots);
    // 色はクロム 3 出力すべての入力なので、3 出力すべてを導出し直す（モードは保たれる）。
    this._pushChartOptions();
    this._pushCandleOptions();
    this._pushDimmedCandles();
    // 装着済みの背景プリミティブ（取引密度帯など）へも同じ保持値を配る。ここを落とすと
    //   帯だけ旧色で残る（減光ローソクで実際に起きた欠陥と同型）。
    for (const primitive of this._h._backgroundPrimitives.values()) {
      this._pushChromeToBackgroundPrimitive(primitive);
    }
    this._notifyChromeObservers();
  }

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
      if (this._h._chart && typeof this._h._chart.options === 'function') {
        const o = this._h._chart.options();
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
    if (!range || !this._h._baseCandles) {
      return null;
    }
    // 入力は「**現在所有されている**ローソク集合」であって基準の全件ではない。
    //   トリム中（MP スナップショット・リプレイの as-of）に全件を書き戻すと、色を塗り直したつもりで
    //   「どのバーが存在するか」まで変えてしまい、T より後のバーが再表示される（§3.4 が許すのは
    //   自分が所有する per-bar 色の塗り直しだけで、バー集合の変更は含まれない）。
    //   トリム状態の単一情報源は host._lastTrimIdx（null＝未トリム）。
    const owned = this._h._lastTrimIdx === null
      ? this._h._baseCandles
      : this._h._baseCandles.slice(0, this._h._lastTrimIdx + 1);
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
    if (!this._h._chart || typeof this._h._chart.applyOptions !== 'function') {
      return;
    }
    this._h._chart.applyOptions(this._deriveChartOptions());
  }

  _pushCandleOptions() {
    if (!this._h._mainSeries || typeof this._h._mainSeries.applyOptions !== 'function') {
      return;
    }
    this._h._mainSeries.applyOptions(this._deriveCandleOptions());
  }

  _pushDimmedCandles() {
    const dimmed = this._deriveDimmedCandles();
    if (dimmed) {
      this._h._mainSeries.setData(dimmed);
    }
  }

  // クロム保持値の購読口。本ロールの外にある描画（リプレイ減光境界の lwc プリミティブ）へ、
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
