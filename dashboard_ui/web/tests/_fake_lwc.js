// lightweight-charts のテストダブル（チャート一覧の検定で共有する）。
//
// 固定したいのは「View がライブラリの面をどう呼ぶか」（createChart / addSeries / setData /
//   createPriceLine / applyOptions / removePriceLine / remove）であって canvas の画素ではない
//   （実描画の実測は e2e の責務・_fake_dom.js と同じ理由）。
//
// stats は計算量テスト（charts_paint_complexity.test.js）の Test Spy を兼ねる:
//   発行（createPriceLine / setData …）を数え、「発行 − 出力に残った線 = 0」を表明する。

/** v5 の面のうち View が触る部分だけを持つ lwc ダブルを作る。 */
export function fakeLwc() {
  const charts = [];
  const stats = {
    createChart: 0,
    addSeries: 0,
    setData: 0,
    createPriceLine: 0,
    removePriceLine: 0,
    applyOptions: 0,
    chartRemove: 0,
  };

  function makeSeries(kind, seriesOptions) {
    const series = {
      kind,
      options: seriesOptions ?? {},
      data: [],
      priceLines: [],
      setData(rows) {
        stats.setData += 1;
        series.data = rows;
      },
      createPriceLine(lineOptions) {
        stats.createPriceLine += 1;
        const line = {
          options: { ...lineOptions },
          applyOptions(patch) {
            stats.applyOptions += 1;
            Object.assign(line.options, patch);
          },
        };
        series.priceLines.push(line);
        return line;
      },
      removePriceLine(line) {
        stats.removePriceLine += 1;
        series.priceLines = series.priceLines.filter((kept) => kept !== line);
      },
    };
    return series;
  }

  const lwc = {
    ColorType: { Solid: 'solid' },
    LineStyle: { Dashed: 2 },
    CandlestickSeries: Symbol('CandlestickSeries'),
    createChart(container, chartOptions) {
      stats.createChart += 1;
      const chart = {
        container,
        options: chartOptions ?? {},
        series: [],
        removed: false,
        addSeries(kind, seriesOptions) {
          stats.addSeries += 1;
          const series = makeSeries(kind, seriesOptions);
          chart.series.push(series);
          return series;
        },
        remove() {
          stats.chartRemove += 1;
          chart.removed = true;
        },
      };
      charts.push(chart);
      return chart;
    },
  };

  /** 全チャートに現存する価格線の総数（「出力に残った線」の数え上げ）。 */
  function attachedPriceLines() {
    return charts.reduce(
      (sum, chart) => sum + chart.series.reduce((s, series) => s + series.priceLines.length, 0),
      0,
    );
  }

  /** stats の複製（後で差分を取るための静止画）。 */
  function snapshot() {
    return { ...stats };
  }

  return { lwc, charts, stats, attachedPriceLines, snapshot };
}
