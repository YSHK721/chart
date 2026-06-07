// ComputeHttpClient（adapter/front/compute_http_client.js）— ComputeGateway 実装・B方式（fetch）。
//
// 設計入力: 内部設計書 §3.3.5（ComputeRequest→fetch POST→ComputeResult）、§6.3.1 リクエスト、
//   §6.3.2/6.3.3 正常応答（series）、§6.3.4 エラー応答（error.type/message）、§7.1.1 契約。
//
// 契約:
//   - POST /compute に JSON ボディ（indicatorId/variant/params/datasetRef）を送る。
//   - 200 応答なら body.series を返す。
//   - 非200 応答は body.error（{type,message}）を読み ComputeError（error_type 保持）を throw。
//   - ネットワーク例外（fetch reject）は ComputeError（error_type='network'）へ翻訳して throw。
//
// fetch は注入可能（コンストラクタ）。DOM・実ネットワークに依存しない（テスト容易）。

// ComputeError は domain へ集約（単一定義）。本ファイルからは re-export し、呼び出し側
//   （import { ComputeHttpClient, ComputeError } ...）を破壊しない。error_type を保持する。
import { ComputeError } from '../../domain/compute_error.js';

export { ComputeError };

const COMPUTE_ENDPOINT = '/compute';

export class ComputeHttpClient {
  // deps.fetch: 注入された fetch（既定はグローバル fetch）。
  constructor({ fetch } = {}) {
    this._fetch = fetch;
  }

  // ComputeRequest -> series（§7.1.1）。非200/ネットワーク例外は ComputeError へ翻訳。
  async compute({ indicatorId, variant, params, datasetRef } = {}) {
    const body = JSON.stringify({ indicatorId, variant, params, datasetRef });

    let response;
    try {
      response = await this._fetch(COMPUTE_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
      });
    } catch (cause) {
      // ネットワーク例外（タイムアウト・接続断等）を翻訳（§7.1.1 例外翻訳）。
      throw new ComputeError(`計算 API への接続に失敗しました: ${cause.message}`, { error_type: 'network' });
    }

    const payload = await response.json();

    if (!response.ok) {
      const error = (payload && payload.error) || {};
      throw new ComputeError(error.message || `計算 API エラー (HTTP ${response.status})`, {
        error_type: error.type || 'internal',
      });
    }

    return payload.series;
  }
}
