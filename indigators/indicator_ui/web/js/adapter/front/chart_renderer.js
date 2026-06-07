// ChartRenderer（adapter/front/chart_renderer.js）— ChartRendererPort 実装・upstream 隔離点（唯一）。
//
// 設計入力: 内部設計書 §3.3.4 / §7.1.2。
// ★ lightweight-charts v4.1.3 の JS API 名（addLineSeries / createPriceLine / setData /
//   applyOptions / removeSeries / removePriceLine）を呼ぶのは本ファイルだけ。
//   他ファイルでこれらの API 名を参照しない（§2.2 grep 0 件強制）。v5 移行時も波及をここに限定。
//
// 隠蔽する責務:
//   - line  → chart.addLineSeries(...) + series.setData(points)
//   - horizontal_line → mainSeries.createPriceLine(...)
//   - lineStyle 文字列 → v4.1.3 整数（solid=0 / dotted=1 / dashed=2）
//   - 系列キー {instanceId}::{series_name}（§5.7 衝突回避）
//
// DOM 非依存: chart / mainSeries は composition root から注入（テストは Fake を渡す）。

// lineStyle 文字列 → lightweight-charts v4.1.3 LineStyle 整数（実値は bundled JS で確認済み）。
const LINE_STYLE_INT = Object.freeze({ solid: 0, dotted: 1, dashed: 2 });

function toLineStyleInt(style) {
  return LINE_STYLE_INT[style] ?? LINE_STYLE_INT.solid;
}

export class ChartRenderer {
  // chart: LightweightCharts.createChart(...) の戻り（addLineSeries/removeSeries を持つ）。
  // mainSeries: addCandlestickSeries(...) の戻り（createPriceLine/removePriceLine を持つ）。
  constructor({ chart, mainSeries }) {
    this._chart = chart;
    this._mainSeries = mainSeries;
    // instanceId -> { lines: Map<seriesKey, series>, priceLines: [{pl, opt}], visible }
    this._instances = new Map();
  }

  _slot(instanceId) {
    let slot = this._instances.get(instanceId);
    if (!slot) {
      slot = { lines: new Map(), priceLines: [], hlinePayloads: null, visible: true };
      this._instances.set(instanceId, slot);
    }
    return slot;
  }

  // line 系列群を生成（§7.1.2: 系列キー {instanceId}::{name} で生成・表示）。
  renderLine(instanceId, payloads) {
    const slot = this._slot(instanceId);
    for (const p of payloads ?? []) {
      const series = this._chart.addLineSeries({
        color: p.color,
        lineWidth: p.width,
        lineStyle: toLineStyleInt(p.style),
        priceLineVisible: false,
        lastValueVisible: false,
        title: p.name,
      });
      series.setData(p.data ?? []);
      const key = `${instanceId}::${p.name}`;
      slot.lines.set(key, series);
    }
  }

  // horizontal_line 群をメイン系列の priceLine として生成（price_range_power 用）。
  renderHorizontal(instanceId, hlines) {
    const slot = this._slot(instanceId);
    slot.hlinePayloads = hlines ?? [];
    this._createPriceLines(slot, slot.hlinePayloads);
  }

  _createPriceLines(slot, hlines) {
    for (const h of hlines ?? []) {
      const pl = this._mainSeries.createPriceLine({
        price: h.price,
        color: h.color,
        lineWidth: h.width,
        lineStyle: toLineStyleInt(h.style),
        title: h.text,
        axisLabelVisible: h.axis_label_visible ?? false,
      });
      slot.priceLines.push(pl);
    }
  }

  // UC-03 再計算: 既存系列を再生成せず data のみ差し替え。
  setData(seriesKey, points) {
    for (const slot of this._instances.values()) {
      const series = slot.lines.get(seriesKey);
      if (series) {
        series.setData(points ?? []);
        return;
      }
    }
  }

  // UC-04 表示/非表示。line は applyOptions({visible})、priceLine は除去/再生成。
  setVisible(instanceId, visible) {
    const slot = this._instances.get(instanceId);
    if (!slot) {
      return;
    }
    slot.visible = visible;
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
    for (const pl of slot.priceLines) {
      this._mainSeries.removePriceLine(pl);
    }
    slot.priceLines = [];
  }

  // UC-05 削除（冪等）。
  remove(instanceId) {
    const slot = this._instances.get(instanceId);
    if (!slot) {
      return;
    }
    for (const series of slot.lines.values()) {
      this._chart.removeSeries(series);
    }
    this._removePriceLines(slot);
    this._instances.delete(instanceId);
  }
}
