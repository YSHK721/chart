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

// σ 水準線のカラースキーム（histogram の level_colors と同義: 中心からの距離で 緑→赤）。
// 端点は common/level_colors.py の _CALM/_HOT（#2e7d32 / #d32f2f）に一致させる。
const SCHEME_CALM = [46, 125, 50]; // 緑（中心＝穏やか）
const SCHEME_HOT = [211, 47, 47]; // 赤（両極端＝過熱）
// 明度係数（背景 #131722 に馴染ませる。小さいほど暗い。0..1）。灰一色より色で識別でき、かつ控えめ。
const LEVEL_LINE_DIM = 0.55;

function lerp(a, b, t) {
  return a + (b - a) * t;
}

// 中心からの距離比 t∈[0,1] を 緑→赤 へ補間し dim で減光した rgb 文字列にする。
function schemeColor(t, dim) {
  const r = Math.round(lerp(SCHEME_CALM[0], SCHEME_HOT[0], t) * dim);
  const g = Math.round(lerp(SCHEME_CALM[1], SCHEME_HOT[1], t) * dim);
  const b = Math.round(lerp(SCHEME_CALM[2], SCHEME_HOT[2], t) * dim);
  return `rgb(${r}, ${g}, ${b})`;
}

// 指標値の簡易整形（クロスヘア表示・機能③）。
function fmtValue(v) {
  if (v === null || v === undefined || !Number.isFinite(v)) {
    return '';
  }
  return Number(v).toLocaleString(undefined, { maximumFractionDigits: 3 });
}

export class ChartRenderer {
  // chart: LightweightCharts.createChart(...) の戻り（addSeries/addPane/panes/removePane を持つ）。
  // mainSeries: addSeries(CandlestickSeries, ...) の戻り（pane 0・createPriceLine を持つ）。
  // lwc: グローバル LightweightCharts 名前空間（LineSeries/HistogramSeries/createTextWatermark）。
  constructor({ chart, mainSeries, lwc }) {
    this._chart = chart;
    this._mainSeries = mainSeries;
    this._lwc = lwc ?? {};
    // instanceId -> { lines, priceLines, hlinePayloads, visible, scaleHost, priceLineHost,
    //                 pane, watermark, paneName }
    this._instances = new Map();
    this._mainStretchSet = false;
    // 機能③: クロスヘア移動で pane ウォーターマークへ系列値を追記。
    if (typeof this._chart.subscribeCrosshairMove === 'function') {
      this._chart.subscribeCrosshairMove((param) => this._onCrosshairMove(param));
    }
  }

  // 時間足切替: メインローソク系列のデータを差し替え、可視範囲を全体へ合わせる。
  setCandles(candles) {
    this._mainSeries.setData(candles ?? []);
    this._chart.timeScale().fitContent();
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
  _onCrosshairMove(param) {
    const seriesData = param && param.seriesData;
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

  // UC-04 表示/非表示。line/histogram は applyOptions({visible})、priceLine は除去/再生成。
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
