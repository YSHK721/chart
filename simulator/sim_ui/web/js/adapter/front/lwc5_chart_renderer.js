// lightweight-charts v5.2.0 アダプタ（F-3）。
//
// **本ファイルが sim 表示層で唯一 lwc に触れる場所**である（DIP: View も合成根も lwc を
// 知らない）。移植元 report_ui/web/js/chart.js は vendor v4.1.3 の API で書かれており、
// 統合 UI が読み込む vendor は v5.2.0 なので、置換が要るのは **API の呼び方だけ**である。
// 表示規則（マーカー構築・減光・可視絞り・badge 文言・balance/DD 派生）は移植元から
// **注入**して使う（`logic`）。規則をここへ書き写せば必ず食い違う（パリティのドリフト）。
//
// v5 置換範囲（vendor 実測 2026-08-11・indigators/indicator_ui/web/vendor/lightweight-charts.js
// の v5.2.0 に対する grep で在中を確認済み）:
//   addCandlestickSeries → addSeries(CandlestickSeries, …)
//   addAreaSeries        → addSeries(AreaSeries, …)
//   addBaselineSeries    → addSeries(BaselineSeries, …)
//   series.setMarkers    → createSeriesMarkers(series, markers) が返すハンドルの setMarkers
//   timeScale().subscribeVisibleLogicalRangeChange / setVisibleLogicalRange / getVisibleRange /
//   setVisibleRange / subscribeVisibleTimeRangeChange・chart.subscribeCrosshairMove /
//   setCrosshairPosition / clearCrosshairPosition・CrosshairMode … いずれも v5.2.0 に在中。
//
// 色・レイアウトの定数は移植元 chart.js の `_common()` / 各系列オプションと同値にする
// （見た目の一致はここでしか作れない＝v4/v5 でオプション名が同じであることを実測済み）。

// E2E フック（実 UI の実測点）。移植元 chart.js が `window.__priceChart`（:307）と
// `window.__candlesDimmed`（:382/:386）を出しており、report_ui の verify_*.py はそれを
// 観測点にしている。sim も同じ配り方にする——出さなければ「ブラウザで確かに減光した」を
// 実測する手段が無くなり、パリティは主張だけになる。
// `__candlesDimmed` は移植元と**同名**にする（二画面突合で同じ式を当てるため）。
// window の無い実行環境（node:test）では何もしない。
function publish(name, value) {
  if (typeof window !== "undefined") window[name] = value;
}

const CHART_LAYOUT = {
  layout: { background: { color: "#0e1117" }, textColor: "#c9d1d9" },
  grid: { vertLines: { color: "#1b2230" }, horzLines: { color: "#1b2230" } },
  rightPriceScale: { borderColor: "#272d38" },
  autoSize: true,
};

const BASE_TIME_SCALE = {
  timeVisible: true, secondsVisible: false, borderColor: "#272d38", minBarSpacing: 0.004,
};

/**
 * v5 の 3 窓レンダラを作る。
 * @param {object} lwc     グローバル LightweightCharts（v5.2.0 名前空間）
 * @param {object} hosts   { chart, bal, dd, badge } — View が所有する要素
 * @param {object} logic   移植元 chart.js の純関数群（合成根が /sim/report-js/chart.js から注入）
 */
export function createLwc5ChartRenderer({ lwc, hosts, logic }) {
  let priceChart = null, balChart = null, ddChart = null;
  let candle = null, balSeries = null, ddSeries = null;
  let markerHandle = null;
  let barTimes = [], barsNormal = [], barsDim = [], dimmed = false;
  let rows = [];
  let lastMarkerOpts = { hoverId: null, filter: null };
  let markerHoverCb = null;
  // 自分が vendor へ書き込んでいる区間（R-A3 の再入ガード）。この区間に届いた crosshair は
  //   利用者の操作ではなく**自分の描画が動かした結果**なので、発行元で捨てる。
  let writing = false;
  // crosshair 購読を張ったか（R-A1: 描画のたびに購読を足さない・参照実装 chart.js:311-314）。
  let crosshairWired = false;

  /** vendor への書き込みを 1 か所に集約し、その区間を lock で囲む（R-A3）。 */
  function write(fn) {
    const outer = writing;
    writing = true;
    try { return fn(); } finally { writing = outer; }
  }

  /** vendor の描画経路の**外**で実行する（R-A2）。
   *
   * 実測（v5.2.0・2026-08-12）: crosshair 購読の同期経路の中で vendor へ書き込むと、
   * vendor 自身の hitTest → renderer → priceToCoordinate が再入して
   * `RangeError: Maximum call stack size exceeded` になる（捕捉したスタックは全フレームが
   * vendor 内）。マイクロタスクへ逃がすと、書き込みは当該イベントの処理が終わってから走る。
   */
  const defer = (fn) => queueMicrotask(fn);

  const commonOptions = () => ({
    ...CHART_LAYOUT,
    crosshair: { mode: lwc.CrosshairMode.Normal },
  });

  // 3 窓の可視論理レンジを相互同期する（点3）。lock で相互再入を止める
  //   （参照実装 chart.js:185-195 と同一イディオム）。
  function syncLogicalRange(charts) {
    let lock = false;
    for (const src of charts) {
      src.timeScale().subscribeVisibleLogicalRangeChange((lr) => {
        if (lock || !lr) return;
        lock = true;
        write(() => {
          for (const other of charts) {
            if (other === src) continue;
            try { other.timeScale().setVisibleLogicalRange(lr); } catch (e) { /* noop */ }
          }
        });
        lock = false;
      });
    }
  }

  // クロスヘア（縦線）を全窓で同期する（点4）。他窓の同時刻値は byTime 索引から引く。
  //   書き込み（setCrosshairPosition / clearCrosshairPosition）は**購読の同期経路に置かない**
  //   （R-A2）。ハンドラは param から必要な値を読むだけで、反映は描画経路の外で行う。
  function syncCrosshair(items) {
    for (const src of items) {
      src.chart.subscribeCrosshairMove((param) => {
        if (writing) return;  // 自分の描画が動かした crosshair は偽入力（R-A3）
        const clear = !param || param.time === undefined || param.point === undefined;
        const time = clear ? null : param.time;
        defer(() => write(() => {
          for (const other of items) {
            if (other === src) continue;
            try {
              if (clear) {
                other.chart.clearCrosshairPosition();
              } else {
                const v = other.byTime.get(time);
                other.chart.setCrosshairPosition(v === undefined ? 0 : v, time, other.series);
              }
            } catch (e) { /* noop */ }
          }
        }));
      });
    }
  }

  /** param からマーカー id（"e"+id / "x"+id）を読む。命中していなければ null（読み取りのみ）。 */
  function markerIdOf(param) {
    const oid = param && param.hoveredObjectId;
    if (typeof oid !== "string" || (oid[0] !== "e" && oid[0] !== "x")) return null;
    const id = Number(oid.slice(1));
    return Number.isNaN(id) ? null : id;
  }

  // マーカーグリフ命中を注入コールバックへ通知する。購読は 1 回だけ張り（R-A1）、
  //   ハンドラは読み取りと通知のみを行う（R-A2）。chart は入力（通知）と出力（描画）の
  //   一方向で、状態の真実源は linkage 側にある（R-A4）。
  function ensureCrosshairWired(chart) {
    if (crosshairWired || !chart) return;
    crosshairWired = true;
    chart.subscribeCrosshairMove((param) => {
      if (writing) return;                    // 自分の描画由来は発行しない（R-A3）
      const id = markerIdOf(param);
      // 重複排除はここでは行わない（参照実装 chart.js:312-321 に抑止なし）。冪等ガードは
      //   真実源の linkage.setHover が持つ（R-A4）。renderer 側で id を記憶すると、
      //   テーブル起点で hover が変わったとき「null 通知＝既知」と誤判定して解除が届かない
      //   （ISSUE-379 で実測した明示バグ）。
      defer(() => { if (markerHoverCb) markerHoverCb(id); });
    });
  }

  function destroy() {
    for (const c of [priceChart, balChart, ddChart]) {
      if (c) { try { c.remove(); } catch (e) { /* noop */ } }
    }
    priceChart = balChart = ddChart = null;
    candle = balSeries = ddSeries = null;
    markerHandle = null;
    dimmed = false;
    // 破棄した chart に張った購読は道連れになる。次の chart で張り直せるよう戻す
    //   （参照実装 chart.js:224 `_destroyCharts` が `_crosshairWired = false` に戻すのと同じ）。
    crosshairWired = false;
    publish("__simPriceChart", null);
    publish("__simCandleSeries", null);
    publish("__candlesDimmed", false);
  }

  function setMarkers(markers) {
    if (!candle) return;
    // v5: 系列に setMarkers は無い。ハンドルを 1 本だけ作って以降は使い回す
    // （呼ぶたびに作ると重ね描きになり、消したはずのマーカーが残る）。
    write(() => {
      if (markerHandle) markerHandle.setMarkers(markers);
      else markerHandle = lwc.createSeriesMarkers(candle, markers);
    });
  }

  function visibleTrades(source) {
    const range = priceChart ? priceChart.timeScale().getVisibleRange() : null;
    return logic.visibleTradesInRange(source, range);
  }

  function renderMarkers(source, opts) {
    if (!candle) return;
    const { hoverId = null, filter = null } = opts || {};
    lastMarkerOpts = { hoverId, filter };
    let visible = visibleTrades(source || rows);
    if (filter) visible = visible.filter((t) => filter.has(t.id));
    if (hosts.badge) hosts.badge.textContent = logic.chartBadgeText(visible.length);
    if (visible.length > logic.MARKER_CAP) { setMarkers([]); return; }
    setMarkers(logic.buildTradeMarkers(visible, hoverId));
  }

  function restoreCandles() {
    // R-A5: 事前計算済みの barsNormal を 1 回流すだけ（毎回の再構築をしない）。
    if (dimmed && candle) { write(() => candle.setData(barsNormal)); dimmed = false; }
    publish("__candlesDimmed", dimmed);
  }

  // 3 窓を構築する（`render` が lock 区間の中でだけ呼ぶ）。
  function build(segment, opts) {
    priceChart = lwc.createChart(hosts.chart, {
      ...commonOptions(), timeScale: { ...BASE_TIME_SCALE, visible: false },
    });
    if (hosts.bal) {
      balChart = lwc.createChart(hosts.bal, {
        ...commonOptions(), timeScale: { ...BASE_TIME_SCALE, visible: false },
      });
    }
    if (hosts.dd) {
      ddChart = lwc.createChart(hosts.dd, {
        ...commonOptions(), timeScale: { ...BASE_TIME_SCALE, visible: true },
      });
    }

    const bars = segment.bars || [];
    candle = priceChart.addSeries(lwc.CandlestickSeries, {
      upColor: "#26a69a", downColor: "#ef5350",
      borderVisible: false, wickUpColor: "#26a69a", wickDownColor: "#ef5350",
    });
    barsNormal = bars.map((b) => ({
      time: b.time, open: b.open, high: b.high, low: b.low, close: b.close,
    }));
    barsDim = logic.buildDimBars(bars);
    barTimes = bars.map((b) => b.time);
    candle.setData(barsNormal);
    publish("__simPriceChart", priceChart);
    publish("__simCandleSeries", candle);

    const initialDeposit = (opts && opts.initialDeposit)
      || (segment.meta && segment.meta.initial_deposit)
      || logic.DEFAULT_DEPOSIT;
    const curve = (segment.agg && segment.agg.balance_curve) || [];
    const { balData, ddData } = logic.balanceForwardFill(barTimes, curve, initialDeposit);

    if (balChart) {
      // 点1 Balance: エリア系列（フィル＋縦グラデ・低不透明度）。
      balSeries = balChart.addSeries(lwc.AreaSeries, {
        lineColor: "rgba(59,130,246,0.9)",
        topColor: "rgba(59,130,246,0.40)", bottomColor: "rgba(59,130,246,0.03)",
        lineWidth: 2, priceLineVisible: false, lastValueVisible: true,
      });
      balSeries.setData(balData);
    }
    if (ddChart) {
      // 点2 Drawdown: ベースライン 0 基準（アンダーウォーター・下方フィル）。
      ddSeries = ddChart.addSeries(lwc.BaselineSeries, {
        baseValue: { type: "price", price: 0 },
        topLineColor: "rgba(239,83,80,0)", topFillColor1: "rgba(239,83,80,0)", topFillColor2: "rgba(239,83,80,0)",
        bottomLineColor: "rgba(239,83,80,0.9)", bottomFillColor1: "rgba(239,83,80,0.05)", bottomFillColor2: "rgba(239,83,80,0.42)",
        lineWidth: 2, priceLineVisible: false, lastValueVisible: true,
      });
      ddSeries.setData(ddData);
    }

    syncLogicalRange([priceChart, balChart, ddChart].filter(Boolean));
    const items = [{
      chart: priceChart, series: candle,
      byTime: logic.byTimeResolve(barsNormal.map((b) => ({ time: b.time, value: b.close }))),
    }];
    if (balSeries) items.push({ chart: balChart, series: balSeries, byTime: logic.byTimeResolve(balData) });
    if (ddSeries) items.push({ chart: ddChart, series: ddSeries, byTime: logic.byTimeResolve(ddData) });
    syncCrosshair(items);
    ensureCrosshairWired(priceChart);

    rows = segment.trades || [];
    const first = rows.length ? rows[0].entry_time : (barTimes[0] || 0);
    const last = rows.length ? rows[rows.length - 1].exit_time : (barTimes[barTimes.length - 1] || 0);
    priceChart.timeScale().setVisibleRange({ from: first - 600, to: last + 600 });
    // pan/zoom で可視件数が変わるため、直近の描画意図（hover/filter）を保ったまま再描画する。
    priceChart.timeScale().subscribeVisibleTimeRangeChange(() => {
      renderMarkers(rows, lastMarkerOpts);
    });
    renderMarkers(rows, { hoverId: null, filter: null });
  }

  return {
    /** 区間（segment）を 3 窓へ描画する（点1,2,3,4,7）。再描画は前の窓を破棄してから行う。 */
    render(segment, opts) {
      if (!hosts.chart) return;
      destroy();
      // 構築中の書き込みはすべて自分由来。区間全体を lock で囲む（R-A3）。
      write(() => build(segment, opts));
    },

    renderMarkers,
    restoreCandles,

    /** ペア区間 [entry_time, exit_time] 以外のローソク足を減光する（点 S4）。 */
    dimCandlesForTrade(trade) {
      if (!candle) return;
      if (!trade || trade.entry_price == null) { restoreCandles(); return; }
      // R-A5: barsNormal / barsDim は render で事前計算済み。hover 時は merge を 1 回流すだけ。
      write(() => candle.setData(logic.mergeDimBarsForTrade(barTimes, barsNormal, barsDim, trade)));
      dimmed = true;
      publish("__candlesDimmed", true);
    },

    /** 時刻 t を中心にズームする（明細行クリック連動）。 */
    focusTime(time, span = 3 * 3600) {
      if (!priceChart) return;
      write(() => priceChart.timeScale().setVisibleRange({ from: time - span / 2, to: time + span / 2 }));
    },

    /** マーカーグリフ hover の通知先を登録する（chart→linkage の直接 import を作らない）。 */
    onMarkerHover(cb) { markerHoverCb = cb; },

    /** 登録済み通知先を直接起動する（移植元 chart.js の `emitMarkerHover` と同流儀）。
     *  グリフの画素を掴まずに chart→table 方向を駆動できる＝E2E の実測点になる。 */
    emitMarkerHover(id) { if (markerHoverCb) markerHoverCb(id); },

    resize() {
      write(() => {
        for (const c of [priceChart, balChart, ddChart]) {
          if (c) { try { c.resize ? c.resize() : c.applyOptions({}); } catch (e) { /* noop */ } }
        }
      });
    },

    destroy,

    currentRows() { return rows; },

    /** E2E フック用（TBD-04 の実測で使う実体）。 */
    handles() { return { priceChart, candle, markerHandle }; },
  };
}
