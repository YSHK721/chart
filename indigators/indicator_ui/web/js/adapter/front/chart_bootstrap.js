// chart_bootstrap.js — チャート土台の共有生成ヘルパ（ISSUE-123・present/replay 両アプリ共有）。
//
// 設計入力（値渡し是正）: composition root（present 622 行 / replay 293 行）に、lwc チャート生成
//   （createChart オプション一式＋メインローソク系列）と pane 高供給（updatePaneHeight）が
//   複製（値渡し）されており、present 側の改善（クロスヘア Normal 化・現在値ライン固定色
//   ISSUE-084）が replay に未伝播となるドリフトが実測された。本ヘルパへ単一ソース化し、
//   両 composition root は呼ぶだけにする（合成ルート固有の配線は各ルートに残す＝責務不変）。
//
// 責務: lwc の createChart / addSeries(CandlestickSeries) / timeScale().height() の呼び出しを
//   本所へ閉じる（composition root から upstream 生成 API の重複参照を除去）。renderer 以降の
//   系列操作 API 隔離は従来どおり ChartRenderer が担う。
//
// 色（基本設計_指標カラーテーマ.md A-9）: 生成時オプションの色リテラルは chrome_tokens.js
//   （クロム配線点の単一情報源）から引く。ここに直書きすると「テーマなし」へ戻すときの復元値が
//   二重定義になる（§7.2 S1 の欠点）。値は現行と文字列同値のため挙動は不変。

import { CHROME_CURRENT } from '../../usecase/chrome_tokens.js';

// 価格の表示形式（ISSUE-368 A-3）。**銘柄仕様が解決できているときだけ**設定する。
//   実測（vendor v5.2.0 バンドル・系列共通既定）: `priceFormat:{type:"price",precision:2,minMove:.01}`。
//   アプリ側は従来この指定を 1 か所も持っておらず、この既定に委ねていた＝JP225（digits=0・tick=1）
//   でも軸・現在値・クロスヘアは小数 2 桁で出る。値だけを刻みへ丸めると「軸は 2 桁・入る値は整数」の
//   乖離が残るため、桁も台帳に従わせる。
//   解決できないときに既定の桁を**こちらで決め直さない**（決められないことを 0 桁と偽らない）。
//   仕様は引数で受ける＝本ヘルパは台帳を知らない（front での解決点を増やさない）。
function priceFormatOf(symbolSpec) {
  if (!symbolSpec) {
    return {};
  }
  const { tick, digits } = symbolSpec;
  if (!Number.isFinite(tick) || tick <= 0 || !Number.isInteger(digits) || digits < 0) {
    return {};   // 壊れた仕様で軸を固定すると、誤りが「読める表示」に化けて気付けない。
  }
  return { priceFormat: { type: 'price', precision: digits, minMove: tick } };
}

// チャート＋メインローソク系列を生成して返す（present を正とした共通オプション）。
//   - crosshair Normal(0): Magnet スナップ無効（ユーザー要望・enum 無い環境向け 0 フォールバック）。
//   - 現在値ライン: 固定橙・常時表示（ISSUE-084。日別プロファイルのローソク透明化でも消えない）。
//   - symbolSpec: 銘柄仕様 `{symbol, tick, digits}`（呼び出し側が台帳から解決したもの）。未指定は
//     priceFormat を設定しない＝lwc 既定のまま（従来の呼び出しは byte 等価）。
export function createChartWithMainSeries({ lwc, container, symbolSpec = null }) {
  // v5: background は { type: ColorType.Solid, color }、panes のリサイズ separator は既定 ON。
  const chart = lwc.createChart(container, {
    layout: {
      background: { type: lwc.ColorType.Solid, color: CHROME_CURRENT.layoutBackground },
      textColor: CHROME_CURRENT.layoutTextColor,
      // ペイン境界のドラッグ・リサイズ（separator）を有効化（高さ調整・機能④）。
      panes: {
        enableResize: true,
        separatorColor: CHROME_CURRENT.paneSeparator,
        separatorHoverColor: CHROME_CURRENT.paneSeparatorHover,
      },
    },
    grid: {
      vertLines: { color: CHROME_CURRENT.gridVertLines },
      horzLines: { color: CHROME_CURRENT.gridHorzLines },
    },
    // クロスヘアを Normal（自由追従）に。既定 Magnet(1) は水平線を最寄り足の価格へスナップさせるため、
    //   カーソル位置どおりに動かしたいという要望で Normal(0) に変更（enum 無い環境向けに 0 フォールバック）。
    crosshair: { mode: (lwc.CrosshairMode && lwc.CrosshairMode.Normal) || 0 },
    rightPriceScale: { borderColor: CHROME_CURRENT.rightPriceScaleBorder },
    // 日中足（1m/1h 等）でも時刻が読めるよう timeVisible を有効化（秒は非表示）。
    timeScale: { borderColor: CHROME_CURRENT.timeScaleBorder, timeVisible: true, secondsVisible: false },
    autoSize: true,
  });
  // v5: addCandlestickSeries は廃止。addSeries(CandlestickSeries, ...) でメイン pane(0) に追加。
  // ISSUE-084: 現在値ラインは固定色（橙）で常時表示する。lwc 既定の priceLineColor=''（バー色追従）は
  //   日別プロファイルのローソク透明化（setCandleTransparency）で線ごと消えるため、candle 色に依存しない
  //   固定色を明示する（POC 赤・POC* 黄・カーソル青と重ならない配色）。lastValueVisible で軸ラベルも表示。
  const up = CHROME_CURRENT.candleUp;
  const down = CHROME_CURRENT.candleDown;
  const mainSeries = chart.addSeries(lwc.CandlestickSeries, {
    upColor: up, downColor: down,
    borderUpColor: up, borderDownColor: down,
    wickUpColor: up, wickDownColor: down,
    priceLineVisible: true,
    priceLineColor: CHROME_CURRENT.priceLine,
    priceLineWidth: 1,
    lastValueVisible: true,
    // 表示桁（価格軸・現在値ラベル・クロスヘアの読み）を銘柄の刻みへ合わせる（A-3）。
    //   仕様が無ければキーごと現れない＝従来と同一のオプションになる。
    ...priceFormatOf(symbolSpec),
  });
  return { chart, mainSeries };
}

// 価格軸ホイールズームの座標→価格変換に使う pane 高（container 高 - timeScale 高）の供給関数を作る。
//   coordinateToPrice(paneHeight) で価格レンジ下端を読むために必要。container/timeScale 非対応
//   （SSR/テスト）では設定できないため no-op（handlePriceWheel は pane 高未供給時に安全に false）。
//   リサイズで container 高が変わるため、呼び出し側（wheel 発火時等）が随時再実行して追随する。
export function makeUpdatePaneHeight({ container, chart, renderer }) {
  return () => {
    if (typeof renderer.setPaneHeight !== 'function') {
      return;
    }
    const ch = container && typeof container.clientHeight === 'number' ? container.clientHeight : 0;
    const ts = typeof chart.timeScale === 'function' ? chart.timeScale() : null;
    const th = ts && typeof ts.height === 'function' ? ts.height() : 0;
    const paneHeight = ch - th;
    if (paneHeight > 0) {
      renderer.setPaneHeight(paneHeight);
    }
  };
}
