// composition_root_front.js（フロント側 Composition Root）。
//
// 設計入力: 内部設計書 §2.1（framework/front/composition_root_front.js）。
// 依存配線: catalog / compute / persistence / renderer を IndicatorController に注入する唯一の点。
//   - upstream JS（LightweightCharts）は ChartRenderer の生成にのみ使い、ここでは
//     chart / mainSeries を作って渡す（系列追加系 API 名はここで参照しない）。
//
// 注: 本ファイルは LightweightCharts.createChart / addCandlestickSeries を呼ぶ「組み立て」点。
//   系列追加系 API は ChartRenderer 内に閉じる（§2.2 隔離・grep 0 件対象）。

import { SAMPLE_DATA } from '../../../data/sample_data.js';
import { ChartRenderer } from './chart_renderer.js';
import { EmbeddedComputeGateway } from './embedded_compute_gateway.js';
import { LocalStorageGateway } from './local_storage_gateway.js';
import { IndicatorCatalogClient } from './catalog_client.js';
import { IndicatorController } from './indicator_controller.js';

// グローバル LightweightCharts（bundled JS が window へ公開）を引数で受け取り、
// チャート + ローソク系列を生成して ChartRenderer に渡す。
export function bootstrap({ lwc, container, doc = (typeof document !== 'undefined' ? document : null), storage } = {}) {
  // チャート生成（組み立て点。系列追加系 API は ChartRenderer に隠蔽）。
  const chart = lwc.createChart(container, {
    layout: { background: { color: '#131722' }, textColor: '#d1d4dc' },
    grid: { vertLines: { color: '#1f2530' }, horzLines: { color: '#1f2530' } },
    rightPriceScale: { borderColor: '#2a2e39' },
    timeScale: { borderColor: '#2a2e39', timeVisible: false },
    autoSize: true,
  });
  const mainSeries = chart.addCandlestickSeries({
    upColor: '#26a69a', downColor: '#ef5350',
    borderUpColor: '#26a69a', borderDownColor: '#ef5350',
    wickUpColor: '#26a69a', wickDownColor: '#ef5350',
  });
  mainSeries.setData(SAMPLE_DATA.candles);
  chart.timeScale().fitContent();

  // ポート実装の組み立て。
  const renderer = new ChartRenderer({ chart, mainSeries });
  const compute = new EmbeddedComputeGateway(SAMPLE_DATA);
  const persistence = new LocalStorageGateway(storage);
  const catalog = new IndicatorCatalogClient();

  const controller = new IndicatorController({ catalog, compute, persistence, renderer, document: doc });
  return { chart, mainSeries, renderer, controller };
}
