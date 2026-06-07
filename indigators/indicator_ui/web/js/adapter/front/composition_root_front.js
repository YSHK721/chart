// composition_root_front.js（フロント側 Composition Root）。
//
// 設計入力: 内部設計書 §2.1（framework/front/composition_root_front.js）、§3.3.5（ComputeHttpClient）、
//   §6.3（/candles）、内部設計_パラメータ設定ダイアログ §9（B方式 params 実反映）。
// 依存配線: catalog / compute / persistence / renderer を IndicatorController に注入する唯一の点。
//   - upstream JS（LightweightCharts）は ChartRenderer の生成にのみ使い、ここでは
//     chart / mainSeries を作って渡す（系列追加系 API 名はここで参照しない）。
//
// 方式切替（B方式を既定に）:
//   - served（http://・https://）: ComputeHttpClient（fetch /compute）を注入し、candles は
//     GET /candles から取得する（same-origin・CORS/バンドル不要）。params が実再計算される。
//   - file:// 単体時: 従来 EmbeddedComputeGateway + SAMPLE_DATA にフォールバック（A方式）。
//   判定は location.protocol（http/https → 'b' / それ以外 → 'a'）。

import { SAMPLE_DATA } from '../../../data/sample_data.js';
import { ChartRenderer } from './chart_renderer.js';
import { ComputeHttpClient } from './compute_http_client.js';
import { EmbeddedComputeGateway } from './embedded_compute_gateway.js';
import { LocalStorageGateway } from './local_storage_gateway.js';
import { IndicatorCatalogClient } from './catalog_client.js';
import { IndicatorController } from './indicator_controller.js';

// protocol → モード判定。http/https は served（B方式）、それ以外（file: 等）は A方式。
export function modeForProtocol(protocol) {
  return protocol === 'http:' || protocol === 'https:' ? 'b' : 'a';
}

// GET /candles?datasetRef=sample で candles を取得する（B方式）。失敗時は null（フォールバック）。
async function fetchCandles(fetchImpl, datasetRef = 'sample') {
  if (typeof fetchImpl !== 'function') {
    return null;
  }
  try {
    const resp = await fetchImpl(`/candles?datasetRef=${encodeURIComponent(datasetRef)}`);
    if (!resp.ok) {
      return null;
    }
    const payload = await resp.json();
    return payload && payload.ok ? payload.candles : null;
  } catch {
    return null;
  }
}

// グローバル LightweightCharts（bundled JS が window へ公開）を引数で受け取り、
// チャート + ローソク系列を生成して ChartRenderer に渡す。
// served（http://）時は ComputeHttpClient + /candles、file:// 時は EmbeddedComputeGateway + SAMPLE_DATA。
export function bootstrap({
  lwc,
  container,
  doc = (typeof document !== 'undefined' ? document : null),
  storage,
  // served 判定・/candles 取得・/compute 用の注入（テスト・SSR で差し替え可能）。
  protocol = (typeof location !== 'undefined' ? location.protocol : 'file:'),
  fetch = (typeof globalThis !== 'undefined' ? globalThis.fetch : undefined),
} = {}) {
  const mode = modeForProtocol(protocol);

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
  // 初期ローソクは SAMPLE_DATA で即時描画（A方式の主データ・B方式の暫定）。
  mainSeries.setData(SAMPLE_DATA.candles);
  chart.timeScale().fitContent();

  // ポート実装の組み立て（モード別）。
  //   B方式: ComputeHttpClient（fetch /compute）— params 実反映。
  //   A方式: EmbeddedComputeGateway（埋め込み事前計算）— params 未反映。
  const compute = mode === 'b'
    ? new ComputeHttpClient({ fetch })
    : new EmbeddedComputeGateway(SAMPLE_DATA);

  const renderer = new ChartRenderer({ chart, mainSeries });
  const persistence = new LocalStorageGateway(storage);
  const catalog = new IndicatorCatalogClient();

  const controller = new IndicatorController({ catalog, compute, persistence, renderer, document: doc, mode });

  // B方式は /candles から実 OHLCV を取得し、メイン系列を差し替える（/compute と時間軸を揃える）。
  //   取得失敗時は SAMPLE_DATA のまま（フォールバック）。ready は呼び出し側で await 可能。
  const ready = (mode === 'b')
    ? fetchCandles(fetch).then((candles) => {
        if (candles && candles.length > 0) {
          mainSeries.setData(candles);
          chart.timeScale().fitContent();
        }
      })
    : Promise.resolve();

  return { chart, mainSeries, renderer, controller, mode, ready };
}
